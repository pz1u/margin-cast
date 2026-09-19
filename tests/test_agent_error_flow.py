from pathlib import Path
import tempfile
import unittest

from src.agent_result_router import AgentResultRouter
from src.agent_runtime import AgentRuntime
from src.agent_schemas import (
    AgentErrorCategory,
    AgentResponseStatus,
    ToolCall,
    ValueSource,
)
from src.agent_tool_contracts import execute_tool
from src.decision_service import MarginCastDecisionService
from src.execution_defaults import get_execution_defaults
from src.generate_data import generate_dataset, save_dataset
from src.mock_llm_provider import MockLLMProvider
from src.prepare_analysis_data import prepare_analysis_data
from src.response_policy import PriceResponsePolicy
from tests.test_agent_runtime import RecordingToolExecutor, price_question


class RecordingResponsePolicy:
    def __init__(self):
        self.calls = []
        self.policy = PriceResponsePolicy()

    def evaluate(self, run_result):
        self.calls.append(run_result)
        return self.policy.evaluate(run_result)


def price_call(menu_id="M01", scenarios=None):
    return ToolCall(
        call_id="error-flow-call",
        name="compare_price_strategies",
        arguments={
            "menu_id": menu_id,
            "scenarios": scenarios
            or [{"name": "가격 인상", "list_price": 9500, "discount": 0}],
        },
    )


