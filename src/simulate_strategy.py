"""기준 수요와 탄력성 추정치를 연결해 가격·할인 전략을 Monte Carlo 비교한다."""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False

try:
    from .confidence_score import calculate_confidence
    from .estimate_elasticity import estimate_price_elasticity
    from .generate_data import PAYMENT_RATE, PLATFORM_RATE
    from .train_demand_model import CATEGORICAL_FEATURES, NUMERIC_FEATURES, add_model_features, build_model
except ImportError:
    from confidence_score import calculate_confidence
    from estimate_elasticity import estimate_price_elasticity
    from generate_data import PAYMENT_RATE, PLATFORM_RATE
    from train_demand_model import CATEGORICAL_FEATURES, NUMERIC_FEATURES, add_model_features, build_model


DEFAULT_SCENARIOS = [
    {"name": "현재 가격", "list_price": 9_000, "discount": 0},
    {"name": "1,000원 할인", "list_price": 9_000, "discount": 1_000},
    {"name": "500원 인상", "list_price": 9_500, "discount": 0},
    {"name": "1,000원 인상", "list_price": 10_000, "discount": 0},
]


def validate_scenarios(scenarios):
    names = set()
    for scenario in scenarios:
        required = {"name", "list_price", "discount"}
        if not required.issubset(scenario):
            raise ValueError(f"시나리오 필수 값이 없습니다: {required - set(scenario)}")
        if scenario["name"] in names:
            raise ValueError("시나리오 이름은 중복될 수 없습니다.")
        names.add(scenario["name"])
        if scenario["list_price"] <= 0 or not 0 <= scenario["discount"] < scenario["list_price"]:
            raise ValueError("정가는 양수이고 할인액은 정가보다 작아야 합니다.")


def _build_kma_reference(frame, panel, forecasts, menu_id, horizon_days):
    forecast = pd.DataFrame(forecasts).copy()
    required = {"date", "time", "is_rain"}
    missing = sorted(required - set(forecast.columns))
    if missing:
        raise ValueError(f"미래 날씨 문맥에 필수 값이 없습니다: {missing}")
    forecast["date"] = pd.to_datetime(forecast["date"])
    forecast["hour"] = forecast["time"].str.slice(0, 2).astype(int)
    panel_last_date = pd.to_datetime(panel["date"]).max()
    forecast = forecast[forecast["date"] > panel_last_date]
    available_dates = sorted(forecast["date"].unique())
    if len(available_dates) < horizon_days:
        raise ValueError(
            f"미래 예보는 {len(available_dates)}일뿐입니다. 요청한 {horizon_days}일을 채울 수 없습니다."
        )
    selected_dates = available_dates[:horizon_days]
    forecast = forecast[forecast["date"].isin(selected_dates)]

    latest = frame[(frame["menu_id"] == menu_id) & (frame["day_index"] == frame["day_index"].max())]
    template = latest.set_index(["hour", "channel"])
    records = []
    for forecast_date in selected_dates:
        day_forecast = forecast[forecast["date"] == forecast_date]
        day_gap = int((pd.Timestamp(forecast_date) - panel_last_date).days)
        for hour in range(11, 22):
            distances = (day_forecast["hour"] - hour).abs()
            nearest = day_forecast.loc[distances.idxmin()]
            for channel in ("STORE", "DELIVERY"):
                row = template.loc[(hour, channel)].copy()
                row["date"] = pd.Timestamp(forecast_date)
                row["day_index"] = int(frame["day_index"].max()) + day_gap
                row["weekday"] = int(pd.Timestamp(forecast_date).weekday())
                row["hour"] = hour
                row["channel"] = channel
                row["weather"] = "RAIN" if bool(nearest["is_rain"]) else "CLEAR"
                row["is_rain"] = int(bool(nearest["is_rain"]))
                row["is_weekend"] = int(row["weekday"] >= 5)
                row["is_lunch"] = int(hour in (12, 13))
                row["is_dinner"] = int(hour in (18, 19, 20))
                row["time_index"] = (row["day_index"] - 1) * 24 + hour
                row["forecast_at"] = nearest.get("forecast_at")
                row["tmp_c"] = nearest.get("tmp_c")
                row["pcp_raw"] = nearest.get("pcp_raw")
                row["pcp_mm_estimate"] = nearest.get("pcp_mm_estimate")
                row["pty_code"] = nearest.get("pty_code")
                row["reh_pct"] = nearest.get("reh_pct")
                records.append(row)
    return pd.DataFrame(records).reset_index(drop=True)


