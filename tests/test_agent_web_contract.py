from functools import partial
import json
from pathlib import Path
import tempfile
import unittest

from src.agent_audit import AgentAuditStore
from src.agent_result_router import AgentResultRouter
from src.agent_runtime import AgentMissingInputError, AgentRuntime
from src.agent_schemas import ToolCall
from src.agent_tool_contracts import execute_tool
from src.agent_web_contract import AgentWebStatus, map_agent_result, map_runtime_error
from src.decision_service import MarginCastDecisionService
from src.execution_defaults import get_execution_defaults
from src.generate_data import generate_dataset, save_dataset
from src.mock_llm_provider import MockLLMProvider
from src.prepare_analysis_data import prepare_analysis_data
from src.response_policy import PriceResponsePolicy
from tests.test_agent_runtime import price_question


class AgentWebContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine_directory = tempfile.TemporaryDirectory()
        root = Path(cls.engine_directory.name)
        tables, truth = generate_dataset()
        save_dataset(tables, truth, root)
        panel_path = root / "processed" / "demand_panel.csv"
        prepare_analysis_data(root, panel_path)
        (root / "ground_truth.json").unlink()
        cls.service = MarginCastDecisionService(panel_path)

    @classmethod
    def tearDownClass(cls):
        cls.engine_directory.cleanup()

    def run_and_route(self, provider, execution_id, audit_store):
        run_result = AgentRuntime(
            provider,
            tool_executor=partial(execute_tool, service=self.service),
            execution_id_factory=lambda: execution_id,
        ).run(price_question())
        outcome = AgentResultRouter(
            PriceResponsePolicy(
                recommendation_id_factory=lambda: f"recommendation-{execution_id}"
            ),
            execution_defaults=get_execution_defaults(),
            audit_recorder=audit_store,
        ).route(run_result)
        return run_result, outcome

    def test_pass_maps_to_completed_with_separate_recommendation_id(self):
        with tempfile.TemporaryDirectory() as directory:
            audit_store = AgentAuditStore(Path(directory) / "audit.json")
            run_result, outcome = self.run_and_route(
                MockLLMProvider(),
                "execution-pass",
                audit_store,
            )

            web_result = map_agent_result(run_result, outcome)

            self.assertEqual(web_result.status, AgentWebStatus.COMPLETED)
            self.assertEqual(web_result.execution_id, "execution-pass")
            self.assertEqual(
                web_result.recommendation_id,
                "recommendation-execution-pass",
            )
            self.assertNotEqual(web_result.execution_id, web_result.recommendation_id)

    def test_rejected_execution_is_audited_without_recommendation(self):
        with tempfile.TemporaryDirectory() as directory:
            audit_store = AgentAuditStore(Path(directory) / "audit.json")
            provider = MockLLMProvider(
                final_text="합성 데이터 결과이며 근거 품질은 미보정 상태입니다. 7"
            )
            run_result, outcome = self.run_and_route(
                provider,
                "execution-rejected",
                audit_store,
            )

            web_result = map_agent_result(run_result, outcome)
            audit = audit_store.get_execution("execution-rejected")

            self.assertEqual(web_result.status, AgentWebStatus.REJECTED)
            self.assertIsNone(web_result.recommendation_id)
            self.assertIsNone(outcome.agent_response)
            self.assertEqual(audit["execution_id"], "execution-rejected")
            self.assertIsNone(audit["recommendation_id"])
            self.assertEqual(audit["response_policy_status"], "REJECTED")
            self.assertIn(
                "LLM_EXPLANATION_CONTAINS_NUMBER",
                audit["violation_codes"],
            )
            self.assertEqual(
                audit["called_tool_names"],
                ["compare_price_strategies"],
            )
            self.assertIn("total_ms", audit["timings_ms"])

            serialized = json.dumps(audit, ensure_ascii=False).lower()
            for forbidden in (
                "api_key",
                "address",
                "latitude",
                "longitude",
                run_result.agent_input.text.lower(),
                provider.final_text.lower(),
            ):
                self.assertNotIn(forbidden, serialized)

    def test_missing_input_exception_maps_to_normal_web_state(self):
        provider = MockLLMProvider(
            tool_call=ToolCall(
                call_id="missing-input-call",
                name="compare_price_strategies",
                arguments={},
            )
        )
        runtime = AgentRuntime(
            provider,
            execution_id_factory=lambda: "execution-needs-input",
        )

        with self.assertRaises(AgentMissingInputError) as context:
            runtime.run(price_question())

        web_result = map_runtime_error(context.exception)
        self.assertEqual(web_result.status, AgentWebStatus.NEEDS_INPUT)
        self.assertEqual(web_result.execution_id, "execution-needs-input")
        self.assertIsNone(web_result.recommendation_id)
        self.assertEqual(
            web_result.agent_response.missing_input.fields,
            ("menu_id", "scenarios"),
        )


if __name__ == "__main__":
    unittest.main()
