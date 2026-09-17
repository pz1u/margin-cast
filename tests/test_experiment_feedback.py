import json
from pathlib import Path
import tempfile
import unittest

from src.evidence_quality import EVIDENCE_QUALITY_POLICY_FINGERPRINT
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
        "actual": {
            "units": actual_units,
            "contribution_profit": actual_profit,
            "baseline_contribution_profit": baseline_profit,
        },
    }


def plan_payload():
    payload = feedback_payload()
    payload.pop("baseline_method")
    payload.pop("actual")
    return payload


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
        self.assertEqual(
            record["decision_context"]["evidence_quality"]["version"], "heuristic-v1"
        )

    def test_single_record_summary_reports_stable_metrics(self):
        self.store.record(feedback_payload())

        summary = self.store.summary("M01")

        self.assertEqual(summary["record_count"], 1)
        self.assertEqual(summary["calibration"]["units"]["mean_absolute_error"], 3.0)
        self.assertEqual(summary["calibration"]["units"]["p80_coverage"], 1.0)
        profit_delta = summary["calibration"]["profit_delta"]
        self.assertEqual(profit_delta["mean_absolute_error"], 10_000.0)
        self.assertEqual(profit_delta["p80_coverage"], 1.0)
        self.assertEqual(profit_delta["direction_accuracy"], 1.0)

    def test_single_record_summary_detects_interval_miss_and_wrong_direction(self):
        self.store.record(
            feedback_payload(
                actual_units=80,
                actual_profit=550_000,
                baseline_profit=570_000,
            )
        )

        summary = self.store.summary("M01")

        self.assertEqual(summary["record_count"], 1)
        self.assertEqual(summary["calibration"]["units"]["mean_absolute_error"], 20.0)
        self.assertEqual(summary["calibration"]["units"]["p80_coverage"], 0.0)
        profit_delta = summary["calibration"]["profit_delta"]
        self.assertEqual(profit_delta["mean_absolute_error"], 70_000.0)
        self.assertEqual(profit_delta["p80_coverage"], 0.0)
        self.assertEqual(profit_delta["direction_accuracy"], 0.0)

    def test_period_must_match_prediction_horizon(self):
        payload = feedback_payload()
        payload["end_date"] = "2026-09-19"

        with self.assertRaises(ExperimentFeedbackError) as context:
            self.store.record(payload)

        self.assertEqual(context.exception.code, "PERIOD_MISMATCH")
        self.assertEqual(context.exception.details["observed_days"], 5)
        self.assertFalse(self.path.exists())

    def test_plan_preserves_prediction_until_actual_result_is_completed(self):
        planned = self.store.plan(plan_payload())
        before = self.store.summary("M01")

        completed = self.store.complete(
            planned["feedback_id"],
            "matched_period",
            {
                "units": 97,
                "contribution_profit": 610_000,
                "baseline_contribution_profit": 570_000,
            },
        )
        after = self.store.summary("M01")

        self.assertEqual(planned["status"], "planned")
        self.assertEqual(before["planned_count"], 1)
        self.assertEqual(before["record_count"], 0)
        self.assertEqual(completed["prediction"], planned["prediction"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(after["planned_count"], 0)
        self.assertEqual(after["record_count"], 1)

    def test_completed_plan_cannot_be_recorded_twice(self):
        planned = self.store.plan(plan_payload())
        actual = {
            "units": 97,
            "contribution_profit": 610_000,
            "baseline_contribution_profit": 570_000,
        }
        self.store.complete(planned["feedback_id"], "matched_period", actual)

        with self.assertRaises(ExperimentFeedbackError) as context:
            self.store.complete(planned["feedback_id"], "matched_period", actual)

        self.assertEqual(context.exception.code, "FEEDBACK_ALREADY_COMPLETED")

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
        quality = summary["evidence_quality_calibration"][0]
        self.assertEqual(quality["version"], "heuristic-v1")
        self.assertEqual(
            quality["formula_fingerprint"], EVIDENCE_QUALITY_POLICY_FINGERPRINT
        )
        self.assertEqual(quality["label"], "MEDIUM")
        self.assertEqual(quality["record_count"], 2)

    def test_summary_keeps_quality_versions_and_fingerprints_separate(self):
        self.store.record(feedback_payload())
        next_version = feedback_payload(name="v2 기준 실험")
        next_version["decision_context"]["evidence_quality"].update(
            {
                "version": "heuristic-v2",
                "formula_fingerprint": "2" * 64,
                "label": "HIGH",
                "score": 82.0,
            }
        )
        self.store.record(next_version)

        quality_groups = self.store.summary("M01")["evidence_quality_calibration"]

        self.assertEqual(len(quality_groups), 2)
        self.assertEqual(
            {
                (group["version"], group["formula_fingerprint"], group["label"])
                for group in quality_groups
            },
            {
                (
                    "heuristic-v1",
                    EVIDENCE_QUALITY_POLICY_FINGERPRINT,
                    "MEDIUM",
                ),
                ("heuristic-v2", "2" * 64, "HIGH"),
            },
        )

    def test_corrupt_store_has_stable_error(self):
        self.path.write_text("{", encoding="utf-8")

        with self.assertRaises(ExperimentFeedbackError) as context:
            self.store.summary()

        self.assertEqual(context.exception.code, "FEEDBACK_STORE_CORRUPT")


if __name__ == "__main__":
    unittest.main()
