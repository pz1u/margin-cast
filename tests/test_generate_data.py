import unittest
from collections import defaultdict
import math
import random

from src.generate_data import (
    BUNDLE_RULES, DAYS, PRICE_EXPERIMENTS, PROMOTION_EXPERIMENTS,
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
        self.assertTrue(100 * DAYS <= len(orders) <= 250 * DAYS)
        self.assertGreater(len(items), len(orders))
        self.assertEqual(len({row["order_id"] for row in orders}), len(orders))
        self.assertEqual(len({row["order_item_id"] for row in items}), len(items))
        self.assertEqual({row["order_id"] for row in items}, {row["order_id"] for row in orders})
        totals = defaultdict(int)
        for item in items:
            totals[item["order_id"]] += item["contribution_profit"]
        self.assertEqual(dict(totals), {row["order_id"]: row["contribution_profit"] for row in orders})

    def test_paired_price_and_promotion_demand(self):
        audit = self.truth["demand_audit"]
        for event in PRICE_EXPERIMENTS:
            period = [row for row in audit if row["menu_id"] == event["menu_id"]
                      and event["start_day"] <= row["day_index"] <= event["end_day"]]
            self.assertLess(sum(row["actual_count"] for row in period),
                            sum(row["no_intervention_count"] for row in period))
        for event in PROMOTION_EXPERIMENTS:
            promotion = [row for row in audit if row["menu_id"] == event["menu_id"]
                         and event["start_day"] <= row["day_index"] <= event["end_day"]]
            self.assertGreater(sum(row["price_only_count"] for row in promotion),
                               sum(row["no_intervention_count"] for row in promotion))
            self.assertGreater(sum(row["actual_count"] for row in promotion),
                               sum(row["price_only_count"] for row in promotion))

    def test_v2_has_repeated_multi_menu_interventions(self):
        self.assertEqual(self.truth["schema_version"], 2)
        price_by_menu = defaultdict(list)
        for event in PRICE_EXPERIMENTS:
            price_by_menu[event["menu_id"]].append(event["price"])
        self.assertEqual(set(price_by_menu), {"M01", "M02", "M03"})
        self.assertGreaterEqual(price_by_menu["M01"].count(9500), 2)
        self.assertGreaterEqual(price_by_menu["M01"].count(10000), 2)
        promotion_by_menu = defaultdict(int)
        for event in PROMOTION_EXPERIMENTS:
            promotion_by_menu[event["menu_id"]] += 1
        self.assertGreaterEqual(promotion_by_menu["M01"], 3)
        self.assertGreaterEqual(promotion_by_menu["M02"], 2)

    def test_hidden_parameters_are_not_observed_features(self):
        for table in self.tables.values():
            self.assertFalse({"price_elasticity", "base_demand", "promotion_lift",
                              "no_intervention_count", "origin", "_bundle_origin",
                              "original_menu_ids"} & set(table[0]))

    def test_reproducibility(self):
        second, truth = generate_dataset()
        self.assertEqual(truth, self.truth)
        for name in self.tables:
            self.assertEqual(self.tables[name], second[name])

    def test_bundle_replaces_orders_and_preserves_components(self):
        control, _ = generate_dataset(include_bundles=False)
        origins = self.truth["bundle_audit"]["origins"]
        incremental = sum(row["origin"] == "incremental" for row in origins)
        self.assertGreater(incremental, 0)
        self.assertEqual(len(self.tables["orders"]) - len(control["orders"]), incremental)
        self.assertEqual({row["origin"] for row in origins},
                         {"incremental", "chicken_only", "copurchase", "other_main"})
        grouped = defaultdict(list)
        for item in self.tables["order_items"]:
            if item["bundle_id"]:
                grouped[item["order_id"]].append(item)
        self.assertEqual(set(grouped), {row["order_id"] for row in origins})
        for components in grouped.values():
            self.assertEqual({row["menu_id"] for row in components}, {"M01", "M06"})
            self.assertEqual(len(components), 2)
            self.assertEqual(sum(row["net_sales"] for row in components), 10000)
            self.assertEqual(sum(row["discount_amount"] for row in components), 1000)
        # 치킨마요 증가량은 다른 주메뉴에서 전환한 고객과 신규 고객의 합이어야 한다.
        chicken = lambda tables: sum(row["quantity"] for row in tables["order_items"]
                                     if row["menu_id"] == "M01")
        self.assertEqual(chicken(self.tables) - chicken(control),
                         sum(row["origin"] in {"incremental", "other_main"} for row in origins))
        audit = self.truth["bundle_audit"]
        self.assertEqual(audit["incremental_orders"], incremental)
        self.assertEqual(
            audit["converted_existing_orders"],
            sum(row["origin"] != "incremental" for row in origins),
        )
        self.assertEqual(audit["incremental_menu_quantities"], {"M01": incremental, "M06": incremental})
        # 세트 켜기/끄기가 실험 외 기간의 난수와 거래 금액에 영향을 주면 안 된다.
        def outside(tables):
            return [{key: value for key, value in row.items() if key != "order_id"}
                    for row in tables["orders"]
                    if not BUNDLE_RULES["start_day"] <= row["day_index"] <= BUNDLE_RULES["end_day"]]
        self.assertEqual(outside(self.tables), outside(control))


if __name__ == "__main__":
    unittest.main()
