from pathlib import Path
import tempfile
import unittest

from src.confidence_score import calculate_confidence
from src.estimate_elasticity import estimate_price_elasticity
from src.generate_data import generate_dataset, save_dataset
from src.prepare_analysis_data import prepare_analysis_data


class ConfidenceScoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        tables, truth = generate_dataset()
        save_dataset(tables, truth, cls.root)
        cls.panel, _ = prepare_analysis_data(
            cls.root, cls.root / "processed" / "demand_panel.csv"
        )
        cls.elasticity = estimate_price_elasticity(cls.panel, "M01")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_confidence_is_separate_from_probability_and_explained(self):
        result = calculate_confidence(
            self.panel,
            self.elasticity,
            {"name": "500원 인상", "list_price": 9500, "discount": 0},
            "observed_history",
        )
        self.assertFalse(result["is_probability"])
        self.assertIn(result["label"], {"LOW", "MEDIUM", "HIGH"})
        self.assertEqual(sum(result["components"].values()), result["score"])
        self.assertEqual(result["evidence"]["price_events"], 4)

    def test_forecast_and_observed_range_raise_confidence(self):
        supported = {"name": "500원 인상", "list_price": 9500, "discount": 0}
        extrapolated = {"name": "큰 인상", "list_price": 12000, "discount": 0}
        observed = calculate_confidence(
            self.panel, self.elasticity, supported, "observed_history"
        )
        forecast = calculate_confidence(
            self.panel, self.elasticity, supported, "kma_forecast"
        )
        outside = calculate_confidence(
            self.panel, self.elasticity, extrapolated, "observed_history"
        )
        self.assertGreater(forecast["score"], observed["score"])
        self.assertGreater(observed["score"], outside["score"])


if __name__ == "__main__":
    unittest.main()
