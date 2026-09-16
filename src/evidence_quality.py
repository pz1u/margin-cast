"""관측 근거·추정 정밀도·시나리오 범위·날씨 문맥으로 근거 품질을 산정한다."""

import hashlib
import json
from types import MappingProxyType

import numpy as np


EVIDENCE_QUALITY_VERSION = "heuristic-v1"
EVIDENCE_QUALITY_VALIDATION_STATUS = "PENDING_REAL_STORE_FEEDBACK"
EVIDENCE_QUALITY_POLICY = MappingProxyType(
    {
        "price_event_points": 5.0,
        "price_event_max": 20.0,
        "nonbaseline_level_points": 5.0,
        "nonbaseline_level_max": 10.0,
        "promotion_event_points": 10.0,
        "promotion_event_max": 30.0,
        "precision_max": 30.0,
        "precision_denominator_floor": 0.05,
        "scenario_support_max": 20.0,
        "scenario_distance_penalty": 200.0,
        "weather_forecast_points": 20.0,
        "observed_history_points": 5.0,
        "high_threshold": 80.0,
        "medium_threshold": 50.0,
        "bundle_fixed_score": 35.0,
    }
)
EVIDENCE_QUALITY_VERSION_FINGERPRINTS = MappingProxyType(
    {
        "heuristic-v1": "8035bb468bd655604f9c8d3ef98957f0242591eb1d8a8320d29aa89225386637",
    }
)


def evidence_quality_policy_fingerprint(policy):
    """산식에 쓰는 정책 값만 정규화해 재현 가능한 지문을 만든다."""
    canonical = json.dumps(
        dict(policy), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


EVIDENCE_QUALITY_POLICY_FINGERPRINT = evidence_quality_policy_fingerprint(
    EVIDENCE_QUALITY_POLICY
)


def validate_evidence_quality_version(
    policy=EVIDENCE_QUALITY_POLICY,
    version=EVIDENCE_QUALITY_VERSION,
):
    """정책 값이 바뀌었는데 버전을 그대로 둔 실수를 즉시 차단한다."""
    fingerprint = evidence_quality_policy_fingerprint(policy)
    expected = EVIDENCE_QUALITY_VERSION_FINGERPRINTS.get(version)
    if expected != fingerprint:
        raise RuntimeError(
            "근거 품질 산식과 버전의 지문이 일치하지 않습니다. "
            "산식 변경 시 새 버전과 기준 지문을 등록하세요."
        )
    return fingerprint


validate_evidence_quality_version()


def _event_count(active):
    active = np.asarray(active, dtype=bool)
    if len(active) == 0:
        return 0
    return int(active[0]) + int(np.sum(active[1:] & ~active[:-1]))


def _precision_points(estimate, standard_error):
    maximum = EVIDENCE_QUALITY_POLICY["precision_max"]
    denominator = max(
        abs(float(estimate)), EVIDENCE_QUALITY_POLICY["precision_denominator_floor"]
    )
    relative_error = min(float(standard_error) / denominator, 1.0)
    return maximum * (1 - relative_error)


def calculate_evidence_quality(panel, elasticity_report, scenario, context_source):
    """근거 품질은 개선확률과 별개인 미보정 휴리스틱 점수이며 0~100 범위다."""
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

    policy = EVIDENCE_QUALITY_POLICY
    price_evidence = min(
        policy["price_event_max"], price_events * policy["price_event_points"]
    ) + min(
        policy["nonbaseline_level_max"],
        nonbaseline_levels * policy["nonbaseline_level_points"],
    )
    price_precision = _precision_points(
        elasticity_report["elasticity"], elasticity_report["robust_standard_error"]
    )
    if scenario["discount"] > 0:
        promotion_evidence = min(
            policy["promotion_event_max"],
            promotion_events * policy["promotion_event_points"],
        )
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
        scenario_support = policy["scenario_support_max"]
    else:
        nearest = low if paid_price < low else high
        relative_distance = abs(paid_price - nearest) / nearest
        scenario_support = max(
            0.0,
            policy["scenario_support_max"]
            - relative_distance * policy["scenario_distance_penalty"],
        )
    weather_context = (
        policy["weather_forecast_points"]
        if context_source == "kma_forecast"
        else policy["observed_history_points"]
    )

    score = round(evidence + precision + scenario_support + weather_context, 1)
    label = (
        "HIGH"
        if score >= policy["high_threshold"]
        else "MEDIUM"
        if score >= policy["medium_threshold"]
        else "LOW"
    )
    return {
        "version": EVIDENCE_QUALITY_VERSION,
        "formula_fingerprint": EVIDENCE_QUALITY_POLICY_FINGERPRINT,
        "score": score,
        "label": label,
        "is_probability": False,
        "validation": {
            "status": EVIDENCE_QUALITY_VALIDATION_STATUS,
            "empirically_calibrated": False,
            "statement": (
                "실제 매장 피드백이 없어 점수와 예측 정확도의 관계는 아직 검증되지 않았다."
            ),
        },
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
            "모델 근거의 품질을 나타내는 미보정 휴리스틱 점수이며 전략의 성공확률이 아니다."
        ),
    }
