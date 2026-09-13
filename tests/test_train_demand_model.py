from pathlib import Path
import tempfile
import unittest

from src.generate_data import generate_dataset, save_dataset
from src.prepare_analysis_data import prepare_analysis_data
from src.train_demand_model import run_training, train_and_evaluate


class DemandModelTests(unittest.TestCase):
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

    def test_time_ordered_model_beats_historical_benchmark(self):
        _, report, predictions = train_and_evaluate(self.panel)
        self.assertFalse(report["ground_truth_used"])
        expected_train_days = int(self.panel.loc[self.panel["split"] == "train", "day_index"].nunique())
        self.assertEqual(report["train_days"], expected_train_days)
        self.assertEqual(len(predictions), int((self.panel["split"] != "train").sum()))
        for split in ("validation", "test"):
            self.assertLess(
                report["splits"][split]["model"]["mae"],
                report["splits"][split]["historical_benchmark"]["mae"],
            )

    def test_training_outputs_are_created(self):
        output = self.root / "model-report"
        _, report, predictions = run_training(self.panel_path, output)
        self.assertTrue((output / "README.md").is_file())
        self.assertTrue((output / "metrics.json").is_file())
        self.assertTrue((output / "predictions.csv").is_file())
        self.assertEqual(len(predictions), sum(v["rows"] for v in report["splits"].values()))


if __name__ == "__main__":
    unittest.main()
