"""검증된 추천을 기존 Feedback Tool과 연결하는 최소 Agent 계층."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from uuid import uuid4

from .agent_audit import AgentAuditStore
from .agent_runtime import AgentRunResult, ToolExecutor
from .agent_schemas import (
    AgentResponse,
    AgentResponseStatus,
    JsonObject,
    MissingInput,
    Provenance,
    ToolResult,
    ValueSource,
)
from .agent_tool_contracts import execute_tool


class AgentFeedbackError(ValueError):
    pass


@dataclass(frozen=True)
class FeedbackWorkflowOutcome:
    recommendation_id: str
    feedback_status: str
    feedback_id: str | None = None
    tool_result: ToolResult | None = None
    agent_response: AgentResponse | None = None

    def __post_init__(self) -> None:
        if self.feedback_status not in {"not_planned", "planned", "completed"}:
            raise ValueError("feedback_status를 확인하세요.")


def _missing_response(
    fields: tuple[str, ...],
    question: str,
    *,
    sources: tuple[ValueSource, ...] = (ValueSource.USER,),
) -> AgentResponse:
    return AgentResponse(
        status=AgentResponseStatus.NEEDS_INPUT,
        missing_input=MissingInput(
            fields=fields,
            reason="실험 피드백 Tool 실행에 필요한 입력 또는 출처가 없습니다.",
            question=question,
            source_requirement=sources,
        ),
    )


def _valid_provenance(
    provenance: dict[str, Provenance],
    field_name: str,
    allowed_sources: set[ValueSource],
) -> bool:
    item = provenance.get(field_name)
    return isinstance(item, Provenance) and item.source in allowed_sources


class AgentFeedbackWorkflow:
    """사용자 확인 뒤에만 Feedback Tool을 호출하고 감사 상태를 갱신한다."""

    def __init__(
        self,
        audit_store: AgentAuditStore,
        *,
        tool_executor: ToolExecutor = execute_tool,
        call_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.audit_store = audit_store
        self.tool_executor = tool_executor
        self.call_id_factory = call_id_factory or (lambda: uuid4().hex)

    @staticmethod
    def _selected_strategy(run_result: AgentRunResult) -> JsonObject:
        raw = run_result.tool_result.raw
        selected_id = raw.get("recommended_action", {}).get("scenario_id")
        selected = next(
            (
                strategy
                for strategy in raw.get("strategies", [])
                if strategy.get("scenario_id") == selected_id
            ),
            None,
        )
        if selected is None:
            raise AgentFeedbackError("추천 시나리오 원본을 찾을 수 없습니다.")
        return selected

    @staticmethod
    def _plan_payload(
        run_result: AgentRunResult,
        planning_inputs: JsonObject,
        horizon_days: int,
    ) -> JsonObject:
        raw = run_result.tool_result.raw
        selected = AgentFeedbackWorkflow._selected_strategy(run_result)
        evidence = selected["evidence_quality"]
        data_provenance = raw["data_provenance"]
        weather = raw["weather_context"]
        interval = lambda values: {
            name: values[name] for name in ("mean", "p10", "p90")
        }
        return {
            "experiment_name": planning_inputs.get(
                "experiment_name", selected["name"]
            ),
            "menu_id": raw["request"]["menu_id"],
            "start_date": planning_inputs["start_date"],
            "end_date": planning_inputs["end_date"],
            "scenario": {
                "name": selected["name"],
                "list_price": selected["list_price"],
                "discount": selected["discount"],
            },
            "prediction": {
                "horizon_days": horizon_days,
                "units": interval(selected["units"]),
                "contribution_profit": interval(selected["contribution_profit"]),
                "profit_delta": interval(selected["profit_delta"]),
            },
            "decision_context": {
                "engine_version": raw["engine_version"],
                "operation": run_result.tool_call.name,
                "data_provenance": {
                    "source_type": data_provenance["source_type"],
                    "dataset_version": data_provenance["dataset_version"],
                    "uses_actual_store_data": data_provenance[
                        "uses_actual_store_data"
                    ],
                },
                "weather_context": {
                    "source": weather["source"],
                    "menu_specific_causal_effect_validated": weather[
                        "menu_specific_causal_effect_validated"
                    ],
                },
                "evidence_quality": {
                    "version": evidence["version"],
                    "formula_fingerprint": evidence["formula_fingerprint"],
                    "label": evidence["label"],
                    "score": evidence["score"],
                    "validation_status": evidence["validation"]["status"],
                },
                "decision_action": raw["recommended_action"]["action"],
            },
        }

    def create_experiment_plan(
        self,
        run_result: AgentRunResult,
        agent_response: AgentResponse,
        *,
        execution_confirmed: bool,
        planning_inputs: JsonObject | None = None,
        provenance: dict[str, Provenance] | None = None,
    ) -> FeedbackWorkflowOutcome:
        recommendation_id = agent_response.recommendation_id
        if agent_response.status is not AgentResponseStatus.COMPLETED or not recommendation_id:
            raise AgentFeedbackError("PASS 추천 응답이 필요합니다.")
        if not isinstance(execution_confirmed, bool):
            raise TypeError("execution_confirmed는 boolean이어야 합니다.")
        audit = self.audit_store.get(recommendation_id)
        if audit is None:
            raise AgentFeedbackError("추천 감사 기록이 필요합니다.")
        if not execution_confirmed:
            return FeedbackWorkflowOutcome(
                recommendation_id=recommendation_id,
                feedback_status="not_planned",
            )

        planning_inputs = deepcopy(planning_inputs or {})
        provenance = dict(provenance or {})
        allowed_sources = {ValueSource.USER, ValueSource.ENGINE, ValueSource.DEFAULT}
        missing_dates = tuple(
            field
            for field in ("start_date", "end_date")
            if field not in planning_inputs
            or not _valid_provenance(provenance, field, allowed_sources)
        )
        if missing_dates:
            response = _missing_response(
                missing_dates,
                "실험 기간의 시작일과 종료일을 알려주세요.",
                sources=(ValueSource.USER, ValueSource.ENGINE, ValueSource.DEFAULT),
            )
            return FeedbackWorkflowOutcome(
                recommendation_id=recommendation_id,
                feedback_status="not_planned",
                agent_response=response,
            )

        horizon_days = run_result.tool_result.raw.get("request", {}).get(
            "horizon_days"
        )
        if not isinstance(horizon_days, int):
            horizon_days = planning_inputs.get("horizon_days")
            if not isinstance(horizon_days, int) or not _valid_provenance(
                provenance,
                "horizon_days",
                allowed_sources,
            ):
                response = _missing_response(
                    ("experiment_days",),
                    "실험 기간을 얼마나 잡을까요?",
                    sources=(ValueSource.USER, ValueSource.ENGINE, ValueSource.DEFAULT),
                )
                return FeedbackWorkflowOutcome(
                    recommendation_id=recommendation_id,
                    feedback_status="not_planned",
                    agent_response=response,
                )

        payload = self._plan_payload(run_result, planning_inputs, horizon_days)
        raw_result = self.tool_executor("create_experiment_plan", deepcopy(payload))
        tool_result = ToolResult(
            call_id=self.call_id_factory(),
            tool_name="create_experiment_plan",
            raw=raw_result,
        )
        if raw_result["status"] != "ok":
            return FeedbackWorkflowOutcome(
                recommendation_id=recommendation_id,
                feedback_status="not_planned",
                tool_result=tool_result,
            )
        feedback_id = raw_result["plan"]["feedback_id"]
        self.audit_store.update_feedback(
            recommendation_id,
            feedback_id=feedback_id,
            feedback_status="planned",
            called_tool_name="create_experiment_plan",
        )
        return FeedbackWorkflowOutcome(
            recommendation_id=recommendation_id,
            feedback_id=feedback_id,
            feedback_status="planned",
            tool_result=tool_result,
        )

    def record_experiment_result(
        self,
        recommendation_id: str,
        feedback_id: str,
        *,
        baseline_method: str | None,
        actual: JsonObject | None,
        provenance: dict[str, Provenance] | None = None,
    ) -> FeedbackWorkflowOutcome:
        audit = self.audit_store.get(recommendation_id)
        if audit is None or audit.get("feedback_id") != feedback_id:
            raise AgentFeedbackError("추천과 feedback_id 연결을 확인할 수 없습니다.")
        actual = deepcopy(actual or {})
        provenance = dict(provenance or {})
        required_actual = (
            "units",
            "contribution_profit",
            "baseline_contribution_profit",
        )
        missing = [
            f"actual.{field}"
            for field in required_actual
            if field not in actual
            or not _valid_provenance(
                provenance,
                f"actual.{field}",
                {ValueSource.USER},
            )
        ]
        if baseline_method is None or not _valid_provenance(
            provenance,
            "baseline_method",
            {ValueSource.USER},
        ):
            missing.append("baseline_method")
        if missing:
            labels = {
                "actual.units": "실제 판매량",
                "actual.contribution_profit": "실제 기여이익",
                "actual.baseline_contribution_profit": "기준선 기여이익",
                "baseline_method": "기준선 산정 방식",
            }
            response = _missing_response(
                tuple(missing),
                "다음 값을 알려주세요: "
                + ", ".join(labels[field] for field in missing),
            )
            return FeedbackWorkflowOutcome(
                recommendation_id=recommendation_id,
                feedback_id=feedback_id,
                feedback_status="planned",
                agent_response=response,
            )

        payload = {
            "feedback_id": feedback_id,
            "baseline_method": baseline_method,
            "actual": actual,
        }
        raw_result = self.tool_executor("record_experiment_result", deepcopy(payload))
        tool_result = ToolResult(
            call_id=self.call_id_factory(),
            tool_name="record_experiment_result",
            raw=raw_result,
        )
        if raw_result["status"] != "ok":
            return FeedbackWorkflowOutcome(
                recommendation_id=recommendation_id,
                feedback_id=feedback_id,
                feedback_status="planned",
                tool_result=tool_result,
            )
        self.audit_store.update_feedback(
            recommendation_id,
            feedback_id=feedback_id,
            feedback_status="completed",
            called_tool_name="record_experiment_result",
        )
        return FeedbackWorkflowOutcome(
            recommendation_id=recommendation_id,
            feedback_id=feedback_id,
            feedback_status="completed",
            tool_result=tool_result,
        )

    def list_pending_experiments(self, menu_id: str | None = None) -> JsonObject:
        arguments = {} if menu_id is None else {"menu_id": menu_id}
        return self.tool_executor("list_pending_experiments", arguments)

    def get_feedback_summary(self, menu_id: str | None = None) -> JsonObject:
        arguments = {} if menu_id is None else {"menu_id": menu_id}
        return self.tool_executor("get_feedback_summary", arguments)
