"""시간순 분할로 수요 기준 모델을 학습하고 단순 기준선과 비교한다."""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


CATEGORICAL_FEATURES = ["menu_id", "channel", "weather", "hour", "weekday"]
NUMERIC_FEATURES = [
    "is_weekend",
    "is_lunch",
    "is_dinner",
    "time_index",
    "price_ratio",
    "promotion_discount_ratio",
    "bundle_available",
    "bundle_price_ratio",
]
BENCHMARK_KEYS = ["menu_id", "channel", "hour", "weekday"]


def add_model_features(panel):
    frame = panel.copy()
    frame["price_ratio"] = frame["regular_paid_unit_price"] / frame["initial_list_price"]
    frame["promotion_discount_ratio"] = frame["promotion_discount"] / frame["initial_list_price"]
    frame["bundle_price_ratio"] = np.where(
        frame["bundle_available"].eq(1),
        frame["bundle_price"] / frame["initial_list_price"],
        0.0,
    )
    return frame


def build_model():
    return PoissonDemandModel(alpha=0.1)


class PoissonDemandModel:
    """NumPy IRLS로 적합하는 작은 Poisson 회귀 모델."""

    def __init__(self, alpha=0.1, max_iter=100, tolerance=1e-8):
        self.alpha = alpha
        self.max_iter = max_iter
        self.tolerance = tolerance

    def _design(self, frame, fit=False):
        columns = [np.ones(len(frame))]
        if fit:
            self.categories_ = {
                name: sorted(frame[name].unique().tolist()) for name in CATEGORICAL_FEATURES
            }
            self.numeric_mean_ = frame[NUMERIC_FEATURES].mean().astype(float)
            self.numeric_scale_ = frame[NUMERIC_FEATURES].std(ddof=0).replace(0, 1).astype(float)
        for name in CATEGORICAL_FEATURES:
            # 첫 범주는 기준 수준으로 두어 계수 해석과 수치 안정성을 확보한다.
            for category in self.categories_[name][1:]:
                columns.append(frame[name].eq(category).to_numpy(dtype=float))
        normalized = (frame[NUMERIC_FEATURES] - self.numeric_mean_) / self.numeric_scale_
        columns.extend(normalized[name].to_numpy(dtype=float) for name in NUMERIC_FEATURES)
        return np.column_stack(columns)

    def fit(self, frame, target):
        design = self._design(frame, fit=True)
        target = np.asarray(target, dtype=float)
        coefficients = np.zeros(design.shape[1])
        coefficients[0] = np.log(max(target.mean(), 1e-8))
        penalty = np.eye(design.shape[1]) * self.alpha
        penalty[0, 0] = 0
        for iteration in range(1, self.max_iter + 1):
            linear = np.clip(design @ coefficients, -12, 12)
            mean = np.exp(linear)
            adjusted = linear + (target - mean) / np.maximum(mean, 1e-8)
            weighted_design = design * np.sqrt(mean)[:, None]
            weighted_target = adjusted * np.sqrt(mean)
            updated = np.linalg.solve(
                weighted_design.T @ weighted_design + penalty,
                weighted_design.T @ weighted_target,
            )
            if np.max(np.abs(updated - coefficients)) < self.tolerance:
                coefficients = updated
                break
            coefficients = updated
        self.coefficients_ = coefficients
        self.n_iter_ = iteration
        return self

    def predict(self, frame):
        return np.exp(np.clip(self._design(frame) @ self.coefficients_, -12, 12))


def fit_historical_benchmark(train):
    return train.groupby(BENCHMARK_KEYS, observed=True)["units_sold"].mean()


def predict_historical_benchmark(frame, benchmark, train_mean):
    index = pd.MultiIndex.from_frame(frame[BENCHMARK_KEYS])
    return np.asarray(benchmark.reindex(index).fillna(train_mean), dtype=float)


