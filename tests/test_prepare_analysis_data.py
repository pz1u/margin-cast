from pathlib import Path
import tempfile
import unittest

from src.generate_data import (
    COST_SHOCK_DAY,
    PRICE_EXPERIMENTS,
    PROMOTION_EXPERIMENTS,
    generate_dataset,
    save_dataset,
)
from src.prepare_analysis_data import (
    build_demand_panel,
    load_observed_tables,
    prepare_analysis_data,
    validate_demand_panel,
)


class AnalysisPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        tables, truth = generate_dataset()
        save_dataset(tables, truth, cls.temporary.name)
        Path(cls.temporary.name, "ground_truth.json").unlink()
        cls.tables = load_observed_tables(cls.temporary.name)
        cls.panel = build_demand_panel(cls.tables)
        cls.days = len(cls.tables["daily_context"])

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_complete_grid_includes_zero_sales(self):
        self.assertEqual(len(self.panel), self.days * 11 * 2 * 8)
        self.assertFalse(self.panel.duplicated(["date", "hour", "channel", "menu_id"]).any())
        zero_ratio = (self.panel["units_sold"] == 0).mean()
        self.assertGreater(zero_ratio, 0)
        self.assertLess(zero_ratio, 1)

    def test_source_totals_and_time_split(self):
        report = validate_demand_panel(self.panel, self.tables)
        self.assertTrue(report["passed"])
        self.assertFalse(report["ground_truth_loaded"])
        train_days = self.days * 2 // 3
        validation_days = self.days * 5 // 6 - train_days
        test_days = self.days - self.days * 5 // 6
        self.assertEqual(
            report["split_days"],
            {"train": train_days, "validation": validation_days, "test": test_days},
        )
        self.assertEqual(
            report["split_rows"],
            {
                "train": train_days * 11 * 2 * 8,
                "validation": validation_days * 11 * 2 * 8,
                "test": test_days * 11 * 2 * 8,
            },
        )

    def test_observable_intervention_features(self):
        for event in PRICE_EXPERIMENTS:
            rows = self.panel[
                (self.panel["menu_id"] == event["menu_id"])
                & self.panel["day_index"].between(event["start_day"], event["end_day"])
            ]
            self.assertEqual(set(rows["offered_list_price"]), {event["price"]})
        for event in PROMOTION_EXPERIMENTS:
            rows = self.panel[
                (self.panel["menu_id"] == event["menu_id"])
                & self.panel["day_index"].between(event["start_day"], event["end_day"])
            ]
            self.assertEqual(set(rows["promotion_discount"]), {event["discount"]})
        chicken = self.panel[self.panel["menu_id"] == "M01"]
        bundle = self.tables["bundles"].iloc[0]
        bundle_days = self.tables["daily_context"].loc[
            self.tables["daily_context"]["date"].between(bundle.start_date, bundle.end_date),
            "day_index",
        ]
        self.assertEqual(set(chicken.loc[chicken["day_index"].isin(bundle_days), "bundle_available"]), {1})
        self.assertEqual(set(chicken.loc[chicken["day_index"] == bundle_days.max() + 1, "bundle_available"]), {0})
        self.assertEqual(set(chicken.loc[chicken["day_index"] == COST_SHOCK_DAY, "unit_cost"]), {4900})

    def test_ground_truth_fields_are_absent(self):
        forbidden = {"price_elasticity", "base_demand", "promotion_lift", "no_intervention_count", "origin"}
        self.assertFalse(forbidden.intersection(self.panel.columns))

    def test_saved_panel_roundtrip(self):
        output = Path(self.temporary.name, "processed", "demand_panel.csv")
        panel, report = prepare_analysis_data(self.temporary.name, output)
        self.assertTrue(output.is_file())
        self.assertTrue(output.with_name("panel_validation.json").is_file())
        self.assertEqual(len(panel), report["rows"])


if __name__ == "__main__":
    unittest.main()
