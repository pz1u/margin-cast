from pathlib import Path
import tempfile
import unittest

from src.evaluate_effect_models import evaluate_effect_models, run_evaluation
from src.generate_data import generate_dataset, save_dataset
from src.prepare_analysis_data import prepare_analysis_data


class EffectModelEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        tables, cls.truth = generate_dataset()
        save_dataset(tables, cls.truth, cls.root)
        cls.panel_path = cls.root / "processed" / "demand_panel.csv"
        cls.panel, _ = prepare_analysis_data(cls.root, cls.panel_path)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_effects_are_evaluated_without_training_leakage(self):
        report = evaluate_effect_models(self.panel, self.truth)
        self.assertFalse(report["ground_truth_used_for_training"])
        self.assertTrue(report["ground_truth_used_for_evaluation"])
        self.assertEqual(
            {row["menu_id"] for row in report["price_elasticity"]["menus"]},
            {"M01", "M02", "M03"},
        )
        self.assertEqual(
            {row["menu_id"] for row in report["promotion_lift"]["menus"]},
            {"M01", "M02"},
        )
        self.assertEqual(report["price_elasticity"]["interval_coverage"], 1.0)
        self.assertEqual(report["promotion_lift"]["interval_coverage"], 1.0)

    def test_evaluation_writes_reviewable_reports(self):
        output = self.root / "effects-report"
        report = run_evaluation(
            self.panel_path, self.root / "ground_truth.json", output
        )
        self.assertTrue((output / "README.md").is_file())
        self.assertTrue((output / "effects.json").is_file())
        self.assertLess(report["price_elasticity"]["mean_absolute_error"], 0.5)
        self.assertLess(report["promotion_lift"]["mean_absolute_error"], 0.1)


if __name__ == "__main__":
    unittest.main()
