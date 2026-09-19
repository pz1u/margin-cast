from copy import deepcopy
from functools import partial
import json
from pathlib import Path
import tempfile
import unittest

from src.agent_audit import AgentAuditStore
from src.agent_feedback import AgentFeedbackWorkflow
from src.agent_result_router import AgentResultRouter
from src.agent_runtime import AgentRuntime
from src.agent_schemas import AgentResponseStatus, Provenance, ValueSource
from src.agent_tool_contracts import execute_tool
from src.decision_service import MarginCastDecisionService
from src.execution_defaults import get_execution_defaults
from src.experiment_feedback import ExperimentFeedbackStore
from src.generate_data import generate_dataset, save_dataset
from src.mock_llm_provider import MockLLMProvider
from src.prepare_analysis_data import prepare_analysis_data
from src.response_policy import PriceResponsePolicy
from tests.test_agent_runtime import price_question


class RecordingFeedbackExecutor:
    def __init__(self, store):
        self.store = store
        self.calls = []

    def __call__(self, tool_name, arguments):
        self.calls.append((tool_name, deepcopy(arguments)))
        return execute_tool(tool_name, arguments, feedback_store=self.store)


class AgentFeedbackWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine_directory = tempfile.TemporaryDirectory()
        root = Path(cls.engine_directory.name)
        tables, truth = generate_dataset()
        save_dataset(tables, truth, root)
        panel_path = root / "processed" / "demand_panel.csv"
        prepare_analysis_data(root, panel_path)
        (root / "ground_truth.json").unlink()
        service = MarginCastDecisionService(panel_path)
        cls.run_result = AgentRuntime(
            MockLLMProvider(),
            tool_executor=partial(execute_tool, service=service),
        ).run(price_question())

    @classmethod
    def tearDownClass(cls):
        cls.engine_directory.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.audit_store = AgentAuditStore(root / "audit.json")
        self.feedback_store = ExperimentFeedbackStore(root / "feedback.json")
        self.executor = RecordingFeedbackExecutor(self.feedback_store)
        router = AgentResultRouter(
            PriceResponsePolicy(recommendation_id_factory=lambda: "recommendation-test"),
            execution_defaults=get_execution_defaults(),
            audit_recorder=self.audit_store,
        )
        self.routing = router.route(self.run_result)
        self.response = self.routing.agent_response
        self.workflow = AgentFeedbackWorkflow(
            self.audit_store,
            tool_executor=self.executor,
            call_id_factory=lambda: "feedback-tool-call",
        )

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def plan_inputs():
        values = {
            "start_date": "2026-10-01",
            "end_date": "2026-10-14",
        }
        provenance = {
            name: Provenance(ValueSource.USER, f"user-plan:{name}")
            for name in values
        }
        return values, provenance

    @staticmethod
    def actual_inputs():
        actual = {
            "units": 95,
            "contribution_profit": 610_000,
            "baseline_contribution_profit": 570_000,
        }
        provenance = {
            f"actual.{name}": Provenance(ValueSource.USER, f"user-actual:{name}")
            for name in actual
        }
        provenance["baseline_method"] = Provenance(
            ValueSource.USER,
            "user-actual:baseline_method",
        )
        return actual, provenance

    def plan(self):
        values, provenance = self.plan_inputs()
        return self.workflow.create_experiment_plan(
            self.run_result,
            self.response,
            execution_confirmed=True,
            planning_inputs=values,
            provenance=provenance,
        )

    def test_pass_recommendation_has_separate_not_planned_audit_id(self):
        self.assertEqual(self.response.status, AgentResponseStatus.COMPLETED)
        self.assertEqual(self.response.recommendation_id, "recommendation-test")
        audit = self.audit_store.get(self.response.recommendation_id)
        self.assertEqual(audit["feedback_status"], "not_planned")
        self.assertIsNone(audit["feedback_id"])
        self.assertEqual(self.feedback_store.list(), [])

    def test_unconfirmed_execution_does_not_call_plan_tool(self):
        outcome = self.workflow.create_experiment_plan(
            self.run_result,
            self.response,
            execution_confirmed=False,
        )

        self.assertEqual(outcome.feedback_status, "not_planned")
        self.assertIsNone(outcome.feedback_id)
        self.assertEqual(self.executor.calls, [])
        self.assertEqual(self.feedback_store.list(), [])

    def test_confirmed_execution_creates_distinct_planned_feedback(self):
        outcome = self.plan()

        self.assertEqual(self.executor.calls[0][0], "create_experiment_plan")
        self.assertEqual(outcome.feedback_status, "planned")
        self.assertNotEqual(outcome.feedback_id, outcome.recommendation_id)
        self.assertEqual(outcome.tool_result.raw["status"], "ok")
        persisted = self.feedback_store.pending()
        self.assertEqual(persisted[0]["feedback_id"], outcome.feedback_id)
        audit = self.audit_store.get(outcome.recommendation_id)
        self.assertEqual(audit["feedback_id"], outcome.feedback_id)
        self.assertEqual(audit["feedback_status"], "planned")

        selected_id = self.response.facts["selected_scenario_id"]["value"]
        selected = next(
            item
            for item in self.run_result.tool_result.raw["strategies"]
            if item["scenario_id"] == selected_id
        )
        prediction = self.executor.calls[0][1]["prediction"]
        self.assertEqual(prediction["units"]["mean"], selected["units"]["mean"])
        self.assertEqual(
            prediction["contribution_profit"]["mean"],
            selected["contribution_profit"]["mean"],
        )

    def test_missing_plan_period_returns_missing_input_without_tool_call(self):
        outcome = self.workflow.create_experiment_plan(
            self.run_result,
            self.response,
            execution_confirmed=True,
        )

        self.assertEqual(outcome.feedback_status, "not_planned")
        self.assertEqual(outcome.agent_response.status, AgentResponseStatus.NEEDS_INPUT)
        self.assertEqual(
            outcome.agent_response.missing_input.fields,
            ("start_date", "end_date"),
        )
        self.assertEqual(
            outcome.agent_response.missing_input.source_requirement,
            (ValueSource.USER, ValueSource.ENGINE, ValueSource.DEFAULT),
        )
        self.assertEqual(self.executor.calls, [])

    def test_actual_result_completes_same_feedback_without_changing_values(self):
        planned = self.plan()
        actual, provenance = self.actual_inputs()

        completed = self.workflow.record_experiment_result(
            planned.recommendation_id,
            planned.feedback_id,
            baseline_method="matched_period",
            actual=actual,
            provenance=provenance,
        )

        self.assertEqual(completed.feedback_status, "completed")
        self.assertEqual(completed.feedback_id, planned.feedback_id)
        self.assertEqual(self.executor.calls[-1][0], "record_experiment_result")
        self.assertEqual(self.executor.calls[-1][1]["actual"], actual)
        record = self.feedback_store.list()[0]
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["actual"], {**actual})
        audit = self.audit_store.get(planned.recommendation_id)
        self.assertEqual(audit["feedback_status"], "completed")

    def test_partial_actual_result_asks_only_for_missing_values(self):
        planned = self.plan()
        before_record_calls = len(self.executor.calls)
        outcome = self.workflow.record_experiment_result(
            planned.recommendation_id,
            planned.feedback_id,
            baseline_method=None,
            actual={"units": 95},
            provenance={
                "actual.units": Provenance(ValueSource.USER, "user-actual:units")
            },
        )

        self.assertEqual(outcome.feedback_status, "planned")
        self.assertEqual(outcome.agent_response.status, AgentResponseStatus.NEEDS_INPUT)
        self.assertEqual(
            outcome.agent_response.missing_input.fields,
            (
                "actual.contribution_profit",
                "actual.baseline_contribution_profit",
                "baseline_method",
            ),
        )
        self.assertEqual(len(self.executor.calls), before_record_calls)
        self.assertEqual(self.feedback_store.pending()[0]["status"], "planned")

    def test_audit_keeps_decisions_and_excludes_sensitive_or_conversation_text(self):
        audit = self.audit_store.get(self.response.recommendation_id)
        serialized = json.dumps(audit, ensure_ascii=False).lower()
        self.assertEqual(audit["engine_decision"], audit["presented_decision"])
        self.assertEqual(audit["response_policy_status"], "PASS")
        self.assertEqual(audit["violation_codes"], [])
        self.assertTrue(audit["tool_result_ref"].startswith("sha256:"))
        self.assertEqual(
            set(audit),
            {
                "recommendation_id",
                "prompt_version",
                "model_identifier",
                "called_tool_names",
                "selected_scenario_id",
                "engine_decision",
                "presented_decision",
                "response_policy_status",
                "violation_codes",
                "feedback_id",
                "feedback_status",
                "tool_result_ref",
            },
        )
        for forbidden in (
            "api_key",
            "address",
            "latitude",
            "longitude",
            self.run_result.agent_input.text.lower(),
            self.response.explanation.lower(),
        ):
            self.assertNotIn(forbidden, serialized)

    def test_pending_and_summary_use_registered_tools(self):
        planned = self.plan()
        pending = self.workflow.list_pending_experiments("M01")
        self.assertEqual(pending["plans"][0]["feedback_id"], planned.feedback_id)

        actual, provenance = self.actual_inputs()
        self.workflow.record_experiment_result(
            planned.recommendation_id,
            planned.feedback_id,
            baseline_method="matched_period",
            actual=actual,
            provenance=provenance,
        )
        summary = self.workflow.get_feedback_summary("M01")
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["record_count"], 1)
        self.assertEqual(
            [name for name, _ in self.executor.calls],
            [
                "create_experiment_plan",
                "list_pending_experiments",
                "record_experiment_result",
                "get_feedback_summary",
            ],
        )


if __name__ == "__main__":
    unittest.main()
