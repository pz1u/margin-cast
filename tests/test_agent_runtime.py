from functools import partial
import json
from pathlib import Path
import tempfile
import unittest

from src.agent_result_router import AgentResultRouter
from src.agent_runtime import AgentRuntime, AgentRuntimeError
from src.agent_schemas import (
    AgentInput,
    AgentResponse,
    AgentResponseStatus,
    Provenance,
    ToolCall,
    ValueSource,
)
from src.agent_tool_contracts import TOOL_SCHEMAS, execute_tool
from src.decision_service import MarginCastDecisionService
from src.execution_defaults import get_execution_defaults
from src.generate_data import generate_dataset, save_dataset
from src.llm_provider import LLMProvider
from src.mock_llm_provider import MockLLMProvider
from src.prepare_analysis_data import prepare_analysis_data
from src.response_policy import ResponsePolicyOutcome


class RecordingToolExecutor:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        return self.result


class SequenceClock:
    def __init__(self, *values):
        self.values = iter(values)

    def __call__(self):
        return next(self.values)


class PassingPolicy:
    def evaluate(self, run_result):
        validation = {
            "status": "PASS",
            "violations": [],
            "engine_decision": "EXPERIMENT",
            "llm_decision_claim": "EXPERIMENT",
            "presented_decision": "EXPERIMENT",
        }
        return ResponsePolicyOutcome(
            policy_validation=validation,
            agent_response=AgentResponse(
                status=AgentResponseStatus.COMPLETED,
                recommendation_id="timing-recommendation",
                policy_validation=validation,
            ),
        )


def price_question():
    return AgentInput(
        message_id="user-1",
        text="치킨마요를 9,500원으로 올리면 어때?",
        business_inputs={"list_price": 9500},
        provenance={
            "list_price": Provenance(ValueSource.USER, "user-1:list_price")
        },
    )


class AgentRuntimeTests(unittest.TestCase):
    def test_runtime_and_policy_timings_are_logged_with_cold_label(self):
        raw_result = {
            "status": "ok",
            "recommended_action": {"action": "EXPERIMENT"},
        }
        runtime = AgentRuntime(
            MockLLMProvider(),
            tool_executor=RecordingToolExecutor(raw_result),
            clock=SequenceClock(0, 1, 3, 4, 7, 8, 13, 15),
        )
        run_result = runtime.run(price_question(), timing_label="cold")
        router = AgentResultRouter(
            PassingPolicy(),
            clock=SequenceClock(20, 20.25),
        )

        with self.assertLogs("src.agent_result_router", level="INFO") as logs:
            outcome = router.route(run_result)

        self.assertEqual(
            outcome.timings_ms,
            {
                "timing_label": "cold",
                "first_provider_ms": 2000,
                "tool_execution_ms": 3000,
                "second_provider_ms": 5000,
                "runtime_total_ms": 15000,
                "response_policy_ms": 250,
                "total_ms": 15250,
                "model_identifier": "MockLLMProvider",
            },
        )
        self.assertIn('"timing_label": "cold"', logs.output[0])

    def test_price_question_runs_one_tool_call_and_preserves_raw_result(self):
        raw_result = {
            "status": "ok",
            "request": {"menu_id": "M01"},
            "recommended_action": {
                "scenario_id": "price-test",
                "action": "EXPERIMENT",
                "reason": "테스트용 엔진 판단",
            },
            "strategies": [
                {
                    "name": "9,500원 가격 인상",
                    "profit_delta": {"mean": 135_791.25},
                    "success_probability": 0.823456,
                }
            ],
        }
        executor = RecordingToolExecutor(raw_result)
        provider = MockLLMProvider()
        runtime = AgentRuntime(provider, tool_executor=executor)

        result = runtime.run(price_question())

        self.assertIsInstance(provider, LLMProvider)
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(result.agent_input.text, "치킨마요를 9,500원으로 올리면 어때?")
        self.assertEqual(result.tool_call, provider.tool_call)
        self.assertEqual(result.tool_call.name, "compare_price_strategies")
        self.assertEqual(result.tool_call.arguments["scenarios"][0]["list_price"], 9500)
        self.assertEqual(executor.calls, [(result.tool_call.name, result.tool_call.arguments)])
        self.assertEqual(result.tool_result.raw, raw_result)
        self.assertEqual(
            result.tool_result.raw["strategies"][0]["profit_delta"]["mean"],
            135_791.25,
        )
        self.assertEqual(
            result.final_response.text,
            "합성 데이터 결과이며 근거 품질은 아직 미보정 상태입니다.",
        )
        self.assertEqual(result.final_response.decision_claim.value, "EXPERIMENT")

        first_messages, first_tools = provider.requests[0]
        self.assertEqual(first_messages[0].content, result.agent_input.text)
        self.assertEqual(first_tools, tuple(TOOL_SCHEMAS))

        second_messages, _ = provider.requests[1]
        tool_message = second_messages[-1]
        self.assertEqual(tool_message.tool_call_id, result.tool_call.call_id)
        self.assertEqual(tool_message.tool_name, result.tool_call.name)
        self.assertEqual(json.loads(tool_message.content), raw_result)

    def test_invalid_tool_arguments_are_rejected_before_execution(self):
        invalid_call = ToolCall(
            call_id="price-call-invalid",
            name="compare_price_strategies",
            arguments={"menu_id": "M01"},
        )
        provider = MockLLMProvider(tool_call=invalid_call)
        executor = RecordingToolExecutor({"status": "ok"})
        runtime = AgentRuntime(provider, tool_executor=executor)

        with self.assertRaises(AgentRuntimeError):
            runtime.run(price_question())

        self.assertEqual(executor.calls, [])


class AgentRuntimeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        tables, truth = generate_dataset()
        save_dataset(tables, truth, cls.root)
        cls.panel_path = cls.root / "processed" / "demand_panel.csv"
        prepare_analysis_data(cls.root, cls.panel_path)
        (cls.root / "ground_truth.json").unlink()
        cls.service = MarginCastDecisionService(cls.panel_path)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_mock_runtime_uses_real_dispatcher_and_engine_defaults(self):
        provider = MockLLMProvider()
        executor = partial(execute_tool, service=self.service)
        runtime = AgentRuntime(provider, tool_executor=executor)

        result = runtime.run(price_question())
        direct_result = execute_tool(
            result.tool_call.name,
            result.tool_call.arguments,
            service=self.service,
        )

        self.assertEqual(result.tool_result.raw, direct_result)
        self.assertEqual(result.tool_result.raw["status"], "ok")
        self.assertEqual(
            result.tool_result.raw["request"],
            {
                "menu_id": "M01",
                **get_execution_defaults()["compare_price_strategies"],
            },
        )
        self.assertNotIn("horizon_days", result.tool_call.arguments)
        self.assertNotIn("simulations", result.tool_call.arguments)
        self.assertNotIn("seed", result.tool_call.arguments)


if __name__ == "__main__":
    unittest.main()