def regression_metrics(actual, predicted):
    actual = np.asarray(actual, dtype=float)
    predicted = np.clip(np.asarray(predicted, dtype=float), 0, None)
    return {
        "mae": float(np.mean(np.abs(actual - predicted))),
        "rmse": float(np.sqrt(np.mean((actual - predicted) ** 2))),
        "mean_actual": float(actual.mean()),
        "mean_predicted": float(predicted.mean()),
    }


def train_and_evaluate(panel):
    frame = add_model_features(panel)
    train = frame[frame["split"] == "train"].copy()
    model = build_model()
    model.fit(train[CATEGORICAL_FEATURES + NUMERIC_FEATURES], train["units_sold"])

    benchmark = fit_historical_benchmark(train)
    train_mean = float(train["units_sold"].mean())
    results = {}
    predictions = []
    for split in ("validation", "test"):
        evaluation = frame[frame["split"] == split].copy()
        model_prediction = np.clip(
            model.predict(evaluation[CATEGORICAL_FEATURES + NUMERIC_FEATURES]), 0, None
        )
        benchmark_prediction = predict_historical_benchmark(evaluation, benchmark, train_mean)
        model_metrics = regression_metrics(evaluation["units_sold"], model_prediction)
        benchmark_metrics = regression_metrics(evaluation["units_sold"], benchmark_prediction)
        results[split] = {
            "rows": int(len(evaluation)),
            "model": model_metrics,
            "historical_benchmark": benchmark_metrics,
            "mae_improvement": float(
                1 - model_metrics["mae"] / benchmark_metrics["mae"]
            ),
        }
        prediction = evaluation[["date", "hour", "channel", "menu_id", "units_sold"]].copy()
        prediction["split"] = split
        prediction["predicted_units"] = model_prediction
        prediction["benchmark_units"] = benchmark_prediction
        predictions.append(prediction)

    report = {
        "target": "units_sold",
        "train_days": int(train["day_index"].nunique()),
        "train_rows": int(len(train)),
        "features": CATEGORICAL_FEATURES + NUMERIC_FEATURES,
        "ground_truth_used": False,
        "splits": results,
    }
    return model, report, pd.concat(predictions, ignore_index=True)


def render_report(report):
    rows = []
    for split, values in report["splits"].items():
        rows.append(
            f"| {split} | {values['model']['mae']:.4f} | {values['model']['rmse']:.4f} | "
            f"{values['historical_benchmark']['mae']:.4f} | {values['mae_improvement']:+.2%} |"
        )
    return """# 수요 기준 모델 평가

날짜×시간×채널×메뉴 단위의 `units_sold`를 예측한다. Day 1~60만 학습하고
Day 61~75와 Day 76~90을 순서대로 평가했다. 생성기의 Ground Truth는 사용하지 않았다.

| 구간 | Poisson MAE | Poisson RMSE | 과거 조건부 평균 MAE | MAE 개선율 |
|---|---:|---:|---:|---:|
""" + "\n".join(rows) + """

과거 조건부 평균은 학습 구간의 메뉴×채널×시간×요일 평균이다. Poisson 모델은 여기에
날씨, 추세, 예정 가격·할인·세트 변수를 추가한다. 0판매 셀도 모두 학습과 평가에 포함한다.

이 평가는 예측 파이프라인의 기준 성능이다. 가격 변화의 인과효과는 별도 탄력성 모델에서
실험 구간과 통제 변수를 명시해 추정한다.
"""


def run_training(panel_path, output_dir):
    panel = pd.read_csv(panel_path, encoding="utf-8-sig")
    model, report, predictions = train_and_evaluate(panel)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "metrics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "README.md").write_text(render_report(report), encoding="utf-8")
    predictions.to_csv(output_dir / "predictions.csv", index=False, encoding="utf-8-sig")
    return model, report, predictions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--panel", type=Path, default=root / "data" / "processed" / "demand_panel.csv")
    parser.add_argument("--output-dir", type=Path, default=root / "reports" / "modeling" / "baseline")
    args = parser.parse_args()
    _, report, _ = run_training(args.panel, args.output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
