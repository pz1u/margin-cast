"""G단계 Web/API가 Agent 실행 결과를 매핑할 최소 상태 계약."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import Enum

from .agent_result_router import AgentResultRoutingOutcome
from .agent_runtime import AgentMissingInputError, AgentRunResult
from .agent_schemas import AgentResponse, AgentResponseStatus, JsonObject


class AgentWebStatus(str, Enum):
    COMPLETED = "COMPLETED"
    NEEDS_INPUT = "NEEDS_INPUT"
    REJECTED = "REJECTED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class AgentWebResult:
    execution_id: str
    status: AgentWebStatus
    recommendation_id: str | None
    agent_response: AgentResponse | None
    policy_validation: JsonObject
    timings_ms: JsonObject

    def __post_init__(self) -> None:
        if not isinstance(self.execution_id, str) or not self.execution_id.strip():
            raise ValueError("execution_id가 필요합니다.")
        if not isinstance(self.status, AgentWebStatus):
            raise TypeError("status는 AgentWebStatus여야 합니다.")
        object.__setattr__(self, "policy_validation", deepcopy(self.policy_validation))
        object.__setattr__(self, "timings_ms", deepcopy(self.timings_ms))


def map_agent_result(
    run_result: AgentRunResult,
    outcome: AgentResultRoutingOutcome,
) -> AgentWebResult:
    """Runtime/Policy 결과를 HTTP 본문에서 사용할 네 상태로 축약한다."""

    response = outcome.agent_response
    policy_status = outcome.policy_validation.get("status")
    if policy_status == "REJECTED":
        status = AgentWebStatus.REJECTED
    elif response is None:
        status = AgentWebStatus.ERROR
    elif response.status is AgentResponseStatus.COMPLETED:
        status = AgentWebStatus.COMPLETED
    elif response.status is AgentResponseStatus.NEEDS_INPUT:
        status = AgentWebStatus.NEEDS_INPUT
    else:
        status = AgentWebStatus.ERROR

    recommendation_id = (
        response.recommendation_id
        if response is not None and status is AgentWebStatus.COMPLETED
        else None
    )
    return AgentWebResult(
        execution_id=run_result.execution_id,
        status=status,
        recommendation_id=recommendation_id,
        agent_response=response,
        policy_validation=outcome.policy_validation,
        timings_ms=outcome.timings_ms or {},
    )


def map_runtime_error(error: Exception) -> AgentWebResult:
    """Tool 실행 전 MissingInput 예외를 Web의 정상 대화 상태로 변환한다."""

    execution_id = getattr(error, "execution_id", None)
    if not isinstance(execution_id, str) or not execution_id.strip():
        raise ValueError("실행 오류에 execution_id가 없습니다.")

    if isinstance(error, AgentMissingInputError):
        return AgentWebResult(
            execution_id=execution_id,
            status=AgentWebStatus.NEEDS_INPUT,
            recommendation_id=None,
            agent_response=error.agent_response,
            policy_validation={"status": "NOT_RUN", "reason": "MISSING_INPUT"},
            timings_ms={},
        )

    return AgentWebResult(
        execution_id=execution_id,
        status=AgentWebStatus.ERROR,
        recommendation_id=None,
        agent_response=None,
        policy_validation={
            "status": "NOT_RUN",
            "reason": getattr(error, "code", type(error).__name__),
        },
        timings_ms={},
    )
