"""가격 Tool Call 세로 흐름을 검증하는 두 응답짜리 Mock Provider."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy

from .agent_schemas import JsonObject, LLMResponse, Message, MessageRole, ToolCall


def _default_price_tool_call() -> ToolCall:
    return ToolCall(
        call_id="mock-price-call-1",
        name="compare_price_strategies",
        arguments={
            "menu_id": "M01",
            "scenarios": [
                {
                    "name": "9,500원 가격 인상",
                    "list_price": 9500,
                    "discount": 0,
                }
            ],
        },
    )


class MockLLMProvider:
    """자연어 해석 없이 준비된 Tool Call과 최종 문자열을 순서대로 반환한다."""

    def __init__(
        self,
        tool_call: ToolCall | None = None,
        final_text: str = "tool result received",
    ) -> None:
        self.tool_call = tool_call or _default_price_tool_call()
        self.final_text = final_text
        self.requests: list[
            tuple[tuple[Message, ...], tuple[JsonObject, ...]]
        ] = []

    def generate(
        self,
        messages: Sequence[Message],
        tools: Sequence[JsonObject],
    ) -> LLMResponse:
        self.requests.append(
            (
                tuple(deepcopy(tuple(messages))),
                tuple(deepcopy(tuple(tools))),
            )
        )
        if len(self.requests) == 1:
            return LLMResponse(tool_calls=(self.tool_call,))
        if len(self.requests) == 2:
            if not messages or messages[-1].role is not MessageRole.TOOL:
                raise RuntimeError("두 번째 Mock 호출에는 ToolResult 메시지가 필요합니다.")
            return LLMResponse(text=self.final_text)
        raise RuntimeError("MockLLMProvider는 한 실행에서 두 번만 호출할 수 있습니다.")
