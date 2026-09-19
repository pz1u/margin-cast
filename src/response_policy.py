"""가격 ToolResult에서 UI 사실을 고정하는 최소 Response Policy."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re

from .agent_runtime import AgentRunResult
from .agent_schemas import (
    AgentResponse,
    AgentResponseStatus,
    Decision,
    DecisionAction,
    JsonObject,
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


class PriceResponsePolicy:
    """가격 비교 한 건의 ENGINE facts와 Decision 일치만 검증한다."""

    def _validation(
        self,
        *,
        status,
        violations,
        engine_decision,
        llm_decision_claim,
    ):
        return {
            "status": status,
            "violations": _unique(violations),
            "engine_decision": engine_decision,
            "llm_decision_claim": llm_decision_claim,
            "presented_decision": engine_decision if status == "PASS" else None,
        }

    def evaluate(self, run_result: AgentRunResult) -> ResponsePolicyOutcome:
        if not isinstance(run_result, AgentRunResult):
            raise TypeError("run_result는 AgentRunResult여야 합니다.")

        violations = []
        raw = run_result.tool_result.raw
        call_id = run_result.tool_result.call_id
        final_response = run_result.final_response
        claim = final_response.decision_claim if final_response is not None else None
        claim_value = claim.value if claim is not None else None

        if run_result.tool_result.tool_name != "compare_price_strategies":
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
        data_provenance = raw.get("data_provenance")
        if not isinstance(data_provenance, dict) or not data_provenance:
            data_provenance = None
            violations.append("DATA_PROVENANCE_MISSING")
        else:
            label = data_provenance.get("label")
            if not isinstance(label, str) or not label.strip():
                violations.append("DATA_PROVENANCE_LABEL_MISSING")

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

        explanation = (final_response.text or "") if final_response is not None else ""
        if final_response is None:
            violations.append("LLM_FINAL_RESPONSE_MISSING")
        if re.search(r"\d", explanation):
            violations.append("LLM_EXPLANATION_CONTAINS_NUMBER")

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

        if violations:
            validation = self._validation(
                status="REJECTED",
                violations=violations,
                engine_decision=(
                    engine_decision.value if engine_decision is not None else None
                ),
                llm_decision_claim=claim_value,
            )
            return ResponsePolicyOutcome(policy_validation=validation)

        validation = self._validation(
            status="PASS",
            violations=[],
            engine_decision=engine_decision.value,
            llm_decision_claim=claim_value,
        )
        response = AgentResponse(
            status=AgentResponseStatus.COMPLETED,
            facts=facts,
            fact_provenance=provenance,
            decision=Decision(
                action=engine_decision,
                reason=engine_reason,
                source_ref=f"{call_id}:recommended_action",
            ),
            explanation=explanation,
            notices=tuple(notices),
            policy_validation=validation,
        )
        return ResponsePolicyOutcome(
            policy_validation=validation,
            agent_response=response,
        )
