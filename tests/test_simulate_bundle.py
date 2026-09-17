from pathlib import Path
import tempfile
import unittest

from src.evidence_quality import EVIDENCE_QUALITY_POLICY_FINGERPRINT
from src.generate_data import generate_dataset, save_dataset
from src.prepare_analysis_data import load_observed_tables, prepare_analysis_data
from src.simulate_bundle import (
    DEFAULT_BUNDLE_SCENARIO,
    build_bundle_evidence,
    run_bundle_simulation,
    simulate_bundle,
    validate_bundle_scenario,
)


class BundleSimulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        tables, truth = generate_dataset()
        save_dataset(tables, truth, cls.root)
        cls.panel_path = cls.root / "processed" / "demand_panel.csv"
        prepare_analysis_data(cls.root, cls.panel_path)
        (cls.root / "ground_truth.json").unlink()
        cls.evidence = build_bundle_evidence(load_observed_tables(cls.root))

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_evidence_excludes_ground_truth_and_has_opportunities(self):
        self.assertFalse(self.evidence["ground_truth_used"])
        self.assertGreater(self.evidence["main_without_components"]["orders"], 0)
        self.assertGreater(self.evidence["main_with_components"]["orders"], 0)
        self.assertGreater(self.evidence["other_main"]["orders"], 0)

    def test_bundle_simulation_is_reproducible_and_separates_origins(self):
        first = simulate_bundle(
            self.evidence, DEFAULT_BUNDLE_SCENARIO, simulations=500, seed=7
        )
        second = simulate_bundle(
            self.evidence, DEFAULT_BUNDLE_SCENARIO, simulations=500, seed=7
        )
        self.assertEqual(first, second)
        self.assertEqual(first["evidence_quality"]["label"], "LOW")
        self.assertEqual(first["evidence_quality"]["version"], "heuristic-v1")
        self.assertEqual(
            first["evidence_quality"]["formula_fingerprint"],
            EVIDENCE_QUALITY_POLICY_FINGERPRINT,
        )
        self.assertFalse(
            first["evidence_quality"]["validation"]["empirically_calibrated"]
        )
        self.assertFalse(first["ground_truth_used"])
        self.assertEqual(
            set(first["orders"]),
            {
                "converted_main_without_components",
                "converted_existing_copurchase",
                "cannibalized_other_main",
                "incremental",
            },
        )

    def test_report_is_created_without_ground_truth_file(self):
        output = self.root / "bundle-report"
        report = run_bundle_simulation(
            self.root, self.panel_path, output, simulations=500
        )
        self.assertTrue((output / "README.md").is_file())
        self.assertTrue((output / "bundle-simulation.json").is_file())
        self.assertIn("scenario_contribution_profit", report["scenario"])

    def test_incremental_demand_changes_profit_while_zero_rates_do_not(self):
        zero = {
            **DEFAULT_BUNDLE_SCENARIO,
            "take_rate": 0,
            "copurchase_take_rate": 0,
            "incremental_demand_rate": 0,
            "cannibalization_rate": 0,
        }
        active = {**zero, "incremental_demand_rate": 0.2}
        zero_result = simulate_bundle(self.evidence, zero, simulations=500, seed=11)
        active_result = simulate_bundle(self.evidence, active, simulations=500, seed=11)
        self.assertEqual(zero_result["profit_delta"]["mean"], 0)
        self.assertGreater(active_result["orders"]["incremental"]["mean"], 0)
        self.assertGreater(active_result["profit_delta"]["mean"], 0)

    def test_invalid_rate_is_rejected(self):
        scenario = {**DEFAULT_BUNDLE_SCENARIO, "cannibalization_rate": 1.1}
        with self.assertRaises(ValueError):
            validate_bundle_scenario(scenario)

    def test_evidence_accepts_generic_main_and_multiple_components(self):
        evidence = build_bundle_evidence(
            load_observed_tables(self.root),
            main_menu_id="M02",
            component_menu_ids=["M05", "M07"],
        )
        self.assertEqual(evidence["main_menu_id"], "M02")
        self.assertEqual(evidence["component_menu_ids"], ["M05", "M07"])
        self.assertEqual(evidence["prices"]["components"], 5500.0)

    def test_bundle_menu_ids_reject_unknown_duplicate_and_main_component(self):
        tables = load_observed_tables(self.root)
        invalid_values = (["M99"], ["M06", "M06"], ["M01"])
        for component_menu_ids in invalid_values:
            with self.subTest(component_menu_ids=component_menu_ids):
                with self.assertRaises(ValueError):
                    build_bundle_evidence(
                        tables,
                        main_menu_id="M01",
                        component_menu_ids=component_menu_ids,
                    )


if __name__ == "__main__":
    unittest.main()
