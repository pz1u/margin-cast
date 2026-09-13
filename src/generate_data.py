"""알려진 수요 규칙으로 90일 POS 데이터와 평가 전용 정답을 생성한다."""

import argparse
import csv
from datetime import date, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import random
import sys

DEFAULT_START = "2026-01-05"
DAYS = 90
HOUR_WEIGHTS = {11: 0.8, 12: 1.8, 13: 1.6, 14: 0.5, 15: 0.4,
                16: 0.5, 17: 0.8, 18: 1.5, 19: 1.7, 20: 1.4, 21: 0.5}
DAY_FACTORS = [0.94, 0.96, 0.98, 1.00, 1.08, 1.18, 1.12]
PLATFORM_RATE = {"STORE": 0.0, "DELIVERY": 0.15}
PAYMENT_RATE = {"STORE": 0.02, "DELIVERY": 0.03}


def create_menu_config():
    """base_demand는 주메뉴 주문/일, attach_probability는 주메뉴당 확률이다."""
    rows = [
        ("M01", "치킨마요", "MAIN", 9000, 3800, 44, -1.25, 1.18, 0),
        ("M02", "돈까스덮밥", "MAIN", 10000, 4200, 42, -1.05, 1.12, 0),
        ("M03", "제육덮밥", "MAIN", 9500, 3900, 38, -1.15, 1.12, 0),
        ("M04", "카레", "MAIN", 8000, 3000, 32, -1.40, 1.15, 0),
        ("M05", "감자튀김", "SIDE", 3500, 1200, 0, -1.50, 1.10, 0.13),
        ("M06", "콜라", "DRINK", 2000, 700, 0, -1.10, 1.08, 0.035),
        ("M07", "제로콜라", "DRINK", 2000, 750, 0, -1.10, 1.08, 0.07),
        ("M08", "미니우동", "SIDE", 3000, 1100, 0, -1.30, 1.10, 0.10),
    ]
    keys = ["menu_id", "menu_name", "category", "base_price", "base_cost",
            "base_demand", "price_elasticity", "promotion_lift", "attach_probability"]
    return [dict(zip(keys, row)) for row in rows]


def random_stream(seed, name):
    # Python hash()의 프로세스별 변동과 생성 단계 사이의 난수 의존을 피한다.
    digest = hashlib.sha256(f"{seed}:{name}".encode()).digest()
    return random.Random(int.from_bytes(digest, "big"))


def poisson_quantile(mean, uniform):
    """작은 셀 평균에 대한 역누적분포 표본. 외부 바이너리 의존성이 없다."""
    if not math.isfinite(mean) or not 0 <= mean <= 100 or not 0 <= uniform < 1:
        raise ValueError("Poisson 평균은 0~100, 균등 난수는 [0, 1)이어야 합니다.")
    if mean == 0 or uniform == 0:
        return 0
    probability = cumulative = math.exp(-mean)
    count = 0
    while uniform > cumulative:
        count += 1
        probability *= mean / count
        updated = cumulative + probability
        if updated == cumulative:
            raise ArithmeticError("Poisson 누적 확률의 부동소수점 정밀도를 초과했습니다.")
        cumulative = updated
    return count


def calculate_price_factor(current_price, base_price, elasticity):
    if not all(math.isfinite(value) for value in (current_price, base_price, elasticity)):
        raise ValueError("가격과 탄력성은 유한한 값이어야 합니다.")
    if current_price <= 0 or base_price <= 0:
        raise ValueError("가격은 양수여야 합니다.")
    return (current_price / base_price) ** elasticity


def get_day_factor(day_date):
    return DAY_FACTORS[day_date.weekday()]


def get_hour_factor(hour):
    return HOUR_WEIGHTS[hour] / sum(HOUR_WEIGHTS.values())


def menu_terms(menu, day):
    price, discount, promotion_id = menu["base_price"], 0, ""
    if menu["menu_id"] == "M01":
        if 36 <= day <= 42:
            price = 9500
        elif 53 <= day <= 59:
            price = 10000
        elif 21 <= day <= 27:
            discount, promotion_id = 1000, "P01"
    return price, discount, promotion_id


def unit_cost(menu, day):
    if menu["menu_id"] == "M01":
        return 4900 if day >= 67 else 4100 if day >= 45 else 3800
    # 공급가 충격은 식재료에 적용한다. 음료 매입가는 유지한다.
    if day >= 67 and menu["category"] != "DRINK":
        return round(menu["base_cost"] * 1.20)
    return menu["base_cost"]


def generate_weather(rng, start_date):
    calendar = []
    for index in range(DAYS):
        day_date = date.fromisoformat(start_date) + timedelta(days=index)
        calendar.append({"date": day_date.isoformat(), "day_index": index + 1,
                         "weekday": day_date.weekday(),
                         "weather": "RAIN" if rng.random() < 0.28 else "CLEAR",
                         "open_hour": 11, "close_hour": 22})
    return calendar


