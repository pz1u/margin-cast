"""저장된 POS 파일의 정합성, 경제적 관계, seed 재현성을 검증한다."""

import argparse
from collections import Counter, defaultdict
import csv
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import tempfile


TABLE_NAMES = ("menus", "orders", "order_items", "menu_cost_history", "promotions",
               "bundles", "experiments", "daily_context")
MONEY_FIELDS = ("gross_sales", "discount_amount", "net_sales", "ingredient_cost",
                "platform_fee", "payment_fee", "additional_promotion_cost", "contribution_profit")
INTEGER_FIELDS = set(MONEY_FIELDS) | {"day_index", "weekday", "hour", "open_hour", "close_hour",
                                     "initial_list_price", "quantity", "list_price", "unit_cost",
                                     "discount_per_unit", "bundle_price", "value"}


def require(condition, message):
    # assert는 python -O에서 사라지므로 데이터 검증에는 명시적인 예외를 쓴다.
    if not condition:
        raise ValueError(message)


def load_dataset(data_dir):
    data_dir = Path(data_dir)
    tables = {}
    for name in TABLE_NAMES:
        with (data_dir / f"{name}.csv").open(encoding="utf-8-sig", newline="") as handle:
            tables[name] = [{key: int(value) if key in INTEGER_FIELDS else value
                             for key, value in row.items()} for row in csv.DictReader(handle)]
    truth = json.loads((data_dir / "ground_truth.json").read_text(encoding="utf-8"))
    return tables, truth


