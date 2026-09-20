"""가격 ToolResult에서 UI 사실을 고정하는 최소 Response Policy."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from uuid import uuid4

from .agent_runtime import AgentRunResult
from .agent_schemas import (
    AgentPresentation,
    AgentResponse,
    AgentResponseStatus,
    Decision,
    DecisionAction,
    JsonObject,
    PresentationSource,
    Provenance,
    ValueSource,
)


@dataclass(frozen=True)
class ResponsePolicyOutcome:
    """PASS일 때만 사용자에게 전달 가능한 AgentResponse를 포함한다."""

    policy_validation: JsonObject
    agent_response: AgentResponse | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_validation", deepcopy(self.policy_validation))


def _fact(value, source_ref):
    return {
        "value": deepcopy(value),
        "source": ValueSource.ENGINE.value,
        "source_ref": source_ref,
    }


def _unique(values):
    return list(dict.fromkeys(values))


_PRESENTATION_ONLY_VIOLATIONS = {
    "LLM_EXPLANATION_CONTAINS_NUMBER",
    "LLM_NEXT_ACTION_CONTAINS_NUMBER",
}

_PRICE_TOOLS = {
    "compare_price_strategies",
    "compare_price_strategies_with_forecast",
}
_WEATHER_TERMS = re.compile(r"비|강수|눈|기온|온도|습도|날씨")
_BUSINESS_EFFECT_TERMS = re.compile(r"판매량|판매|수요|매출|이익")
_CAUSAL_DIRECTION_TERMS = re.compile(
    r"때문|로\s*인해|영향|증가|감소|늘(?:어|고|었)|줄(?:어|고|었)|오르|내리"
)

_SAFE_EXPLANATIONS = {
    DecisionAction.RECOMMEND: "현재 근거에서는 실행을 검토할 수 있는 전략입니다.",
    DecisionAction.EXPERIMENT: (
        "불확실성이 남아 있어 전면 적용보다 제한된 실험으로 확인하는 것이 적절합니다."
    ),
    DecisionAction.HOLD: (
        "현재 근거만으로는 적용을 권하기 어려워 추가 데이터 확인이 필요합니다."
    ),
}

_SAFE_NEXT_ACTIONS = {
    DecisionAction.RECOMMEND: "적용 전 운영 조건과 관측 계획을 확인해주세요.",
    DecisionAction.EXPERIMENT: "제한된 범위에서 결과를 확인해주세요.",
    DecisionAction.HOLD: "추가 데이터를 확인한 뒤 다시 검토해주세요.",
}


class PriceResponsePolicy:
    """가격 비교 한 건의 ENGINE facts와 Decision 일치만 검증한다."""

    def __init__(self, recommendation_id_factory=None) -> None:
        self.recommendation_id_factory = recommendation_id_factory or (
            lambda: uuid4().hex
        )

    def _validation(
        self,
        *,
        status,
        violations,
        engine_decision,
        llm_decision_claim,
        initial_llm_policy_status=None,
        initial_violations=None,
        fallback_used=False,
        presentation_source=None,
    ):
        return {
            "status": status,
            "violations": _unique(violations),
            "engine_decision": engine_decision,
            "llm_decision_claim": llm_decision_claim,
            "presented_decision": engine_decision if status == "PASS" else None,
            "initial_llm_policy_status": initial_llm_policy_status or status,
            "initial_violations": _unique(
                violations if initial_violations is None else initial_violations
            ),
            "fallback_used": fallback_used,
            "presentation_source": (
                presentation_source.value
                if isinstance(presentation_source, PresentationSource)
                else presentation_source
            ),
        }

    @staticmethod
    def _fallback_presentation(
        engine_decision,
        evidence_quality,
        data_provenance,
        weather_context=None,
    ):
        explanation_parts = [_SAFE_EXPLANATIONS[engine_decision]]
        validation = evidence_quality.get("validation")
        if validation.get("empirically_calibrated") is False:
            explanation_parts.append(
                "근거 품질은 실제 매장 결과로 아직 보정되지 않았습니다."
            )
        if data_provenance.get("label") == "SYNTHETIC_DATA_PROTOTYPE":
            explanation_parts.append("현재 결과는 합성 데이터 기반 프로토타입입니다.")
        if (
            isinstance(weather_context, dict)
            and weather_context.get("uses_future_forecast") is True
        ):
            explanation_parts.append(
                "실제 단기예보를 미래 수요 문맥에 반영했습니다."
            )
        return " ".join(explanation_parts), _SAFE_NEXT_ACTIONS[engine_decision]

    @staticmethod
    def _contains_unvalidated_weather_causality(text):
        if not isinstance(text, str):
            return False
        return all(
            pattern.search(text) is not None
            for pattern in (
                _WEATHER_TERMS,
                _BUSINESS_EFFECT_TERMS,
                _CAUSAL_DIRECTION_TERMS,
            )
        )

    @staticmethod
    def _fallback_violations(explanation, next_action):
        violations = []
        if not isinstance(explanation, str) or not explanation.strip():
            violations.append("POLICY_FALLBACK_EXPLANATION_INVALID")
        elif re.search(r"\d", explanation):
            violations.append("POLICY_FALLBACK_EXPLANATION_CONTAINS_NUMBER")
        if not isinstance(next_action, str) or not next_action.strip():
            violations.append("POLICY_FALLBACK_NEXT_ACTION_INVALID")
        elif re.search(r"\d", next_action):
            violations.append("POLICY_FALLBACK_NEXT_ACTION_CONTAINS_NUMBER")
        return violations

    def evaluate(self, run_result: AgentRunResult) -> ResponsePolicyOutcome:
        if not isinstance(run_result, AgentRunResult):
            raise TypeError("run_result는 AgentRunResult여야 합니다.")

        violations = []
        raw = run_result.tool_result.raw
        call_id = run_result.tool_result.call_id
        final_response = run_result.final_response
        claim = final_response.decision_claim if final_response is not None else None
        claim_value = claim.value if claim is not None else None

        is_forecast_tool = (
            run_result.tool_result.tool_name
            == "compare_price_strategies_with_forecast"
        )
        if run_result.tool_result.tool_name not in _PRICE_TOOLS:
            violations.append("UNSUPPORTED_POLICY_TOOL")
        if raw.get("status") != "ok":
            violations.append("TOOL_RESULT_NOT_OK")

        recommended = raw.get("recommended_action")
        if not isinstance(recommended, dict):
            recommended = {}
            violations.append("RECOMMENDED_ACTION_MISSING")

        selected_id = recommended.get("scenario_id")
        if not isinstance(selected_id, str) or not selected_id.strip():
            selected_id = None
            violations.append("SELECTED_SCENARIO_ID_MISSING")

        strategies = raw.get("strategies")
        if not isinstance(strategies, list):
            strategies = []
            violations.append("STRATEGIES_MISSING")
        elif any(not isinstance(strategy, dict) for strategy in strategies):
            violations.append("STRATEGIES_INVALID")

        scenario_ids = [
            strategy.get("scenario_id")
            for strategy in strategies
            if isinstance(strategy, dict)
        ]
        if any(not isinstance(value, str) or not value.strip() for value in scenario_ids):
            violations.append("SCENARIO_ID_MISSING")
        valid_ids = [value for value in scenario_ids if isinstance(value, str) and value.strip()]
        if len(valid_ids) != len(set(valid_ids)):
            violations.append("DUPLICATE_SCENARIO_ID")

        selected_matches = [
            strategy
            for strategy in strategies
            if isinstance(strategy, dict) and strategy.get("scenario_id") == selected_id
        ]
        selected = selected_matches[0] if len(selected_matches) == 1 else None
        if selected_id is not None and selected is None:
            violations.append("SELECTED_SCENARIO_NOT_FOUND")

        engine_action = recommended.get("action")
        try:
            engine_decision = DecisionAction(engine_action)
        except (TypeError, ValueError):
            engine_decision = None
            violations.append("ENGINE_DECISION_INVALID")
        engine_reason = recommended.get("reason")
        if not isinstance(engine_reason, str) or not engine_reason.strip():
            violations.append("ENGINE_DECISION_REASON_INVALID")

        if claim is None:
            violations.append("LLM_DECISION_CLAIM_MISSING")
        elif engine_decision is not None and claim is not engine_decision:
            violations.append("DECISION_MISMATCH")

        facts = {}
        provenance = {}
        notices = []
        evidence_quality = None
        weather_context = raw.get("weather_context")
        data_provenance = raw.get("data_provenance")
        if not isinstance(data_provenance, dict) or not data_provenance:
            data_provenance = None
            violations.append("DATA_PROVENANCE_MISSING")
        else:
            label = data_provenance.get("label")
            if not isinstance(label, str) or not label.strip():
                violations.append("DATA_PROVENANCE_LABEL_MISSING")

        forecast_context_valid = False
        if is_forecast_tool:
            if not isinstance(weather_context, dict):
                violations.append("FORECAST_CONTEXT_MISSING")
            else:
                applied_from = weather_context.get("applied_from")
                applied_to = weather_context.get("applied_to")
                uses_forecast = weather_context.get("uses_future_forecast")
                causal_validated = weather_context.get(
                    "menu_specific_causal_effect_validated"
                )
                if (
                    weather_context.get("source") != "kma_forecast"
                    or uses_forecast is not True
                    or not isinstance(applied_from, str)
                    or not applied_from.strip()
                    or not isinstance(applied_to, str)
                    or not applied_to.strip()
                    or not isinstance(causal_validated, bool)
                ):
                    violations.append("FORECAST_CONTEXT_INVALID")
                else:
                    forecast_context_valid = True

        if selected is not None and selected_id is not None and engine_decision is not None:
            selected_path = f"strategies[scenario_id={selected_id}]"
            try:
                evidence_quality = selected["evidence_quality"]
                if not isinstance(evidence_quality, dict):
                    raise TypeError("evidence_quality는 객체여야 합니다.")
                fact_values = {
                    "selected_scenario_id": (
                        selected_id,
                        "recommended_action.scenario_id",
                    ),
                    "expected_units": (
                        selected["units"]["mean"],
                        f"{selected_path}.units.mean",
                    ),
                    "expected_contribution_profit": (
                        selected["contribution_profit"]["mean"],
                        f"{selected_path}.contribution_profit.mean",
                    ),
                    "profit_delta": (
                        selected["profit_delta"]["mean"],
                        f"{selected_path}.profit_delta.mean",
                    ),
                    "interval_80": (
                        {
                            "lower": selected["profit_delta"]["p10"],
                            "upper": selected["profit_delta"]["p90"],
                        },
                        f"{selected_path}.profit_delta[p10,p90]",
                    ),
                    "success_probability": (
                        selected["success_probability"],
                        f"{selected_path}.success_probability",
                    ),
                    "downside_risk": (
                        selected["downside_risk"],
                        f"{selected_path}.downside_risk",
                    ),
                    "evidence_quality": (
                        evidence_quality,
                        f"{selected_path}.evidence_quality",
                    ),
                    "engine_decision": (
                        engine_decision.value,
                        "recommended_action.action",
                    ),
                }
                if forecast_context_valid:
                    fact_values.update(
                        {
                            "forecast_used": (
                                weather_context["uses_future_forecast"],
                                "weather_context.uses_future_forecast",
                            ),
                            "forecast_applied_dates": (
                                {
                                    "from": weather_context["applied_from"],
                                    "to": weather_context["applied_to"],
                                },
                                "weather_context[applied_from,applied_to]",
                            ),
                            "weather_causal_effect_validated": (
                                weather_context[
                                    "menu_specific_causal_effect_validated"
                                ],
                                (
                                    "weather_context."
                                    "menu_specific_causal_effect_validated"
                                ),
                            ),
                        }
                    )
                if data_provenance is not None:
                    fact_values["data_provenance"] = (
                        data_provenance,
                        "data_provenance",
                    )
            except (KeyError, TypeError):
                violations.append("SELECTED_SCENARIO_FACTS_INVALID")
            else:
                for name, (value, path) in fact_values.items():
                    source_ref = f"{call_id}:{path}"
                    facts[name] = _fact(value, source_ref)
                    provenance[name] = Provenance(ValueSource.ENGINE, source_ref)

                selected_decision = selected.get("decision")
                selected_action = (
                    selected_decision.get("action")
                    if isinstance(selected_decision, dict)
                    else None
                )
                if selected_action != engine_decision.value:
                    violations.append("ENGINE_DECISION_INCONSISTENT")

                validation = evidence_quality.get("validation")
                calibrated = (
                    validation.get("empirically_calibrated")
                    if isinstance(validation, dict)
                    else None
                )
                if calibrated is False:
                    evidence_notice = evidence_quality.get("interpretation")
                    if not isinstance(evidence_notice, str) or not evidence_notice.strip():
                        violations.append("UNCALIBRATED_EVIDENCE_NOTICE_MISSING")
                    else:
                        notices.append(evidence_notice)
                elif not isinstance(calibrated, bool):
                    violations.append("EVIDENCE_CALIBRATION_STATUS_INVALID")

        if data_provenance is not None:
            warning = data_provenance.get("warning")
            is_synthetic = (
                data_provenance.get("label") == "SYNTHETIC_DATA_PROTOTYPE"
            )
            if is_synthetic and (
                not isinstance(warning, str) or not warning.strip()
            ):
                violations.append("SYNTHETIC_DATA_WARNING_MISSING")
            elif isinstance(warning, str) and warning.strip():
                notices.insert(0, warning)

        if forecast_context_valid:
            notices.append(
                "실제 기상청 단기예보를 "
                f"{weather_context['applied_from']}부터 "
                f"{weather_context['applied_to']}까지 미래 수요 문맥에 반영했습니다."
            )
            if (
                weather_context.get("menu_specific_causal_effect_validated")
                is False
            ):
                notices.append(
                    "날씨와 이 메뉴 판매량 사이의 인과효과는 검증되지 않았습니다."
                )

        explanation = (final_response.text or "") if final_response is not None else ""
        next_action = (
            final_response.next_action if final_response is not None else None
        )
        if final_response is None:
            violations.append("LLM_FINAL_RESPONSE_MISSING")
        if re.search(r"\d", explanation):
            violations.append("LLM_EXPLANATION_CONTAINS_NUMBER")
        if next_action is not None and re.search(r"\d", next_action):
            violations.append("LLM_NEXT_ACTION_CONTAINS_NUMBER")
        if (
            forecast_context_valid
            and weather_context.get("menu_specific_causal_effect_validated") is False
            and (
                self._contains_unvalidated_weather_causality(explanation)
                or self._contains_unvalidated_weather_causality(next_action)
            )
        ):
            violations.append("UNVALIDATED_WEATHER_CAUSAL_CLAIM")

        if any(
            fact.get("source") != ValueSource.ENGINE.value
            for fact in facts.values()
        ) or any(item.source is not ValueSource.ENGINE for item in provenance.values()):
            violations.append("FACT_SOURCE_NOT_ENGINE")
        if any(
            facts[name].get("source_ref") != item.ref
            for name, item in provenance.items()
        ):
            violations.append("FACT_PROVENANCE_MISMATCH")

        initial_violations = _unique(violations)
        engine_value = (
            engine_decision.value if engine_decision is not None else None
        )
        presentation_source = PresentationSource.LLM
        fallback_used = False

        if initial_violations:
            presentation_only = set(initial_violations).issubset(
                _PRESENTATION_ONLY_VIOLATIONS
            )
            if presentation_only:
                fallback_explanation, fallback_next_action = (
                    self._fallback_presentation(
                        engine_decision,
                        evidence_quality,
                        data_provenance,
                        weather_context,
                    )
                )
                fallback_violations = self._fallback_violations(
                    fallback_explanation,
                    fallback_next_action,
                )
                if not fallback_violations:
                    explanation = fallback_explanation
                    next_action = fallback_next_action
                    presentation_source = PresentationSource.POLICY_FALLBACK
                    fallback_used = True
                else:
                    validation = self._validation(
                        status="REJECTED",
                        violations=[*initial_violations, *fallback_violations],
                        engine_decision=engine_value,
                        llm_decision_claim=claim_value,
                        initial_llm_policy_status="REJECTED",
                        initial_violations=initial_violations,
                    )
                    return ResponsePolicyOutcome(policy_validation=validation)
            else:
                validation = self._validation(
                    status="REJECTED",
                    violations=initial_violations,
                    engine_decision=engine_value,
                    llm_decision_claim=claim_value,
                    initial_llm_policy_status="REJECTED",
                    initial_violations=initial_violations,
                )
                return ResponsePolicyOutcome(policy_validation=validation)

        validation = self._validation(
            status="PASS",
            violations=[],
            engine_decision=engine_value,
            llm_decision_claim=claim_value,
            initial_llm_policy_status=(
                "REJECTED" if initial_violations else "PASS"
            ),
            initial_violations=initial_violations,
            fallback_used=fallback_used,
            presentation_source=presentation_source,
        )
        response = AgentResponse(
            status=AgentResponseStatus.COMPLETED,
            recommendation_id=self.recommendation_id_factory(),
            facts=facts,
            fact_provenance=provenance,
            decision=Decision(
                action=engine_decision,
                reason=engine_reason,
                source_ref=f"{call_id}:recommended_action",
            ),
            explanation=explanation,
            presentation=AgentPresentation(
                next_action=next_action,
                source=presentation_source,
            ),
            notices=tuple(notices),
            policy_validation=validation,
        )
        return ResponsePolicyOutcome(
            policy_validation=validation,
            agent_response=response,
        )