def build_reference_forecast(panel, menu_id="M01", horizon_days=14, forecasts=None):
    frame = add_model_features(panel)
    train = frame[frame["split"] == "train"]
    model = build_model().fit(
        train[CATEGORICAL_FEATURES + NUMERIC_FEATURES], train["units_sold"]
    )
    if forecasts is None:
        last_days = sorted(frame["day_index"].unique())[-horizon_days:]
        reference = frame[
            (frame["menu_id"] == menu_id) & frame["day_index"].isin(last_days)
        ].copy()
        context_source = "observed_history"
    else:
        reference = _build_kma_reference(
            frame, panel, forecasts, menu_id=menu_id, horizon_days=horizon_days
        )
        context_source = "kma_forecast"
    baseline_price = int(reference["initial_list_price"].iloc[0])
    reference["offered_list_price"] = baseline_price
    reference["regular_paid_unit_price"] = baseline_price
    reference["promotion_discount"] = 0
    reference["bundle_available"] = 0
    reference["bundle_price"] = 0
    reference = add_model_features(reference)
    reference["base_mean"] = np.clip(
        model.predict(reference[CATEGORICAL_FEATURES + NUMERIC_FEATURES]), 0, None
    )
    return reference, baseline_price, context_source


def summarize_distribution(values):
    return {
        "mean": float(np.mean(values)),
        "p05": float(np.quantile(values, 0.05)),
        "p10": float(np.quantile(values, 0.10)),
        "p50": float(np.quantile(values, 0.50)),
        "p90": float(np.quantile(values, 0.90)),
        "p95": float(np.quantile(values, 0.95)),
    }


def simulate_scenarios(
    reference,
    elasticity_report,
    scenarios,
    baseline_price,
    simulations=10_000,
    seed=42,
    demand_log_sigma=0.08,
    cost_relative_std=0.05,
):
    validate_scenarios(scenarios)
    if simulations < 100:
        raise ValueError("안정적인 확률 요약을 위해 simulations는 100 이상이어야 합니다.")
    rng = np.random.default_rng(seed)
    base_mean = reference["base_mean"].to_numpy(dtype=float)
    costs = reference["unit_cost"].to_numpy(dtype=float)
    channels = reference["channel"].to_numpy()
    fee_rates = np.array([PLATFORM_RATE[value] + PAYMENT_RATE[value] for value in channels])

    if demand_log_sigma < 0 or cost_relative_std < 0:
        raise ValueError("수요와 원가 불확실성은 0 이상이어야 합니다.")
    demand_factor = rng.lognormal(
        mean=-(demand_log_sigma**2) / 2,
        sigma=demand_log_sigma,
        size=simulations,
    )
    cost_factor = rng.lognormal(
        mean=-(cost_relative_std**2) / 2,
        sigma=cost_relative_std,
        size=simulations,
    )
    baseline_counts = rng.poisson(demand_factor[:, None] * base_mean[None, :])
    baseline_margin = (
        baseline_price
        - costs[None, :] * cost_factor[:, None]
        - baseline_price * fee_rates[None, :]
    )
    baseline_profit = np.sum(baseline_counts * baseline_margin, axis=1)
    baseline_units = baseline_counts.sum(axis=1)

    elasticity = float(elasticity_report["elasticity"])
    elasticity_error = float(elasticity_report["robust_standard_error"])
    promo_effect = float(elasticity_report["promotion_log_effect"])
    promo_error = float(elasticity_report["promotion_robust_standard_error"])
    sampled_elasticity = rng.normal(elasticity, elasticity_error, simulations)
    sampled_promo_effect = rng.normal(promo_effect, promo_error, simulations)
    results = []
    for index, scenario in enumerate(scenarios):
        paid_price = scenario["list_price"] - scenario["discount"]
        is_reference = paid_price == baseline_price and scenario["discount"] == 0
        if is_reference:
            units = baseline_units
            profit = baseline_profit
            multiplier_samples = np.ones(simulations)
        else:
            log_multiplier = sampled_elasticity * np.log(paid_price / baseline_price)
            if scenario["discount"] > 0:
                log_multiplier += sampled_promo_effect
            multiplier_samples = np.exp(np.clip(log_multiplier, -5, 5))
            scenario_mean = (
                multiplier_samples[:, None]
                * demand_factor[:, None]
                * base_mean[None, :]
            )
            counts = rng.poisson(scenario_mean)
            unit_margin = (
                paid_price
                - costs[None, :] * cost_factor[:, None]
                - paid_price * fee_rates[None, :]
            )
            units = counts.sum(axis=1)
            profit = np.sum(counts * unit_margin, axis=1)
        delta = profit - baseline_profit
        results.append(
            {
                "name": scenario["name"],
                "list_price": int(scenario["list_price"]),
                "discount": int(scenario["discount"]),
                "paid_price": int(paid_price),
                "is_reference": is_reference,
                "demand_multiplier": summarize_distribution(multiplier_samples),
                "units": summarize_distribution(units),
                "contribution_profit": summarize_distribution(profit),
                "profit_delta": summarize_distribution(delta),
                "success_probability": None if is_reference else float(np.mean(delta > 0)),
            }
        )
    return results


