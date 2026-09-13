"""관측 가능한 POS 파일로 0판매 셀을 포함한 수요 분석 패널을 만든다."""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


MONEY_COLUMNS = [
    "gross_sales",
    "discount_amount",
    "net_sales",
    "ingredient_cost",
    "platform_fee",
    "payment_fee",
    "additional_promotion_cost",
    "contribution_profit",
]


def load_observed_tables(data_dir):
    """Ground Truth를 읽지 않고 실제 서비스에서 관측 가능한 파일만 읽는다."""
    data_dir = Path(data_dir)
    tables = {
        name: pd.read_csv(data_dir / f"{name}.csv", encoding="utf-8-sig", low_memory=False)
        for name in (
            "menus",
            "orders",
            "order_items",
            "menu_cost_history",
            "promotions",
            "bundles",
            "experiments",
            "daily_context",
        )
    }
    for column in ("date", "effective_date", "start_date", "end_date"):
        for table in tables.values():
            if column in table:
                table[column] = pd.to_datetime(table[column])
    return tables


def build_demand_panel(tables):
    """날짜×영업시간×채널×메뉴의 완전한 패널과 알려진 사전 특성을 만든다."""
    calendar = tables["daily_context"].copy()
    menus = tables["menus"].copy()

    grid_rows = []
    for context in calendar.to_dict("records"):
        for hour in range(int(context["open_hour"]), int(context["close_hour"])):
            for channel in ("STORE", "DELIVERY"):
                for menu_id in menus["menu_id"]:
                    grid_rows.append(
                        {
                            "date": context["date"],
                            "day_index": int(context["day_index"]),
                            "weekday": int(context["weekday"]),
                            "weather": context["weather"],
                            "hour": hour,
                            "channel": channel,
                            "menu_id": menu_id,
                        }
                    )
    panel = pd.DataFrame(grid_rows).merge(menus, on="menu_id", how="left", validate="many_to_one")

    order_context = tables["orders"][["order_id", "date", "hour", "channel"]]
    sold = tables["order_items"].merge(order_context, on="order_id", how="left", validate="many_to_one")
    sold["order_count"] = 1
    sold["bundle_units"] = sold["quantity"].where(sold["bundle_id"].notna(), 0)
    sold["promotion_units"] = sold["quantity"].where(sold["promotion_id"].notna(), 0)
    aggregations = {
        "quantity": "sum",
        "order_count": "sum",
        "bundle_units": "sum",
        "promotion_units": "sum",
        **{column: "sum" for column in MONEY_COLUMNS},
    }
    sales = (
        sold.groupby(["date", "hour", "channel", "menu_id"], as_index=False)
        .agg(aggregations)
        .rename(columns={"quantity": "units_sold"})
    )
    panel = panel.merge(
        sales,
        on=["date", "hour", "channel", "menu_id"],
        how="left",
        validate="one_to_one",
    )
    count_columns = ["units_sold", "order_count", "bundle_units", "promotion_units", *MONEY_COLUMNS]
    panel[count_columns] = panel[count_columns].fillna(0).astype("int64")

    panel["offered_list_price"] = panel["initial_list_price"].astype("int64")
    panel["promotion_discount"] = 0
    panel["bundle_available"] = 0
    panel["bundle_price"] = 0
    for event in tables["experiments"].itertuples(index=False):
        active = (
            (panel["menu_id"] == event.menu_id)
            & panel["date"].between(event.start_date, event.end_date)
        )
        if event.kind == "PRICE":
            panel.loc[active, "offered_list_price"] = int(event.value)
    for promotion in tables["promotions"].itertuples(index=False):
        active = (
            (panel["menu_id"] == promotion.menu_id)
            & panel["date"].between(promotion.start_date, promotion.end_date)
        )
        panel.loc[active, "promotion_discount"] = int(promotion.discount_per_unit)
    for bundle in tables["bundles"].itertuples(index=False):
        active = (
            panel["menu_id"].isin([bundle.main_menu_id, bundle.drink_menu_id])
            & panel["date"].between(bundle.start_date, bundle.end_date)
        )
        panel.loc[active, "bundle_available"] = 1
        panel.loc[active, "bundle_price"] = int(bundle.bundle_price)
    panel["regular_paid_unit_price"] = panel["offered_list_price"] - panel["promotion_discount"]

    panel["unit_cost"] = 0
    for history in (
        tables["menu_cost_history"].sort_values(["effective_date", "menu_id"]).itertuples(index=False)
    ):
        active = (panel["menu_id"] == history.menu_id) & (panel["date"] >= history.effective_date)
        panel.loc[active, "unit_cost"] = int(history.unit_cost)

    panel["is_weekend"] = (panel["weekday"] >= 5).astype("int8")
    panel["is_rain"] = (panel["weather"] == "RAIN").astype("int8")
    panel["is_lunch"] = panel["hour"].isin([12, 13]).astype("int8")
    panel["is_dinner"] = panel["hour"].isin([18, 19, 20]).astype("int8")
    panel["time_index"] = (panel["day_index"] - 1) * 24 + panel["hour"]
    total_days = int(panel["day_index"].max())
    train_end = total_days * 2 // 3
    validation_end = total_days * 5 // 6
    panel["split"] = np.select(
        [panel["day_index"] <= train_end, panel["day_index"] <= validation_end],
        ["train", "validation"],
        default="test",
    )
    panel["split"] = pd.Categorical(panel["split"], ["train", "validation", "test"], ordered=True)

    return panel.sort_values(["date", "hour", "channel", "menu_id"]).reset_index(drop=True)


