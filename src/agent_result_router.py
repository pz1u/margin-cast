"""ToolResult 상태를 성공 정책, 부족 입력, 공통 오류 흐름으로 분기한다."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol

from .agent_runtime import AgentRunResult
from .agent_schemas import (
    AgentError,
    AgentErrorCategory,
    AgentErrorOrigin,
    AgentResponse,
    AgentResponseStatus,
    JsonObject,
    MissingInput,
    ValueSource,
)
from .response_policy import ResponsePolicyOutcome


class ResponsePolicy(Protocol):
    def evaluate(self, run_result: AgentRunResult) -> ResponsePolicyOutcome:
        ...


@dataclass(frozen=True)
class AgentResultRoutingOutcome:
    """정상 정책 결과 또는 정책을 거치지 않은 오류/부족 입력 결과."""

    agent_response: AgentResponse | None
    policy_validation: JsonObject

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_validation", deepcopy(self.policy_validation))


_INVALID_INPUT_CODES = {
    "INVALID_ARGUMENTS",
    "INVALID_HORIZON",
    "INVALID_INPUT",
    "INVALID_LOCATION",
    "INVALID_SCENARIO",
    "INVALID_SCENARIOS",
    "INVALID_SEED",
    "INVALID_SIMULATIONS",
}
_UNSUPPORTED_CODES = {"UNKNOWN_TOOL", "UNSUPPORTED", "UNSUPPORTED_MENU"}
_INSUFFICIENT_DATA_CODES = {"INSUFFICIENT_DATA", "INSUFFICIENT_FORECAST"}
_ENGINE_ERROR_CODES = {"ENGINE_ERROR", "INVALID_PANEL", "PANEL_NOT_FOUND"}


def _error_category(code: str) -> AgentErrorCategory:
    if code in _INVALID_INPUT_CODES:
        return AgentErrorCategory.INVALID_INPUT
    if code in _UNSUPPORTED_CODES:
        return AgentErrorCategory.UNSUPPORTED
    if code in _INSUFFICIENT_DATA_CODES:
        return AgentErrorCategory.INSUFFICIENT_DATA
    if code in _ENGINE_ERROR_CODES:
        return AgentErrorCategory.ENGINE_ERROR
    return AgentErrorCategory.ENGINE_ERROR


def _question_for(fields: tuple[str, ...]) -> str:
    if "scenario.bundle_price" in fields or "bundle_price" in fields:
        return "생각하신 세트 판매가는 얼마인가요?"
    if any(field in {"experiment_days", "experiment_duration"} for field in fields):
        return "실험 기간을 얼마나 잡을까요?"
    joined = ", ".join(fields)
    return f"계산에 필요한 값({joined})을 알려주세요."


class AgentResultRouter:
    """성공 ToolResult에만 Response Policy를 적용한다."""

    def __init__(
        self,
        response_policy: ResponsePolicy,
        *,
        execution_defaults: dict[str, JsonObject] | None = None,
    ) -> None:
        self.response_policy = response_policy
        self.execution_defaults = deepcopy(execution_defaults or {})

    def route(self, run_result: AgentRunResult) -> AgentResultRoutingOutcome:
        if not isinstance(run_result, AgentRunResult):
            raise TypeError("run_result는 AgentRunResult여야 합니다.")

        raw = run_result.tool_result.raw
        if raw["status"] == "ok":
            policy_outcome = self.response_policy.evaluate(run_result)
            return AgentResultRoutingOutcome(
                agent_response=policy_outcome.agent_response,
                policy_validation=policy_outcome.policy_validation,
            )

        error = raw.get("error")
        if not isinstance(error, dict):
            return self._agent_error(
                AgentErrorCategory.ENGINE_ERROR,
                "도구 오류 응답 형식을 확인할 수 없습니다.",
                original_code=None,
                details={},
                retryable=False,
            )

        original_code = error.get("code")
        message = error.get("message")
        details = error.get("details")
        retryable = error.get("retryable", False)
        if not isinstance(original_code, str) or not original_code.strip():
            original_code = None
        if not isinstance(message, str) or not message.strip():
            message = "계산 도구 실행에 실패했습니다."
        if not isinstance(details, dict):
            details = {}
        if not isinstance(retryable, bool):
            retryable = False

        if original_code == "MISSING_INPUT":
            return self._missing_input_or_error(
                run_result,
                message=message,
                details=details,
                retryable=retryable,
            )

        category = _error_category(original_code or "ENGINE_ERROR")
        if original_code in {"INVALID_SCENARIO", "INVALID_SCENARIOS"} and (
            "중복" in message or details.get("duplicate_scenarios")
        ):
            message = "같은 가격·할인 조건이 중복되어 있습니다. 중복된 대안 중 하나를 제거해주세요."

        return self._agent_error(
            category,
            message,
            original_code=original_code,
            details=details,
            retryable=retryable,
        )

    def _missing_input_or_error(
        self,
        run_result: AgentRunResult,
        *,
        message: str,
        details: JsonObject,
        retryable: bool,
    ) -> AgentResultRoutingOutcome:
        raw_fields = details.get("missing_fields")
        fields = (
            tuple(raw_fields)
            if isinstance(raw_fields, list)
            and raw_fields
            and all(isinstance(field, str) and field.strip() for field in raw_fields)
            else ()
        )
        defaults = self.execution_defaults.get(run_result.tool_call.name, {})
        user_fields = tuple(field for field in fields if field not in defaults)

        if not user_fields:
            return self._agent_error(
                AgentErrorCategory.ENGINE_ERROR,
                "실행 기본값으로 처리해야 할 입력이 도구 오류로 반환됐습니다.",
                original_code="MISSING_INPUT",
                details=details,
                retryable=retryable,
            )

        response = AgentResponse(
            status=AgentResponseStatus.NEEDS_INPUT,
            missing_input=MissingInput(
                fields=user_fields,
                reason=message,
                question=_question_for(user_fields),
                source_requirement=(ValueSource.USER,),
            ),
        )
        return AgentResultRoutingOutcome(
            agent_response=response,
            policy_validation={"status": "NOT_RUN", "reason": "MISSING_INPUT"},
        )

    @staticmethod
    def _agent_error(
        category: AgentErrorCategory,
        message: str,
        *,
        original_code: str | None,
        details: JsonObject,
        retryable: bool,
    ) -> AgentResultRoutingOutcome:
        response = AgentResponse(
            status=AgentResponseStatus.ERROR,
            error=AgentError(
                code=category,
                message=message,
                origin=AgentErrorOrigin.TOOL,
                retryable=retryable,
                original_code=original_code,
                details=details,
            ),
        )
        return AgentResultRoutingOutcome(
            agent_response=response,
            policy_validation={"status": "NOT_RUN", "reason": category.value},
        )