def validate_integrity(tables, truth):
    for name in TABLE_NAMES:
        require(bool(tables.get(name)), f"필수 테이블 누락 또는 비어 있음: {name}")
    hidden = {"price_elasticity", "base_demand", "promotion_lift", "origin", "original_menu_ids",
              "no_intervention_count", "expected_actual", "_bundle_origin", "_original_menu_ids"}
    for name, rows in tables.items():
        require(all(not hidden.intersection(row) for row in rows), f"관측 데이터에 숨겨진 정답 포함: {name}")
    menus = {row["menu_id"]: row for row in tables["menus"]}
    orders = {row["order_id"]: row for row in tables["orders"]}
    calendar = {row["date"]: row for row in tables["daily_context"]}
    promotions = {row["promotion_id"]: row for row in tables["promotions"]}
    bundles = {row["bundle_id"]: row for row in tables["bundles"]}
    require(len(menus) == len(tables["menus"]) == 8, "메뉴 개수 또는 키 오류")
    require(len(orders) == len(tables["orders"]), "주문 키 중복")
    require(len(promotions) == len(tables["promotions"]), "프로모션 키 중복")
    require(len(bundles) == len(tables["bundles"]), "세트 키 중복")
    require(len({row["experiment_id"] for row in tables["experiments"]})
            == len(tables["experiments"]), "실험 키 중복")
    require(len(calendar) == len(tables["daily_context"]) == truth["days"] == 90, "90일 달력 오류")
    for index in range(90):
        current = date.fromisoformat(truth["start_date"]) + timedelta(days=index)
        context = calendar.get(current.isoformat())
        require(context is not None and context["day_index"] == index + 1, "달력 날짜 누락")
        require(context["weekday"] == current.weekday(), "요일 오류")
        require(context["weather"] in {"RAIN", "CLEAR"}, "날씨 값 오류")
    for name in ("menu_cost_history", "promotions", "experiments"):
        require(all(row["menu_id"] in menus for row in tables[name]), f"메뉴 참조 오류: {name}")
    for row in bundles.values():
        require(row["main_menu_id"] in menus and row["drink_menu_id"] in menus, "세트 메뉴 참조 오류")
    for name in ("promotions", "experiments", "bundles"):
        for row in tables[name]:
            require(row["start_date"] in calendar and row["end_date"] in calendar
                    and row["start_date"] <= row["end_date"], f"이벤트 날짜 오류: {name}")
    costs = defaultdict(list)
    for row in tables["menu_cost_history"]:
        require(row["effective_date"] in calendar and row["unit_cost"] >= 0, "원가 이력 오류")
        costs[row["menu_id"]].append(row)
    require(len({(r["menu_id"], r["effective_date"]) for r in tables["menu_cost_history"]})
            == len(tables["menu_cost_history"]), "원가 이력 키 중복")
    for order in orders.values():
        timestamp = datetime.fromisoformat(order["ordered_at"])
        context = calendar.get(order["date"])
        require(context is not None, "운영 기간 밖 주문")
        require(timestamp.date().isoformat() == order["date"] and timestamp.hour == order["hour"],
                "주문 시각과 날짜/시간 불일치")
        require(order["day_index"] == context["day_index"] and order["weather"] == context["weather"],
                "주문과 일별 맥락 불일치")
        require(context["open_hour"] <= order["hour"] < context["close_hour"], "영업시간 밖 주문")
        require(order["channel"] in {"STORE", "DELIVERY"}, "주문 채널 오류")
    timestamps = [row["ordered_at"] for row in tables["orders"]]
    require(timestamps == sorted(timestamps), "주문 시간 정렬 오류")
    items_by_order = defaultdict(list)
    bundle_items = defaultdict(list)
    seen = set()
    for item in tables["order_items"]:
        require(item["order_item_id"] not in seen, "주문상품 키 중복")
        seen.add(item["order_item_id"])
        require(item["order_id"] in orders and item["menu_id"] in menus, "주문상품 참조 오류")
        order, menu = orders[item["order_id"]], menus[item["menu_id"]]
        quantity = item["quantity"]
        require(isinstance(quantity, int) and quantity > 0, "수량은 양의 정수여야 함")
        require(all(isinstance(item[key], int) for key in MONEY_FIELDS), "금액은 정수 원 단위여야 함")
        require(all(item[key] >= 0 for key in MONEY_FIELDS if key != "contribution_profit"), "음수 금액")
        require(0 <= item["discount_amount"] <= item["gross_sales"], "할인액 범위 오류")
        expected_price = menu["initial_list_price"]
        for event in tables["experiments"]:
            if (event["kind"] == "PRICE" and event["menu_id"] == item["menu_id"]
                    and event["start_date"] <= order["date"] <= event["end_date"]):
                expected_price = event["value"]
        require(item["list_price"] == expected_price, "가격 실험/원복 불일치")
        history = [row for row in costs[item["menu_id"]] if row["effective_date"] <= order["date"]]
        require(bool(history), "해당 주문일의 원가 이력 누락")
        expected_cost = max(history, key=lambda row: row["effective_date"])["unit_cost"]
        require(item["unit_cost"] == expected_cost, "주문 시점 원가 불일치")
        require(item["ingredient_cost"] == expected_cost * quantity, "원재료비 불일치")
        require(item["gross_sales"] == item["list_price"] * quantity, "정가 매출 불일치")
        require(item["net_sales"] == item["gross_sales"] - item["discount_amount"], "실결제 금액 불일치")
        for field, rate_key in [("platform_fee", "platform_rates"), ("payment_fee", "payment_rates")]:
            require(item[field] == round(item["net_sales"] * truth[rate_key][order["channel"]]),
                    f"수수료 불일치: {field}")
        expected_profit = item["net_sales"] - sum(item[key] for key in (
            "ingredient_cost", "platform_fee", "payment_fee", "additional_promotion_cost"))
        require(item["contribution_profit"] == expected_profit, "기여이익 불일치 또는 할인 중복 차감")
        active_promos = [row for row in promotions.values() if row["menu_id"] == item["menu_id"]
                        and row["start_date"] <= order["date"] <= row["end_date"]]
        require(item["promotion_id"] == (active_promos[0]["promotion_id"] if active_promos else ""),
                "프로모션 기간/메뉴 불일치")
        if item["bundle_id"]:
            require(item["bundle_id"] in bundles, "세트 참조 오류")
            bundle = bundles[item["bundle_id"]]
            require(bundle["start_date"] <= order["date"] <= bundle["end_date"], "세트 기간 오류")
            bundle_items[item["order_id"]].append(item)
        else:
            expected_discount = sum(row["discount_per_unit"] for row in active_promos) * quantity
            require(item["discount_amount"] == expected_discount, "할인 금액 불일치")
        items_by_order[item["order_id"]].append(item)
    require(set(items_by_order) == set(orders), "상품 없는 주문")
    for order_id, items in items_by_order.items():
        require(len({row["menu_id"] for row in items}) == len(items), "주문 내 메뉴 중복")
        require(sum(row["quantity"] for row in items if menus[row["menu_id"]]["category"] == "MAIN") == 1,
                "주문당 주메뉴 1개 규칙 위반")
        for field in MONEY_FIELDS:
            require(orders[order_id][field] == sum(row[field] for row in items), f"주문 합계 불일치: {field}")
    for items in bundle_items.values():
        bundle = bundles[items[0]["bundle_id"]]
        require(len(items) == 2 and {row["menu_id"] for row in items}
                == {bundle["main_menu_id"], bundle["drink_menu_id"]}, "세트 구성품 오류")
        require(all(row["quantity"] == 1 and row["bundle_id"] == bundle["bundle_id"] for row in items),
                "세트 구성품 수량/ID 오류")
        require(sum(row["net_sales"] for row in items) == bundle["bundle_price"], "세트 금액 불일치")
    return orders, items_by_order, bundle_items


