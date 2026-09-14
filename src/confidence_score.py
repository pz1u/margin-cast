"""관측 근거·추정 정밀도·시나리오 범위·날씨 문맥으로 신뢰도를 산정한다."""

import numpy as np


def _event_count(active):
    active = np.asarray(active, dtype=bool)
    if len(active) == 0:
        return 0
    return int(active[0]) + int(np.sum(active[1:] & ~active[:-1]))


def _precision_points(estimate, standard_error, maximum=30.0):
    denominator = max(abs(float(estimate)), 0.05)
    relative_error = min(float(standard_error) / denominator, 1.0)
    return maximum * (1 - relative_error)


def calculate_confidence(panel, elasticity_report, scenario, context_source):
    """신뢰도는 개선확률과 별개인 근거 품질 점수이며 0~100 범위다."""
    menu_id = elasticity_report["menu_id"]
    train = panel[(panel["menu_id"] == menu_id) & (panel["split"] == "train")]
    daily = (
        train.groupby("day_index", as_index=False)
        .agg(
            offered_list_price=("offered_list_price", "first"),
            initial_list_price=("initial_list_price", "first"),
            promotion_discount=("promotion_discount", "first"),
        )
        .sort_values("day_index")
    )
    price_active = daily["offered_list_price"].ne(daily["initial_list_price"])
    promotion_active = daily["promotion_discount"].gt(0)
    price_events = _event_count(price_active)
    promotion_events = _event_count(promotion_active)
    nonbaseline_levels = max(int(daily["offered_list_price"].nunique()) - 1, 0)

    price_evidence = min(20.0, price_events * 5.0) + min(10.0, nonbaseline_levels * 5.0)
    price_precision = _precision_points(
        elasticity_report["elasticity"], elasticity_report["robust_standard_error"]
    )
    if scenario["discount"] > 0:
        promotion_evidence = min(30.0, promotion_events * 10.0)
        promotion_precision = _precision_points(
            elasticity_report["promotion_log_effect"],
            elasticity_report["promotion_robust_standard_error"],
        )
        evidence = (price_evidence + promotion_evidence) / 2
        precision = (price_precision + promotion_precision) / 2
    else:
        promotion_evidence = None
        promotion_precision = None
        evidence = price_evidence
        precision = price_precision

    paid_price = scenario["list_price"] - scenario["discount"]
    observed = elasticity_report["paid_price_levels"]
    low, high = min(observed), max(observed)
    if low <= paid_price <= high:
        scenario_support = 20.0
    else:
        nearest = low if paid_price < low else high
        relative_distance = abs(paid_price - nearest) / nearest
        scenario_support = max(0.0, 20.0 - relative_distance * 200.0)
    weather_context = 20.0 if context_source == "kma_forecast" else 5.0

    score = round(evidence + precision + scenario_support + weather_context, 1)
    label = "HIGH" if score >= 80 else "MEDIUM" if score >= 50 else "LOW"
    return {
        "score": score,
        "label": label,
        "is_probability": False,
        "components": {
            "experiment_evidence": round(evidence, 1),
            "estimation_precision": round(precision, 1),
            "scenario_support": round(scenario_support, 1),
            "weather_context": weather_context,
        },
        "evidence": {
            "price_events": price_events,
            "promotion_events": promotion_events,
            "observed_paid_price_range": [int(low), int(high)],
            "scenario_paid_price": int(paid_price),
            "uses_promotion_effect": bool(scenario["discount"] > 0),
        },
        "interpretation": (
            "모델 근거의 품질을 나타내는 휴리스틱 점수이며 전략의 성공확률이 아니다."
        ),
    }