def run_simulation(
    panel_path,
    output_dir,
    scenarios=None,
    menu_id="M01",
    horizon_days=14,
    simulations=10_000,
    seed=42,
    weather_forecast_path=None,
    demand_log_sigma=0.08,
    cost_relative_std=0.05,
):
    panel = pd.read_csv(panel_path, encoding="utf-8-sig")
    elasticity_report = estimate_price_elasticity(panel, menu_id=menu_id)
    forecasts = None
    if weather_forecast_path is not None:
        forecasts = json.loads(Path(weather_forecast_path).read_text(encoding="utf-8"))
    reference, baseline_price, context_source = build_reference_forecast(
        panel, menu_id=menu_id, horizon_days=horizon_days, forecasts=forecasts
    )
    scenarios = DEFAULT_SCENARIOS if scenarios is None else scenarios
    results = simulate_scenarios(
        reference,
        elasticity_report,
        scenarios,
        baseline_price,
        simulations=simulations,
        seed=seed,
        demand_log_sigma=demand_log_sigma,
        cost_relative_std=cost_relative_std,
    )
    for scenario, result in zip(scenarios, results):
        if result["is_reference"]:
            result["confidence"] = None
        else:
            result["confidence"] = calculate_confidence(
                panel, elasticity_report, scenario, context_source
            )
    report = {
        "menu_id": menu_id,
        "horizon_days": horizon_days,
        "reference_context": (
            f"미래 {horizon_days}일의 기상청 예보·요일·시간·채널과 현재 원가"
            if context_source == "kma_forecast"
            else f"마지막 {horizon_days}일의 요일·시간·채널·관측 날씨와 현재 원가"
        ),
        "weather_context_source": context_source,
        "baseline_price": baseline_price,
        "simulations": simulations,
        "seed": seed,
        "uncertainty": {
            "demand_log_sigma": demand_log_sigma,
            "cost_relative_std": cost_relative_std,
            "elasticity_standard_error": elasticity_report["robust_standard_error"],
            "promotion_standard_error": elasticity_report[
                "promotion_robust_standard_error"
            ],
        },
        "elasticity": elasticity_report["elasticity"],
        "elasticity_standard_error": elasticity_report["robust_standard_error"],
        "ground_truth_used": False,
        "scenarios": results,
        "limitations": [
            (
                "기상청 예보를 영업시간별 가장 가까운 발표값으로 정렬했다."
                if context_source == "kma_forecast"
                else "최근 관측 날씨를 시나리오 문맥으로 재사용했으며 실제 미래에는 예보가 필요하다."
            ),
            "1,000원 할인은 관측 실험과 같은 프로모션 노출 효과가 재현된다고 가정한다.",
            "세트 구성은 신규 수요와 잠식 효과를 관측 데이터만으로 분리할 수 없어 이 비교에 포함하지 않았다.",
            "수요 공통 변동과 원가 변동 폭은 실제 예측 오차로 보정되기 전까지 명시적 가정값이다.",
        ],
    }
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "simulation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "README.md").write_text(render_report(report), encoding="utf-8")
    render_chart(report, output_dir / "strategy-comparison.png")
    return report


