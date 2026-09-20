import json
from pathlib import Path
import unittest

from src.agent_tool_contracts import execute_tool
from src.decision_service import MarginCastDecisionService


FIXTURE_DIR = Path(__file__).parent / "agent" / "fixtures"


class AgentToolFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = MarginCastDecisionService()
        cls.manifest = json.loads(
            (FIXTURE_DIR / "fixture-inputs.json").read_text(encoding="utf-8")
        )

    def load_fixture(self, filename):
        return json.loads((FIXTURE_DIR / filename).read_text(encoding="utf-8"))

    def test_fixtures_match_current_execute_tool_results(self):
        for filename, call in self.manifest.items():
            with self.subTest(filename=filename):
                actual = execute_tool(
                    call["tool_name"], call["arguments"], self.service
                )
                self.assertEqual(self.load_fixture(filename), actual)

    def test_price_fixtures_cover_all_decision_actions_and_result_fields(self):
        expected_actions = {
            "price_recommend.json": "RECOMMEND",
            "price_experiment.json": "EXPERIMENT",
            "price_hold.json": "HOLD",
        }
        for filename, expected_action in expected_actions.items():
            with self.subTest(filename=filename):
                result = self.load_fixture(filename)
                selected_name = result["recommended_action"]["name"]
                selected = next(
                    row for row in result["strategies"] if row["name"] == selected_name
                )
                self.assertEqual(result["recommended_action"]["action"], expected_action)
                self.assertEqual(result["decision_ranking"][0]["name"], selected_name)
                self.assertIn("mean", selected["units"])
                self.assertIn("mean", selected["contribution_profit"])
                self.assertEqual(
                    set(("p05", "p10", "p50", "p90", "p95"))
                    - set(selected["profit_delta"]),
                    set(),
                )
                self.assertIn("success_probability", selected)
                self.assertIn("downside_risk", selected)
                self.assertIn("evidence_quality", selected)
                self.assertIn("decision", selected)
                self.assertIn("data_provenance", result)

    def test_capabilities_fixture_exposes_engine_execution_defaults(self):
        defaults = self.load_fixture("capabilities.json")["execution_defaults"]
        self.assertEqual(
            defaults,
            {
                "compare_price_strategies": {
                    "horizon_days": 14,
                    "simulations": 10000,
                    "seed": 42,
                },
                "compare_price_strategies_with_forecast": {
                    "horizon_days": 4,
                    "simulations": 10000,
                    "seed": 42,
                },
                "simulate_bundle_strategy": {
                    "horizon_days": 14,
                    "simulations": 10000,
                    "seed": 42,
                },
            },
        )

    def test_error_fixtures_keep_stable_error_codes(self):
        self.assertEqual(
            self.load_fixture("unsupported_menu.json")["error"]["code"],
            "UNSUPPORTED_MENU",
        )
        self.assertEqual(
            self.load_fixture("invalid_scenario.json")["error"]["code"],
            "INVALID_SCENARIO",
        )


if __name__ == "__main__":
    unittest.main()
