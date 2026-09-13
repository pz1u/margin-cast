from pathlib import Path
import tempfile
import unittest

from src.generate_data import generate_dataset, save_dataset
from src.prepare_analysis_data import prepare_analysis_data
from src.simulate_strategy import run_simulation, validate_scenarios


class StrategySimulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        tables, truth = generate_dataset()
        save_dataset(tables, truth, cls.root)
        cls.panel_path = cls.root / "processed" / "demand_panel.csv"
        prepare_analysis_data(cls.root, cls.panel_path)
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

    def test_invalid_scenario_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_scenarios([{"name": "오류", "list_price": 9000, "discount": 9000}])


if __name__ == "__main__":
    unittest.main()
