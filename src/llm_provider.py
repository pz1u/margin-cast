"""공급자 SDK를 Agent Loop에서 분리하는 LLM Provider 계약."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from .agent_schemas import JsonObject, LLMResponse, Message


@runtime_checkable
class LLMProvider(Protocol):
    """공급자별 응답을 공통 LLMResponse로 변환한다."""

    def generate(
        self,
        messages: Sequence[Message],
        tools: Sequence[JsonObject],
    ) -> LLMResponse:
        """대화와 정적 도구 계약을 받아 한 번의 모델 응답을 반환한다."""
        ...
