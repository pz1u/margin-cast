"""관측 장바구니 기회와 명시적 전환 가정으로 세트 전략을 시뮬레이션한다."""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from .evidence_quality import (
        EVIDENCE_QUALITY_VALIDATION_STATUS,
        EVIDENCE_QUALITY_VERSION,
    )
except ImportError:
    from evidence_quality import EVIDENCE_QUALITY_VALIDATION_STATUS, EVIDENCE_QUALITY_VERSION

try:
    from .generate_data import PAYMENT_RATE, PLATFORM_RATE
    from .prepare_analysis_data import load_observed_tables
    from .simulate_strategy import summarize_distribution
except ImportError:
    from generate_data import PAYMENT_RATE, PLATFORM_RATE
    from prepare_analysis_data import load_observed_tables
    from simulate_strategy import summarize_distribution


DEFAULT_BUNDLE_SCENARIO = {
    "name": "치킨마요 콜라 세트",
    "bundle_price": 10_000,
    "take_rate": 0.30,
    "copurchase_take_rate": 0.55,
    "incremental_demand_rate": 0.10,
    "cannibalization_rate": 0.02,
}


def validate_bundle_scenario(scenario):
    required = {
        "name",
        "bundle_price",
        "take_rate",
        "copurchase_take_rate",
        "incremental_demand_rate",
        "cannibalization_rate",
    }
    missing = sorted(required - set(scenario))
    if missing:
        raise ValueError(f"세트 시나리오 필수 값이 없습니다: {missing}")
    if not isinstance(scenario["name"], str) or not scenario["name"].strip():
        raise ValueError("세트 시나리오 이름은 비어 있지 않은 문자열이어야 합니다.")
    if not isinstance(scenario["bundle_price"], int) or scenario["bundle_price"] <= 0:
        raise ValueError("세트 가격은 양의 원 단위 정수여야 합니다.")
    for name in required - {"name", "bundle_price"}:
        value = scenario[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ValueError(f"{name}은 0~1 범위여야 합니다.")


def build_bundle_evidence(tables, main_menu_id="M01", drink_menu_id="M06"):
    bundle_start = tables["bundles"]["start_date"].min()
    orders = tables["orders"][["order_id", "date", "channel"]]
    items = tables["order_items"].merge(orders, on="order_id", how="left", validate="many_to_one")
    history = items[items["date"] < bundle_start]
    operating_days = int((bundle_start - tables["daily_context"]["date"].min()).days)

    baskets = history.groupby("order_id").agg(
        menu_ids=("menu_id", set),
        channel=("channel", "first"),
    )
    chicken = baskets["menu_ids"].map(lambda values: main_menu_id in values)
    drink = baskets["menu_ids"].map(lambda values: drink_menu_id in values)
    main_ids = set(tables["menus"].loc[tables["menus"]["category"] == "MAIN", "menu_id"])
    other_main = baskets["menu_ids"].map(
        lambda values: main_menu_id not in values and bool(main_ids.intersection(values))
    )

    def segment(values):
        return {
            "orders": int(len(values)),
            "daily_orders": float(len(values) / operating_days),
            "delivery_share": float(values["channel"].eq("DELIVERY").mean()),
        }

    current_cost = (
        tables["menu_cost_history"]
        .sort_values("effective_date")
        .groupby("menu_id", as_index=True)["unit_cost"]
        .last()
        .astype(float)
        .to_dict()
    )
    prices = tables["menus"].set_index("menu_id")["initial_list_price"].astype(float).to_dict()
    other_items = history[
        history["order_id"].isin(baskets.index[other_main]) & history["menu_id"].isin(main_ids)
    ]
    other_items = other_items[other_items["menu_id"] != main_menu_id]
    other_weights = other_items.groupby("menu_id")["quantity"].sum()
    weighted_price = float(
        sum(prices[menu_id] * count for menu_id, count in other_weights.items())
        / other_weights.sum()
    )
    weighted_cost = float(
        sum(current_cost[menu_id] * count for menu_id, count in other_weights.items())
        / other_weights.sum()
    )
    return {
        "observed_days": operating_days,
        "main_without_drink": segment(baskets[chicken & ~drink]),
        "main_with_drink": segment(baskets[chicken & drink]),
        "other_main": segment(baskets[other_main]),
        "main_daily_orders": float(chicken.sum() / operating_days),
        "prices": {"main": prices[main_menu_id], "drink": prices[drink_menu_id]},
        "costs": {"main": current_cost[main_menu_id], "drink": current_cost[drink_menu_id]},
        "other_main_average": {"price": weighted_price, "cost": weighted_cost},
        "bundle_observed": True,
        "ground_truth_used": False,
    }


def _average_fee_rate(delivery_share):
    store_rate = PLATFORM_RATE["STORE"] + PAYMENT_RATE["STORE"]
    delivery_rate = PLATFORM_RATE["DELIVERY"] + PAYMENT_RATE["DELIVERY"]
    return (1 - delivery_share) * store_rate + delivery_share * delivery_rate


def simulate_bundle(
    evidence,
    scenario,
    *,
    horizon_days=14,
    simulations=10_000,
    seed=42,
    demand_log_sigma=0.08,
    cost_relative_std=0.05,
    baseline_daily_profit=None,
):
    validate_bundle_scenario(scenario)
    if horizon_days < 1 or simulations < 100:
        raise ValueError("horizon_days는 1 이상, simulations는 100 이상이어야 합니다.")
    rng = np.random.default_rng(seed)
    demand_factor = rng.lognormal(
        -(demand_log_sigma**2) / 2, demand_log_sigma, simulations
    )
    cost_factor = rng.lognormal(
        -(cost_relative_std**2) / 2, cost_relative_std, simulations
    )

    def conversions(segment_name, rate):
        mean = evidence[segment_name]["daily_orders"] * horizon_days * demand_factor
        opportunities = rng.poisson(mean)
        return rng.binomial(opportunities, rate)

    chicken_only = conversions("main_without_drink", scenario["take_rate"])
    copurchase = conversions("main_with_drink", scenario["copurchase_take_rate"])
    cannibalized = conversions("other_main", scenario["cannibalization_rate"])
    incremental = rng.poisson(
        evidence["main_daily_orders"]
        * horizon_days
        * scenario["incremental_demand_rate"]
        * demand_factor
    )

    bundle_price = float(scenario["bundle_price"])
    main_price = evidence["prices"]["main"]
    drink_price = evidence["prices"]["drink"]
    main_cost = evidence["costs"]["main"]
    drink_cost = evidence["costs"]["drink"]

    fee_chicken = _average_fee_rate(evidence["main_without_drink"]["delivery_share"])
    fee_copurchase = _average_fee_rate(evidence["main_with_drink"]["delivery_share"])
    fee_other = _average_fee_rate(evidence["other_main"]["delivery_share"])
    delta_chicken = (
        bundle_price * (1 - fee_chicken)
        - (main_cost + drink_cost) * cost_factor
        - (main_price * (1 - fee_chicken) - main_cost * cost_factor)
    )
    delta_copurchase = (bundle_price - main_price - drink_price) * (1 - fee_copurchase)
    other = evidence["other_main_average"]
    delta_other = (
        bundle_price * (1 - fee_other)
        - (main_cost + drink_cost) * cost_factor
        - (other["price"] * (1 - fee_other) - other["cost"] * cost_factor)
    )
    incremental_profit = (
        bundle_price * (1 - fee_chicken) - (main_cost + drink_cost) * cost_factor
    )
    profit_delta = (
        chicken_only * delta_chicken
        + copurchase * delta_copurchase
        + cannibalized * delta_other
        + incremental * incremental_profit
    )
    result = {
        "name": scenario["name"],
        "bundle_price": int(bundle_price),
        "horizon_days": horizon_days,
        "simulations": simulations,
        "seed": seed,
        "assumptions": {
            key: scenario[key]
            for key in (
                "take_rate",
                "copurchase_take_rate",
                "incremental_demand_rate",
                "cannibalization_rate",
            )
        },
        "orders": {
            "converted_main_without_drink": summarize_distribution(chicken_only),
            "converted_existing_copurchase": summarize_distribution(copurchase),
            "cannibalized_other_main": summarize_distribution(cannibalized),
            "incremental": summarize_distribution(incremental),
        },
        "profit_delta": summarize_distribution(profit_delta),
        "success_probability": float(np.mean(profit_delta > 0)),
        "evidence_quality": {
            "version": EVIDENCE_QUALITY_VERSION,
            "score": 35.0,
            "label": "LOW",
            "is_probability": False,
            "validation": {
                "status": EVIDENCE_QUALITY_VALIDATION_STATUS,
                "empirically_calibrated": False,
                "statement": (
                    "실제 매장 피드백이 없어 점수와 예측 정확도의 관계는 아직 검증되지 않았다."
                ),
            },
            "reason": "신규 수요와 기존 주문 전환·잠식 비율이 관측치가 아닌 시나리오 가정이다.",
        },
        "ground_truth_used": False,
    }
    if baseline_daily_profit is not None:
        baseline = rng.choice(np.asarray(baseline_daily_profit, dtype=float), (simulations, horizon_days)).sum(axis=1)
        result["baseline_contribution_profit"] = summarize_distribution(baseline)
        result["scenario_contribution_profit"] = summarize_distribution(baseline + profit_delta)
    return result


def run_bundle_simulation(
    data_dir,
    panel_path,
    output_dir,
    scenario=None,
    horizon_days=14,
    simulations=10_000,
    seed=42,
):
    tables = load_observed_tables(data_dir)
    evidence = build_bundle_evidence(tables)
    panel = pd.read_csv(panel_path, encoding="utf-8-sig")
    clean_days = panel[(panel["day_index"] >= 141) & (panel["day_index"] < 155)]
    daily_profit = clean_days.groupby("date")["contribution_profit"].sum().to_numpy()
    scenario = dict(DEFAULT_BUNDLE_SCENARIO if scenario is None else scenario)
    result = simulate_bundle(
        evidence,
        scenario,
        horizon_days=horizon_days,
        simulations=simulations,
        seed=seed,
        baseline_daily_profit=daily_profit,
    )
    report = {
        "evidence": evidence,
        "scenario": result,
        "limitations": [
            "Take Rate·신규 수요·잠식률은 POS만으로 분리되지 않아 명시적 시나리오 가정으로 받는다.",
            "한 번의 14일 세트 실험만 관측되어 가격별 세트 반응곡선을 추정하지 않았다.",
            "고객별 반복 구매와 다른 사이드 메뉴의 교차효과는 포함하지 않았다.",
        ],
    }
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "bundle-simulation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "README.md").write_text(render_report(report), encoding="utf-8")
    return report


def render_report(report):
    result = report["scenario"]
    assumptions = result["assumptions"]
    limits = "\n".join(f"- {value}" for value in report["limitations"])
    return f"""# 세트 전략 시뮬레이션

- 전략: {result['name']}, {result['bundle_price']:,}원
- 기간·반복: {result['horizon_days']}일, {result['simulations']:,}회
- 기대 기여이익 변화: **{result['profit_delta']['mean']:+,.0f}원**
- 80% 예상 범위: **[{result['profit_delta']['p10']:+,.0f}, {result['profit_delta']['p90']:+,.0f}]원**
- 개선확률: **{result['success_probability']:.1%}**
- 근거 품질: **{result['evidence_quality']['label']}** (`{result['evidence_quality']['version']}`)

## 시나리오 가정

- 콜라 미구매 치킨마요 고객 Take Rate: {assumptions['take_rate']:.1%}
- 기존 치킨마요+콜라 고객 전환율: {assumptions['copurchase_take_rate']:.1%}
- 기존 치킨마요 대비 신규 수요율: {assumptions['incremental_demand_rate']:.1%}
- 다른 주메뉴 잠식률: {assumptions['cannibalization_rate']:.1%}

이 비율은 관측된 확정값이 아니며 사용자가 바꿔 비교하는 시나리오 입력이다. 계산에는
`ground_truth.json`을 사용하지 않았다.

## 해석 한계

{limits}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--data-dir", type=Path, default=root / "data")
    parser.add_argument(
        "--panel", type=Path, default=root / "data" / "processed" / "demand_panel.csv"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=root / "reports" / "simulation" / "bundle"
    )
    parser.add_argument("--simulations", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    report = run_bundle_simulation(
        args.data_dir,
        args.panel,
        args.output_dir,
        simulations=args.simulations,
        seed=args.seed,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