def validate_generated_data(tables, truth):
    orders, baskets, bundle_items = validate_integrity(tables, truth)
    checks, warnings = {}, []

    def check(name, passed):
        checks[name] = bool(passed)
        require(passed, f"관계 검증 실패: {name}")

    check("order_count_10000_to_20000", 10000 <= len(orders) <= 20000)
    check("more_items_than_orders", len(tables["order_items"]) > len(orders))
    hourly = Counter(row["hour"] for row in orders.values())
    hourly_means = {name: sum(hourly[hour] for hour in hours) / (90 * len(hours))
                    for name, hours in {"lunch": (12, 13), "dinner": (18, 19, 20), "offpeak": (14, 15, 16)}.items()}
    check("lunch_peak", hourly_means["lunch"] > hourly_means["offpeak"])
    check("dinner_peak", hourly_means["dinner"] > hourly_means["offpeak"])
    weather_shares = {}
    for weather in ("CLEAR", "RAIN"):
        subset = [row for row in orders.values() if row["weather"] == weather]
        require(bool(subset), f"날씨별 표본 없음: {weather}")
        weather_shares[weather] = sum(row["channel"] == "DELIVERY" for row in subset) / len(subset)
    check("rain_delivery_share", weather_shares["RAIN"] > weather_shares["CLEAR"])
    weekday_means = {}
    for name, weekend in (("weekday", False), ("weekend", True)):
        dates = {row["date"] for row in tables["daily_context"] if (row["weekday"] >= 5) == weekend}
        weekday_means[name] = sum(row["date"] in dates for row in orders.values()) / len(dates)
    check("weekend_demand", weekday_means["weekend"] > weekday_means["weekday"])

    def period(first, last):
        selected = [row for row in orders.values() if first <= row["day_index"] <= last]
        ids = {row["order_id"] for row in selected}
        chicken = sum(item["quantity"] for oid in ids for item in baskets[oid] if item["menu_id"] == "M01")
        sales = sum(row["net_sales"] for row in selected)
        profit = sum(row["contribution_profit"] for row in selected)
        return {"days": [first, last], "orders": len(selected), "chicken_units": chicken,
                "chicken_per_day": chicken / (last - first + 1), "net_sales": sales,
                "contribution_profit": profit, "margin": profit / sales}

    observed = {}
    for label, before_days, after_days in [("discount", (14, 20), (21, 27)),
                                          ("price_9500", (29, 35), (36, 42)),
                                          ("price_10000", (46, 52), (53, 59))]:
        before, after = period(*before_days), period(*after_days)
        change = after["chicken_per_day"] / before["chicken_per_day"] - 1
        observed[label] = {"before": before, "after": after, "quantity_change": change}
        if (change > 0) != (label == "discount") or change == 0:
            warnings.append(f"{label}: 기간별 관측 판매량의 방향이 가정과 다름; 날씨/표본 잡음 확인 필요")
    cost_before, cost_after = period(60, 66), period(68, 74)
    check("cost_shock_margin", cost_after["margin"] < cost_before["margin"])
    if not (cost_after["net_sales"] > cost_before["net_sales"]
            and cost_after["contribution_profit"] < cost_before["contribution_profit"]):
        warnings.append("원가 상승 전후 관측 기간에서 매출 증가/기여이익 감소 데모 방향 불충족")

    # 세트 판매로 연관성이 인위적으로 강화되기 전의 장바구니만 평가한다.
    association_ids = [oid for oid, row in orders.items() if row["day_index"] < 75]
    chicken_ids = {oid for oid in association_ids if any(r["menu_id"] == "M01" for r in baskets[oid])}
    cola_ids = {oid for oid in association_ids if any(r["menu_id"] == "M06" for r in baskets[oid])}
    confidence = len(chicken_ids & cola_ids) / len(chicken_ids)
    cola_rate = len(cola_ids) / len(association_ids)
    lift = confidence / cola_rate
    check("chicken_cola_lift", lift > 1.5)
    other_confidence = len(cola_ids - chicken_ids) / (len(association_ids) - len(chicken_ids))
    check("chicken_cola_above_other_mains", confidence > other_confidence)

    paired = {}
    audit = truth["demand_audit"]
    expected_audit_keys = {(day, menu["menu_id"]) for day in range(1, 91)
                           for menu in tables["menus"] if menu["category"] == "MAIN"}
    require(len(audit) == 360 and {(r["day_index"], r["menu_id"]) for r in audit} == expected_audit_keys,
            "일별 주메뉴 정답 누락/중복")
    daily_quantities = Counter()
    for oid, items in baskets.items():
        for item in items:
            daily_quantities[orders[oid]["day_index"], item["menu_id"]] += item["quantity"]
    for row in audit:
        # 실험 전환 전 수량과 POS를 대조한다. 세트 기간은 아래의 주문/메뉴 보존식으로 검증한다.
        if not 75 <= row["day_index"] <= 81:
            actual = daily_quantities[row["day_index"], row["menu_id"]]
            require(actual == row["actual_count"], "생성 정답과 POS 주메뉴 수량 불일치")
    for label, first, last in [("discount", 21, 27), ("price_9500", 36, 42), ("price_10000", 53, 59)]:
        subset = [row for row in audit if row["menu_id"] == "M01" and first <= row["day_index"] <= last]
        actual = sum(row["actual_count"] for row in subset)
        control = sum(row["no_intervention_count"] for row in subset)
        price_only = sum(row["price_only_count"] for row in subset)
        check(f"paired_{label}", actual > control if label == "discount" else actual < control)
        if label == "discount":
            check("promotion_separate_from_price", actual > price_only > control)
        paired[label] = {"actual": actual, "no_intervention": control, "price_only": price_only,
                         "quantity_change": actual / control - 1}

    bundle_audit = truth["bundle_audit"]
    origins = bundle_audit["origins"]
    origin_counts = Counter(row["origin"] for row in origins)
    require(len({row["order_id"] for row in origins}) == len(origins), "세트 정답 키 중복")
    require({row["order_id"] for row in origins} == set(bundle_items), "세트 정답과 관측 주문 불일치")
    bundle_period = period(75, 81)
    check("bundle_incremental_order_conservation", bundle_period["orders"]
          == bundle_audit["no_bundle_order_count"] + origin_counts["incremental"])
    actual_quantities = Counter()
    for oid, items in baskets.items():
        if 75 <= orders[oid]["day_index"] <= 81:
            actual_quantities.update({row["menu_id"]: row["quantity"] for row in items})
    reconstructed = actual_quantities.copy()
    for origin in origins:
        require(origin["origin"] in {"incremental", "chicken_only", "copurchase", "other_main"},
                "알 수 없는 세트 전환 유형")
        reconstructed.subtract({row["menu_id"]: row["quantity"] for row in baskets[origin["order_id"]]})
        reconstructed.update(origin["original_menu_ids"])
    check("bundle_menu_conservation", +reconstructed == Counter(bundle_audit["no_bundle_menu_quantities"])
          and all(count >= 0 for count in reconstructed.values()))
    if truth["bundles_enabled"]:
        check("bundle_cannibalization", sum(origin_counts[key] for key in ("chicken_only", "copurchase", "other_main")) > 0)
        check("bundle_incremental_demand", origin_counts["incremental"] > 0)
    return {"passed": True, "checks": checks, "warnings": warnings,
            "rows": {name: len(rows) for name, rows in tables.items()},
            "hourly_orders_per_day": hourly_means, "delivery_share": weather_shares,
            "orders_per_day": weekday_means, "observed_periods": observed,
            "cost_shock": {"before": cost_before, "after": cost_after},
            "association_before_bundle": {"support": len(chicken_ids & cola_ids) / len(association_ids),
                                          "confidence": confidence, "cola_rate": cola_rate, "lift": lift,
                                          "other_main_cola_rate": other_confidence},
            "paired_counterfactuals_evaluation_only": paired,
            "bundle": {"orders": len(origins), "origins": dict(origin_counts),
                       "incremental_order_share": origin_counts["incremental"] / len(origins) if origins else 0,
                       "net_sales_change": bundle_period["net_sales"] - bundle_audit["no_bundle_net_sales"],
                       "contribution_profit_change": bundle_period["contribution_profit"]
                       - bundle_audit["no_bundle_contribution_profit"]}}


