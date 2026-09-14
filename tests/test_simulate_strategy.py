from pathlib import Path
import tempfile
import unittest

import pandas as pd

from src.generate_data import generate_dataset, save_dataset
from src.prepare_analysis_data import prepare_analysis_data
from src.simulate_strategy import build_reference_forecast, run_simulation, validate_scenarios


class StrategySimulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        tables, truth = generate_dataset()
        save_dataset(tables, truth, cls.root)
        cls.panel_path = cls.root / "processed" / "demand_panel.csv"
        cls.panel, _ = prepare_analysis_data(cls.root, cls.panel_path)
        (cls.root / "ground_truth.json").unlink()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_simulation_is_reproducible_and_price_reduces_units(self):
        first = run_simulation(
            self.panel_path, self.root / "simulation-1", simulations=500, seed=7
        )
        second = run_simulation(
            self.panel_path, self.root / "simulation-2", simulations=500, seed=7
        )
        self.assertEqual(first, second)
        self.assertFalse(first["ground_truth_used"])
        by_name = {row["name"]: row for row in first["scenarios"]}
        self.assertLess(
            by_name["1,000원 인상"]["units"]["mean"],
            by_name["현재 가격"]["units"]["mean"],
        )
        self.assertGreater(
            by_name["1,000원 할인"]["units"]["mean"],
            by_name["현재 가격"]["units"]["mean"],
        )

    def test_probability_and_distribution_contract(self):
        report = run_simulation(
            self.panel_path, self.root / "simulation-contract", simulations=500
        )
        self.assertTrue((self.root / "simulation-contract" / "strategy-comparison.png").is_file())
        reference = report["scenarios"][0]
        self.assertIsNone(reference["success_probability"])
        for scenario in report["scenarios"][1:]:
            self.assertGreaterEqual(scenario["success_probability"], 0)
            self.assertLessEqual(scenario["success_probability"], 1)
            self.assertLessEqual(
                scenario["contribution_profit"]["p05"],
                scenario["contribution_profit"]["p95"],
            )
            self.assertLessEqual(
                scenario["contribution_profit"]["p10"],
                scenario["contribution_profit"]["p90"],
            )
            self.assertFalse(scenario["confidence"]["is_probability"])

    def test_invalid_scenario_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_scenarios([{"name": "오류", "list_price": 9000, "discount": 9000}])

    def test_future_weather_replaces_observed_context_without_filling_missing_days(self):
        last_date = self.panel["date"].max()
        forecasts = []
        for offset in (1, 2):
            date = (last_date + pd.Timedelta(days=offset)).strftime("%Y-%m-%d")
            for hour in (9, 12, 15, 18, 21):
                forecasts.append(
                    {
                        "date": date,
                        "time": f"{hour:02d}:00",
                        "forecast_at": f"{date}T{hour:02d}:00:00+09:00",
                        "is_rain": offset == 2,
                        "tmp_c": 24.0,
                        "pcp_raw": "강수없음" if offset == 1 else "1.0mm 미만",
                        "pcp_mm_estimate": 0.0 if offset == 1 else 0.5,
                        "pty_code": 0 if offset == 1 else 1,
                        "reh_pct": 60.0,
                    }
                )
        reference, _, source = build_reference_forecast(
            self.panel, horizon_days=2, forecasts=forecasts
        )
        self.assertEqual(source, "kma_forecast")
        self.assertEqual(len(reference), 2 * 11 * 2)
        self.assertEqual(set(reference["weather"]), {"CLEAR", "RAIN"})
        with self.assertRaises(ValueError):
            build_reference_forecast(self.panel, horizon_days=3, forecasts=forecasts)


if __name__ == "__main__":
    unittest.main()
