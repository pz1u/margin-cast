import unittest
from collections import defaultdict
import math
import random

from src.generate_data import (
    calculate_item_amounts, calculate_price_factor, generate_dataset, poisson_quantile,
)


class CalculationTests(unittest.TestCase):
    def test_poisson_distribution_and_monotone_coupling(self):
        self.assertEqual(poisson_quantile(0, 0.9), 0)
        self.assertEqual(poisson_quantile(2, math.exp(-2) / 2), 0)
        self.assertEqual(poisson_quantile(2, 0.3), 1)
        rng = random.Random(17)
        counts = []
        for _ in range(20000):
            uniform = rng.random()
            lower = poisson_quantile(2, uniform)
            upper = poisson_quantile(4, uniform)
            self.assertLessEqual(lower, upper)
            counts.append(upper)
        mean = sum(counts) / len(counts)
        variance = sum((value - mean) ** 2 for value in counts) / len(counts)
        self.assertAlmostEqual(mean, 4, delta=0.06)
        self.assertAlmostEqual(variance, 4, delta=0.15)

    def test_price_elasticity_direction_and_identity(self):
        self.assertEqual(calculate_price_factor(9000, 9000, -1.25), 1)
        self.assertLess(calculate_price_factor(9500, 9000, -1.25), 1)
        self.assertGreater(calculate_price_factor(8000, 9000, -1.25), 1)
        self.assertAlmostEqual(calculate_price_factor(18000, 9000, -1), 0.5)
        with self.assertRaises(ValueError):
            calculate_price_factor(0, 9000, -1.25)

    def test_discount_is_not_deducted_twice(self):
        result = calculate_item_amounts(9000, 1000, 3800, "DELIVERY")
        self.assertEqual(result["net_sales"], 8000)
        self.assertEqual(result["platform_fee"], 1200)
        self.assertEqual(result["payment_fee"], 240)
        self.assertEqual(result["contribution_profit"], 2760)

    def test_store_fees_quantity_and_extra_promotion_cost(self):
        result = calculate_item_amounts(9000, 1000, 3800, "STORE", 2, 100)
        self.assertEqual(result["platform_fee"], 0)
        self.assertEqual(result["payment_fee"], 320)
        self.assertEqual(result["contribution_profit"], 7980)


class DatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tables, cls.truth = generate_dataset()

    def test_order_size_keys_and_amounts(self):
        orders, items = self.tables["orders"], self.tables["order_items"]
        self.assertTrue(10000 <= len(orders) <= 20000)
        self.assertGreater(len(items), len(orders))
        self.assertEqual(len({row["order_id"] for row in orders}), len(orders))
        self.assertEqual(len({row["order_item_id"] for row in items}), len(items))
        self.assertEqual({row["order_id"] for row in items}, {row["order_id"] for row in orders})
        totals = defaultdict(int)
        for item in items:
            totals[item["order_id"]] += item["contribution_profit"]
        self.assertEqual(dict(totals), {row["order_id"]: row["contribution_profit"] for row in orders})

    def test_paired_price_and_promotion_demand(self):
        chicken = [row for row in self.truth["demand_audit"] if row["menu_id"] == "M01"]
        for first, last in [(36, 42), (53, 59)]:
            period = [row for row in chicken if first <= row["day_index"] <= last]
            self.assertLess(sum(row["actual_count"] for row in period),
                            sum(row["no_intervention_count"] for row in period))
        promotion = [row for row in chicken if 21 <= row["day_index"] <= 27]
        self.assertGreater(sum(row["price_only_count"] for row in promotion),
                           sum(row["no_intervention_count"] for row in promotion))
        self.assertGreater(sum(row["actual_count"] for row in promotion),
                           sum(row["price_only_count"] for row in promotion))

    def test_hidden_parameters_are_not_observed_features(self):
        for table in self.tables.values():
            self.assertFalse({"price_elasticity", "base_demand", "promotion_lift",
                              "no_intervention_count"} & set(table[0]))

    def test_reproducibility(self):
        second, truth = generate_dataset()
        self.assertEqual(truth, self.truth)
        for name in self.tables:
            self.assertEqual(self.tables[name], second[name])


if __name__ == "__main__":
    unittest.main()
