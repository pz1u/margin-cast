from pathlib import Path
import tempfile
import unittest

from src.generate_data import generate_dataset, save_dataset
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

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_complete_grid_includes_zero_sales(self):
        self.assertEqual(len(self.panel), 90 * 11 * 2 * 8)
        self.assertFalse(self.panel.duplicated(["date", "hour", "channel", "menu_id"]).any())
        zero_ratio = (self.panel["units_sold"] == 0).mean()
        self.assertGreater(zero_ratio, 0)
        self.assertLess(zero_ratio, 1)

    def test_source_totals_and_time_split(self):
        report = validate_demand_panel(self.panel, self.tables)
        self.assertTrue(report["passed"])
        self.assertFalse(report["ground_truth_loaded"])
        self.assertEqual(report["split_days"], {"train": 60, "validation": 15, "test": 15})
        self.assertEqual(report["split_rows"], {"train": 10560, "validation": 2640, "test": 2640})

    def test_observable_intervention_features(self):
        chicken = self.panel[self.panel["menu_id"] == "M01"]
        self.assertEqual(set(chicken.loc[chicken["day_index"].between(36, 42), "offered_list_price"]), {9500})
        self.assertEqual(set(chicken.loc[chicken["day_index"].between(53, 59), "offered_list_price"]), {10000})
        self.assertEqual(set(chicken.loc[chicken["day_index"].between(21, 27), "promotion_discount"]), {1000})
        self.assertEqual(set(chicken.loc[chicken["day_index"].between(75, 81), "bundle_available"]), {1})
        self.assertEqual(set(chicken.loc[chicken["day_index"] == 82, "bundle_available"]), {0})
        self.assertEqual(set(chicken.loc[chicken["day_index"] == 67, "unit_cost"]), {4900})

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
