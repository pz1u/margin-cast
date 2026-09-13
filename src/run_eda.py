"""관측 데이터만 사용해 재현 가능한 EDA 요약과 차트를 만든다."""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False

try:
    from .prepare_analysis_data import load_observed_tables
except ImportError:
    from prepare_analysis_data import load_observed_tables


EVENT_COLORS = {
    "DISCOUNT": "#52B788",
    "PRICE": "#F4A261",
    "BUNDLE": "#7B2CBF",
}


def load_panel(panel_path):
    panel = pd.read_csv(panel_path, encoding="utf-8-sig")
    panel["date"] = pd.to_datetime(panel["date"])
    return panel


def build_eda_summary(panel, tables):
    """Ground Truth 없이 관측 가능한 기술 통계만 계산한다."""
    menu_summary = (
        panel.groupby(["menu_id", "menu_name", "category"], as_index=False)
        .agg(
            units_sold=("units_sold", "sum"),
            order_count=("order_count", "sum"),
            net_sales=("net_sales", "sum"),
            contribution_profit=("contribution_profit", "sum"),
        )
        .sort_values("contribution_profit", ascending=False)
    )
    menu_summary["contribution_margin"] = (
        menu_summary["contribution_profit"] / menu_summary["net_sales"]
    )

    main_panel = panel[panel["category"] == "MAIN"]
    hourly = (
        main_panel.groupby("hour")["order_count"].sum() / panel["date"].nunique()
    ).to_dict()
    hourly_groups = {
        "lunch": sum(hourly[hour] for hour in (12, 13)) / 2,
        "dinner": sum(hourly[hour] for hour in (18, 19, 20)) / 3,
        "offpeak": sum(hourly[hour] for hour in (14, 15, 16)) / 3,
    }

    weather_orders = (
        main_panel.groupby(["weather", "channel"])["order_count"].sum().unstack(fill_value=0)
    )
    delivery_share = (
        weather_orders["DELIVERY"] / weather_orders.sum(axis=1)
    ).to_dict()

    daily_orders = main_panel.groupby(["date", "is_weekend"])["order_count"].sum().reset_index()
    orders_per_day = {
        "weekday": float(daily_orders.loc[daily_orders["is_weekend"] == 0, "order_count"].mean()),
        "weekend": float(daily_orders.loc[daily_orders["is_weekend"] == 1, "order_count"].mean()),
    }

    chicken_daily = (
        panel.loc[panel["menu_id"] == "M01"].groupby("date")["units_sold"].sum()
    )
    experiments = []
    for event in tables["experiments"].sort_values("start_date").itertuples(index=False):
        start, end = event.start_date, event.end_date
        duration = (end - start).days + 1
        before = chicken_daily.loc[start - pd.Timedelta(days=duration):start - pd.Timedelta(days=1)]
        during = chicken_daily.loc[start:end]
        experiments.append(
            {
                "experiment_id": event.experiment_id,
                "kind": event.kind,
                "value": int(event.value),
                "start_date": start.strftime("%Y-%m-%d"),
                "end_date": end.strftime("%Y-%m-%d"),
                "before_daily_units": float(before.mean()),
                "during_daily_units": float(during.mean()),
                "observed_change": float(during.mean() / before.mean() - 1),
            }
        )

    orders = tables["orders"][["order_id", "date"]]
    items = tables["order_items"].merge(orders, on="order_id", how="left", validate="many_to_one")
    bundle_start = tables["bundles"]["start_date"].min()
    before_bundle = items[items["date"] < bundle_start]
    baskets = before_bundle.groupby("order_id")["menu_id"].agg(set)
    chicken = baskets.map(lambda values: "M01" in values)
    cola = baskets.map(lambda values: "M06" in values)
    confidence = float((chicken & cola).sum() / chicken.sum())
    cola_rate = float(cola.mean())
    association = {
        "orders": int(len(baskets)),
        "support": float((chicken & cola).mean()),
        "chicken_to_cola_confidence": confidence,
        "overall_cola_order_rate": cola_rate,
        "lift": confidence / cola_rate,
    }

    bundle_order_count = int(
        tables["order_items"].loc[tables["order_items"]["bundle_id"].notna(), "order_id"].nunique()
    )
    split_summary = (
        panel.groupby("split", observed=True)
        .agg(rows=("menu_id", "size"), days=("day_index", "nunique"), units_sold=("units_sold", "sum"))
        .reset_index()
    )
    split_summary["split_order"] = split_summary["split"].map(
        {"train": 0, "validation": 1, "test": 2}
    )
    split_summary = split_summary.sort_values("split_order").drop(columns="split_order")

    return {
        "scope": {
            "start_date": panel["date"].min().strftime("%Y-%m-%d"),
            "end_date": panel["date"].max().strftime("%Y-%m-%d"),
            "days": int(panel["date"].nunique()),
            "panel_rows": int(len(panel)),
            "zero_sales_rows": int((panel["units_sold"] == 0).sum()),
            "zero_sales_ratio": float((panel["units_sold"] == 0).mean()),
            "ground_truth_used": False,
        },
        "totals": {
            "orders": int(main_panel["order_count"].sum()),
            "units_sold": int(panel["units_sold"].sum()),
            "net_sales": int(panel["net_sales"].sum()),
            "contribution_profit": int(panel["contribution_profit"].sum()),
            "contribution_margin": float(
                panel["contribution_profit"].sum() / panel["net_sales"].sum()
            ),
        },
        "hourly_orders_per_day": {str(key): float(value) for key, value in hourly.items()},
        "hourly_groups": hourly_groups,
        "delivery_share": {key: float(value) for key, value in delivery_share.items()},
        "orders_per_day": orders_per_day,
        "menu_summary": menu_summary.to_dict("records"),
        "observed_experiments": experiments,
        "association_before_bundle": association,
        "bundle_observed": {"orders_with_bundle": bundle_order_count},
        "time_split": split_summary.to_dict("records"),
        "interpretation_limits": [
            "실험 전후 비교는 날씨와 표본 변동을 통제한 인과효과가 아니다.",
            "세트 주문 197건 중 신규 수요와 기존 주문 전환은 POS만으로 구분할 수 없다.",
            "75~81일에만 세트가 있어 일반적인 60/15/15 분할의 검증·평가 구간에 실험이 걸친다.",
            "미래 수요 예측 시 실제 미래 날씨 대신 예보 또는 시나리오 입력이 필요하다.",
        ],
    }


