"""POS 연동 매장(현재는 synthetic-pos-v2)의 메뉴·원가 설정과 세트 관측 정보를 제공한다.

계산 엔진의 contribution profit 정의를 그대로 따른다.

    contribution_profit = 할인 후 매출 - 식재료 원가 - 플랫폼 수수료 - 결제 수수료

사용자가 수정한 식재료 원가는 `user_cost_overrides()`로 계산 엔진에 전달되어 실제 기여이익
계산에 쓰인다. 수요 예측과 가격탄력성은 원가를 사용하지 않으므로 바뀌지 않는다. 신규 메뉴는
과거 주문이 없으므로 가격탄력성·성공확률을 만들지 않고 DATA_COLLECTING으로만 표시한다.
포장비와 요청별 수수료율은 엔진이 모델링하지 않아 다루지 않는다.
"""

import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path

try:
    from .decision_service import DATA_PROVENANCE
    from .generate_data import PAYMENT_RATE, PLATFORM_RATE
    from .prepare_analysis_data import load_observed_tables
    from .simulate_bundle import InsufficientBundleHistory, build_bundle_evidence
except ImportError:
    from decision_service import DATA_PROVENANCE
    from generate_data import PAYMENT_RATE, PLATFORM_RATE
    from prepare_analysis_data import load_observed_tables
    from simulate_bundle import InsufficientBundleHistory, build_bundle_evidence


CATEGORIES = ("MAIN", "SIDE", "DRINK")
CHANNELS = ("STORE", "DELIVERY")
MIN_PRICE = 1_000
MAX_PRICE = 100_000
MAX_COST = 100_000
MAX_CUSTOM_MENUS = 30
MAX_NAME_LENGTH = 30
MAX_BUNDLE_COMPONENTS = 4

ANALYZABLE = "ANALYZABLE"
INSUFFICIENT_PRICE_VARIATION = "INSUFFICIENT_PRICE_VARIATION"
DATA_COLLECTING = "DATA_COLLECTING"
ANALYSIS_LABELS = {
    ANALYZABLE: "분석 가능",
    INSUFFICIENT_PRICE_VARIATION: "가격 변화 데이터 부족",
    DATA_COLLECTING: "데이터 수집 중",
}
NEW_MENU_MESSAGE = (
    "아직 판매·가격 변경 데이터가 충분하지 않아 가격 전략 시뮬레이션을 사용할 수 없습니다."
)

COST_SOURCE_USER = "USER"
COST_SOURCE_POS = "POS_HISTORY"

UNIDENTIFIABLE_BUNDLE_FIELDS = (
    ("take_rate", "세트를 본 단품 고객 중 세트를 고를 비율"),
    ("copurchase_take_rate", "이미 함께 사던 고객 중 세트로 바꿀 비율"),
    ("incremental_demand_rate", "세트 때문에 새로 생기는 주문 비율"),
    ("cannibalization_rate", "다른 메뉴 고객이 세트로 옮겨 오는 비율"),
)
UNIDENTIFIABLE_NOTE = "판매 데이터만으로 확인할 수 없는 값"

# 관측값이 아닌 계획용 가정이다. 화면과 Agent는 이 값을 DEFAULT 출처로만 다룬다.
BUNDLE_PLANNING_SCENARIOS = (
    {
        "scenario_id": "conservative",
        "label": "보수 계획",
        "assumptions": {
            "take_rate": 0.15,
            "copurchase_take_rate": 0.35,
            "incremental_demand_rate": 0.03,
            "cannibalization_rate": 0.05,
        },
    },
    {
        "scenario_id": "base",
        "label": "기준 계획",
        "assumptions": {
            "take_rate": 0.30,
            "copurchase_take_rate": 0.55,
            "incremental_demand_rate": 0.10,
            "cannibalization_rate": 0.02,
        },
    },
    {
        "scenario_id": "optimistic",
        "label": "낙관 계획",
        "assumptions": {
            "take_rate": 0.40,
            "copurchase_take_rate": 0.70,
            "incremental_demand_rate": 0.15,
            "cannibalization_rate": 0.01,
        },
    },
)


