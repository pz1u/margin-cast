from pathlib import Path
import json
import tempfile
import unittest

import pandas as pd

from src.decision_service import DecisionServiceError, MarginCastDecisionService
from src.generate_data import PAYMENT_RATE, PLATFORM_RATE, generate_dataset, save_dataset
from src.http_api import dispatch_api
from src.prepare_analysis_data import load_observed_tables, prepare_analysis_data
from src.store_profile import (
    ANALYZABLE,
    DATA_COLLECTING,
    INSUFFICIENT_PRICE_VARIATION,
    StoreProfileError,
    StoreProfileService,
    fee_rates,
    unit_economics,
)

BUNDLE_SCENARIO = {
    "name": "테스트 세트",
    "bundle_price": 10_000,
    "take_rate": 0.3,
    "copurchase_take_rate": 0.5,
    "incremental_demand_rate": 0.1,
    "cannibalization_rate": 0.02,
}
PRICE_SCENARIOS = [{"name": "500원 인상", "list_price": 9500, "discount": 0}]


class StoreProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        tables, truth = generate_dataset()
        save_dataset(tables, truth, cls.root)
        cls.panel_path = cls.root / "processed" / "demand_panel.csv"
        prepare_analysis_data(cls.root, cls.panel_path)
        (cls.root / "ground_truth.json").unlink()
        cls.tables = load_observed_tables(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.store_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.store_dir.cleanup)
        self.store_path = Path(self.store_dir.name) / "store_profile.json"
        self.service = MarginCastDecisionService(self.panel_path)
        self.store = StoreProfileService(self.service, store_path=self.store_path)
        self.service.set_cost_overrides_provider(self.store.user_cost_overrides)

    def menu(self, menu_id):
        return next(row for row in self.store.list_menus() if row["menu_id"] == menu_id)

    def new_menu(self, **changes):
        arguments = {
            "menu_name": "새 김치볶음밥",
            "category": "MAIN",
            "list_price": 9000,
            "ingredient_cost": 3600,
            "channels": ["STORE", "DELIVERY"],
        }
        arguments.update(changes)
        return self.store.add_menu(arguments)

    # -------------------------------------------------------------- POS view
    def test_pos_menus_are_listed_with_cost_history_costs(self):
        menus = self.store.list_menus()
        self.assertEqual(
            [row["menu_id"] for row in menus], sorted(self.tables["menus"]["menu_id"])
        )
        latest = (
            self.tables["menu_cost_history"]
            .sort_values("effective_date")
            .groupby("menu_id")["unit_cost"]
            .last()
        )
        for row in menus:
            self.assertEqual(row["source"], "POS")
            self.assertEqual(row["ingredient_cost"], int(latest[row["menu_id"]]))
            self.assertEqual(row["cost_source"], "POS_HISTORY")
        self.assertNotIn("packaging_cost", menus[0])

    def test_status_reports_pos_state_and_demo_notice(self):
        status = self.store.get_status()
        self.assertEqual(status["dataset_version"], "synthetic-pos-v2")
        self.assertFalse(status["uses_actual_store_data"])
        self.assertEqual(status["order_count"], len(self.tables["orders"]))
        self.assertEqual(status["menu_count"], len(self.tables["menus"]))
        self.assertEqual(status["analyzable_menu_count"], 3)
        self.assertIn("synthetic-pos-v2", status["demo_notice"])

    def test_analysis_status_comes_from_engine_capabilities(self):
        capabilities = self.service.get_capabilities()
        supported = {row["menu_id"] for row in capabilities["supported_menus"]}
        for row in self.store.list_menus():
            expected = ANALYZABLE if row["menu_id"] in supported else INSUFFICIENT_PRICE_VARIATION
            self.assertEqual(row["analysis"]["status"], expected)

    def test_unit_economics_matches_engine_rows(self):
        items = self.tables["order_items"].merge(
            self.tables["orders"][["order_id", "channel"]], on="order_id"
        )
        plain = items[(items["promotion_id"] == "") | items["promotion_id"].isna()]
        plain = plain[(plain["bundle_id"] == "") | plain["bundle_id"].isna()]
        for channel, share in (("STORE", 0.0), ("DELIVERY", 1.0)):
            row = plain[(plain["channel"] == channel) & (plain["menu_id"] == "M01")].iloc[0]
            economics = unit_economics(int(row["list_price"]), int(row["unit_cost"]), share)
            self.assertLessEqual(
                abs(economics["contribution_per_unit"] - row["contribution_profit"]), 1.0
            )

    def test_cost_rate_is_ingredient_over_price_and_not_an_input(self):
        economics = self.menu("M01")["economics"]
        row = self.menu("M01")
        self.assertAlmostEqual(economics["cost_rate"], row["ingredient_cost"] / row["list_price"])

    def test_fees_are_read_only_engine_constants(self):
        fees = fee_rates()
        self.assertFalse(fees["editable"])
        self.assertEqual(fees["source"], "ENGINE")
        self.assertEqual(fees["DELIVERY"]["platform"], PLATFORM_RATE["DELIVERY"])
        self.assertEqual(fees["DELIVERY"]["payment"], PAYMENT_RATE["DELIVERY"])
        self.assertEqual(fees["STORE"]["payment"], PAYMENT_RATE["STORE"])
        self.assertEqual(fees["STORE"]["platform"], 0.0)

    # ---------------------------------------------------------- cost override
    def test_cost_override_is_saved_and_reset(self):
        result = self.store.update_menu_cost({"menu_id": "M01", "ingredient_cost": 5200})
        self.assertEqual(result["ingredient_cost"], 5200)
        self.assertEqual(result["cost_source"], "USER")
        self.assertEqual(self.store.effective_unit_cost("M01")["source"], "USER")
        reloaded = StoreProfileService(self.service, store_path=self.store_path)
        self.assertEqual(reloaded.user_cost_overrides(), {"M01": 5200})
        restored = self.store.update_menu_cost({"menu_id": "M01", "reset": True})
        self.assertEqual(restored["cost_source"], "POS_HISTORY")
        self.assertEqual(self.store.user_cost_overrides(), {})
        self.assertEqual(self.store.effective_unit_cost("M01")["source"], "POS_HISTORY")

    def test_override_equal_to_pos_cost_is_not_recorded(self):
        pos_cost = self.menu("M02")["pos_ingredient_cost"]
        self.store.update_menu_cost({"menu_id": "M02", "ingredient_cost": pos_cost})
        self.assertEqual(self.store.user_cost_overrides(), {})

    def test_pos_price_cannot_be_edited(self):
        with self.assertRaises(StoreProfileError) as context:
            self.store.update_menu_cost(
                {"menu_id": "M01", "ingredient_cost": 4000, "list_price": 12000}
            )
        self.assertEqual(context.exception.code, "INVALID_MENU")

    def test_invalid_cost_inputs_are_rejected(self):
        for arguments in (
            {"menu_id": "M01", "ingredient_cost": -1},
            {"menu_id": "M01", "ingredient_cost": "4000"},
            {"menu_id": "M01", "ingredient_cost": True},
            {"menu_id": "M01", "ingredient_cost": 4000, "reset": True},
            {"menu_id": "M01"},
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(StoreProfileError):
                    self.store.update_menu_cost(arguments)
        with self.assertRaises(StoreProfileError) as context:
            self.store.update_menu_cost({"menu_id": "NOPE", "ingredient_cost": 100})
        self.assertEqual(context.exception.code, "MENU_NOT_FOUND")

    def test_override_changes_profit_but_not_demand_or_elasticity(self):
        baseline = self.service.compare_price_strategies(
            "M01", PRICE_SCENARIOS, horizon_days=14, simulations=500, seed=11
        )
        self.assertEqual(baseline["cost_basis"]["source"], "POS_HISTORY")
        self.store.update_menu_cost({"menu_id": "M01", "ingredient_cost": 5900})
        changed = self.service.compare_price_strategies(
            "M01", PRICE_SCENARIOS, horizon_days=14, simulations=500, seed=11
        )
        self.assertEqual(changed["cost_basis"]["source"], "USER")
        self.assertEqual(changed["cost_basis"]["unit_cost"], 5900.0)
        for before, after in zip(baseline["strategies"], changed["strategies"]):
            self.assertEqual(before["units"], after["units"])
            self.assertEqual(before["demand_multiplier"], after["demand_multiplier"])
            self.assertLess(
                after["contribution_profit"]["mean"], before["contribution_profit"]["mean"]
            )
            expected_drop = (5900 - baseline["cost_basis"]["unit_cost"]) * before["units"]["mean"]
            self.assertAlmostEqual(
                before["contribution_profit"]["mean"] - after["contribution_profit"]["mean"],
                expected_drop,
                delta=expected_drop * 0.03,
            )
        self.assertEqual(baseline["model"], changed["model"])

    def test_override_does_not_mutate_cached_reference_demand(self):
        self.service.compare_price_strategies(
            "M01", PRICE_SCENARIOS, horizon_days=14, simulations=200, seed=1
        )
        cached = self.service._reference_cache[("M01", 14)][0]
        before = cached[["unit_cost", "base_mean"]].copy()
        self.store.update_menu_cost({"menu_id": "M01", "ingredient_cost": 6100})
        self.service.compare_price_strategies(
            "M01", PRICE_SCENARIOS, horizon_days=14, simulations=200, seed=1
        )
        pd.testing.assert_frame_equal(before, cached[["unit_cost", "base_mean"]])

    def test_override_reaches_bundle_simulation(self):
        arguments = {
            "scenario": BUNDLE_SCENARIO,
            "horizon_days": 14,
            "simulations": 500,
            "seed": 3,
        }
        baseline = self.service.simulate_bundle_strategy(**arguments)
        self.store.update_menu_cost({"menu_id": "M06", "ingredient_cost": 1700})
        changed = self.service.simulate_bundle_strategy(**arguments)
        sources = {row["menu_id"]: row["source"] for row in changed["cost_basis"]}
        self.assertEqual(sources, {"M01": "POS_HISTORY", "M06": "USER"})
        self.assertLess(
            changed["strategy"]["profit_delta"]["mean"],
            baseline["strategy"]["profit_delta"]["mean"],
        )
        self.assertEqual(baseline["strategy"]["orders"], changed["strategy"]["orders"])

    # -------------------------------------------------------------- new menus
    def test_new_menu_is_added_with_derived_economics_and_is_not_analyzable(self):
        menu = self.new_menu()
        self.assertEqual(menu["menu_id"], "U001")
        self.assertEqual(menu["source"], "MANUAL")
        self.assertAlmostEqual(menu["economics"]["cost_rate"], 3600 / 9000)
        self.assertGreater(menu["economics"]["contribution_per_unit"], 0)
        self.assertEqual(menu["analysis"]["status"], DATA_COLLECTING)
        self.assertIn("데이터가 충분하지 않아", menu["analysis"]["message"])
        self.assertNotIn("elasticity", json.dumps(menu))
        self.assertNotIn("success_probability", json.dumps(menu))
        self.assertEqual(self.store.get_status()["analyzable_menu_count"], 3)

    def test_new_menu_is_rejected_by_price_and_bundle_engine(self):
        menu = self.new_menu()
        with self.assertRaises(DecisionServiceError) as context:
            self.service.compare_price_strategies(menu["menu_id"], PRICE_SCENARIOS)
        self.assertEqual(context.exception.code, "UNSUPPORTED_MENU")
        with self.assertRaises(StoreProfileError):
            self.store.bundle_observation(
                {"main_menu_id": menu["menu_id"], "component_menu_ids": ["M06"]}
            )
        self.assertNotIn(menu["menu_id"], self.store.user_cost_overrides())

    def test_new_menu_channels_change_the_fee_mix(self):
        store_only = self.new_menu(menu_name="매장 전용", channels=["STORE"])
        delivery_only = self.new_menu(menu_name="배달 전용", channels=["DELIVERY"])
        self.assertEqual(store_only["economics"]["delivery_share"], 0.0)
        self.assertEqual(delivery_only["economics"]["delivery_share"], 1.0)
        self.assertGreater(
            store_only["economics"]["contribution_per_unit"],
            delivery_only["economics"]["contribution_per_unit"],
        )

    def test_new_menu_validation_and_duplicate_names(self):
        self.new_menu()
        with self.assertRaises(StoreProfileError) as duplicate:
            self.new_menu()
        self.assertEqual(duplicate.exception.code, "DUPLICATE_MENU")
        with self.assertRaises(StoreProfileError):
            self.new_menu(menu_name=self.menu("M01")["menu_name"])
        for changes in (
            {"menu_name": " "},
            {"category": "DESSERT"},
            {"list_price": 500},
            {"ingredient_cost": -1},
            {"channels": []},
            {"channels": ["PICKUP"]},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(StoreProfileError):
                    self.new_menu(**{"menu_name": "검증 메뉴", **changes})
        with self.assertRaises(StoreProfileError):
            self.store.add_menu({"menu_name": "포장비 포함", "category": "MAIN"})
        with self.assertRaises(StoreProfileError):
            self.new_menu(menu_name="원가율 입력", cost_rate=0.4)

    def test_custom_menu_can_be_deleted_but_pos_menu_cannot(self):
        menu = self.new_menu()
        self.store.delete_menu({"menu_id": menu["menu_id"]})
        self.assertEqual(
            len(self.store.list_menus()), len(self.tables["menus"])
        )
        with self.assertRaises(StoreProfileError):
            self.store.delete_menu({"menu_id": "M01"})

    def test_corrupt_store_file_is_reported(self):
        self.store_path.write_text("{", encoding="utf-8")
        with self.assertRaises(StoreProfileError) as context:
            self.store.list_menus()
        self.assertEqual(context.exception.code, "STORE_PROFILE_CORRUPT")

    # ----------------------------------------------------------------- bundle
    def test_bundle_observation_uses_pos_history_and_marks_unknowns(self):
        result = self.store.bundle_observation(
            {"main_menu_id": "M02", "component_menu_ids": ["M06"]}
        )
        self.assertTrue(result["ready"])
        self.assertGreater(result["observed"]["main_units_sold"], 0)
        self.assertGreater(result["observed"]["copurchase_orders"], 0)
        self.assertTrue(0 < result["observed"]["copurchase_rate"] < 1)
        fields = {row["field"] for row in result["unidentifiable"]}
        self.assertEqual(
            fields,
            {
                "take_rate",
                "copurchase_take_rate",
                "incremental_demand_rate",
                "cannibalization_rate",
            },
        )
        self.assertTrue(
            all(row["note"] == "판매 데이터만으로 확인할 수 없는 값" for row in result["unidentifiable"])
        )
        self.assertEqual(
            [row["scenario_id"] for row in result["planning_scenarios"]],
            ["conservative", "base", "optimistic"],
        )
        for scenario in result["planning_scenarios"]:
            self.assertEqual(scenario["source"], "DEFAULT")
            self.assertFalse(scenario["is_observed"])

    def test_bundle_observation_rejects_invalid_menu_selection(self):
        for arguments in (
            {"main_menu_id": "M05", "component_menu_ids": ["M06"]},
            {"main_menu_id": "M01", "component_menu_ids": ["M01"]},
            {"main_menu_id": "M01", "component_menu_ids": ["M99"]},
            {"main_menu_id": "M01", "component_menu_ids": []},
            {"main_menu_id": "M01", "component_menu_ids": ["M06", "M06"]},
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(StoreProfileError):
                    self.store.bundle_observation(arguments)

    def test_bundle_simulation_accepts_selected_menus(self):
        default = self.service.simulate_bundle_strategy(BUNDLE_SCENARIO, 14, 500, 5)
        selected = self.service.simulate_bundle_strategy(
            BUNDLE_SCENARIO,
            14,
            500,
            5,
            main_menu_id="M02",
            component_menu_ids=["M07"],
        )
        self.assertEqual(default["bundle_menus"]["main_menu_id"], "M01")
        self.assertEqual(selected["bundle_menus"]["component_menu_ids"], ["M07"])
        self.assertNotEqual(
            default["strategy"]["profit_delta"], selected["strategy"]["profit_delta"]
        )


class StoreHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        StoreProfileTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.store_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.store_dir.cleanup)
        self.service = MarginCastDecisionService(self.panel_path)
        self.store = StoreProfileService(
            self.service, store_path=Path(self.store_dir.name) / "store.json"
        )
        self.service.set_cost_overrides_provider(self.store.user_cost_overrides)

    def call(self, method, path, body=None):
        return dispatch_api(
            method,
            path,
            None if body is None else json.dumps(body),
            self.service,
            store_profile=self.store,
        )

    def test_profile_endpoint_exposes_status_fees_and_menus(self):
        status, payload = self.call("GET", "/api/store/profile")
        self.assertEqual(status, 200)
        self.assertEqual(payload["store"]["dataset_version"], "synthetic-pos-v2")
        self.assertEqual(len(payload["menus"]), 8)
        self.assertFalse(payload["fees"]["editable"])

    def test_cost_and_menu_endpoints(self):
        status, payload = self.call(
            "POST", "/api/store/menus/cost", {"menu_id": "M01", "ingredient_cost": 5000}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["menu"]["cost_source"], "USER")
        status, payload = self.call(
            "POST",
            "/api/store/menus",
            {
                "menu_name": "새 메뉴",
                "category": "SIDE",
                "list_price": 4000,
                "ingredient_cost": 1200,
                "channels": ["STORE"],
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["menu"]["analysis"]["status"], "DATA_COLLECTING")
        status, payload = self.call("POST", "/api/store/menus/cost", {"menu_id": "M01", "ingredient_cost": -3})
        self.assertEqual(status, 400)
        status, payload = self.call("POST", "/api/store/menus/cost", {"menu_id": "ZZ", "ingredient_cost": 3})
        self.assertEqual(status, 404)
        status, payload = self.call(
            "POST",
            "/api/store/menus",
            {
                "menu_name": "새 메뉴",
                "category": "SIDE",
                "list_price": 4000,
                "ingredient_cost": 1200,
                "channels": ["STORE"],
            },
        )
        self.assertEqual(status, 409)

    def test_fees_cannot_be_written(self):
        status, payload = self.call("POST", "/api/store/fees", {"delivery_platform": 0.01})
        self.assertEqual(status, 404)
        status, payload = self.call("POST", "/api/store/profile", {})
        self.assertEqual(status, 405)

    def test_bundle_endpoints_use_selected_menus_without_hardcoding(self):
        status, payload = self.call(
            "POST",
            "/api/store/bundle/observation",
            {"main_menu_id": "M03", "component_menu_ids": ["M05"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["main"]["menu_id"], "M03")
        status, payload = self.call(
            "POST",
            "/api/store/bundle/simulate",
            {
                "main_menu_id": "M03",
                "component_menu_ids": ["M05"],
                "scenario": BUNDLE_SCENARIO,
                "horizon_days": 14,
                "simulations": 500,
                "seed": 9,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["bundle_menus"]["main_menu_id"], "M03")
        self.assertEqual(payload["bundle_menus"]["component_menu_ids"], ["M05"])
        status, payload = self.call(
            "POST",
            "/api/store/bundle/simulate",
            {"main_menu_id": "M03", "scenario": BUNDLE_SCENARIO},
        )
        self.assertEqual(status, 400)

    def test_store_routes_report_unavailable_without_service(self):
        status, payload = dispatch_api("GET", "/api/store/profile", None, self.service)
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"]["code"], "STORE_PROFILE_UNAVAILABLE")

    def test_agent_bundle_tool_contract_is_unchanged(self):
        status, payload = dispatch_api(
            "POST",
            "/api/strategies/bundle",
            json.dumps(
                {
                    "scenario": BUNDLE_SCENARIO,
                    "horizon_days": 7,
                    "simulations": 500,
                    "seed": 1,
                    "main_menu_id": "M02",
                }
            ),
            self.service,
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "INVALID_ARGUMENTS")


if __name__ == "__main__":
    unittest.main()