def verify_reproducibility(data_dir, truth):
    try:
        from .generate_data import generate_dataset, save_dataset
    except ImportError:
        from generate_data import generate_dataset, save_dataset
    tables, regenerated_truth = generate_dataset(truth["seed"], truth["start_date"], truth["bundles_enabled"])
    hashes = {}
    with tempfile.TemporaryDirectory(prefix="margincast-repro-") as temp_dir:
        save_dataset(tables, regenerated_truth, temp_dir)
        for filename in [f"{name}.csv" for name in TABLE_NAMES] + ["ground_truth.json"]:
            original = (Path(data_dir) / filename).read_bytes()
            regenerated = (Path(temp_dir) / filename).read_bytes()
            require(original == regenerated, f"seed 재현성 실패: {filename}")
            hashes[filename] = hashlib.sha256(original).hexdigest()
    return {"identical": True, "files_compared": len(hashes), "sha256": hashes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data")
    parser.add_argument("--check-reproducibility", action="store_true")
    args = parser.parse_args()
    tables, truth = load_dataset(args.data_dir)
    report = validate_generated_data(tables, truth)
    if args.check_reproducibility:
        report["reproducibility"] = verify_reproducibility(args.data_dir, truth)
    text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    (args.data_dir / "validation_report.json").write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
