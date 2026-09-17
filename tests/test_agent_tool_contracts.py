from pathlib import Path
import json
import tempfile
import unittest

from src.agent_tool_contracts import TOOL_SCHEMAS, execute_tool
from src.decision_service import MarginCastDecisionService
from src.generate_data import generate_dataset, save_dataset
from src.prepare_analysis_data import prepare_analysis_data


class AgentToolContractTests(unittest.TestCase):
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

    def test_tool_schemas_are_strict_and_have_unique_names(self):
        names = [schema["name"] for schema in TOOL_SCHEMAS]
        self.assertEqual(
            names,
            [
                "get_margincast_capabilities",
                "compare_price_strategies",
                "simulate_bundle_strategy",
            ],
        )
        self.assertEqual(len(names), len(set(names)))
        for schema in TOOL_SCHEMAS:
            self.assertTrue(schema["strict"])
            self.assertFalse(schema["parameters"]["additionalProperties"])

    def test_capabilities_dispatch(self):
        result = execute_tool("get_margincast_capabilities", {}, self.service)
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["data"]["ground_truth_used"])
        self.assertEqual(result["data_provenance"]["label"], "SYNTHETIC_DATA_PROTOTYPE")

    def test_compare_dispatch_accepts_json_string(self):
        arguments = {
            "menu_id": "M01",
            "scenarios": [{"name": "500원 인상", "list_price": 9500, "discount": 0}],
            "horizon_days": 14,
            "simulations": 500,
            "seed": 42,
        }
        result = execute_tool(
            "compare_price_strategies",
            json.dumps(arguments, ensure_ascii=False),
            self.service,
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["request"]["menu_id"], "M01")
        self.assertEqual(result["strategies"][0]["name"], "현재 가격")

    def test_domain_error_is_returned_as_structured_json(self):
        arguments = {
            "menu_id": "M04",
            "scenarios": [{"name": "500원 인상", "list_price": 8500, "discount": 0}],
            "horizon_days": 14,
            "simulations": 500,
            "seed": 42,
        }
        result = execute_tool("compare_price_strategies", arguments, self.service)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "UNSUPPORTED_MENU")
        self.assertFalse(result["error"]["retryable"])
        self.assertEqual(
            result["error"]["details"]["reason_code"],
            "INSUFFICIENT_PRICE_VARIATION",
        )
        self.assertIn("next_step", result["error"]["details"])

    def test_bundle_dispatch_returns_experiment_decision(self):
        arguments = {
            "scenario": {
                "name": "치킨마요 콜라 세트",
                "bundle_price": 10000,
                "take_rate": 0.3,
                "copurchase_take_rate": 0.55,
                "incremental_demand_rate": 0.1,
                "cannibalization_rate": 0.02,
            },
            "horizon_days": 14,
            "simulations": 500,
            "seed": 42,
        }
        result = execute_tool("simulate_bundle_strategy", arguments, self.service)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["decision"]["action"], "EXPERIMENT")
        self.assertFalse(result["strategy"]["ground_truth_used"])

    def test_invalid_json_and_unknown_tool_are_stable_errors(self):
        invalid = execute_tool("compare_price_strategies", "{", self.service)
        unknown = execute_tool("invent_numbers", {}, self.service)
        self.assertEqual(invalid["error"]["code"], "INVALID_ARGUMENTS")
        self.assertEqual(unknown["error"]["code"], "UNKNOWN_TOOL")


if __name__ == "__main__":
    unittest.main()