def generate_event_tables(menus, start_date):
    def event_date(day):
        return (date.fromisoformat(start_date) + timedelta(days=day - 1)).isoformat()

    costs = []
    for menu in menus:
        previous = None
        for day in range(1, DAYS + 1):
            cost = unit_cost(menu, day)
            if cost != previous:
                costs.append({"menu_id": menu["menu_id"], "effective_date": event_date(day),
                              "unit_cost": cost})
                previous = cost
    promotions = [{"promotion_id": "P01", "menu_id": "M01", "start_date": event_date(21),
                   "end_date": event_date(27), "discount_per_unit": 1000,
                   "funded_by": "MERCHANT", "additional_promotion_cost": 0}]
    experiments = []
    for identifier, kind, first, last, value in [
        ("E01", "DISCOUNT", 21, 27, 1000),
        ("E02", "PRICE", 36, 42, 9500),
        ("E03", "PRICE", 53, 59, 10000),
    ]:
        experiments.append({"experiment_id": identifier, "kind": kind, "menu_id": "M01",
                            "start_date": event_date(first), "end_date": event_date(last), "value": value})
    return {"menu_cost_history": costs, "promotions": promotions, "experiments": experiments}


def calculate_item_amounts(list_price, discount, cost, channel, quantity=1,
                           additional_promotion_cost=0):
    """원 단위 반올림을 항목별 적용. 실결제가에서 할인액을 다시 빼지 않는다."""
    if channel not in PLATFORM_RATE:
        raise ValueError("지원하지 않는 주문 채널입니다.")
    if (not isinstance(quantity, int) or quantity < 1 or list_price <= 0
            or not 0 <= discount <= list_price or cost < 0 or additional_promotion_cost < 0):
        raise ValueError("가격, 할인, 원가, 수량을 확인하세요.")
    revenue = (list_price - discount) * quantity
    platform = round(revenue * PLATFORM_RATE[channel])
    payment = round(revenue * PAYMENT_RATE[channel])
    return {"gross_sales": list_price * quantity, "discount_amount": discount * quantity,
            "net_sales": revenue, "ingredient_cost": cost * quantity,
            "platform_fee": platform, "payment_fee": payment,
            "additional_promotion_cost": additional_promotion_cost,
            "contribution_profit": revenue - cost * quantity - platform - payment
            - additional_promotion_cost}


def generate_demand(menus, calendar, rng):
    """같은 균등 난수의 Poisson 분위수로 개입 전후 잠재 수요를 짝짓는다."""
    cells, audit = [], []
    for context in calendar:
        day = context["day_index"]
        # 일별 공통 잡음은 평균 1인 lognormal이다. 셀별 Poisson 잡음과 구분한다.
        noise = rng.lognormvariate(-0.04**2 / 2, 0.04)
        growth = 1.06 if day >= 68 else 1.0
        rainy = context["weather"] == "RAIN"
        delivery_share = 0.72 if rainy else 0.43
        weather_factor = 0.98 if rainy else 1.0
        for menu in menus[:4]:
            price, discount, promotion_id = menu_terms(menu, day)
            price_factor = calculate_price_factor(price - discount, menu["base_price"],
                                                   menu["price_elasticity"])
            promo_factor = menu["promotion_lift"] if promotion_id else 1.0
            record = {"date": context["date"], "day_index": day, "menu_id": menu["menu_id"],
                      "expected_no_intervention": 0.0, "expected_actual": 0.0,
                      "no_intervention_count": 0, "price_only_count": 0, "actual_count": 0}
            for hour in HOUR_WEIGHTS:
                for channel, share in [("STORE", 1 - delivery_share), ("DELIVERY", delivery_share)]:
                    mean = (menu["base_demand"] * get_day_factor(date.fromisoformat(context["date"]))
                            * get_hour_factor(hour) * weather_factor * share * noise * growth)
                    uniform = rng.random()
                    count = poisson_quantile(mean * price_factor * promo_factor, uniform)
                    cells.append({**context, "hour": hour, "channel": channel,
                                  "menu_id": menu["menu_id"], "count": count})
                    record["expected_no_intervention"] += mean
                    record["expected_actual"] += mean * price_factor * promo_factor
                    record["no_intervention_count"] += poisson_quantile(mean, uniform)
                    record["price_only_count"] += poisson_quantile(mean * price_factor, uniform)
                    record["actual_count"] += count
            audit.append(record)
    return cells, audit


