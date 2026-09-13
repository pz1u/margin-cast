from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from src.generate_data import generate_dataset, save_dataset
from src.validate_data import load_dataset, validate_generated_data, validate_integrity, verify_reproducibility


class ValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tables, cls.truth = generate_dataset()

    def test_all_patterns_and_demo_directions(self):
        report = validate_generated_data(self.tables, self.truth)
        self.assertTrue(report["passed"])
        self.assertTrue(all(report["checks"].values()))
        self.assertEqual(report["warnings"], [])
        association = report["association_before_bundle"]
        self.assertTrue(0.25 < association["confidence"] < 0.30)
        self.assertTrue(0.08 < association["cola_rate"] < 0.12)
        self.assertLess(report["bundle"]["incremental_order_share"], 1)

    def test_multiple_seeds_without_rerolling(self):
        for seed in (7, 19, 101, 2026):
            with self.subTest(seed=seed):
                tables, truth = generate_dataset(seed)
                report = validate_generated_data(tables, truth)
                self.assertTrue(report["passed"])
                self.assertNotEqual(tables["orders"], self.tables["orders"])

    def test_custom_start_date_preserves_day_based_events(self):
        tables, truth = generate_dataset(start_date="2025-12-17")
        self.assertEqual(tables["daily_context"][-1]["date"], "2026-03-16")
        self.assertTrue(validate_generated_data(tables, truth)["passed"])

    def test_csv_roundtrip_and_file_reproducibility(self):
        with tempfile.TemporaryDirectory() as directory:
            save_dataset(self.tables, self.truth, directory)
            tables, truth = load_dataset(directory)
            self.assertEqual(tables, self.tables)
            self.assertTrue(validate_generated_data(tables, truth)["passed"])
            result = verify_reproducibility(directory, truth)
            self.assertTrue(result["identical"])
            self.assertEqual(result["files_compared"], 9)
            with (Path(directory) / "orders.csv").open("ab") as handle:
                handle.write(b"\n")
            with self.assertRaisesRegex(ValueError, "seed 재현성 실패: orders.csv"):
                verify_reproducibility(directory, truth)

    def test_invalid_reference_is_rejected(self):
        tables = deepcopy(self.tables)
        tables["order_items"][0]["order_id"] = "missing"
        with self.assertRaisesRegex(ValueError, "참조 오류"):
            validate_integrity(tables, self.truth)

    def test_double_discount_is_rejected_even_if_order_sum_matches(self):
        tables = deepcopy(self.tables)
        item = next(row for row in tables["order_items"] if row["promotion_id"])
        order = next(row for row in tables["orders"] if row["order_id"] == item["order_id"])
        item["contribution_profit"] -= item["discount_amount"]
        order["contribution_profit"] -= item["discount_amount"]
        with self.assertRaisesRegex(ValueError, "할인 중복 차감"):
            validate_integrity(tables, self.truth)

    def test_future_cost_is_rejected(self):
        tables = deepcopy(self.tables)
        item = next(row for row in tables["order_items"] if row["menu_id"] == "M01")
        item["unit_cost"] = 4900
        with self.assertRaisesRegex(ValueError, "주문 시점 원가"):
            validate_integrity(tables, self.truth)

    def test_missing_bundle_component_is_rejected(self):
        tables = deepcopy(self.tables)
        item = next(row for row in tables["order_items"] if row["bundle_id"])
        item["bundle_id"] = ""
        with self.assertRaises(ValueError):
            validate_integrity(tables, self.truth)

    def test_hidden_origin_in_later_row_is_rejected(self):
        tables = deepcopy(self.tables)
        tables["orders"][5]["origin"] = "incremental"
        with self.assertRaisesRegex(ValueError, "숨겨진 정답"):
            validate_integrity(tables, self.truth)

    def test_tampered_ground_truth_counts_are_rejected(self):
        truth = deepcopy(self.truth)
        truth["demand_audit"][0]["actual_count"] += 1
        with self.assertRaisesRegex(ValueError, "POS 주메뉴 수량"):
            validate_generated_data(self.tables, truth)


if __name__ == "__main__":
    unittest.main()