class AgentErrorFlowTests(unittest.TestCase):
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

    def real_executor(self, name, arguments):
        return execute_tool(name, arguments, service=self.service)

    def router(self, policy=None):
        return AgentResultRouter(
            policy or RecordingResponsePolicy(),
            execution_defaults=get_execution_defaults(),
        )

    def test_success_result_is_sent_to_response_policy(self):
        policy = RecordingResponsePolicy()
        run_result = AgentRuntime(
            MockLLMProvider(),
            tool_executor=self.real_executor,
        ).run(price_question())

        outcome = self.router(policy).route(run_result)

        self.assertEqual(len(policy.calls), 1)
        self.assertIs(policy.calls[0], run_result)
        self.assertEqual(outcome.agent_response.status, AgentResponseStatus.COMPLETED)
        self.assertEqual(outcome.policy_validation["status"], "PASS")

    def test_missing_input_skips_policy_and_creates_question(self):
        tool_error = {
            "status": "error",
            "error": {
                "code": "MISSING_INPUT",
                "message": "세트 가격이 필요합니다.",
                "retryable": False,
                "details": {"missing_fields": ["scenario.bundle_price"]},
            },
        }
        provider = MockLLMProvider()
        policy = RecordingResponsePolicy()
        run_result = AgentRuntime(
            provider,
            tool_executor=RecordingToolExecutor(tool_error),
        ).run(price_question())

        outcome = self.router(policy).route(run_result)

        self.assertEqual(policy.calls, [])
        self.assertEqual(len(provider.requests), 1)
        self.assertIsNone(run_result.final_response)
        self.assertEqual(outcome.agent_response.status, AgentResponseStatus.NEEDS_INPUT)
        self.assertEqual(
            outcome.agent_response.missing_input.fields,
            ("scenario.bundle_price",),
        )
        self.assertEqual(
            outcome.agent_response.missing_input.question,
            "생각하신 세트 판매가는 얼마인가요?",
        )
        self.assertEqual(
            outcome.agent_response.missing_input.source_requirement,
            (ValueSource.USER,),
        )

    def test_duplicate_scenario_is_invalid_input_and_is_not_removed(self):
        scenarios = [
            {"name": "첫 대안", "list_price": 9500, "discount": 0},
            {"name": "둘째 대안", "list_price": 9500, "discount": 0},
        ]
        provider = MockLLMProvider(tool_call=price_call(scenarios=scenarios))
        policy = RecordingResponsePolicy()
        run_result = AgentRuntime(
            provider,
            tool_executor=self.real_executor,
        ).run(price_question())

        outcome = self.router(policy).route(run_result)

        self.assertEqual(policy.calls, [])
        self.assertEqual(len(run_result.tool_call.arguments["scenarios"]), 2)
        self.assertEqual(
            run_result.tool_result.raw["error"]["code"],
            "INVALID_SCENARIO",
        )
        self.assertEqual(outcome.agent_response.error.code, AgentErrorCategory.INVALID_INPUT)
        self.assertEqual(outcome.agent_response.error.original_code, "INVALID_SCENARIO")
        self.assertIn("중복", outcome.agent_response.error.message)
        self.assertEqual(outcome.agent_response.facts, {})
        self.assertIsNone(outcome.agent_response.decision)

    def test_unsupported_menu_preserves_next_step_without_numbers(self):
        provider = MockLLMProvider(tool_call=price_call(menu_id="M04"))
        policy = RecordingResponsePolicy()
        run_result = AgentRuntime(
            provider,
            tool_executor=self.real_executor,
        ).run(price_question())
        raw_error = run_result.tool_result.raw["error"]

        outcome = self.router(policy).route(run_result)

        self.assertEqual(policy.calls, [])
        self.assertEqual(outcome.agent_response.error.code, AgentErrorCategory.UNSUPPORTED)
        self.assertEqual(outcome.agent_response.error.original_code, "UNSUPPORTED_MENU")
        self.assertEqual(
            outcome.agent_response.error.details["observed_price_levels"],
            raw_error["details"]["observed_price_levels"],
        )
        self.assertEqual(
            outcome.agent_response.error.details["required_unique_price_levels"],
            raw_error["details"]["required_unique_price_levels"],
        )
        self.assertEqual(
            outcome.agent_response.error.details["next_step"],
            raw_error["details"]["next_step"],
        )
        self.assertEqual(outcome.agent_response.facts, {})
        self.assertIsNone(outcome.agent_response.decision)

    def test_explicit_insufficient_data_maps_without_facts(self):
        tool_error = {
            "status": "error",
            "error": {
                "code": "INSUFFICIENT_DATA",
                "message": "가격 변화 관측이 부족합니다.",
                "retryable": False,
                "details": {"next_step": "가격 변경 관측을 추가하세요."},
            },
        }
        run_result = AgentRuntime(
            MockLLMProvider(),
            tool_executor=RecordingToolExecutor(tool_error),
        ).run(price_question())

        outcome = self.router().route(run_result)

        self.assertEqual(
            outcome.agent_response.error.code,
            AgentErrorCategory.INSUFFICIENT_DATA,
        )
        self.assertEqual(outcome.agent_response.facts, {})
        self.assertIsNone(outcome.agent_response.decision)

    def test_panel_not_found_maps_to_engine_error_without_facts(self):
        tool_error = {
            "status": "error",
            "error": {
                "code": "PANEL_NOT_FOUND",
                "message": "분석 패널을 찾을 수 없습니다.",
                "retryable": True,
                "details": {"path": "missing.csv"},
            },
        }
        policy = RecordingResponsePolicy()
        run_result = AgentRuntime(
            MockLLMProvider(),
            tool_executor=RecordingToolExecutor(tool_error),
        ).run(price_question())

        outcome = self.router(policy).route(run_result)

        self.assertEqual(policy.calls, [])
        self.assertEqual(outcome.agent_response.error.code, AgentErrorCategory.ENGINE_ERROR)
        self.assertEqual(outcome.agent_response.error.original_code, "PANEL_NOT_FOUND")
        self.assertEqual(outcome.agent_response.error.details, {"path": "missing.csv"})
        self.assertEqual(outcome.agent_response.facts, {})
        self.assertIsNone(outcome.agent_response.decision)

    def test_execution_default_field_is_not_asked_from_user(self):
        tool_error = {
            "status": "error",
            "error": {
                "code": "MISSING_INPUT",
                "message": "실행 설정이 누락됐습니다.",
                "retryable": False,
                "details": {"missing_fields": ["horizon_days"]},
            },
        }
        run_result = AgentRuntime(
            MockLLMProvider(),
            tool_executor=RecordingToolExecutor(tool_error),
        ).run(price_question())

        outcome = self.router().route(run_result)

        self.assertEqual(outcome.agent_response.status, AgentResponseStatus.ERROR)
        self.assertEqual(outcome.agent_response.error.code, AgentErrorCategory.ENGINE_ERROR)
        self.assertIsNone(outcome.agent_response.missing_input)


if __name__ == "__main__":
    unittest.main()
