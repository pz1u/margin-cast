import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from src.agent_audit import AgentAuditStore
from src.agent_chat_service import AgentChatService, MemorySessionStore
from src.agent_schemas import ToolCall
from src.agent_tool_contracts import execute_tool
from src.decision_service import MarginCastDecisionService
from src.forecast_decision_service import ForecastDecisionService
from src.generate_data import generate_dataset, save_dataset
from src.http_api import dispatch_api
from src.mock_llm_provider import MockLLMProvider
from src.prepare_analysis_data import prepare_analysis_data


def forecast_rows(days=5):
    return [
        {
            "date": f"2026-09-{21 + day:02d}",
            "time": "12:00",
            "forecast_at": f"2026-09-{21 + day:02d}T12:00:00+09:00",
            "is_rain": day % 2 == 0,
            "tmp_c": 23.0,
            "reh_pct": 70.0,
        }
        for day in range(days)
    ]


def forecast_tool_call(price=9500, call_id="mock-forecast-call-1"):
    return ToolCall(
        call_id=call_id,
        name="compare_price_strategies_with_forecast",
        arguments={
            "menu_id": "M01",
            "scenarios": [
                {"name": "가격 변경", "list_price": price, "discount": 0}
            ],
            "location": {"kma_nx": 60, "kma_ny": 127},
            "horizon_days": 4,
        },
    )


class RecordingLocationResolver:
    def __init__(self):
        self.calls = []

    def __call__(self, location):
        self.calls.append(json.loads(json.dumps(location)))
        return {
            "source": location["source"],
            "kma_nx": 60,
            "kma_ny": 127,
        }


class ForecastExecutor:
    def __init__(self, engine, *, available_days=5):
        self.calls = []
        self.forecast_service = ForecastDecisionService(
            engine,
            forecast_fetcher=lambda nx, ny, env_path: forecast_rows(available_days),
        )
        self.engine = engine

    def __call__(self, tool_name, arguments):
        result = execute_tool(
            tool_name,
            arguments,
            service=self.engine,
            forecast_service=self.forecast_service,
        )
        self.calls.append((tool_name, json.loads(json.dumps(arguments)), result))
        return result