def validate_demand_panel(panel, tables):
    expected_rows = len(tables["daily_context"]) * 11 * 2 * len(tables["menus"])
    if len(panel) != expected_rows:
        raise ValueError(f"패널 행 수 오류: expected={expected_rows}, actual={len(panel)}")
    keys = ["date", "hour", "channel", "menu_id"]
    if panel.duplicated(keys).any():
        raise ValueError("분석 패널 키가 중복되었습니다.")
    if panel[["offered_list_price", "regular_paid_unit_price", "unit_cost"]].le(0).any().any():
        raise ValueError("가격 또는 원가가 채워지지 않은 셀이 있습니다.")
    forbidden = {
        "price_elasticity",
        "base_demand",
        "promotion_lift",
        "no_intervention_count",
        "expected_actual",
        "origin",
        "original_menu_ids",
    }
    leaked = forbidden.intersection(panel.columns)
    if leaked:
        raise ValueError(f"Ground Truth 누수 컬럼: {sorted(leaked)}")

    source_items = tables["order_items"]
    totals = {
        "units_sold": int(source_items["quantity"].sum()),
        **{column: int(source_items[column].sum()) for column in MONEY_COLUMNS},
    }
    for column, expected in totals.items():
        actual = int(panel[column].sum())
        if actual != expected:
            raise ValueError(f"원천 합계 불일치: {column}, expected={expected}, actual={actual}")
    total_days = int(panel["day_index"].max())
    expected_split_days = {
        "train": total_days * 2 // 3,
        "validation": total_days * 5 // 6 - total_days * 2 // 3,
        "test": total_days - total_days * 5 // 6,
    }
    split_days = panel.groupby("split", observed=True)["day_index"].nunique().to_dict()
    if split_days != expected_split_days:
        raise ValueError(f"시간순 분할 오류: {split_days}")

    return {
        "passed": True,
        "rows": len(panel),
        "key_columns": keys,
        "zero_sales_rows": int((panel["units_sold"] == 0).sum()),
        "zero_sales_ratio": float((panel["units_sold"] == 0).mean()),
        "split_days": split_days,
        "split_rows": panel.groupby("split", observed=True).size().astype(int).to_dict(),
        "source_totals": totals,
        "ground_truth_loaded": False,
    }


def prepare_analysis_data(data_dir, output_path):
    tables = load_observed_tables(data_dir)
    panel = build_demand_panel(tables)
    report = validate_demand_panel(panel, tables)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(output_path, index=False, encoding="utf-8-sig", lineterminator="\n")
    report_path = output_path.with_name("panel_validation.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return panel, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--data-dir", type=Path, default=root / "data")
    parser.add_argument("--output", type=Path, default=root / "data" / "processed" / "demand_panel.csv")
    args = parser.parse_args()
    _, report = prepare_analysis_data(args.data_dir, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
