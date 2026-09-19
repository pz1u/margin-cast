"""LLM Provider의 단일 Tool Call을 MarginCast Dispatcher에 연결한다."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass
import json

from jsonschema import Draft202012Validator

from .agent_schemas import (
    AgentInput,
    JsonObject,
    LLMResponse,
    Message,
    MessageRole,
    ToolCall,
    ToolResult,
)
from .agent_tool_contracts import TOOL_SCHEMAS, execute_tool
from .llm_provider import LLMProvider


ToolExecutor = Callable[[str, JsonObject], JsonObject]


class AgentRuntimeError(RuntimeError):
    """B단계 단일 호출 계약을 만족하지 않는 Provider 응답."""


@dataclass(frozen=True)
class AgentRunResult:
    """Response Policy 적용 전까지 관찰할 수 있는 단일 실행 결과."""

    agent_input: AgentInput
    tool_call: ToolCall
    tool_result: ToolResult
    final_response: LLMResponse | None
    messages: tuple[Message, ...]


class AgentRuntime:
    """가격 Tool Call 한 번과 후속 Provider 응답 한 번을 실행한다."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        tools: Sequence[JsonObject] = TOOL_SCHEMAS,
        tool_executor: ToolExecutor = execute_tool,
    ) -> None:
        self.provider = provider
        self.tools = tuple(deepcopy(tool) for tool in tools)
        self.tool_executor = tool_executor

    def _validate_tool_call(self, tool_call: ToolCall) -> None:
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

    def run(self, agent_input: AgentInput) -> AgentRunResult:
        if not isinstance(agent_input, AgentInput):
            raise TypeError("agent_input은 AgentInput이어야 합니다.")

        messages = [Message(role=MessageRole.USER, content=agent_input.text)]
        first_response = self.provider.generate(tuple(messages), self.tools)
        if not isinstance(first_response, LLMResponse):
            raise AgentRuntimeError("Provider는 LLMResponse를 반환해야 합니다.")

        tool_call = self._require_single_tool_call(first_response)
        self._validate_tool_call(tool_call)
        messages.append(
            Message(
                role=MessageRole.ASSISTANT,
                content=first_response.text or "",
                tool_calls=(tool_call,),
            )
        )

        raw_result = self.tool_executor(tool_call.name, deepcopy(tool_call.arguments))
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
            return AgentRunResult(
                agent_input=agent_input,
                tool_call=tool_call,
                tool_result=tool_result,
                final_response=None,
                messages=tuple(messages),
            )

        final_response = self.provider.generate(tuple(messages), self.tools)
        if not isinstance(final_response, LLMResponse):
            raise AgentRuntimeError("Provider는 LLMResponse를 반환해야 합니다.")
        self._validate_final_response(final_response)
        messages.append(
            Message(role=MessageRole.ASSISTANT, content=final_response.text or "")
        )

        return AgentRunResult(
            agent_input=agent_input,
            tool_call=tool_call,
            tool_result=tool_result,
            final_response=final_response,
            messages=tuple(messages),
        )
