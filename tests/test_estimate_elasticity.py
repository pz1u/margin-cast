from pathlib import Path
import tempfile
import unittest

from src.estimate_elasticity import demand_multiplier, estimate_price_elasticity, run_estimation
from src.generate_data import generate_dataset, save_dataset
from src.prepare_analysis_data import prepare_analysis_data


class ElasticityTests(unittest.TestCase):
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

    def test_observed_price_experiment_estimates_negative_elasticity(self):
        report = estimate_price_elasticity(self.panel)
        self.assertFalse(report["ground_truth_used"])
        self.assertEqual(report["split"], "train")
        self.assertEqual(report["price_levels"], [9000, 9500, 10000])
        self.assertEqual(report["paid_price_levels"], [8000, 9000, 9500, 10000])
        self.assertLess(report["elasticity"], 0)
        self.assertGreater(report["elasticity"], -3)
        self.assertGreater(report["promotion_lift"], 1)
        self.assertGreater(report["confidence_interval_95"][1], report["confidence_interval_95"][0])

    def test_price_multiplier_is_monotonic_for_negative_elasticity(self):
        elasticity = -1.2
        self.assertGreater(demand_multiplier(elasticity, 9000, 8500), 1)
        self.assertEqual(demand_multiplier(elasticity, 9000, 9000), 1)
        self.assertLess(demand_multiplier(elasticity, 9000, 9500), 1)
        with self.assertRaises(ValueError):
            demand_multiplier(elasticity, 9000, 0)

    def test_report_files_are_created(self):
        output = self.root / "elasticity-report"
        report = run_estimation(self.panel_path, output)
        self.assertTrue((output / "README.md").is_file())
        self.assertTrue((output / "elasticity.json").is_file())
        self.assertLess(report["elasticity"], 0)


if __name__ == "__main__":
    unittest.main()
