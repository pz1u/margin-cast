"""기대이익·개선확률·하방 위험·신뢰도로 전략의 실행 단계를 정한다."""


CONFIDENCE_WEIGHT = {"LOW": 0.5, "MEDIUM": 0.75, "HIGH": 1.0}
ACTION_PRIORITY = {"HOLD": 0, "EXPERIMENT": 1, "RECOMMEND": 2}


def assess_strategy(strategy):
    if strategy.get("is_reference"):
        raise ValueError("기준 전략은 의사결정 후보로 평가하지 않습니다.")
    expected = float(strategy["profit_delta"]["mean"])
    lower_80 = float(strategy["profit_delta"]["p10"])
    probability = float(strategy["success_probability"])
    confidence = strategy["confidence"]
    label = confidence["label"]
    confidence_weight = CONFIDENCE_WEIGHT[label]
    downside_penalty = max(0.0, -lower_80)
    decision_value = (
        expected * confidence_weight * (0.5 + 0.5 * probability) - downside_penalty
    )

    if expected <= 0 or probability < 0.5:
        action = "HOLD"
        reason = "기대이익이 양수가 아니거나 개선확률이 50% 미만이다."
    elif label == "LOW" or lower_80 < 0 or probability < 0.75:
        action = "EXPERIMENT"
        reason = "기대 개선은 있으나 근거·하방 범위·개선확률 중 하나가 본 실행 기준에 못 미친다."
    else:
        action = "RECOMMEND"
        reason = "개선확률이 75% 이상이고 80% 범위의 하한이 0 이상이며 신뢰도가 MEDIUM 이상이다."
    return {
        "action": action,
        "decision_value": float(decision_value),
        "unit": "KRW risk-adjusted heuristic",
        "is_probability": False,
        "downside_risk": bool(lower_80 < 0),
        "reason": reason,
        "inputs": {
            "expected_profit_delta": expected,
            "profit_delta_p10": lower_80,
            "success_probability": probability,
            "confidence_label": label,
            "confidence_score": confidence["score"],
        },
    }


def rank_strategies(strategies):
    candidates = []
    for strategy in strategies:
        if strategy.get("is_reference"):
            continue
        strategy["decision"] = assess_strategy(strategy)
        candidates.append(strategy)
    return sorted(
        candidates,
        key=lambda row: (
            ACTION_PRIORITY[row["decision"]["action"]],
            row["decision"]["decision_value"],
        ),
        reverse=True,
    )
