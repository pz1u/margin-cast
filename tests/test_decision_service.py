from pathlib import Path
from datetime import timedelta
import tempfile
import unittest

import pandas as pd

from src.decision_service import DecisionServiceError, MarginCastDecisionService
from src.generate_data import generate_dataset, save_dataset
from src.prepare_analysis_data import prepare_analysis_data


class DecisionServiceTests(unittest.TestCase):
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

    def test_capabilities_only_expose_evidence_supported_menu(self):
        result = self.service.get_capabilities()
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["data"]["ground_truth_used"])
        self.assertEqual(result["data_provenance"]["source_type"], "synthetic_pos")
        self.assertFalse(result["data_provenance"]["uses_actual_store_data"])
        self.assertEqual(
            [row["menu_id"] for row in result["supported_menus"]],
            ["M01", "M02", "M03"],
        )
        self.assertNotIn("price_elasticity", result["data"])

    def test_compare_adds_reference_and_returns_ranked_result(self):
        result = self.service.compare_price_strategies(
            "M01",
            [{"name": "가격 인상", "list_price": 9500, "discount": 0}],
            simulations=500,
            seed=7,
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["strategies"]), 2)
        self.assertTrue(result["strategies"][0]["is_reference"])
        self.assertIsNone(result["strategies"][0]["evidence_quality"])
        quality = result["strategies"][1]["evidence_quality"]
        self.assertFalse(quality["is_probability"])
        self.assertFalse(quality["validation"]["empirically_calibrated"])
        self.assertEqual(result["data_provenance"]["label"], "SYNTHETIC_DATA_PROTOTYPE")
        self.assertEqual(result["highest_expected_profit"]["name"], "가격 인상")
        self.assertIn("downside_risk", result["strategies"][1])
        self.assertIn(result["recommended_action"]["action"], {"HOLD", "EXPERIMENT", "RECOMMEND"})
        self.assertEqual(result["decision_ranking"][0]["rank"], 1)

    def test_same_request_is_reproducible(self):
        request = [{"name": "가격 인상", "list_price": 10000, "discount": 0}]
        first = self.service.compare_price_strategies("M01", request, simulations=500, seed=9)
        second = self.service.compare_price_strategies("M01", request, simulations=500, seed=9)
        self.assertEqual(first, second)

    def test_future_weather_changes_context_without_claiming_causality(self):
        last_date = pd.to_datetime(self.service._load_panel()["date"]).max()
        forecasts = [
            {
                "date": (last_date + timedelta(days=offset)).strftime("%Y-%m-%d"),
                "time": "12:00",
                "forecast_at": (
                    last_date + timedelta(days=offset, hours=12)
                ).isoformat(),
                "is_rain": offset == 2,
                "tmp_c": 22.0 + offset,
                "pcp_raw": "1mm" if offset == 2 else "강수없음",
                "pcp_mm_estimate": 1.0 if offset == 2 else 0.0,
                "pty_code": 1 if offset == 2 else 0,
                "reh_pct": 70.0,
            }
            for offset in (1, 2)
        ]
        result = self.service.compare_price_strategies(
            "M01",
            [{"name": "가격 인상", "list_price": 9500, "discount": 0}],
            horizon_days=2,
            simulations=500,
            seed=7,
            forecasts=forecasts,
        )

        self.assertEqual(result["weather_context_source"], "kma_forecast")
        self.assertEqual(
            result["strategies"][1]["evidence_quality"]["components"]["weather_context"],
            20.0,
        )
        self.assertFalse(result["weather_context"]["menu_specific_causal_effect_validated"])
        self.assertTrue(
            any("기상청 단기예보" in note for note in result["interpretation_notes"])
        )

    def test_unsupported_menu_has_stable_error_code(self):
        with self.assertRaises(DecisionServiceError) as context:
            self.service.compare_price_strategies(
                "M04",
                [{"name": "가격 인상", "list_price": 8500, "discount": 0}],
                simulations=500,
            )
        self.assertEqual(context.exception.code, "UNSUPPORTED_MENU")
        self.assertEqual(context.exception.details["supported_menu_ids"], ["M01", "M02", "M03"])
        self.assertEqual(context.exception.details["observed_price_levels"], [8000])
        self.assertEqual(context.exception.details["required_unique_price_levels"], 2)
        self.assertIn("최소 한 개 가격", context.exception.details["next_step"])

    def test_invalid_numeric_types_are_rejected(self):
        with self.assertRaises(DecisionServiceError) as context:
            self.service.compare_price_strategies(
                "M01",
                [{"name": "오류", "list_price": True, "discount": 0}],
                simulations=500,
            )
        self.assertEqual(context.exception.code, "INVALID_SCENARIO")

    def test_reference_only_request_is_rejected(self):
        with self.assertRaises(DecisionServiceError) as context:
            self.service.compare_price_strategies(
                "M01",
                [{"name": "현재만", "list_price": 9000, "discount": 0}],
                simulations=500,
            )
        self.assertEqual(context.exception.code, "INVALID_SCENARIOS")

    def test_low_evidence_quality_bundle_is_limited_to_experiment(self):
        result = self.service.simulate_bundle_strategy(
            {
                "name": "세트 실험",
                "bundle_price": 10000,
                "take_rate": 0.3,
                "copurchase_take_rate": 0.55,
                "incremental_demand_rate": 0.1,
                "cannibalization_rate": 0.02,
            },
            simulations=500,
            seed=7,
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["strategy"]["evidence_quality"]["label"], "LOW")
        self.assertEqual(result["decision"]["action"], "EXPERIMENT")
        self.assertFalse(result["decision"]["is_probability"])


if __name__ == "__main__":
    unittest.main()