class AgentWeatherFlowTests(unittest.TestCase):
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
        self.session_store = MemorySessionStore()
        self.location_resolver = RecordingLocationResolver()
        self.audit_path = Path(self.temporary.name) / "agent-audit.json"

    def tearDown(self):
        self.temporary.cleanup()

    def service(self, *, available_days=5, final_text=None, tool_calls=None):
        executor = ForecastExecutor(self.engine, available_days=available_days)
        provider_text = final_text or (
            "실제 단기예보를 미래 수요 문맥에 반영했으며 "
            "근거 품질은 아직 미보정 상태입니다."
        )
        provider_calls = iter(tool_calls) if tool_calls is not None else None
        service = AgentChatService(
            provider_factory=lambda: MockLLMProvider(
                tool_call=(
                    next(provider_calls)
                    if provider_calls is not None
                    else forecast_tool_call()
                ),
                final_text=provider_text,
            ),
            tool_executor=executor,
            capabilities_loader=self.engine.get_capabilities,
            audit_store=AgentAuditStore(self.audit_path),
            session_store=self.session_store,
            location_resolver=self.location_resolver,
            now_factory=lambda: datetime(2026, 9, 20, 12, tzinfo=timezone.utc),
        )
        return service, executor

    @staticmethod
    def request(service, message, session_id="weather-session", location=None):
        body = {"session_id": session_id, "message": message}
        if location is not None:
            body["location"] = location
        return dispatch_api(
            "POST",
            "/api/agent/chat",
            body,
            agent_chat_service=service,
        )

    def test_weather_request_without_location_is_structured_needs_input(self):
        service, executor = self.service()

        status, payload = self.request(
            service,
            "우리 매장 기준 다음 4일 동안 치킨마요를 9,500원으로 올리면 어때?",
        )

        self.assertEqual((status, payload["status"]), (200, "NEEDS_INPUT"))
        self.assertEqual(payload["missing_input"]["input_type"], "location")
        self.assertEqual(
            [option["value"] for option in payload["missing_input"]["options"]],
            ["current_location", "address_search"],
        )
        self.assertEqual(executor.calls, [])

    def test_weather_condition_phrases_are_forecast_intent(self):
        for message in (
            "비 오면 치킨마요가 더 잘 팔린다고 설명해줘",
            "비 때문에 몇 개 더 팔리는 거야?",
            "습도가 높아서 매출이 오르는 거지?",
            "기온이 수요에 영향을 줘?",
        ):
            with self.subTest(message=message):
                self.assertTrue(AgentChatService._is_forecast_request(message))

        self.assertFalse(AgentChatService._is_forecast_request("재료 비용을 알려줘"))

    def test_browser_location_completes_forecast_and_exposes_engine_weather_facts(self):
        service, executor = self.service()
        self.request(
            service,
            "우리 매장 기준 다음 4일 동안 치킨마요를 9,500원으로 올리면 어때?",
        )

        status, payload = self.request(
            service,
            "현재 위치를 사용합니다.",
            location={
                "source": "browser_geolocation",
                "latitude": 37.5665,
                "longitude": 126.978,
            },
        )

        self.assertEqual((status, payload["status"]), (200, "COMPLETED"))
        self.assertEqual(executor.calls[0][0], "compare_price_strategies_with_forecast")
        self.assertEqual(
            executor.calls[0][1]["location"],
            {"kma_nx": 60, "kma_ny": 127},
        )
        self.assertEqual(executor.calls[0][1]["horizon_days"], 4)
        self.assertIs(payload["facts"]["forecast_used"]["value"], True)
        self.assertEqual(
            payload["facts"]["forecast_applied_dates"]["value"],
            {"from": "2026-09-21", "to": "2026-09-24"},
        )
        self.assertTrue(
            any("실제 기상청 단기예보" in notice for notice in payload["notices"])
        )
        self.assertNotIn("kma_nx", json.dumps(payload, ensure_ascii=False))

    def test_address_is_discarded_and_valid_grid_is_reused(self):
        service, executor = self.service()
        address = "서울특별시 중구 세종대로 테스트 매장"
        question = "우리 매장 기준 다음 4일 동안 치킨마요 9,500원은 어때?"
        self.request(service, question)
        first_status, first = self.request(
            service,
            "매장 위치를 검색합니다.",
            location={"source": "address_search", "address_query": address},
        )
        second_status, second = self.request(service, question)

        self.assertEqual((first_status, first["status"]), (200, "COMPLETED"))
        self.assertEqual((second_status, second["status"]), (200, "COMPLETED"))
        self.assertEqual(len(self.location_resolver.calls), 1)
        self.assertEqual(len(executor.calls), 2)
        state = self.session_store.get("weather-session")
        self.assertEqual(state.store_location["kma_nx"], 60)
        self.assertIn("resolved_at", state.store_location)
        self.assertIn("expires_at", state.store_location)
        serialized_state = json.dumps(state.__dict__, ensure_ascii=False)
        serialized_audit = self.audit_path.read_text(encoding="utf-8")
        serialized_responses = json.dumps([first, second], ensure_ascii=False)
        for serialized in (serialized_state, serialized_audit, serialized_responses):
            self.assertNotIn(address, serialized)
            self.assertNotIn("latitude", serialized)
            self.assertNotIn("longitude", serialized)
            self.assertNotIn("api_key", serialized.lower())

    def test_followup_price_reuses_same_session_grid_and_forecast_horizon(self):
        service, executor = self.service(
            tool_calls=[
                forecast_tool_call(),
                forecast_tool_call(9700, "mock-forecast-call-2"),
            ]
        )
        self.request(
            service,
            "우리 매장 기준 다음 4일 동안 치킨마요 9,500원은 어때?",
        )
        first_status, first = self.request(
            service,
            "현재 위치를 사용합니다.",
            location={
                "source": "browser_geolocation",
                "latitude": 37.5665,
                "longitude": 126.978,
            },
        )
        second_status, second = self.request(service, "그럼 9,700원은?")

        self.assertEqual((first_status, first["status"]), (200, "COMPLETED"))
        self.assertEqual((second_status, second["status"]), (200, "COMPLETED"))
        self.assertEqual(len(self.location_resolver.calls), 1)
        self.assertEqual(len(executor.calls), 2)
        self.assertEqual(executor.calls[1][0], "compare_price_strategies_with_forecast")
        self.assertEqual(executor.calls[1][1]["location"], {"kma_nx": 60, "kma_ny": 127})
        self.assertEqual(executor.calls[1][1]["horizon_days"], 4)
        self.assertEqual(executor.calls[1][1]["scenarios"][0]["list_price"], 9700)

    def test_weather_causal_question_does_not_downgrade_to_plain_price_tool(self):
        service, executor = self.service(
            tool_calls=[
                forecast_tool_call(),
                forecast_tool_call(call_id="mock-forecast-causal-followup"),
            ]
        )
        question = "우리 매장 기준 다음 4일 동안 치킨마요 9,500원은 어때?"
        self.request(service, question)
        first_status, first = self.request(
            service,
            "현재 위치를 사용합니다.",
            location={
                "source": "browser_geolocation",
                "latitude": 37.5665,
                "longitude": 126.978,
            },
        )

        second_status, second = self.request(
            service,
            "비 때문에 몇 개 더 팔리는 거야?",
        )

        self.assertEqual((first_status, first["status"]), (200, "COMPLETED"))
        self.assertEqual((second_status, second["status"]), (200, "COMPLETED"))
        self.assertEqual(len(executor.calls), 2)
        self.assertEqual(
            executor.calls[1][0], "compare_price_strategies_with_forecast"
        )
        self.assertIs(second["facts"]["forecast_used"]["value"], True)

    def test_store_location_does_not_cross_sessions(self):
        service, _ = self.service()
        question = "우리 매장 기준 다음 4일 동안 치킨마요 9,500원은 어때?"
        self.request(service, question, session_id="weather-session-a")
        first_status, first = self.request(
            service,
            "현재 위치를 사용합니다.",
            session_id="weather-session-a",
            location={
                "source": "browser_geolocation",
                "latitude": 37.5665,
                "longitude": 126.978,
            },
        )
        second_status, second = self.request(
            service,
            question,
            session_id="weather-session-b",
        )

        self.assertEqual((first_status, first["status"]), (200, "COMPLETED"))
        self.assertEqual((second_status, second["status"]), (200, "NEEDS_INPUT"))
        self.assertEqual(second["missing_input"]["input_type"], "location")
        self.assertIsNotNone(self.session_store.get("weather-session-a").store_location)
        self.assertIsNone(self.session_store.get("weather-session-b").store_location)

    def test_insufficient_forecast_returns_error_without_facts(self):
        service, executor = self.service(available_days=1)
        self.request(
            service,
            "우리 매장 기준 다음 4일 동안 치킨마요 9,500원은 어때?",
        )
        status, payload = self.request(
            service,
            "현재 위치를 사용합니다.",
            location={
                "source": "browser_geolocation",
                "latitude": 37.5665,
                "longitude": 126.978,
            },
        )

        self.assertEqual((status, payload["status"]), (500, "ERROR"))
        self.assertEqual(payload["error"]["code"], "INSUFFICIENT_FORECAST")
        self.assertEqual(payload["error"]["details"]["available_days"], 1)
        self.assertNotIn("facts", payload)
        self.assertIsNone(payload["recommendation_id"])
        self.assertEqual(len(executor.calls), 1)

    def test_horizon_beyond_tool_contract_asks_for_supported_period(self):
        service, executor = self.service()

        status, payload = self.request(
            service,
            "우리 매장 기준 다음 7일 동안 치킨마요 9,500원은 어때?",
        )

        self.assertEqual((status, payload["status"]), (200, "NEEDS_INPUT"))
        self.assertEqual(payload["missing_input"]["fields"], ["horizon_days"])
        self.assertIn("최대 5일", payload["question"])
        self.assertEqual(executor.calls, [])

    def test_weather_causal_claim_is_rejected_without_fallback(self):
        service, _ = self.service(
            final_text="비가 와서 치킨마요 판매량이 증가합니다."
        )
        self.request(
            service,
            "우리 매장 기준 다음 4일 동안 치킨마요 9,500원은 어때?",
        )
        _, payload = self.request(
            service,
            "현재 위치를 사용합니다.",
            location={
                "source": "browser_geolocation",
                "latitude": 37.5665,
                "longitude": 126.978,
            },
        )

        self.assertEqual(payload["status"], "REJECTED")
        self.assertIn(
            "UNVALIDATED_WEATHER_CAUSAL_CLAIM",
            payload["policy_validation"]["violation_codes"],
        )
        audit = json.loads(self.audit_path.read_text(encoding="utf-8"))[-1]
        self.assertFalse(audit["fallback_used"])

    def test_forecast_numeric_presentation_keeps_h0_fallback(self):
        service, executor = self.service(
            final_text="실제 단기예보 4일을 반영했습니다."
        )
        self.request(
            service,
            "우리 매장 기준 다음 4일 동안 치킨마요 9,500원은 어때?",
        )
        _, payload = self.request(
            service,
            "현재 위치를 사용합니다.",
            location={
                "source": "browser_geolocation",
                "latitude": 37.5665,
                "longitude": 126.978,
            },
        )

        self.assertEqual(payload["status"], "COMPLETED")
        self.assertEqual(payload["presentation"]["source"], "POLICY_FALLBACK")
        self.assertNotRegex(payload["presentation"]["explanation"], r"\d")
        raw = executor.calls[-1][2]
        self.assertEqual(
            payload["facts"]["engine_decision"]["value"],
            raw["recommended_action"]["action"],
        )


if __name__ == "__main__":
    unittest.main()
