from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from src.agent_runtime import AgentRuntime
from src.agent_schemas import DecisionAction, LLMResponse, ToolResult, ValueSource
from src.agent_tool_contracts import execute_tool
from src.decision_service import MarginCastDecisionService
from src.generate_data import generate_dataset, save_dataset
from src.mock_llm_provider import MockLLMProvider
from src.prepare_analysis_data import prepare_analysis_data
from src.response_policy import PriceResponsePolicy
from tests.test_agent_runtime import price_question


class PriceResponsePolicyTests(unittest.TestCase):
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
        cls.policy = PriceResponsePolicy()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_agent(self, provider=None):
        provider = provider or MockLLMProvider()
        executor = lambda name, arguments: execute_tool(
            name,
            arguments,
            service=self.service,
        )
        return AgentRuntime(provider, tool_executor=executor).run(price_question())

    @staticmethod
    def replace_raw(run_result, raw):
        return replace(
            run_result,
            tool_result=ToolResult(
                call_id=run_result.tool_result.call_id,
                tool_name=run_result.tool_result.tool_name,
                raw=raw,
            ),
        )

    def test_engine_facts_are_selected_by_scenario_id_and_pass_policy(self):
        run_result = self.run_agent()
        outcome = self.policy.evaluate(run_result)

        self.assertEqual(outcome.policy_validation["status"], "PASS")
        response = outcome.agent_response
        self.assertIsNotNone(response)
        raw = run_result.tool_result.raw
        selected_id = raw["recommended_action"]["scenario_id"]
        selected = next(
            strategy
            for strategy in raw["strategies"]
            if strategy["scenario_id"] == selected_id
        )

        expected_values = {
            "selected_scenario_id": selected_id,
            "expected_units": selected["units"]["mean"],
            "expected_contribution_profit": selected["contribution_profit"]["mean"],
            "profit_delta": selected["profit_delta"]["mean"],
            "interval_80": {
                "lower": selected["profit_delta"]["p10"],
                "upper": selected["profit_delta"]["p90"],
            },
            "success_probability": selected["success_probability"],
            "downside_risk": selected["downside_risk"],
            "evidence_quality": selected["evidence_quality"],
            "engine_decision": raw["recommended_action"]["action"],
        }
        self.assertEqual(set(response.facts), set(expected_values))
        for name, expected in expected_values.items():
            with self.subTest(fact=name):
                self.assertEqual(response.facts[name]["value"], expected)
                self.assertEqual(response.facts[name]["source"], "ENGINE")
                self.assertTrue(response.facts[name]["source_ref"])
                if name not in {"selected_scenario_id", "engine_decision"}:
                    self.assertIn(
                        f"scenario_id={selected_id}",
                        response.facts[name]["source_ref"],
                    )
                self.assertEqual(
                    response.fact_provenance[name].source,
                    ValueSource.ENGINE,
                )
                self.assertEqual(
                    response.fact_provenance[name].ref,
                    response.facts[name]["source_ref"],
                )

        engine_action = DecisionAction(raw["recommended_action"]["action"])
        self.assertEqual(response.decision.action, engine_action)
        self.assertEqual(
            outcome.policy_validation["llm_decision_claim"],
            engine_action.value,
        )
        self.assertEqual(
            outcome.policy_validation["presented_decision"],
            engine_action.value,
        )
        self.assertEqual(response.policy_validation["status"], "PASS")
        self.assertIn(raw["data_provenance"]["warning"], response.notices)
        self.assertIn(selected["evidence_quality"]["interpretation"], response.notices)

    def test_decision_mismatch_is_rejected_without_agent_response(self):
        run_result = self.run_agent()
        engine_action = DecisionAction(
            run_result.tool_result.raw["recommended_action"]["action"]
        )
        mismatch = next(action for action in DecisionAction if action is not engine_action)
        changed = replace(
            run_result,
            final_response=LLMResponse(
                text="근거 품질은 아직 미보정 상태입니다.",
                decision_claim=mismatch,
            ),
        )

        outcome = self.policy.evaluate(changed)

        self.assertEqual(outcome.policy_validation["status"], "REJECTED")
        self.assertIn("DECISION_MISMATCH", outcome.policy_validation["violations"])
        self.assertIsNone(outcome.policy_validation["presented_decision"])
        self.assertIsNone(outcome.agent_response)

    def test_different_mock_explanations_keep_same_facts_and_decision(self):
        first_run = self.run_agent(
            MockLLMProvider(final_text="근거 품질은 미보정 상태입니다.")
        )
        second_run = self.run_agent(
            MockLLMProvider(final_text="현재 근거는 실제 결과로 보정되지 않았습니다.")
        )

        first = self.policy.evaluate(first_run).agent_response
        second = self.policy.evaluate(second_run).agent_response

        self.assertNotEqual(first.explanation, second.explanation)
        self.assertEqual(first.facts, second.facts)
        self.assertEqual(first.fact_provenance, second.fact_provenance)
        self.assertEqual(first.decision, second.decision)

    def test_missing_synthetic_warning_is_rejected(self):
        run_result = self.run_agent()
        raw = deepcopy(run_result.tool_result.raw)
        del raw["data_provenance"]["warning"]

        outcome = self.policy.evaluate(self.replace_raw(run_result, raw))

        self.assertEqual(outcome.policy_validation["status"], "REJECTED")
        self.assertIn(
            "SYNTHETIC_DATA_WARNING_MISSING",
            outcome.policy_validation["violations"],
        )
        self.assertIsNone(outcome.agent_response)

    def test_missing_uncalibrated_evidence_notice_is_rejected(self):
        run_result = self.run_agent()
        raw = deepcopy(run_result.tool_result.raw)
        selected_id = raw["recommended_action"]["scenario_id"]
        selected = next(
            strategy
            for strategy in raw["strategies"]
            if strategy["scenario_id"] == selected_id
        )
        self.assertFalse(
            selected["evidence_quality"]["validation"]["empirically_calibrated"]
        )
        del selected["evidence_quality"]["interpretation"]

        outcome = self.policy.evaluate(self.replace_raw(run_result, raw))

        self.assertEqual(outcome.policy_validation["status"], "REJECTED")
        self.assertIn(
            "UNCALIBRATED_EVIDENCE_NOTICE_MISSING",
            outcome.policy_validation["violations"],
        )
        self.assertIsNone(outcome.agent_response)

    def test_missing_selected_scenario_is_rejected(self):
        run_result = self.run_agent()
        raw = deepcopy(run_result.tool_result.raw)
        raw["recommended_action"]["scenario_id"] = "price-not-found"

        outcome = self.policy.evaluate(self.replace_raw(run_result, raw))

        self.assertEqual(outcome.policy_validation["status"], "REJECTED")
        self.assertIn(
            "SELECTED_SCENARIO_NOT_FOUND",
            outcome.policy_validation["violations"],
        )
        self.assertIsNone(outcome.agent_response)

    def test_numeric_llm_explanation_is_rejected(self):
        run_result = self.run_agent()
        engine_action = DecisionAction(
            run_result.tool_result.raw["recommended_action"]["action"]
        )
        changed = replace(
            run_result,
            final_response=LLMResponse(
                text="이익 개선 확률은 82%입니다.",
                decision_claim=engine_action,
            ),
        )

        outcome = self.policy.evaluate(changed)

        self.assertEqual(outcome.policy_validation["status"], "REJECTED")
        self.assertIn(
            "LLM_EXPLANATION_CONTAINS_NUMBER",
            outcome.policy_validation["violations"],
        )
        self.assertIsNone(outcome.agent_response)


if __name__ == "__main__":
    unittest.main()
