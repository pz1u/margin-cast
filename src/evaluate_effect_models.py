"""관측 데이터로 추정한 가격·할인 효과를 평가 전용 Ground Truth와 비교한다."""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from .estimate_elasticity import estimate_price_elasticity
except ImportError:
    from estimate_elasticity import estimate_price_elasticity


def _contains(interval, value):
    return bool(interval[0] <= value <= interval[1])


def evaluate_effect_models(panel, ground_truth):
    truth_by_menu = {row["menu_id"]: row for row in ground_truth["menus"]}
    train = panel[panel["split"] == "train"]
    observed_price_levels = train.groupby("menu_id")["offered_list_price"].nunique()
    menu_ids = sorted(observed_price_levels[observed_price_levels >= 2].index)

    price_rows = []
    discount_rows = []
    for menu_id in menu_ids:
        estimate = estimate_price_elasticity(panel, menu_id=menu_id)
        true_elasticity = float(truth_by_menu[menu_id]["price_elasticity"])
        price_rows.append(
            {
                "menu_id": menu_id,
                "estimated": estimate["elasticity"],
                "ground_truth": true_elasticity,
                "error": estimate["elasticity"] - true_elasticity,
                "absolute_error": abs(estimate["elasticity"] - true_elasticity),
                "robust_standard_error": estimate["robust_standard_error"],
                "confidence_interval_95": estimate["confidence_interval_95"],
                "ground_truth_in_interval": _contains(
                    estimate["confidence_interval_95"], true_elasticity
                ),
                "observed_price_levels": estimate["paid_price_levels"],
            }
        )

        if train.loc[train["menu_id"] == menu_id, "promotion_discount"].gt(0).any():
            true_lift = float(truth_by_menu[menu_id]["promotion_lift"])
            interval = estimate["promotion_confidence_interval_95"]
            discount_rows.append(
                {
                    "menu_id": menu_id,
                    "estimated_lift": estimate["promotion_lift"],
                    "ground_truth_lift": true_lift,
                    "error": estimate["promotion_lift"] - true_lift,
                    "absolute_error": abs(estimate["promotion_lift"] - true_lift),
                    "estimated_log_effect": estimate["promotion_log_effect"],
                    "robust_standard_error": estimate["promotion_robust_standard_error"],
                    "confidence_interval_95": interval,
                    "ground_truth_in_interval": _contains(interval, true_lift),
                }
            )

    return {
        "method": "Poisson IRLS with HC0 robust standard errors",
        "training_split": "train",
        "ground_truth_used_for_training": False,
        "ground_truth_used_for_evaluation": True,
        "price_elasticity": {
            "menus": price_rows,
            "mean_absolute_error": float(
                np.mean([row["absolute_error"] for row in price_rows])
            ),
            "interval_coverage": float(
                np.mean([row["ground_truth_in_interval"] for row in price_rows])
            ),
        },
        "promotion_lift": {
            "menus": discount_rows,
            "mean_absolute_error": float(
                np.mean([row["absolute_error"] for row in discount_rows])
            ),
            "interval_coverage": float(
                np.mean([row["ground_truth_in_interval"] for row in discount_rows])
            ),
        },
        "limitations": [
            "실험은 7일 단위 순차 배치여서 관측되지 않은 시기 요인과 완전히 분리되지 않는다.",
            "세 메뉴의 탄력성 신뢰구간이 넓어 방향 판단보다 정밀한 수치 비교의 신뢰도가 낮다.",
            "Ground Truth 비교는 합성 데이터 생성 규칙 복원 여부만 평가하며 실제 매장 일반화를 보장하지 않는다.",
        ],
    }


def render_report(report):
    price_rows = "\n".join(
        f"| {row['menu_id']} | {row['estimated']:.3f} | {row['ground_truth']:.3f} | "
        f"{row['error']:+.3f} | [{row['confidence_interval_95'][0]:.3f}, "
        f"{row['confidence_interval_95'][1]:.3f}] | {'포함' if row['ground_truth_in_interval'] else '미포함'} |"
        for row in report["price_elasticity"]["menus"]
    )
    discount_rows = "\n".join(
        f"| {row['menu_id']} | {row['estimated_lift']:.3f} | {row['ground_truth_lift']:.3f} | "
        f"{row['error']:+.3f} | [{row['confidence_interval_95'][0]:.3f}, "
        f"{row['confidence_interval_95'][1]:.3f}] | {'포함' if row['ground_truth_in_interval'] else '미포함'} |"
        for row in report["promotion_lift"]["menus"]
    )
    limits = "\n".join(f"- {value}" for value in report["limitations"])
    return f"""# 가격탄력성·할인 효과 평가

모델은 관측 가능한 Train 패널만 사용해 학습했다. `ground_truth.json`은 학습이 끝난 뒤
합성 데이터의 숨은 생성값과 추정값을 비교하는 평가 단계에서만 읽었다.

## 가격탄력성

| 메뉴 | 추정값 | Ground Truth | 오차 | HC0 95% 신뢰구간 | 정답 포함 |
|---|---:|---:|---:|---:|---|
{price_rows}

- 평균 절대오차: **{report['price_elasticity']['mean_absolute_error']:.3f}**
- 구간 포함률: **{report['price_elasticity']['interval_coverage']:.0%}**

## 할인 추가 노출 효과

할인 실결제 가격의 수요 효과는 가격탄력성이 설명하고, 아래 배수는 할인 배지·노출 등
가격 외 프로모션 효과를 나타낸다.

| 메뉴 | 추정 배수 | Ground Truth | 오차 | HC0 95% 신뢰구간 | 정답 포함 |
|---|---:|---:|---:|---:|---|
{discount_rows}

- 평균 절대오차: **{report['promotion_lift']['mean_absolute_error']:.3f}**
- 구간 포함률: **{report['promotion_lift']['interval_coverage']:.0%}**

## 해석 한계

{limits}
"""


def run_evaluation(panel_path, ground_truth_path, output_dir):
    panel = pd.read_csv(panel_path, encoding="utf-8-sig")
    ground_truth = json.loads(Path(ground_truth_path).read_text(encoding="utf-8"))
    report = evaluate_effect_models(panel, ground_truth)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "effects.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "README.md").write_text(render_report(report), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument(
        "--panel", type=Path, default=root / "data" / "processed" / "demand_panel.csv"
    )
    parser.add_argument(
        "--ground-truth", type=Path, default=root / "data" / "ground_truth.json"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=root / "reports" / "modeling" / "effects"
    )
    args = parser.parse_args()
    report = run_evaluation(args.panel, args.ground_truth, args.output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