class StoreProfileError(ValueError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = {} if details is None else details


def fee_rates():
    """엔진이 실제로 사용하는 수수료율. 값은 generate_data의 상수를 그대로 노출한다."""
    return {
        "STORE": {
            "platform": PLATFORM_RATE["STORE"],
            "payment": PAYMENT_RATE["STORE"],
        },
        "DELIVERY": {
            "platform": PLATFORM_RATE["DELIVERY"],
            "payment": PAYMENT_RATE["DELIVERY"],
        },
        "editable": False,
        "source": "ENGINE",
    }


def unit_economics(list_price, ingredient_cost, delivery_share):
    """엔진과 같은 규칙의 단품 경제성. 수수료는 할인 후 매출(=판매가) 기준이다."""
    if list_price <= 0:
        raise StoreProfileError("INVALID_MENU", "판매가는 0보다 커야 합니다.")
    delivery_share = min(1.0, max(0.0, float(delivery_share)))
    store_rate = PLATFORM_RATE["STORE"] + PAYMENT_RATE["STORE"]
    delivery_rate = PLATFORM_RATE["DELIVERY"] + PAYMENT_RATE["DELIVERY"]
    average_rate = (1 - delivery_share) * store_rate + delivery_share * delivery_rate

    def contribution(rate):
        return list_price - ingredient_cost - list_price * rate

    return {
        "cost_rate": ingredient_cost / list_price,
        "delivery_share": delivery_share,
        "fee_rate": average_rate,
        "fee_per_unit": list_price * average_rate,
        "contribution_per_unit": contribution(average_rate),
        "contribution_by_channel": {
            "STORE": contribution(store_rate),
            "DELIVERY": contribution(delivery_rate),
        },
    }


def _integer(value, label, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int):
        raise StoreProfileError("INVALID_MENU", f"{label}은 원 단위 정수여야 합니다.")
    if not minimum <= value <= maximum:
        raise StoreProfileError(
            "INVALID_MENU", f"{label}은 {minimum:,}~{maximum:,}원 범위여야 합니다."
        )
    return value


def _channels(value):
    if (
        not isinstance(value, list)
        or not value
        or any(item not in CHANNELS for item in value)
        or len(set(value)) != len(value)
    ):
        raise StoreProfileError(
            "INVALID_MENU", "판매 채널은 STORE, DELIVERY 중 한 개 이상이어야 합니다."
        )
    return [channel for channel in CHANNELS if channel in value]


class StoreProfileService:
    def __init__(self, decision_service, store_path=None):
        root = Path(__file__).resolve().parents[1]
        self._decision = decision_service
        self.data_dir = Path(decision_service.data_dir)
        self.path = Path(store_path or root / "data" / "store" / "store_profile.json")
        self._lock = threading.RLock()
        self._tables = None
        self._pos_menus = None
        self._store_delivery_share = None

    # ------------------------------------------------------------------ POS
    def _load_tables(self):
        if self._tables is None:
            self._tables = load_observed_tables(self.data_dir)
        return self._tables

    def _load_pos(self):
        """POS 메뉴, 원가 이력의 최신 원가와 메뉴별 관측 배달 비중을 한 번만 계산한다."""
        if self._pos_menus is not None:
            return self._pos_menus
        tables = self._load_tables()
        cost = (
            tables["menu_cost_history"]
            .sort_values("effective_date")
            .groupby("menu_id")["unit_cost"]
            .last()
            .to_dict()
        )
        orders = tables["orders"][["order_id", "channel"]]
        items = tables["order_items"].merge(
            orders, on="order_id", how="left", validate="many_to_one"
        )
        units = items.groupby("menu_id")["quantity"].sum()
        delivery_units = items[items["channel"].eq("DELIVERY")].groupby("menu_id")["quantity"].sum()
        self._store_delivery_share = float(tables["orders"]["channel"].eq("DELIVERY").mean())
        menus = []
        for row in tables["menus"].sort_values("menu_id").itertuples(index=False):
            sold = int(units.get(row.menu_id, 0))
            menus.append(
                {
                    "menu_id": row.menu_id,
                    "menu_name": row.menu_name,
                    "category": row.category,
                    "list_price": int(row.initial_list_price),
                    "pos_ingredient_cost": int(cost[row.menu_id]),
                    "units_sold": sold,
                    "delivery_share": (
                        float(delivery_units.get(row.menu_id, 0)) / sold if sold else 0.0
                    ),
                }
            )
        self._pos_menus = menus
        return menus

    def _analysis_lookup(self):
        capabilities = self._decision.get_capabilities()
        lookup = {row["menu_id"]: (ANALYZABLE, row) for row in capabilities["supported_menus"]}
        lookup.update(
            {
                row["menu_id"]: (INSUFFICIENT_PRICE_VARIATION, row)
                for row in capabilities["unsupported_menus"]
            }
        )
        return lookup

    def get_status(self):
        tables = self._load_tables()
        state = self._read()
        lookup = self._analysis_lookup()
        analyzable = sum(1 for status, _ in lookup.values() if status == ANALYZABLE)
        try:
            synced = datetime.fromtimestamp((self.data_dir / "orders.csv").stat().st_mtime)
            last_synced_at = synced.astimezone().isoformat(timespec="seconds")
        except OSError:
            last_synced_at = None
        last_order = tables["orders"]["ordered_at"].max()
        return {
            "connection": "SYNTHETIC_POS",
            "connected": True,
            "dataset_version": DATA_PROVENANCE["dataset_version"],
            "uses_actual_store_data": DATA_PROVENANCE["uses_actual_store_data"],
            "last_synced_at": last_synced_at,
            "last_order_at": None if last_order != last_order else str(last_order),
            "order_count": int(len(tables["orders"])),
            "menu_count": len(self._load_pos()) + len(state["custom_menus"]),
            "delivery_share": self._store_delivery_share,
            "custom_menu_count": len(state["custom_menus"]),
            "analyzable_menu_count": analyzable,
            "demo_notice": (
                "현재는 실제 POS가 아니라 synthetic-pos-v2 합성 데이터를 POS 연동 결과처럼 "
                "사용하는 데모입니다. 실서비스에서는 POS 연동 데이터로 대체됩니다."
            ),
        }

    # -------------------------------------------------------------- storage
    @staticmethod
    def _empty_state():
        return {"cost_overrides": {}, "custom_menus": [], "next_custom_number": 1}

    def _read(self):
        if not self.path.exists():
            return self._empty_state()
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise StoreProfileError(
                "STORE_PROFILE_CORRUPT", "매장 설정 저장소를 읽을 수 없습니다."
            ) from error
        if (
            not isinstance(state, dict)
            or not isinstance(state.get("cost_overrides"), dict)
            or not isinstance(state.get("custom_menus"), list)
            or not isinstance(state.get("next_custom_number"), int)
        ):
            raise StoreProfileError(
                "STORE_PROFILE_CORRUPT", "매장 설정 저장소 형식이 올바르지 않습니다."
            )
        return state

    def _write(self, state):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                dir=self.path.parent, prefix=f".{self.path.stem}-", suffix=".tmp"
            )
        except OSError as error:
            raise StoreProfileError(
                "STORE_PROFILE_UNAVAILABLE", "매장 설정 저장소를 준비할 수 없습니다."
            ) from error
        temporary_path = Path(temporary_name)
        try:
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                    json.dump(state, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary_path, self.path)
            except OSError as error:
                raise StoreProfileError(
                    "STORE_PROFILE_UNAVAILABLE", "매장 설정을 저장할 수 없습니다."
                ) from error
        finally:
            if temporary_path.exists():
                try:
                    temporary_path.unlink()
                except OSError:
                    pass

    # ------------------------------------------------------- effective cost
    def user_cost_overrides(self):
        """계산 엔진에 전달할 사용자 식재료 원가. POS 메뉴의 수정값만 포함한다."""
        with self._lock:
            return {key: int(value) for key, value in self._read()["cost_overrides"].items()}

    def effective_unit_cost(self, menu_id):
        """메뉴의 식재료 원가와 출처. 사용자 수정값이 있으면 USER, 없으면 원가 이력이다."""
        overrides = self.user_cost_overrides()
        if menu_id in overrides:
            return {"menu_id": menu_id, "unit_cost": overrides[menu_id], "source": COST_SOURCE_USER}
        for pos in self._load_pos():
            if pos["menu_id"] == menu_id:
                return {
                    "menu_id": menu_id,
                    "unit_cost": pos["pos_ingredient_cost"],
                    "source": COST_SOURCE_POS,
                }
        raise StoreProfileError("MENU_NOT_FOUND", "메뉴를 찾을 수 없습니다.", {"menu_id": str(menu_id)})

    # ----------------------------------------------------------------- menus
    def _pos_view(self, pos, override, analysis):
        status, profile = analysis
        cost = pos["pos_ingredient_cost"] if override is None else override
        if status == ANALYZABLE:
            message = "가격 시뮬레이션을 사용할 수 있습니다."
        else:
            message = (
                "서로 다른 판매가 이력이 부족해 가격 시뮬레이션을 제공할 수 없습니다. "
                "기준 가격 외 한 개 가격을 별도 기간에 운영하면 분석할 수 있습니다."
            )
        return {
            "menu_id": pos["menu_id"],
            "menu_name": pos["menu_name"],
            "category": pos["category"],
            "source": "POS",
            "channels": list(CHANNELS),
            "list_price": pos["list_price"],
            "ingredient_cost": cost,
            "pos_ingredient_cost": pos["pos_ingredient_cost"],
            "cost_source": COST_SOURCE_POS if override is None else COST_SOURCE_USER,
            "units_sold": pos["units_sold"],
            "economics": unit_economics(pos["list_price"], cost, pos["delivery_share"]),
            "analysis": {
                "status": status,
                "label": ANALYSIS_LABELS[status],
                "message": message,
                "observed_price_levels": profile["observed_price_levels"],
            },
        }

    def _custom_view(self, menu):
        if menu["channels"] == ["STORE"]:
            share = 0.0
        elif menu["channels"] == ["DELIVERY"]:
            share = 1.0
        else:
            self._load_pos()
            share = self._store_delivery_share
        return {
            "menu_id": menu["menu_id"],
            "menu_name": menu["menu_name"],
            "category": menu["category"],
            "source": "MANUAL",
            "channels": menu["channels"],
            "list_price": menu["list_price"],
            "ingredient_cost": menu["ingredient_cost"],
            "pos_ingredient_cost": None,
            "cost_source": COST_SOURCE_USER,
            "units_sold": 0,
            "economics": unit_economics(menu["list_price"], menu["ingredient_cost"], share),
            "analysis": {
                "status": DATA_COLLECTING,
                "label": ANALYSIS_LABELS[DATA_COLLECTING],
                "message": NEW_MENU_MESSAGE,
                "observed_price_levels": [],
            },
        }

    def list_menus(self):
        state = self._read()
        lookup = self._analysis_lookup()
        menus = [
            self._pos_view(pos, state["cost_overrides"].get(pos["menu_id"]), lookup[pos["menu_id"]])
            for pos in self._load_pos()
        ]
        menus.extend(self._custom_view(menu) for menu in state["custom_menus"])
        return menus

    def get_profile(self):
        return {
            "status": "ok",
            "store": self.get_status(),
            "fees": fee_rates(),
            "categories": list(CATEGORIES),
            "menus": self.list_menus(),
            "cost_definition": {
                "cost_rate": "식재료 원가 / 판매가",
                "contribution": "판매가 - 식재료 원가 - 수수료(채널 비중 반영)",
                "engine_note": (
                    "식재료 원가를 수정하면 기여이익 시뮬레이션에도 반영됩니다. "
                    "수요 예측과 가격탄력성은 변하지 않습니다."
                ),
            },
        }

    def _all_names(self, state):
        names = {pos["menu_name"].casefold() for pos in self._load_pos()}
        names.update(menu["menu_name"].casefold() for menu in state["custom_menus"])
        return names

    def add_menu(self, arguments):
        expected = {"menu_name", "category", "list_price", "ingredient_cost", "channels"}
        if not isinstance(arguments, dict) or set(arguments) != expected:
            raise StoreProfileError(
                "INVALID_MENU",
                "메뉴 추가 요청의 필드를 확인하세요.",
                {"expected_fields": sorted(expected)},
            )
        name = arguments["menu_name"]
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= MAX_NAME_LENGTH:
            raise StoreProfileError("INVALID_MENU", f"메뉴명은 1~{MAX_NAME_LENGTH}자여야 합니다.")
        name = name.strip()
        if arguments["category"] not in CATEGORIES:
            raise StoreProfileError(
                "INVALID_MENU", "카테고리는 MAIN, SIDE, DRINK 중 하나여야 합니다."
            )
        menu = {
            "menu_name": name,
            "category": arguments["category"],
            "list_price": _integer(arguments["list_price"], "판매가", MIN_PRICE, MAX_PRICE),
            "ingredient_cost": _integer(arguments["ingredient_cost"], "식재료 원가", 0, MAX_COST),
            "channels": _channels(arguments["channels"]),
        }
        with self._lock:
            state = self._read()
            if len(state["custom_menus"]) >= MAX_CUSTOM_MENUS:
                raise StoreProfileError(
                    "INVALID_MENU",
                    f"직접 추가한 메뉴는 {MAX_CUSTOM_MENUS}개까지 등록할 수 있습니다.",
                )
            if name.casefold() in self._all_names(state):
                raise StoreProfileError("DUPLICATE_MENU", "같은 이름의 메뉴가 이미 있습니다.")
            menu["menu_id"] = f"U{state['next_custom_number']:03d}"
            menu["created_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            state["next_custom_number"] += 1
            state["custom_menus"].append(menu)
            self._write(state)
            return self._custom_view(menu)

    def update_menu_cost(self, arguments):
        """식재료 원가를 수정한다. POS 메뉴의 판매가는 POS 값이라 수정할 수 없다."""
        if not isinstance(arguments, dict) or "menu_id" not in arguments:
            raise StoreProfileError("INVALID_MENU", "menu_id를 입력하세요.")
        unknown = sorted(set(arguments) - {"menu_id", "ingredient_cost", "reset"})
        if unknown:
            raise StoreProfileError(
                "INVALID_MENU", "수정할 수 없는 필드가 있습니다.", {"unknown_fields": unknown}
            )
        reset = arguments.get("reset", False)
        if not isinstance(reset, bool):
            raise StoreProfileError("INVALID_MENU", "reset은 true 또는 false여야 합니다.")
        if reset == ("ingredient_cost" in arguments):
            raise StoreProfileError(
                "INVALID_MENU", "ingredient_cost 또는 reset 중 하나만 입력하세요."
            )
        menu_id = arguments["menu_id"]
        cost = None if reset else _integer(arguments["ingredient_cost"], "식재료 원가", 0, MAX_COST)

        with self._lock:
            state = self._read()
            custom = next((m for m in state["custom_menus"] if m["menu_id"] == menu_id), None)
            if custom is not None:
                if reset:
                    raise StoreProfileError(
                        "INVALID_MENU", "직접 추가한 메뉴는 POS 원가로 되돌릴 수 없습니다."
                    )
                custom["ingredient_cost"] = cost
                self._write(state)
                return self._custom_view(custom)
            pos = next((m for m in self._load_pos() if m["menu_id"] == menu_id), None)
            if pos is None:
                raise StoreProfileError(
                    "MENU_NOT_FOUND", "메뉴를 찾을 수 없습니다.", {"menu_id": str(menu_id)}
                )
            # POS 원가와 같은 값은 저장하지 않아 수정 상태가 남지 않게 한다.
            if reset or cost == pos["pos_ingredient_cost"]:
                state["cost_overrides"].pop(menu_id, None)
            else:
                state["cost_overrides"][menu_id] = cost
            self._write(state)
            return self._pos_view(
                pos, state["cost_overrides"].get(menu_id), self._analysis_lookup()[menu_id]
            )

    def delete_menu(self, arguments):
        if not isinstance(arguments, dict) or set(arguments) != {"menu_id"}:
            raise StoreProfileError("INVALID_MENU", "menu_id만 입력하세요.")
        menu_id = arguments["menu_id"]
        with self._lock:
            state = self._read()
            remaining = [m for m in state["custom_menus"] if m["menu_id"] != menu_id]
            if len(remaining) == len(state["custom_menus"]):
                raise StoreProfileError(
                    "MENU_NOT_FOUND",
                    "삭제할 수 있는 직접 추가 메뉴를 찾을 수 없습니다.",
                    {"menu_id": str(menu_id)},
                )
            state["custom_menus"] = remaining
            self._write(state)
        return {"menu_id": menu_id}

    # ---------------------------------------------------------------- bundle
    def bundle_observation(self, arguments):
        """세트 계산 전에 POS에서 관측 가능한 값과 관측할 수 없는 값을 구분해 반환한다."""
        if not isinstance(arguments, dict) or set(arguments) != {
            "main_menu_id",
            "component_menu_ids",
        }:
            raise StoreProfileError(
                "INVALID_BUNDLE_MENUS", "main_menu_id와 component_menu_ids를 입력하세요."
            )
        main_id = arguments["main_menu_id"]
        component_ids = arguments["component_menu_ids"]
        if (
            not isinstance(main_id, str)
            or not isinstance(component_ids, list)
            or not 1 <= len(component_ids) <= MAX_BUNDLE_COMPONENTS
            or any(not isinstance(value, str) for value in component_ids)
        ):
            raise StoreProfileError(
                "INVALID_BUNDLE_MENUS",
                f"구성 메뉴는 1~{MAX_BUNDLE_COMPONENTS}개의 메뉴 ID여야 합니다.",
            )
        tables = self._load_tables()
        try:
            evidence = build_bundle_evidence(
                tables, main_menu_id=main_id, component_menu_ids=component_ids
            )
        except InsufficientBundleHistory as error:
            # 함께 구매된 주문이 없는 조합은 오류가 아니라 '계산 불가' 안내로 돌려준다.
            return {
                "status": "ok",
                "ready": False,
                "not_ready_reason": str(error),
                "unidentifiable": [],
                "planning_scenarios": [],
            }
        except (KeyError, ValueError) as error:
            raise StoreProfileError("INVALID_BUNDLE_MENUS", str(error)) from error

        prices = tables["menus"].set_index("menu_id")["initial_list_price"].to_dict()
        names = tables["menus"].set_index("menu_id")["menu_name"].to_dict()
        bundle_start = tables["bundles"]["start_date"].min()
        orders = tables["orders"][["order_id", "date"]]
        items = tables["order_items"].merge(orders, on="order_id", validate="many_to_one")
        history = items[items["date"] < bundle_start]
        main_units = int(history.loc[history["menu_id"] == main_id, "quantity"].sum())
        with_components = evidence["main_with_components"]["orders"]
        main_orders = evidence["main_without_components"]["orders"] + with_components
        return {
            "status": "ok",
            "ready": True,
            "not_ready_reason": None,
            "main": {"menu_id": main_id, "menu_name": names[main_id], "list_price": int(prices[main_id])},
            "components": [
                {"menu_id": value, "menu_name": names[value], "list_price": int(prices[value])}
                for value in component_ids
            ],
            "list_price_total": int(prices[main_id] + sum(prices[value] for value in component_ids)),
            "observed": {
                "period_days": evidence["observed_days"],
                "main_units_sold": main_units,
                "main_orders": main_orders,
                "copurchase_orders": with_components,
                "copurchase_rate": with_components / main_orders,
            },
            "unidentifiable": [
                {"field": field, "label": label, "note": UNIDENTIFIABLE_NOTE}
                for field, label in UNIDENTIFIABLE_BUNDLE_FIELDS
            ],
            "planning_scenarios": [
                {**scenario, "source": "DEFAULT", "is_observed": False}
                for scenario in BUNDLE_PLANNING_SCENARIOS
            ],
        }