def shade_experiments(axis, experiments):
    labels_seen = set()
    for event in experiments.itertuples(index=False):
        label = event.kind.title() if event.kind not in labels_seen else None
        axis.axvspan(event.start_date, event.end_date + pd.Timedelta(days=1),
                    color=EVENT_COLORS[event.kind], alpha=0.14, label=label)
        labels_seen.add(event.kind)


def render_charts(panel, tables, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    experiments = tables["experiments"].sort_values("start_date")
    daily = panel.groupby("date")[["net_sales", "contribution_profit"]].sum()
    chicken_daily = panel.loc[panel["menu_id"] == "M01"].groupby("date")["units_sold"].sum()
    main_panel = panel[panel["category"] == "MAIN"]
    hourly = main_panel.groupby("hour")["order_count"].sum() / panel["date"].nunique()
    weather = main_panel.groupby(["weather", "channel"])["order_count"].sum().unstack(fill_value=0)
    delivery_share = weather["DELIVERY"] / weather.sum(axis=1)

    figure, axes = plt.subplots(2, 2, figsize=(14, 9), constrained_layout=True)
    axes[0, 0].plot(daily.index, daily["net_sales"] / 1_000_000, label="Net sales", color="#277DA1")
    axes[0, 0].plot(daily.index, daily["contribution_profit"] / 1_000_000,
                    label="Contribution profit", color="#43AA8B")
    shade_experiments(axes[0, 0], experiments)
    axes[0, 0].set(title="Daily store performance", ylabel="KRW millions")
    axes[0, 0].legend(fontsize=8, ncol=2)

    axes[0, 1].plot(chicken_daily.index, chicken_daily.values, color="#E76F51", linewidth=1.8)
    shade_experiments(axes[0, 1], experiments)
    axes[0, 1].set(title="Chicken mayo daily units", ylabel="Units")
    axes[0, 1].legend(fontsize=8, ncol=3)

    axes[1, 0].plot(hourly.index, hourly.values, marker="o", color="#577590")
    axes[1, 0].axvspan(12, 13, color="#F9C74F", alpha=0.18, label="Lunch")
    axes[1, 0].axvspan(18, 20, color="#F9844A", alpha=0.14, label="Dinner")
    axes[1, 0].set(title="Average orders per day by hour", xlabel="Hour", ylabel="Orders")
    axes[1, 0].set_xticks(sorted(hourly.index))
    axes[1, 0].legend()

    axes[1, 1].bar(delivery_share.index, delivery_share.values * 100,
                   color=["#F9C74F" if value == "CLEAR" else "#577590" for value in delivery_share.index])
    axes[1, 1].set(title="Delivery share by weather", ylabel="Percent", ylim=(0, 100))
    for index, value in enumerate(delivery_share.values * 100):
        axes[1, 1].text(index, value + 2, f"{value:.1f}%", ha="center")
    figure.suptitle("MarginCast observed-data EDA", fontsize=16, fontweight="bold")
    dashboard_path = output_dir / "eda-dashboard.png"
    figure.savefig(dashboard_path, dpi=150)
    plt.close(figure)

    menu = panel.groupby(["menu_name"])[["units_sold", "contribution_profit"]].sum().sort_values(
        "contribution_profit"
    )
    figure, axes = plt.subplots(1, 2, figsize=(14, 6), constrained_layout=True)
    axes[0].barh(menu.index, menu["units_sold"], color="#277DA1")
    axes[0].set(title="Units sold by menu", xlabel="Units")
    axes[1].barh(menu.index, menu["contribution_profit"] / 1_000_000, color="#43AA8B")
    axes[1].set(title="Contribution profit by menu", xlabel="KRW millions")
    performance_path = output_dir / "menu-performance.png"
    figure.savefig(performance_path, dpi=150)
    plt.close(figure)
    return [dashboard_path, performance_path]


def render_markdown(summary):
    totals = summary["totals"]
    hourly = summary["hourly_groups"]
    weather = summary["delivery_share"]
    association = summary["association_before_bundle"]
    menu_rows = "\n".join(
        f"| {row['menu_name']} | {row['units_sold']:,} | {row['net_sales']:,} | "
        f"{row['contribution_profit']:,} | {row['contribution_margin']:.2%} |"
        for row in summary["menu_summary"]
    )
    experiment_rows = "\n".join(
        f"| {row['kind']} | {row['start_date']}~{row['end_date']} | "
        f"{row['before_daily_units']:.2f} | {row['during_daily_units']:.2f} | "
        f"{row['observed_change']:+.2%} |"
        for row in summary["observed_experiments"]
    )
    limits = "\n".join(f"- {item}" for item in summary["interpretation_limits"])
    return f"""# MarginCast 관측 데이터 EDA

이 보고서는 `ground_truth.json`을 읽지 않고 POS·메뉴·달력·실험 정의 파일만 사용했다.

![EDA 대시보드](eda-dashboard.png)

## 데이터 범위

- 기간: {summary['scope']['start_date']}~{summary['scope']['end_date']} ({summary['scope']['days']}일)
- 분석 패널: {summary['scope']['panel_rows']:,}행
- 0판매 셀: {summary['scope']['zero_sales_rows']:,}행 ({summary['scope']['zero_sales_ratio']:.2%})
- 주문: {totals['orders']:,}건, 판매 수량: {totals['units_sold']:,}개
- 실결제 매출: {totals['net_sales']:,}원
- 기여이익: {totals['contribution_profit']:,}원, 기여이익률: {totals['contribution_margin']:.2%}

0판매 셀도 학습 패널에 포함했다. 판매가 발생한 행만 사용하면 수요가 없었던 조건이 사라져
수요 수준과 가격·시간 효과가 왜곡될 수 있다.

## 주요 패턴

- 하루·시간당 평균 주문: 점심 {hourly['lunch']:.2f}건, 저녁 {hourly['dinner']:.2f}건,
  14~16시 {hourly['offpeak']:.2f}건.
- 일평균 주문: 평일 {summary['orders_per_day']['weekday']:.2f}건,
  주말 {summary['orders_per_day']['weekend']:.2f}건.
- 배달 비중: 맑음 {weather['CLEAR']:.2%}, 비 {weather['RAIN']:.2%}.
- 세트 도입 전 치킨마요→콜라 confidence {association['chicken_to_cola_confidence']:.2%},
  전체 콜라 주문률 {association['overall_cola_order_rate']:.2%}, Lift {association['lift']:.2f}.
- 관측된 세트 주문: {summary['bundle_observed']['orders_with_bundle']:,}건.

## 메뉴별 성과

![메뉴별 성과](menu-performance.png)

| 메뉴 | 판매 수량 | 실결제 매출(원) | 기여이익(원) | 기여이익률 |
|---|---:|---:|---:|---:|
{menu_rows}

## 치킨마요 실험 전후 관측값

각 실험과 같은 길이의 직전 기간을 비교했다. 이는 기술 통계이며 인과효과 추정값이 아니다.

| 실험 | 기간 | 직전 일평균 | 실험 일평균 | 관측 변화 |
|---|---|---:|---:|---:|
{experiment_rows}

## 시간순 데이터 분할

- Train: Day 1~60
- Validation: Day 61~75
- Test: Day 76~90

이 분할은 미래 데이터를 과거 학습에 섞지 않는다. 다만 세트 실험이 Day 75~81이라
Validation과 Test에 걸치므로 세트 효과 평가에는 별도 실험 설계가 필요하다.

## 해석 한계

{limits}
"""


def run_eda(data_dir, panel_path, output_dir):
    tables = load_observed_tables(data_dir)
    panel = load_panel(panel_path)
    summary = build_eda_summary(panel, tables)
    output_dir = Path(output_dir)
    charts = render_charts(panel, tables, output_dir)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "README.md").write_text(render_markdown(summary), encoding="utf-8")
    return summary, charts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--data-dir", type=Path, default=root / "data")
    parser.add_argument("--panel", type=Path, default=root / "data" / "processed" / "demand_panel.csv")
    parser.add_argument("--output-dir", type=Path, default=root / "reports" / "eda")
    args = parser.parse_args()
    summary, charts = run_eda(args.data_dir, args.panel, args.output_dir)
    print(json.dumps({"scope": summary["scope"], "totals": summary["totals"],
                      "charts": [str(path) for path in charts]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
