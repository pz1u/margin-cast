from pathlib import Path
import tempfile
import unittest

from src.generate_data import generate_dataset, save_dataset
from src.prepare_analysis_data import prepare_analysis_data
from src.run_eda import build_eda_summary, load_panel, run_eda


class EdaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        tables, truth = generate_dataset()
        cls.expected_orders = len(tables["orders"])
        cls.expected_units = sum(row["quantity"] for row in tables["order_items"])
        cls.expected_bundle_orders = len(
            {row["order_id"] for row in tables["order_items"] if row["bundle_id"]}
        )
        save_dataset(tables, truth, cls.root)
        cls.panel_path = cls.root / "processed" / "demand_panel.csv"
        prepare_analysis_data(cls.root, cls.panel_path)
        (cls.root / "ground_truth.json").unlink()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_summary_uses_observed_data(self):
        from src.prepare_analysis_data import load_observed_tables

        tables = load_observed_tables(self.root)
        summary = build_eda_summary(load_panel(self.panel_path), tables)
        self.assertFalse(summary["scope"]["ground_truth_used"])
        self.assertEqual(summary["totals"]["orders"], self.expected_orders)
        self.assertEqual(summary["totals"]["units_sold"], self.expected_units)
        self.assertEqual(summary["bundle_observed"]["orders_with_bundle"], self.expected_bundle_orders)
        self.assertGreater(summary["association_before_bundle"]["lift"], 1)
        experiment_targets = {row["menu_id"] for row in summary["observed_experiments"]}
        self.assertEqual(experiment_targets, {"M01", "M02", "M03"})
        self.assertEqual(len(summary["observed_experiments"]), 17)
        self.assertEqual(summary["modeling_contract"]["target"], "units_sold")
        self.assertIn("contribution_profit", summary["modeling_contract"]["exclude_from_demand_features"])

    def test_report_and_charts_are_created(self):
        output = self.root / "report"
        summary, charts = run_eda(self.root, self.panel_path, output)
        self.assertTrue(summary["scope"]["panel_rows"] > 0)
        self.assertTrue((output / "README.md").is_file())
        self.assertTrue((output / "summary.json").is_file())
        self.assertEqual(len(charts), 2)
        self.assertTrue(all(path.stat().st_size > 10_000 for path in charts))


if __name__ == "__main__":
    unittest.main()
