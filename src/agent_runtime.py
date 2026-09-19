"""LLM Provider의 단일 Tool Call을 MarginCast Dispatcher에 연결한다."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass
import json
from time import perf_counter
from uuid import uuid4

from jsonschema import Draft202012Validator

from .agent_schemas import (
    AgentInput,
    AgentResponse,
    AgentResponseStatus,
    JsonObject,
    LLMResponse,
    Message,
    MessageRole,
    MissingInput,
    ToolCall,
    ToolResult,
    ValueSource,
)
from .agent_tool_contracts import TOOL_SCHEMAS, execute_tool
from .llm_provider import LLMProvider


ToolExecutor = Callable[[str, JsonObject], JsonObject]


class AgentRuntimeError(RuntimeError):
    """B단계 단일 호출 계약을 만족하지 않는 Provider 응답."""

    execution_id: str | None = None


class ProviderInvalidToolCallError(AgentRuntimeError):
    """Conversation State에 있는 필수 인자를 Provider가 누락한 경우."""

    code = "PROVIDER_INVALID_TOOL_CALL"

    def __init__(self, message: str, missing_fields: tuple[str, ...]) -> None:
        super().__init__(message)
        self.missing_fields = missing_fields


class AgentMissingInputError(AgentRuntimeError):
    """도구 호출 전에 사용자의 사업 입력이 실제로 부족한 경우."""

    code = "MISSING_INPUT"

    def __init__(self, missing_input: MissingInput) -> None:
        super().__init__(missing_input.question)
        self.missing_input = missing_input
        self.agent_response = AgentResponse(
            status=AgentResponseStatus.NEEDS_INPUT,
            missing_input=missing_input,
        )


@dataclass(frozen=True)
class AgentRunTiming:
    """한 Agent 실행의 관측 시간. 계산 결과에는 영향을 주지 않는다."""

    label: str
    first_provider_ms: float
    tool_execution_ms: float
    second_provider_ms: float | None
    runtime_total_ms: float

    def as_dict(self) -> JsonObject:
        return {
            "timing_label": self.label,
            "first_provider_ms": self.first_provider_ms,
            "tool_execution_ms": self.tool_execution_ms,
            "second_provider_ms": self.second_provider_ms,
            "runtime_total_ms": self.runtime_total_ms,
        }


@dataclass(frozen=True)
class AgentRunResult:
    """Response Policy 적용 전까지 관찰할 수 있는 단일 실행 결과."""

    execution_id: str
    agent_input: AgentInput
    tool_call: ToolCall
    tool_result: ToolResult
    final_response: LLMResponse | None
    messages: tuple[Message, ...]
    timing: AgentRunTiming
    model_identifier: str
    prompt_version: str


class AgentRuntime:
    """가격 Tool Call 한 번과 후속 Provider 응답 한 번을 실행한다."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        tools: Sequence[JsonObject] = TOOL_SCHEMAS,
        tool_executor: ToolExecutor = execute_tool,
        clock=perf_counter,
        execution_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.provider = provider
        self.tools = tuple(deepcopy(tool) for tool in tools)
        self.tool_executor = tool_executor
        self.clock = clock
        self.execution_id_factory = execution_id_factory or (lambda: uuid4().hex)

    @staticmethod
    def _missing_input_question(fields: tuple[str, ...]) -> str:
        if "menu_id" in fields:
            return "분석할 메뉴를 알려주세요."
        if "scenarios" in fields:
            return "비교할 가격이나 할인 조건을 알려주세요."
        return f"계산에 필요한 값({', '.join(fields)})을 알려주세요."

    def _validate_tool_call(
        self,
        tool_call: ToolCall,
        agent_input: AgentInput,
    ) -> None:
        schema = next(
            (tool for tool in self.tools if tool.get("name") == tool_call.name),
            None,
        )
        if schema is None:
            raise AgentRuntimeError(f"등록되지 않은 도구입니다: {tool_call.name}")

        errors = sorted(
            Draft202012Validator(schema["parameters"]).iter_errors(
                tool_call.arguments
            ),
            key=lambda error: tuple(str(item) for item in error.absolute_path),
        )
        if errors:
            missing_fields = []
            for error in errors:
                if error.validator != "required":
                    continue
                required = error.validator_value
                if not isinstance(required, list) or not isinstance(error.instance, dict):
                    continue
                prefix = ".".join(str(item) for item in error.absolute_path)
                missing_fields.extend(
                    f"{prefix}.{field_name}" if prefix else field_name
                    for field_name in required
                    if field_name not in error.instance
                )
            missing = tuple(dict.fromkeys(missing_fields))
            if missing:
                provided = tuple(
                    field_name
                    for field_name in missing
                    if field_name in agent_input.business_inputs
                )
                if provided:
                    raise ProviderInvalidToolCallError(
                        "Provider가 Conversation State의 필수 Tool 인자를 누락했습니다.",
                        provided,
                    )
                raise AgentMissingInputError(
                    MissingInput(
                        fields=missing,
                        reason="도구 실행에 필요한 사용자 입력이 없습니다.",
                        question=self._missing_input_question(missing),
                        source_requirement=(ValueSource.USER,),
                    )
                )
            error = errors[0]
            path = ".".join(str(item) for item in error.absolute_path) or "arguments"
            raise AgentRuntimeError(f"ToolCall 인자가 계약과 다릅니다: {path}: {error.message}")

    @staticmethod
    def _require_single_tool_call(response: LLMResponse) -> ToolCall:
        if len(response.tool_calls) != 1:
            raise AgentRuntimeError("B단계 Runtime은 Tool Call 한 건만 지원합니다.")
        return response.tool_calls[0]

    @staticmethod
    def _validate_final_response(response: LLMResponse) -> None:
        if response.tool_calls or response.missing_input is not None:
            raise AgentRuntimeError("도구 결과 뒤에는 최종 텍스트 응답이 필요합니다.")
        if response.text is None or not response.text.strip():
            raise AgentRuntimeError("최종 텍스트 응답이 비어 있습니다.")

    def run(
        self,
        agent_input: AgentInput,
        *,
        timing_label: str = "unspecified",
    ) -> AgentRunResult:
        if not isinstance(agent_input, AgentInput):
            raise TypeError("agent_input은 AgentInput이어야 합니다.")
        if not isinstance(timing_label, str) or not timing_label.strip():
            raise ValueError("timing_label은 비어 있지 않은 문자열이어야 합니다.")

        execution_id = self.execution_id_factory()
        if not isinstance(execution_id, str) or not execution_id.strip():
            raise ValueError("execution_id는 비어 있지 않은 문자열이어야 합니다.")
        execution_id = execution_id.strip()

        try:
            return self._run(agent_input, timing_label.strip(), execution_id)
        except Exception as error:
            # Web 경계가 Tool 실행 전 오류도 같은 실행 단위로 추적할 수 있게 한다.
            error.execution_id = execution_id
            raise

    def _run(
        self,
        agent_input: AgentInput,
        timing_label: str,
        execution_id: str,
    ) -> AgentRunResult:

        run_started = self.clock()
        model_identifier = getattr(
            self.provider,
            "model",
            type(self.provider).__name__,
        )
        prompt_version = getattr(self.provider, "prompt_version", "unknown")

        messages = [
            Message(
                role=MessageRole.USER,
                content=agent_input.text,
                context=agent_input.business_inputs,
            )
        ]
        first_provider_started = self.clock()
        first_response = self.provider.generate(tuple(messages), self.tools)
        first_provider_ms = (self.clock() - first_provider_started) * 1000
        if not isinstance(first_response, LLMResponse):
            raise AgentRuntimeError("Provider는 LLMResponse를 반환해야 합니다.")

        tool_call = self._require_single_tool_call(first_response)
        self._validate_tool_call(tool_call, agent_input)
        messages.append(
            Message(
                role=MessageRole.ASSISTANT,
                content=first_response.text or "",
                tool_calls=(tool_call,),
            )
        )

        tool_started = self.clock()
        raw_result = self.tool_executor(tool_call.name, deepcopy(tool_call.arguments))
        tool_execution_ms = (self.clock() - tool_started) * 1000
        tool_result = ToolResult(
            call_id=tool_call.call_id,
            tool_name=tool_call.name,
            raw=raw_result,
        )
        messages.append(
            Message(
                role=MessageRole.TOOL,
                content=json.dumps(tool_result.raw, ensure_ascii=False),
                tool_call_id=tool_call.call_id,
                tool_name=tool_call.name,
            )
        )

        if tool_result.raw["status"] == "error":
            runtime_total_ms = (self.clock() - run_started) * 1000
            return AgentRunResult(
                execution_id=execution_id,
                agent_input=agent_input,
                tool_call=tool_call,
                tool_result=tool_result,
                final_response=None,
                messages=tuple(messages),
                timing=AgentRunTiming(
                    label=timing_label,
                    first_provider_ms=first_provider_ms,
                    tool_execution_ms=tool_execution_ms,
                    second_provider_ms=None,
                    runtime_total_ms=runtime_total_ms,
                ),
                model_identifier=str(model_identifier),
                prompt_version=str(prompt_version),
            )

        second_provider_started = self.clock()
        final_response = self.provider.generate(tuple(messages), self.tools)
        second_provider_ms = (self.clock() - second_provider_started) * 1000
        if not isinstance(final_response, LLMResponse):
            raise AgentRuntimeError("Provider는 LLMResponse를 반환해야 합니다.")
        self._validate_final_response(final_response)
        messages.append(
            Message(role=MessageRole.ASSISTANT, content=final_response.text or "")
        )

        runtime_total_ms = (self.clock() - run_started) * 1000
        return AgentRunResult(
            execution_id=execution_id,
            agent_input=agent_input,
            tool_call=tool_call,
            tool_result=tool_result,
            final_response=final_response,
            messages=tuple(messages),
            timing=AgentRunTiming(
                label=timing_label,
                first_provider_ms=first_provider_ms,
                tool_execution_ms=tool_execution_ms,
                second_provider_ms=second_provider_ms,
                runtime_total_ms=runtime_total_ms,
            ),
            model_identifier=str(model_identifier),
            prompt_version=str(prompt_version),
        )
