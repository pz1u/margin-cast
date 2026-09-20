import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from src.agent_audit import AgentAuditStore
from src.agent_chat_service import AgentChatService, MemorySessionStore
from src.agent_tool_contracts import execute_tool
from src.decision_service import MarginCastDecisionService
from src.generate_data import generate_dataset, save_dataset
from src.openai_provider import OpenAIProvider
from src.prepare_analysis_data import prepare_analysis_data
from tests.test_agent_http_api import RecordingExecutor
from tests.test_agent_weather_flow import (
    ForecastExecutor,
    RecordingLocationResolver,
)
from tests.test_openai_provider import StubHttpClient, StubResponse, final_response, tool_response


def forecast_tool_response(price=9500, call_id="call_openai_forecast"):
    arguments = {
        "menu_id": "M01",
        "scenarios": [
            {"name": "가격 변경", "list_price": price, "discount": 0}
        ],
        "location": {"kma_nx": 60, "kma_ny": 127},
        "horizon_days": 4,
    }
    response = tool_response(
        arguments=arguments,
        name="compare_price_strategies_with_forecast",
    )
    response.payload["output"][1]["call_id"] = call_id
    return response


def weather_final_response():
    content = json.dumps(
        {
            "decision_claim": "EXPERIMENT",
            "explanation": (
                "합성 데이터 결과이며 근거 품질은 아직 미보정 상태입니다. "
                "실제 단기예보를 미래 수요 문맥에 반영했습니다."
            ),
            "next_action": "작은 범위의 검증을 먼저 준비해주세요.",
        },
        ensure_ascii=False,
    )
    return StubResponse(
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": content}],
                }
            ],
        }
    )


class ProviderSequence:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.providers = []

    def __call__(self):
        provider = OpenAIProvider(
            api_key="test-key",
            model="test-openai-model",
            http_client=StubHttpClient(*next(self.responses)),
        )
        self.providers.append(provider)
        return provider


class OpenAIAgentFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine_directory = tempfile.TemporaryDirectory()
        root = Path(cls.engine_directory.name)
        tables, truth = generate_dataset()
        save_dataset(tables, truth, root)
        panel_path = root / "processed" / "demand_panel.csv"
        prepare_analysis_data(root, panel_path)
        (root / "ground_truth.json").unlink()
        cls.engine = MarginCastDecisionService(panel_path)

    @classmethod
    def tearDownClass(cls):
        cls.engine_directory.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.audit_path = Path(self.temporary.name) / "audit.json"

    def tearDown(self):
        self.temporary.cleanup()

    def test_price_request_completes_through_response_policy(self):
        providers = ProviderSequence(
            [(tool_response(), final_response())]
        )
        executor = RecordingExecutor(self.engine)
        service = AgentChatService(
            provider_factory=providers,
            tool_executor=executor,
            capabilities_loader=self.engine.get_capabilities,
            audit_store=AgentAuditStore(self.audit_path),
            session_store=MemorySessionStore(),
        )

        status, payload = service.chat(
            "openai-price",
            "치킨마요를 9,500원으로 올리면 어때?",
        )

        self.assertEqual((status, payload["status"]), (200, "COMPLETED"))
        self.assertEqual(executor.calls[0][0], "compare_price_strategies")
        self.assertEqual(
            payload["facts"]["engine_decision"]["value"],
            "EXPERIMENT",
        )
        self.assertIsNotNone(payload["recommendation_id"])
        self.assertEqual(len(providers.providers), 1)

    def test_missing_price_stays_needs_input_without_provider_call(self):
        providers = ProviderSequence([])
        service = AgentChatService(
            provider_factory=providers,
            tool_executor=RecordingExecutor(self.engine),
            capabilities_loader=self.engine.get_capabilities,
            audit_store=AgentAuditStore(self.audit_path),
            session_store=MemorySessionStore(),
        )

        status, payload = service.chat(
            "openai-missing",
            "치킨마요 가격 올리면 어때?",
        )

        self.assertEqual((status, payload["status"]), (200, "NEEDS_INPUT"))
        self.assertEqual(payload["missing_input"]["fields"], ["list_price"])
        self.assertEqual(providers.providers, [])

    def test_weather_and_followup_reuse_grid_with_openai_provider(self):
        providers = ProviderSequence(
            [
                (forecast_tool_response(), weather_final_response()),
                (
                    forecast_tool_response(9700, "call_openai_forecast_2"),
                    weather_final_response(),
                ),
            ]
        )
        executor = ForecastExecutor(self.engine)
        resolver = RecordingLocationResolver()
        sessions = MemorySessionStore()
        service = AgentChatService(
            provider_factory=providers,
            tool_executor=executor,
            capabilities_loader=self.engine.get_capabilities,
            audit_store=AgentAuditStore(self.audit_path),
            session_store=sessions,
            location_resolver=resolver,
            now_factory=lambda: datetime(2026, 9, 20, 12, tzinfo=timezone.utc),
        )

        status, missing = service.chat(
            "openai-weather",
            "우리 매장 기준 다음 4일 동안 치킨마요를 9,500원으로 올리면 어때?",
        )
        first_status, first = service.chat(
            "openai-weather",
            "현재 위치를 사용합니다.",
            {
                "source": "browser_geolocation",
                "latitude": 37.5665,
                "longitude": 126.978,
            },
        )
        second_status, second = service.chat(
            "openai-weather",
            "그럼 9,700원은?",
        )

        self.assertEqual((status, missing["status"]), (200, "NEEDS_INPUT"))
        self.assertEqual((first_status, first["status"]), (200, "COMPLETED"))
        self.assertEqual((second_status, second["status"]), (200, "COMPLETED"))
        self.assertEqual(len(resolver.calls), 1)
        self.assertEqual(len(executor.calls), 2)
        self.assertEqual(
            executor.calls[1][1]["location"],
            {"kma_nx": 60, "kma_ny": 127},
        )
        self.assertEqual(
            executor.calls[1][1]["scenarios"][0]["list_price"],
            9700,
        )
        self.assertEqual(len(providers.providers), 2)


if __name__ == "__main__":
    unittest.main()