"""LLM Provider와 MarginCast 도구 사이의 최소 Agent Loop."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence

from .agent_schemas import (
    AgentError,
    AgentErrorCategory,
    AgentErrorOrigin,
    AgentInput,
    AgentResponse,
    AgentResponseStatus,
    Decision,
    DecisionAction,
    LLMResponse,
    Message,
    MessageRole,
    MissingInput,
    Provenance,
    StaticCapabilities,
    ToolCall,
    ToolResult,
    ValueSource,
)
from .llm_provider import LLMProvider


ToolExecutor = Callable[[str, dict], dict]

INVALID_INPUT_CODES = {
    "INVALID_ARGUMENTS",
    "INVALID_HORIZON",
    "INVALID_SCENARIO",
    "INVALID_SCENARIOS",
    "INVALID_SEED",
    "INVALID_SIMULATIONS",
}
UNSUPPORTED_CODES = {"UNKNOWN_TOOL", "UNSUPPORTED_MENU"}


def _path_exists(value, path: str) -> bool:
    head, separator, tail = path.partition(".")
    expects_list = head.endswith("[]")
    key = head[:-2] if expects_list else head
    if not isinstance(value, dict) or key not in value or value[key] is None:
        return False
    current = value[key]
    if expects_list:
        if not isinstance(current, list) or not current:
            return False
        return all(_path_exists(item, tail) for item in current) if separator else True
    return _path_exists(current, tail) if separator else True


def _missing_question(fields: tuple[str, ...]) -> str:
    if fields == ("scenario.bundle_price",):
        return "생각하신 세트 판매가는 얼마인가요?"
    joined = ", ".join(fields)
    return f"계산에 필요한 값({joined})을 알려주세요."


def _classify_tool_error(raw: dict) -> AgentError:
    error = raw.get("error") if isinstance(raw.get("error"), dict) else {}
    original_code = error.get("code", "ENGINE_ERROR")
    if original_code in INVALID_INPUT_CODES:
        code = AgentErrorCategory.INVALID_INPUT
    elif original_code in UNSUPPORTED_CODES:
        code = AgentErrorCategory.UNSUPPORTED
    elif original_code == "INSUFFICIENT_DATA":
        code = AgentErrorCategory.INSUFFICIENT_DATA
    elif original_code == "MISSING_INPUT":
        code = AgentErrorCategory.MISSING_INPUT
    else:
        code = AgentErrorCategory.ENGINE_ERROR
    message = error.get("message") or "계산 도구를 실행하지 못했습니다."
    return AgentError(
        code=code,
        message=message,
        origin=AgentErrorOrigin.TOOL,
        retryable=bool(error.get("retryable", False)),
    )


def _missing_from_tool_error(raw: dict, call_id: str) -> MissingInput | None:
    error = raw.get("error") if isinstance(raw.get("error"), dict) else {}
    if error.get("code") != "MISSING_INPUT":
        return None
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    missing_fields = details.get("missing_fields")
    if not isinstance(missing_fields, list) or not all(
        isinstance(field_name, str) and field_name.strip()
        for field_name in missing_fields
    ):
        return None
    fields = tuple(missing_fields)
    return MissingInput(
        fields=fields,
        reason=error.get("message") or "전략 계산에 필요한 사업 입력이 없습니다.",
        question=_missing_question(fields),
        strategy_ref=call_id,
    )


def _extract_decision(tool_result: ToolResult) -> Decision | None:
    raw = tool_result.raw
    if isinstance(raw.get("recommended_action"), dict):
        decision = raw["recommended_action"]
        result_path = "recommended_action"
    elif isinstance(raw.get("decision"), dict):
        decision = raw["decision"]
        result_path = "decision"
    else:
        return None
    try:
        action = DecisionAction(decision["action"])
        reason = decision["reason"]
    except (KeyError, TypeError, ValueError):
        return None
    return Decision(
        action=action,
        reason=reason,
        source_ref=f"{tool_result.call_id}:{result_path}",
    )


class MarginCastAgentLoop:
    """한 사용자 입력에서 추가 질문, 도구 호출 또는 최종 설명까지 진행한다."""

    def __init__(
        self,
        provider: LLMProvider,
        static_capabilities: StaticCapabilities,
        tool_executor: ToolExecutor,
        max_provider_calls: int = 4,
    ) -> None:
        if max_provider_calls < 1:
            raise ValueError("max_provider_calls는 1 이상이어야 합니다.")
        self.provider = provider
        self.static_capabilities = static_capabilities
        self.tool_executor = tool_executor
        self.max_provider_calls = max_provider_calls

    def _known_tool_names(self) -> set[str]:
        return {
            schema["name"]
            for schema in self.static_capabilities.tool_schemas
            if isinstance(schema.get("name"), str)
        }

    def _apply_defaults(self, call: ToolCall) -> ToolCall:
        arguments = dict(self.static_capabilities.execution_defaults.get(call.name, {}))
        arguments.update(call.arguments)
        provenance = {
            key: Provenance(
                source=ValueSource.DEFAULT,
                ref=f"static:{call.name}.execution_defaults.{key}",
            )
            for key in self.static_capabilities.execution_defaults.get(call.name, {})
            if key not in call.arguments
        }
        provenance.update(call.argument_provenance)
        return ToolCall(
            call_id=call.call_id,
            name=call.name,
            arguments=arguments,
            argument_provenance=provenance,
        )

    def _missing_input(self, call: ToolCall) -> MissingInput | None:
        required = self.static_capabilities.required_user_inputs.get(call.name, ())
        missing = tuple(path for path in required if not _path_exists(call.arguments, path))
        if not missing:
            return None
        return MissingInput(
            fields=missing,
            reason="전략 계산에 필요한 사업 입력이 없습니다.",
            question=_missing_question(missing),
            strategy_ref=call.call_id,
        )

    def _provider_error(self, tool_results: tuple[ToolResult, ...]) -> AgentResponse:
        return AgentResponse(
            status=AgentResponseStatus.ERROR,
            error=AgentError(
                code=AgentErrorCategory.ENGINE_ERROR,
                message="LLM Provider에 연결하거나 응답을 처리하지 못했습니다.",
                origin=AgentErrorOrigin.PROVIDER,
                retryable=True,
            ),
            tool_results=tool_results,
        )

    def run(
        self,
        agent_input: AgentInput,
        history: Sequence[Message] = (),
    ) -> AgentResponse:
        messages = [*history, Message(role=MessageRole.USER, content=agent_input.text)]
        tool_results: list[ToolResult] = []
        decision = None

        for _ in range(self.max_provider_calls):
            try:
                llm_response = self.provider.generate(
                    messages,
                    self.static_capabilities.tool_schemas,
                )
            except Exception:
                return self._provider_error(tuple(tool_results))
            if not isinstance(llm_response, LLMResponse):
                return self._provider_error(tuple(tool_results))

            if llm_response.missing_input is not None:
                return AgentResponse(
                    status=AgentResponseStatus.NEEDS_INPUT,
                    explanation=llm_response.text,
                    missing_input=llm_response.missing_input,
                    tool_results=tuple(tool_results),
                )

            if llm_response.tool_calls:
                normalized_calls = tuple(
                    self._apply_defaults(call) for call in llm_response.tool_calls
                )
                messages.append(
                    Message(
                        role=MessageRole.ASSISTANT,
                        content=llm_response.text or "",
                        tool_calls=normalized_calls,
                    )
                )
                for call in normalized_calls:
                    if call.name not in self._known_tool_names():
                        return AgentResponse(
                            status=AgentResponseStatus.ERROR,
                            error=AgentError(
                                code=AgentErrorCategory.UNSUPPORTED,
                                message=f"등록되지 않은 도구입니다: {call.name}",
                                origin=AgentErrorOrigin.RUNTIME,
                            ),
                            tool_results=tuple(tool_results),
                        )
                    missing_input = self._missing_input(call)
                    if missing_input is not None:
                        return AgentResponse(
                            status=AgentResponseStatus.NEEDS_INPUT,
                            missing_input=missing_input,
                            tool_results=tuple(tool_results),
                        )
                    try:
                        raw = self.tool_executor(call.name, call.arguments)
                        tool_result = ToolResult(call.call_id, call.name, raw)
                    except Exception:
                        return AgentResponse(
                            status=AgentResponseStatus.ERROR,
                            error=AgentError(
                                code=AgentErrorCategory.ENGINE_ERROR,
                                message="계산 도구를 실행하지 못했습니다.",
                                origin=AgentErrorOrigin.TOOL,
                            ),
                            tool_results=tuple(tool_results),
                        )
                    tool_results.append(tool_result)
                    if tool_result.raw["status"] == "error":
                        missing_input = _missing_from_tool_error(
                            tool_result.raw,
                            tool_result.call_id,
                        )
                        if missing_input is not None:
                            return AgentResponse(
                                status=AgentResponseStatus.NEEDS_INPUT,
                                missing_input=missing_input,
                                tool_results=tuple(tool_results),
                            )
                        agent_error = _classify_tool_error(tool_result.raw)
                        return AgentResponse(
                            status=AgentResponseStatus.ERROR,
                            error=agent_error,
                            tool_results=tuple(tool_results),
                        )
                    extracted = _extract_decision(tool_result)
                    if extracted is not None:
                        decision = extracted
                    messages.append(
                        Message(
                            role=MessageRole.TOOL,
                            content=json.dumps(tool_result.raw, ensure_ascii=False),
                            tool_call_id=call.call_id,
                            tool_name=call.name,
                        )
                    )
                continue

            return AgentResponse(
                status=AgentResponseStatus.COMPLETED,
                decision=decision,
                explanation=llm_response.text,
                tool_results=tuple(tool_results),
            )

        return AgentResponse(
            status=AgentResponseStatus.ERROR,
            error=AgentError(
                code=AgentErrorCategory.ENGINE_ERROR,
                message="Agent 호출 한도를 초과했습니다.",
                origin=AgentErrorOrigin.RUNTIME,
            ),
            tool_results=tuple(tool_results),
        )
