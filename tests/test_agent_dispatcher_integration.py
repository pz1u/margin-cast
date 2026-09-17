from pathlib import Path
import tempfile
import unittest

from src.agent_integration import (
    MarginCastToolExecutor,
    build_static_capabilities,
    create_margincast_agent_loop,
)
from src.agent_schemas import (
    AgentInput,
    AgentResponseStatus,
    DecisionAction,
    LLMResponse,
    ToolCall,
)
from src.decision_service import MarginCastDecisionService
from src.generate_data import generate_dataset, save_dataset
from src.prepare_analysis_data import prepare_analysis_data


class CountingService:
    def __init__(self, delegate):
        self.delegate = delegate
        self.capability_calls = 0

    def get_capabilities(self):
        self.capability_calls += 1
        return self.delegate.get_capabilities()

    def compare_price_strategies(self, **arguments):
        return self.delegate.compare_price_strategies(**arguments)


class ScriptedProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def generate(self, messages, tools):
        self.requests.append((tuple(messages), tuple(tools)))
        return self.responses.pop(0)


class AgentDispatcherIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        tables, truth = generate_dataset()
        save_dataset(tables, truth, cls.root)
        panel_path = cls.root / "processed" / "demand_panel.csv"
        prepare_analysis_data(cls.root, panel_path)
        (cls.root / "ground_truth.json").unlink()
        cls.service = MarginCastDecisionService(panel_path)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_real_dispatcher_runs_capability_then_price_strategy(self):
        provider = ScriptedProvider(
            [
                LLMResponse(
                    tool_calls=(
                        ToolCall(
                            call_id="capability-1",
                            name="get_margincast_capabilities",
                            arguments={},
                        ),
                    )
                ),
                LLMResponse(
                    tool_calls=(
                        ToolCall(
                            call_id="price-1",
                            name="compare_price_strategies",
                            arguments={
                                "menu_id": "M01",
                                "scenarios": [
                                    {
                                        "name": "9500원 정가",
                                        "list_price": 9500,
                                        "discount": 0,
                                    }
                                ],
                                "horizon_days": 14,
                                "simulations": 500,
                                "seed": 7,
                            },
                        ),
                    )
                ),
                LLMResponse(text="계산 엔진의 실행 판단과 근거를 확인했습니다."),
            ]
        )
        loop = create_margincast_agent_loop(provider, self.service)

        response = loop.run(
            AgentInput(
                message_id="integration-1",
                text="치킨마요를 9,500원으로 올리면 어때?",
            )
        )

        self.assertEqual(response.status, AgentResponseStatus.COMPLETED)
        self.assertEqual(len(response.tool_results), 2)
        capabilities = response.tool_results[0].raw
        comparison = response.tool_results[1].raw
        self.assertEqual(capabilities["status"], "ok")
        self.assertFalse(capabilities["data"]["ground_truth_used"])
        self.assertEqual(comparison["status"], "ok")
        self.assertFalse(comparison["model"]["ground_truth_used"])
        self.assertEqual(
            response.decision.action,
            DecisionAction(comparison["recommended_action"]["action"]),
        )
        self.assertEqual(response.decision.reason, comparison["recommended_action"]["reason"])
        self.assertEqual(
            loop.tool_executor.cached_capabilities.supported_menus,
            tuple(capabilities["supported_menus"]),
        )
        registered_names = [schema["name"] for schema in provider.requests[0][1]]
        self.assertEqual(
            registered_names,
            [
                "get_margincast_capabilities",
                "compare_price_strategies",
                "simulate_bundle_strategy",
            ],
        )

    def test_capability_cache_only_invalidates_for_dynamic_state_errors(self):
        service = CountingService(self.service)
        executor = MarginCastToolExecutor(service)
        capabilities = executor("get_margincast_capabilities", {})
        self.assertEqual(capabilities["status"], "ok")
        self.assertIsNotNone(executor.cached_capabilities)
        self.assertEqual(executor("get_margincast_capabilities", {}), capabilities)
        self.assertEqual(service.capability_calls, 1)

        invalid_capability_call = executor(
            "get_margincast_capabilities",
            {"unexpected": True},
        )
        self.assertEqual(invalid_capability_call["error"]["code"], "INVALID_ARGUMENTS")
        self.assertEqual(service.capability_calls, 1)

        invalid = executor(
            "compare_price_strategies",
            {
                "menu_id": "M01",
                "scenarios": [{"name": "잘못된 가격", "list_price": -1, "discount": 0}],
                "horizon_days": 14,
                "simulations": 500,
                "seed": 7,
            },
        )
        self.assertEqual(invalid["error"]["code"], "INVALID_SCENARIO")
        self.assertIsNotNone(executor.cached_capabilities)

        unsupported = executor(
            "compare_price_strategies",
            {
                "menu_id": "M04",
                "scenarios": [{"name": "가격 변경", "list_price": 8500, "discount": 0}],
                "horizon_days": 14,
                "simulations": 500,
                "seed": 7,
            },
        )
        self.assertEqual(unsupported["error"]["code"], "UNSUPPORTED_MENU")
        self.assertIsNone(executor.cached_capabilities)

        refreshed = executor("get_margincast_capabilities", {})
        self.assertEqual(refreshed["status"], "ok")
        self.assertEqual(service.capability_calls, 2)

    def test_static_capabilities_use_registered_tool_contracts(self):
        capabilities = build_static_capabilities()
        names = [schema["name"] for schema in capabilities.tool_schemas]

        self.assertEqual(
            names,
            [
                "get_margincast_capabilities",
                "compare_price_strategies",
                "simulate_bundle_strategy",
            ],
        )
        self.assertEqual(
            capabilities.execution_defaults["compare_price_strategies"],
            {"horizon_days": 14, "simulations": 10_000, "seed": 42},
        )
        price_schema = next(
            schema
            for schema in capabilities.tool_schemas
            if schema["name"] == "compare_price_strategies"
        )
        self.assertEqual(price_schema["parameters"]["required"], ["menu_id", "scenarios"])


if __name__ == "__main__":
    unittest.main()
