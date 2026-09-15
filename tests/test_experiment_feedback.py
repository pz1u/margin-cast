import json
from pathlib import Path
import tempfile
import unittest

from src.experiment_feedback import (
    ExperimentFeedbackError,
    ExperimentFeedbackStore,
)


def feedback_payload(
    *,
    name="500원 인상 4일 실험",
    actual_units=97,
    actual_profit=610_000,
    baseline_profit=570_000,
):
    return {
        "experiment_name": name,
        "menu_id": "M01",
        "start_date": "2026-09-15",
        "end_date": "2026-09-18",
        "baseline_method": "matched_period",
        "scenario": {"name": "500원 인상", "list_price": 9500, "discount": 0},
        "prediction": {
            "horizon_days": 4,
            "units": {"mean": 100, "p10": 90, "p90": 112},
            "contribution_profit": {"mean": 620_000, "p10": 560_000, "p90": 680_000},
            "profit_delta": {"mean": 50_000, "p10": -10_000, "p90": 110_000},
        },
        "actual": {
            "units": actual_units,
            "contribution_profit": actual_profit,
            "baseline_contribution_profit": baseline_profit,
        },
    }


class ExperimentFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary_directory.name) / "feedback.json"
        self.store = ExperimentFeedbackStore(self.path)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_record_persists_prediction_and_evaluates_actual_outcome(self):
        record = self.store.record(feedback_payload())
        persisted = json.loads(self.path.read_text(encoding="utf-8"))

        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted[0]["feedback_id"], record["feedback_id"])
        self.assertEqual(record["evaluation"]["units"]["error"], -3.0)
        self.assertTrue(record["evaluation"]["units"]["within_80_interval"])
        self.assertEqual(record["evaluation"]["profit_delta"]["actual"], 40_000.0)
        self.assertTrue(record["evaluation"]["profit_delta"]["direction_correct"])

    def test_period_must_match_prediction_horizon(self):
        payload = feedback_payload()
        payload["end_date"] = "2026-09-19"

        with self.assertRaises(ExperimentFeedbackError) as context:
            self.store.record(payload)

        self.assertEqual(context.exception.code, "PERIOD_MISMATCH")
        self.assertEqual(context.exception.details["observed_days"], 5)
        self.assertFalse(self.path.exists())

    def test_invalid_interval_order_is_rejected(self):
        payload = feedback_payload()
        payload["prediction"]["units"] = {"mean": 100, "p10": 105, "p90": 110}

        with self.assertRaises(ExperimentFeedbackError) as context:
            self.store.record(payload)

        self.assertEqual(context.exception.code, "INVALID_FEEDBACK")

    def test_summary_reports_bias_coverage_and_direction_accuracy(self):
        self.store.record(feedback_payload())
        self.store.record(
            feedback_payload(
                name="두 번째 실험",
                actual_units=80,
                actual_profit=550_000,
                baseline_profit=570_000,
            )
        )

        summary = self.store.summary("M01")

        self.assertEqual(summary["record_count"], 2)
        self.assertAlmostEqual(
            summary["calibration"]["units"]["mean_absolute_error"], 11.5
        )
        self.assertEqual(summary["calibration"]["units"]["p80_coverage"], 0.5)
        self.assertEqual(summary["calibration"]["profit_delta"]["direction_accuracy"], 0.5)

    def test_corrupt_store_has_stable_error(self):
        self.path.write_text("{", encoding="utf-8")

        with self.assertRaises(ExperimentFeedbackError) as context:
            self.store.summary()

        self.assertEqual(context.exception.code, "FEEDBACK_STORE_CORRUPT")


if __name__ == "__main__":
    unittest.main()