def render_chart(report, output_path):
    scenarios = report["scenarios"]
    names = [row["name"] for row in scenarios]
    means = np.array([row["contribution_profit"]["mean"] for row in scenarios]) / 1_000_000
    lows = np.array([row["contribution_profit"]["p05"] for row in scenarios]) / 1_000_000
    highs = np.array([row["contribution_profit"]["p95"] for row in scenarios]) / 1_000_000
    errors = np.vstack([means - lows, highs - means])
    figure, axis = plt.subplots(figsize=(10, 6), constrained_layout=True)
    bars = axis.bar(
        names,
        means,
        yerr=errors,
        capsize=5,
        color=["#577590", "#43AA8B", "#F4A261", "#E76F51"],
    )
    axis.set(
        title=f"{report['horizon_days']}일 전략별 기여이익 분포",
        ylabel="기여이익 (백만원)",
    )
    axis.grid(axis="y", alpha=0.2)
    for bar, value in zip(bars, means):
        axis.text(
            bar.get_x() + bar.get_width() / 2,
            value + 0.03,
            f"{value:.2f}",
            ha="center",
        )
    figure.savefig(output_path, dpi=150)
    plt.close(figure)


def render_report(report):
    rows = []
    for scenario in report["scenarios"]:
        success = "기준" if scenario["success_probability"] is None else f"{scenario['success_probability']:.1%}"
        rows.append(
            f"| {scenario['name']} | {scenario['paid_price']:,}원 | "
            f"{scenario['units']['mean']:.1f} | {scenario['contribution_profit']['mean']:,.0f}원 | "
            f"{scenario['profit_delta']['mean']:+,.0f}원 | {success} | "
            f"{scenario['confidence']['label'] if scenario['confidence'] else '기준'} |"
        )
    limitations = "\n".join(f"- {item}" for item in report["limitations"])
    return f"""# 가격·할인 Monte Carlo 시뮬레이션

- 메뉴: `{report['menu_id']}`
- 기간: {report['horizon_days']}일
- 반복: {report['simulations']:,}회, seed `{report['seed']}`
- 탄력성: {report['elasticity']:.3f} ± {report['elasticity_standard_error']:.3f} (표준오차)
- 생성기 Ground Truth 사용: `{str(report['ground_truth_used']).lower()}`

![전략별 기여이익 분포](strategy-comparison.png)

| 전략 | 실결제가 | 기대 판매량 | 기대 기여이익 | 현재 대비 | 성공확률 | 신뢰도 |
|---|---:|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

성공확률은 같은 14일 문맥에서 시나리오 기여이익이 현재 가격의 모의 결과보다 클 확률이다.
각 전략의 상세 5·10·50·90·95 백분위는 `simulation.json`에 저장된다. 10~90 백분위가
기본 80% 예상 범위다. 신뢰도는 근거 품질 점수이며 성공확률과 다른 값이다.

## 해석 한계

{limitations}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--panel", type=Path, default=root / "data" / "processed" / "demand_panel.csv")
    parser.add_argument("--output-dir", type=Path, default=root / "reports" / "simulation")
    parser.add_argument("--simulations", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--horizon-days", type=int, default=14)
    parser.add_argument("--weather-forecast", type=Path)
    parser.add_argument("--demand-log-sigma", type=float, default=0.08)
    parser.add_argument("--cost-relative-std", type=float, default=0.05)
    args = parser.parse_args()
    report = run_simulation(
        args.panel,
        args.output_dir,
        horizon_days=args.horizon_days,
        simulations=args.simulations,
        seed=args.seed,
        weather_forecast_path=args.weather_forecast,
        demand_log_sigma=args.demand_log_sigma,
        cost_relative_std=args.cost_relative_std,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
