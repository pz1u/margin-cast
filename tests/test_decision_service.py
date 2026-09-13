from pathlib import Path
import tempfile
import unittest

from src.decision_service import DecisionServiceError, MarginCastDecisionService
from src.generate_data import generate_dataset, save_dataset
from src.prepare_analysis_data import prepare_analysis_data


class DecisionServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        tables, truth = generate_dataset()
        save_dataset(tables, truth, cls.root)
        cls.panel_path = cls.root / "processed" / "demand_panel.csv"
        prepare_analysis_data(cls.root, cls.panel_path)
        (cls.root / "ground_truth.json").unlink()
        cls.service = MarginCastDecisionService(cls.panel_path)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_capabilities_only_expose_evidence_supported_menu(self):
        result = self.service.get_capabilities()
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["data"]["ground_truth_used"])
        self.assertEqual(
            [row["menu_id"] for row in result["supported_menus"]],
            ["M01", "M02", "M03"],
        )
        self.assertNotIn("price_elasticity", result["data"])

    def test_compare_adds_reference_and_returns_ranked_result(self):
        result = self.service.compare_price_strategies(
            "M01",
            [{"name": "가격 인상", "list_price": 9500, "discount": 0}],
            simulations=500,
            seed=7,
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["strategies"]), 2)
        self.assertTrue(result["strategies"][0]["is_reference"])
        self.assertEqual(result["highest_expected_profit"]["name"], "가격 인상")
        self.assertIn("downside_risk", result["strategies"][1])

    def test_same_request_is_reproducible(self):
        request = [{"name": "가격 인상", "list_price": 10000, "discount": 0}]
        first = self.service.compare_price_strategies("M01", request, simulations=500, seed=9)
        second = self.service.compare_price_strategies("M01", request, simulations=500, seed=9)
        self.assertEqual(first, second)

    def test_unsupported_menu_has_stable_error_code(self):
        with self.assertRaises(DecisionServiceError) as context:
            self.service.compare_price_strategies(
                "M04",
                [{"name": "가격 인상", "list_price": 8500, "discount": 0}],
                simulations=500,
            )
        self.assertEqual(context.exception.code, "UNSUPPORTED_MENU")
        self.assertEqual(context.exception.details["supported_menu_ids"], ["M01", "M02", "M03"])

    def test_invalid_numeric_types_are_rejected(self):
        with self.assertRaises(DecisionServiceError) as context:
            self.service.compare_price_strategies(
                "M01",
                [{"name": "오류", "list_price": True, "discount": 0}],
                simulations=500,
            )
        self.assertEqual(context.exception.code, "INVALID_SCENARIO")

    def test_reference_only_request_is_rejected(self):
        with self.assertRaises(DecisionServiceError) as context:
            self.service.compare_price_strategies(
                "M01",
                [{"name": "현재만", "list_price": 9000, "discount": 0}],
                simulations=500,
            )
        self.assertEqual(context.exception.code, "INVALID_SCENARIOS")


if __name__ == "__main__":
    unittest.main()
