"""관측된 가격 실험으로 메뉴 수요의 가격탄력성을 추정한다."""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def build_elasticity_design(panel, menu_id="M01", split="train"):
    frame = panel[(panel["menu_id"] == menu_id) & (panel["split"] == split)].copy()
    if frame.empty:
        raise ValueError(f"{menu_id}의 {split} 데이터가 없습니다.")
    if frame["offered_list_price"].nunique() < 2:
        raise ValueError("탄력성 추정에는 두 개 이상의 관측 정가가 필요합니다.")

    frame["log_price_ratio"] = np.log(
        frame["offered_list_price"] / frame["initial_list_price"]
    )
    frame["promotion_active"] = frame["promotion_discount"].gt(0).astype(float)
    frame["delivery_rain"] = (
        frame["channel"].eq("DELIVERY") & frame["weather"].eq("RAIN")
    ).astype(float)
    frame["trend"] = (frame["day_index"] - frame["day_index"].mean()) / frame["day_index"].std(ddof=0)

    columns = {
        "intercept": np.ones(len(frame)),
        "log_price_ratio": frame["log_price_ratio"].to_numpy(),
        "promotion_active": frame["promotion_active"].to_numpy(),
        "delivery": frame["channel"].eq("DELIVERY").to_numpy(dtype=float),
        "rain": frame["weather"].eq("RAIN").to_numpy(dtype=float),
        "delivery_rain": frame["delivery_rain"].to_numpy(),
        "trend": frame["trend"].to_numpy(),
    }
    for hour in sorted(frame["hour"].unique())[1:]:
        columns[f"hour_{hour}"] = frame["hour"].eq(hour).to_numpy(dtype=float)
    for weekday in sorted(frame["weekday"].unique())[1:]:
        columns[f"weekday_{weekday}"] = frame["weekday"].eq(weekday).to_numpy(dtype=float)
    names = list(columns)
    design = np.column_stack([columns[name] for name in names])
    return frame, design, names


def fit_poisson_irls(design, target, max_iter=100, tolerance=1e-9, ridge=1e-8):
    target = np.asarray(target, dtype=float)
    coefficients = np.zeros(design.shape[1])
    coefficients[0] = np.log(max(target.mean(), 1e-8))
    penalty = np.eye(design.shape[1]) * ridge
    penalty[0, 0] = 0
    for iteration in range(1, max_iter + 1):
        linear = np.clip(design @ coefficients, -12, 12)
        mean = np.exp(linear)
        adjusted = linear + (target - mean) / np.maximum(mean, 1e-8)
        root_weight = np.sqrt(mean)
        weighted = design * root_weight[:, None]
        updated = np.linalg.solve(
            weighted.T @ weighted + penalty,
            weighted.T @ (adjusted * root_weight),
        )
        if np.max(np.abs(updated - coefficients)) < tolerance:
            coefficients = updated
            break
        coefficients = updated

    fitted = np.exp(np.clip(design @ coefficients, -12, 12))
    bread = np.linalg.inv(design.T @ (design * fitted[:, None]) + penalty)
    score_rows = design * (target - fitted)[:, None]
    robust_covariance = bread @ (score_rows.T @ score_rows) @ bread
    standard_errors = np.sqrt(np.maximum(np.diag(robust_covariance), 0))
    return coefficients, standard_errors, fitted, iteration


def demand_multiplier(elasticity, current_price, scenario_price):
    if current_price <= 0 or scenario_price <= 0:
        raise ValueError("가격은 양수여야 합니다.")
    return float((scenario_price / current_price) ** elasticity)


def estimate_price_elasticity(panel, menu_id="M01"):
    frame, design, names = build_elasticity_design(panel, menu_id=menu_id)
    coefficients, errors, fitted, iterations = fit_poisson_irls(design, frame["units_sold"])
    parameters = dict(zip(names, coefficients))
    parameter_errors = dict(zip(names, errors))
    elasticity = float(parameters["log_price_ratio"])
    standard_error = float(parameter_errors["log_price_ratio"])
    price_levels = sorted(int(value) for value in frame["offered_list_price"].unique())
    baseline_price = int(frame["initial_list_price"].iloc[0])
    report = {
        "menu_id": menu_id,
        "split": "train",
        "rows": int(len(frame)),
        "days": int(frame["day_index"].nunique()),
        "price_levels": price_levels,
        "elasticity": elasticity,
        "robust_standard_error": standard_error,
        "confidence_interval_95": [
            float(elasticity - 1.96 * standard_error),
            float(elasticity + 1.96 * standard_error),
        ],
        "promotion_log_effect": float(parameters["promotion_active"]),
        "iterations": iterations,
        "mean_actual": float(frame["units_sold"].mean()),
        "mean_fitted": float(fitted.mean()),
        "scenario_multipliers": {
            str(price): demand_multiplier(elasticity, baseline_price, price)
            for price in (8_000, 8_500, 9_000, 9_500, 10_000)
        },
        "ground_truth_used": False,
        "method": "Poisson IRLS with HC0 robust standard errors",
    }
    return report


def render_report(report):
    lower, upper = report["confidence_interval_95"]
    rows = "\n".join(
        f"| {int(price):,}원 | {multiplier:.3f} | {multiplier - 1:+.1%} |"
        for price, multiplier in report["scenario_multipliers"].items()
    )
    return f"""# 가격탄력성 추정

- 메뉴: `{report['menu_id']}`
- 사용 구간: Train {report['days']}일, {report['rows']:,}개 셀
- 관측 정가: {', '.join(f'{price:,}원' for price in report['price_levels'])}
- 추정 탄력성: **{report['elasticity']:.3f}**
- HC0 95% 신뢰구간: **[{lower:.3f}, {upper:.3f}]**
- 생성기 Ground Truth 사용: `{str(report['ground_truth_used']).lower()}`

가격탄력성은 가격이 1% 변할 때 기대 수요가 몇 % 변하는지를 나타낸다. 음수이면 가격 인상 시
수요가 감소한다. 할인 기간은 별도 변수로 분리했고 채널·날씨·시간·요일·추세를 통제했다.

## 9,000원 대비 수요 배수

| 시나리오 가격 | 수요 배수 | 예상 변화 |
|---|---:|---:|
{rows}

신뢰구간은 셀별 이분산에 견고한 표준오차로 계산했다. 관측 가격이 세 수준이고 한 메뉴의
21일 실험에 집중되어 있으므로 실제 서비스에서는 더 긴 무작위 가격 실험으로 갱신해야 한다.
"""


def run_estimation(panel_path, output_dir, menu_id="M01"):
    panel = pd.read_csv(panel_path, encoding="utf-8-sig")
    report = estimate_price_elasticity(panel, menu_id=menu_id)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "elasticity.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "README.md").write_text(render_report(report), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--panel", type=Path, default=root / "data" / "processed" / "demand_panel.csv")
    parser.add_argument("--output-dir", type=Path, default=root / "reports" / "modeling" / "elasticity")
    parser.add_argument("--menu-id", default="M01")
    args = parser.parse_args()
    report = run_estimation(args.panel, args.output_dir, args.menu_id)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
