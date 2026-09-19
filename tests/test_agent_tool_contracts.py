from pathlib import Path
import json
import tempfile
import unittest

from src.agent_tool_contracts import TOOL_SCHEMAS, execute_tool
from src.decision_service import MarginCastDecisionService
from src.execution_defaults import (
    DEFAULT_HORIZON_DAYS,
    DEFAULT_SEED,
    DEFAULT_SIMULATIONS,
    FORECAST_DEFAULT_HORIZON_DAYS,
    get_execution_defaults,
)
from src.generate_data import generate_dataset, save_dataset
from src.prepare_analysis_data import prepare_analysis_data


class RecordingDecisionService:
    def __init__(self):
        self.arguments = None

    def compare_price_strategies(
        self,
        menu_id,
        scenarios,
        horizon_days=DEFAULT_HORIZON_DAYS,
        simulations=DEFAULT_SIMULATIONS,
        seed=DEFAULT_SEED,
    ):
        self.arguments = {
            "menu_id": menu_id,
            "scenarios": scenarios,
            "horizon_days": horizon_days,
            "simulations": simulations,
            "seed": seed,
        }
        return {"status": "ok", "request": dict(self.arguments)}


class RecordingForecastService:
    def __init__(self):
        self.arguments = None

    def compare_price_strategies(self, **arguments):
        self.arguments = arguments
        return {"status": "ok", "weather": {"location_source": "kma_grid"}}


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
                "compare_price_strategies_with_forecast",
                "simulate_bundle_strategy",
                "create_experiment_plan",
                "list_pending_experiments",
                "record_experiment_result",
                "get_feedback_summary",
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
        self.assertEqual(result["execution_defaults"], get_execution_defaults())
        self.assertEqual(
            result["execution_defaults"]["compare_price_strategies"],
            {
                "horizon_days": DEFAULT_HORIZON_DAYS,
                "simulations": DEFAULT_SIMULATIONS,
                "seed": DEFAULT_SEED,
            },
        )
        self.assertEqual(
            result["execution_defaults"]["compare_price_strategies_with_forecast"]
            ["horizon_days"],
            FORECAST_DEFAULT_HORIZON_DAYS,
        )
        self.assertEqual(result["data_provenance"]["dataset_version"], "synthetic-pos-v2")
        self.assertNotIn("data_version", result["execution_defaults"])

    def test_execution_settings_are_optional_and_engine_defaults_apply(self):
        service = RecordingDecisionService()
        result = execute_tool(
            "compare_price_strategies",
            {
                "menu_id": "M01",
                "scenarios": [{"name": "500원 인상", "list_price": 9500, "discount": 0}],
            },
            service,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(service.arguments["horizon_days"], DEFAULT_HORIZON_DAYS)
        self.assertEqual(service.arguments["simulations"], DEFAULT_SIMULATIONS)
        self.assertEqual(service.arguments["seed"], DEFAULT_SEED)

    def test_forecast_location_contract_accepts_exactly_one_location_form(self):
        schema = next(
            item
            for item in TOOL_SCHEMAS
            if item["name"] == "compare_price_strategies_with_forecast"
        )
        location = schema["parameters"]["properties"]["location"]

        self.assertEqual(
            [set(option["required"]) for option in location["oneOf"]],
            [
                {"address"},
                {"latitude", "longitude"},
                {"kma_nx", "kma_ny"},
            ],
        )
        self.assertTrue(all(not option["additionalProperties"] for option in location["oneOf"]))
        self.assertEqual(
            schema["parameters"]["required"],
            ["menu_id", "scenarios", "location"],
        )

    def test_forecast_dispatch_uses_structured_location_without_execution_values(self):
        forecast_service = RecordingForecastService()
        result = execute_tool(
            "compare_price_strategies_with_forecast",
            {
                "menu_id": "M01",
                "scenarios": [{"name": "500원 인상", "list_price": 9500, "discount": 0}],
                "location": {"kma_nx": 60, "kma_ny": 127},
            },
            forecast_service=forecast_service,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(forecast_service.arguments["location"], {"kma_nx": 60, "kma_ny": 127})
        self.assertNotIn("horizon_days", forecast_service.arguments)

    def test_bundle_numeric_fields_forbid_llm_values_and_missing_values_need_input(self):
        schema = next(item for item in TOOL_SCHEMAS if item["name"] == "simulate_bundle_strategy")
        scenario = schema["parameters"]["properties"]["scenario"]
        numeric_fields = (
            "bundle_price",
            "take_rate",
            "copurchase_take_rate",
            "incremental_demand_rate",
            "cannibalization_rate",
        )
        for field_name in numeric_fields:
            with self.subTest(field_name=field_name):
                policy = scenario["properties"][field_name]["x-margincast-source-policy"]
                self.assertEqual(policy["allowed_sources"], ["USER", "ENGINE", "DEFAULT"])
                self.assertEqual(policy["planning_scenario_sources"], ["ENGINE", "DEFAULT"])
                self.assertFalse(policy["llm_generation_allowed"])
                self.assertEqual(policy["on_missing"], "MISSING_INPUT")

        result = execute_tool(
            "simulate_bundle_strategy",
            {"scenario": {"name": "세트", "bundle_price": 10000}},
            self.service,
        )
        self.assertEqual(result["error"]["code"], "MISSING_INPUT")
        self.assertEqual(
            result["error"]["details"]["missing_fields"],
            [
                "scenario.cannibalization_rate",
                "scenario.copurchase_take_rate",
                "scenario.incremental_demand_rate",
                "scenario.take_rate",
            ],
        )

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
