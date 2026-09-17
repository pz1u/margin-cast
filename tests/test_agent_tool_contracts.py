from pathlib import Path
import json
import tempfile
import unittest

from src.agent_tool_contracts import TOOL_SCHEMAS, execute_tool
from src.decision_service import MarginCastDecisionService
from src.evidence_quality import EVIDENCE_QUALITY_POLICY_FINGERPRINT
from src.experiment_feedback import ExperimentFeedbackStore
from src.forecast_decision_service import ForecastDecisionService
from src.generate_data import generate_dataset, save_dataset
from src.prepare_analysis_data import prepare_analysis_data


def fake_geocoder(address, env_path):
    return {
        "address_name": "서울 중구 세종대로 110",
        "latitude": 37.5665,
        "longitude": 126.9780,
    }


def fake_forecast_fetcher(nx, ny, env_path):
    return [
        {
            "date": f"2026-09-{15 + day:02d}",
            "time": "12:00",
            "forecast_at": f"2026-09-{15 + day:02d}T12:00:00+09:00",
            "is_rain": False,
        }
        for day in range(5)
    ]


def experiment_plan_payload():
    return {
        "experiment_name": "500원 인상 4일 실험",
        "menu_id": "M01",
        "start_date": "2026-09-15",
        "end_date": "2026-09-18",
        "scenario": {"name": "500원 인상", "list_price": 9500, "discount": 0},
        "prediction": {
            "horizon_days": 4,
            "units": {"mean": 100, "p10": 90, "p90": 112},
            "contribution_profit": {"mean": 620000, "p10": 560000, "p90": 680000},
            "profit_delta": {"mean": 50000, "p10": -10000, "p90": 110000},
        },
        "decision_context": {
            "engine_version": "0.5.0",
            "operation": "compare_price_strategies",
            "data_provenance": {
                "source_type": "synthetic_pos",
                "dataset_version": "synthetic-pos-v2",
                "uses_actual_store_data": False,
            },
            "weather_context": {
                "source": "observed_history",
                "menu_specific_causal_effect_validated": False,
            },
            "evidence_quality": {
                "version": "heuristic-v1",
                "formula_fingerprint": EVIDENCE_QUALITY_POLICY_FINGERPRINT,
                "label": "MEDIUM",
                "score": 70.0,
                "validation_status": "PENDING_REAL_STORE_FEEDBACK",
            },
            "decision_action": "EXPERIMENT",
        },
    }


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
        self.assertEqual(
            result["execution_defaults"]["compare_price_strategies"],
            {"horizon_days": 14, "simulations": 10000, "seed": 42},
        )
        self.assertEqual(
            result["execution_defaults"]["compare_price_strategies_with_forecast"][
                "horizon_days"
            ],
            4,
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

    def test_compare_dispatch_uses_engine_defaults_when_execution_values_are_omitted(self):
        result = execute_tool(
            "compare_price_strategies",
            {
                "menu_id": "M01",
                "scenarios": [{"name": "500원 인상", "list_price": 9500, "discount": 0}],
            },
            self.service,
        )
        self.assertEqual(
            result["request"],
            {"menu_id": "M01", "horizon_days": 14, "simulations": 10000, "seed": 42},
        )

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
            "main_menu_id": "M01",
            "component_menu_ids": ["M06"],
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
        self.assertEqual(result["request"]["main_menu_id"], "M01")

    def test_forecast_tool_accepts_all_location_modes_without_returning_sensitive_location(self):
        forecast_service = ForecastDecisionService(
            self.service,
            geocoder=fake_geocoder,
            forecast_fetcher=fake_forecast_fetcher,
        )
        locations = [
            ({"address": "서울특별시 중구 세종대로 110"}, "address"),
            ({"latitude": 37.5665, "longitude": 126.9780}, "wgs84"),
            ({"nx": 60, "ny": 127}, "kma_grid"),
        ]
        for location, expected_source in locations:
            with self.subTest(location=location):
                result = execute_tool(
                    "compare_price_strategies_with_forecast",
                    {
                        "location": location,
                        "menu_id": "M01",
                        "scenarios": [
                            {"name": "500원 인상", "list_price": 9500, "discount": 0}
                        ],
                        "simulations": 500,
                    },
                    self.service,
                    forecast_service=forecast_service,
                )
                serialized = json.dumps(result, ensure_ascii=False)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["weather"]["location_source"], expected_source)
                self.assertNotIn("서울특별시 중구 세종대로 110", serialized)
                self.assertNotIn("37.5665", serialized)
                self.assertNotIn("126.978", serialized)

    def test_forecast_tool_rejects_mixed_or_incomplete_location(self):
        forecast_service = ForecastDecisionService(
            self.service,
            geocoder=fake_geocoder,
            forecast_fetcher=fake_forecast_fetcher,
        )
        for location in (
            {"address": "서울", "nx": 60, "ny": 127},
            {"latitude": 37.5},
            {"nx": 60},
        ):
            with self.subTest(location=location):
                result = execute_tool(
                    "compare_price_strategies_with_forecast",
                    {"location": location, "menu_id": "M01", "scenarios": []},
                    self.service,
                    forecast_service=forecast_service,
                )
                self.assertEqual(result["error"]["code"], "INVALID_LOCATION")

    def test_feedback_tools_cover_plan_pending_completion_and_summary(self):
        store = ExperimentFeedbackStore(self.root / "feedback.json")
        planned = execute_tool(
            "create_experiment_plan",
            experiment_plan_payload(),
            self.service,
            feedback_store=store,
        )
        pending = execute_tool(
            "list_pending_experiments",
            {"menu_id": "M01"},
            self.service,
            feedback_store=store,
        )
        completed = execute_tool(
            "record_experiment_result",
            {
                "feedback_id": planned["plan"]["feedback_id"],
                "baseline_method": "matched_period",
                "actual": {
                    "units": 97,
                    "contribution_profit": 610000,
                    "baseline_contribution_profit": 570000,
                },
            },
            self.service,
            feedback_store=store,
        )
        summary = execute_tool(
            "get_feedback_summary",
            {"menu_id": "M01"},
            self.service,
            feedback_store=store,
        )
        self.assertEqual(planned["status"], "ok")
        self.assertEqual(len(pending["plans"]), 1)
        self.assertEqual(completed["record"]["status"], "completed")
        self.assertEqual(summary["record_count"], 1)

    def test_feedback_tool_errors_are_structured(self):
        store = ExperimentFeedbackStore(self.root / "empty-feedback.json")
        result = execute_tool(
            "record_experiment_result",
            {
                "feedback_id": "missing",
                "baseline_method": "matched_period",
                "actual": {
                    "units": 1,
                    "contribution_profit": 1,
                    "baseline_contribution_profit": 1,
                },
            },
            self.service,
            feedback_store=store,
        )
        self.assertEqual(result["error"]["code"], "FEEDBACK_NOT_FOUND")

    def test_invalid_json_and_unknown_tool_are_stable_errors(self):
        invalid = execute_tool("compare_price_strategies", "{", self.service)
        unknown = execute_tool("invent_numbers", {}, self.service)
        self.assertEqual(invalid["error"]["code"], "INVALID_ARGUMENTS")
        self.assertEqual(unknown["error"]["code"], "UNKNOWN_TOOL")


if __name__ == "__main__":
    unittest.main()