def generate_orders(menus, cells, rng):
    """주문당 주메뉴 1개에 조건부 사이드/음료 구매를 추가한다."""
    baskets = []
    for cell in cells:
        for _ in range(cell["count"]):
            main = cell["menu_id"]
            selected = [main]
            for menu in menus[4:]:
                probability = menu["attach_probability"]
                if menu["menu_id"] == "M06" and main == "M01":
                    probability = 0.28
                selected_price, discount, promo = menu_terms(menu, cell["day_index"])
                probability *= calculate_price_factor(selected_price - discount, menu["base_price"],
                                                       menu["price_elasticity"])
                probability *= menu["promotion_lift"] if promo else 1.0
                if rng.random() < min(probability, 1.0):
                    selected.append(menu["menu_id"])
            timestamp = (datetime.fromisoformat(cell["date"]) + timedelta(
                hours=cell["hour"], seconds=rng.randrange(3600))).isoformat()
            baskets.append({"ordered_at": timestamp, "date": cell["date"],
                            "day_index": cell["day_index"], "hour": cell["hour"],
                            "weather": cell["weather"], "channel": cell["channel"],
                            "menu_ids": selected, "bundle_id": ""})
    return baskets


def generate_order_items(menus, baskets):
    menu_lookup = {menu["menu_id"]: menu for menu in menus}
    orders, items = [], []
    # 시각 순서로 ID를 부여해 다시 실행해도 CSV 바이트가 같도록 한다.
    for number, basket in enumerate(sorted(baskets, key=lambda row: row["ordered_at"]), 1):
        order_id = f"O{number:06d}"
        order = {key: value for key, value in basket.items() if key not in {"menu_ids", "bundle_id"}}
        order["order_id"] = order_id
        amounts = []
        for menu_id in basket["menu_ids"]:
            menu = menu_lookup[menu_id]
            price, discount, promotion_id = menu_terms(menu, basket["day_index"])
            cost = unit_cost(menu, basket["day_index"])
            financials = calculate_item_amounts(price, discount, cost, basket["channel"])
            amounts.append(financials)
            items.append({"order_item_id": f"I{len(items) + 1:06d}", "order_id": order_id,
                          "menu_id": menu_id, "quantity": 1, "list_price": price,
                          "unit_cost": cost, "promotion_id": promotion_id,
                          "bundle_id": basket["bundle_id"], **financials})
        order.update({key: sum(amount[key] for amount in amounts) for key in amounts[0]})
        orders.append(order)
    return orders, items


def generate_dataset(seed=42, start_date=DEFAULT_START):
    start_date = date.fromisoformat(start_date).isoformat()
    menus = create_menu_config()
    calendar = generate_weather(random_stream(seed, "weather"), start_date)
    cells, demand_audit = generate_demand(menus, calendar, random_stream(seed, "demand"))
    baskets = generate_orders(menus, cells, random_stream(seed, "baskets"))
    orders, items = generate_order_items(menus, baskets)
    tables = generate_event_tables(menus, start_date)
    tables.update({"menus": [{"menu_id": menu["menu_id"], "menu_name": menu["menu_name"],
                              "category": menu["category"], "initial_list_price": menu["base_price"]}
                             for menu in menus],
                   "daily_context": calendar, "orders": orders, "order_items": items})
    truth = {"schema_version": 1, "seed": seed, "start_date": start_date, "days": DAYS,
             "menus": menus, "day_factors": DAY_FACTORS, "hour_weights": HOUR_WEIGHTS,
             "rain_probability": 0.28, "rain_total_demand_factor": 0.98,
             "delivery_share": {"CLEAR": 0.43, "RAIN": 0.72},
             "day_noise_log_sigma": 0.04, "late_growth_factor": 1.06,
             "late_growth_start_day": 68, "chicken_cola_attach_probability": 0.28,
             "platform_rates": PLATFORM_RATE, "payment_rates": PAYMENT_RATE,
             "demand_audit": demand_audit,
             "assumptions": ["주문당 주메뉴 1개; 사이드/음료는 조건부 독립 부가구매",
                             "수요는 일별 공통 lognormal 잡음에 조건부 Poisson",
                             "대조 수요는 동일 난수·날씨·요일에서 가격/프로모션만 제거한 가상 정답",
                             "원가 인상은 매입가 사건이며 수요를 직접 바꾸지 않음",
                             "기여이익은 고정비·세금 제외; 할인액 중복 차감 없음"],
             "runtime": {"python": sys.version.split()[0], "random": "MT19937"}}
    return tables, truth


def save_dataset(tables, truth, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        with (output_dir / f"{name}.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    (output_dir / "ground_truth.json").write_text(
        json.dumps(truth, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--start-date", default=DEFAULT_START)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data")
    args = parser.parse_args()
    tables, truth = generate_dataset(args.seed, args.start_date)
    save_dataset(tables, truth, args.output_dir)
    print(json.dumps({name: len(rows) for name, rows in tables.items()}, indent=2))


if __name__ == "__main__":
    main()
