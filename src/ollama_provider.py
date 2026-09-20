"""Ollama Chat API 응답을 MarginCast 공통 LLM 계약으로 변환한다."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
import json
import os
from urllib.parse import urlsplit

import requests

from .agent_schemas import (
    DecisionAction,
    JsonObject,
    LLMResponse,
    Message,
    MessageRole,
    ToolCall,
)


OLLAMA_SYSTEM_PROMPT = """당신은 MarginCast의 경영 의사결정 Agent입니다.
예상 판매량, 기대 기여이익, 가격탄력성, 개선확률, 신뢰구간, 신뢰도와 위험도를 직접 계산하거나 생성하지 마세요.
계산이 필요한 질문은 제공된 MarginCast Tool을 호출하고, 구조화된 Conversation State에 있는 사용자 확인값을 Tool 인자에 사용하세요.
location이 있는 실제 예보 요청은 compare_price_strategies_with_forecast를 사용하고 위치나 날씨를 추측하지 마세요.
구조화된 Conversation State에 location 또는 horizon_days가 있으면 같은 이름과 값을 Forecast Tool 인자에 빠짐없이 그대로 복사하세요.
Tool 인자에는 해당 Tool의 parameters에 정의된 필드만 사용하고 설명이나 추가 필드를 넣지 마세요.
값이 없는 선택 Tool 인자는 null로 보내지 말고 생략하세요.
ToolResult의 Decision은 변경하거나 재판단하지 말고 decision_claim에 그대로 사용하세요.
최종 JSON은 decision_claim, explanation, next_action 세 필드만 포함하세요.
성공확률과 근거 품질은 서로 다른 개념으로 다루세요.
합성 데이터 provenance가 있으면 합성 데이터라는 고지를 생략하지 마세요.
실제 예보를 사용했다면 예보가 미래 수요 문맥에 반영됐다고 설명하세요.
menu_specific_causal_effect_validated가 false이면 특정 날씨가 메뉴 판매량이나 이익의 증가·감소를 일으킨다고 표현하지 마세요.
최종 explanation과 next_action에는 판매량, 이익, 확률, 가격, 기간 등의 숫자를 새로 만들지 말고 정성적으로만 설명하세요.
최종 응답에는 문자 체계와 관계없이 숫자나 수사를 쓰지 말고 수량, 금액, 비율, 기간을 표현하지 마세요.
실행 기본값과 계산 공식은 추측하지 마세요.
"""
OLLAMA_PROMPT_VERSION = "ollama-price-weather-v1"


FINAL_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "decision_claim": {
            "type": "string",
            "enum": [action.value for action in DecisionAction],
        },
        "explanation": {
            "type": "string",
            "minLength": 1,
            "description": "숫자, 금액, 비율, 기간을 쓰지 않는 정성적 설명",
        },
        "next_action": {
            "type": "string",
            "minLength": 1,
            "description": "숫자, 가격, 기간을 쓰지 않는 정성적 다음 행동",
        },
    },
    "required": ["decision_claim", "explanation", "next_action"],
    "additionalProperties": False,
}


class OllamaProviderError(RuntimeError):
    """Ollama 설정, 통신 또는 공통 계약 변환 실패."""

    def __init__(self, code: str, message: str, details: JsonObject | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = deepcopy(details or {})


def _required_environment(name: str, explicit_value: str | None) -> str:
    value = explicit_value if explicit_value is not None else os.getenv(name)
    if not isinstance(value, str) or not value.strip():
        raise OllamaProviderError(
            "OLLAMA_CONFIGURATION_ERROR",
            f"{name} 환경변수가 필요합니다.",
            {"field": name},
        )
    return value.strip()


def _validate_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise OllamaProviderError(
            "OLLAMA_CONFIGURATION_ERROR",
            "OLLAMA_BASE_URL은 유효한 HTTP URL이어야 합니다.",
            {"field": "OLLAMA_BASE_URL"},
        )
    if parsed.query or parsed.fragment:
        raise OllamaProviderError(
            "OLLAMA_CONFIGURATION_ERROR",
            "OLLAMA_BASE_URL에는 query나 fragment를 사용할 수 없습니다.",
            {"field": "OLLAMA_BASE_URL"},
        )
    return value.rstrip("/")


def _ollama_tools(tools: Sequence[JsonObject]) -> list[JsonObject]:
    converted = []
    for tool in tools:
        name = tool.get("name")
        description = tool.get("description")
        parameters = tool.get("parameters")
        if (
            tool.get("type") != "function"
            or not isinstance(name, str)
            or not name.strip()
            or not isinstance(description, str)
            or not isinstance(parameters, dict)
        ):
            raise OllamaProviderError(
                "OLLAMA_TOOL_SCHEMA_ERROR",
                "공통 Tool 스키마를 Ollama 형식으로 변환할 수 없습니다.",
            )
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": deepcopy(parameters),
                },
            }
        )
    return converted


def _message_content(message: Message) -> str:
    if not message.context:
        return message.content
    state = json.dumps(message.context, ensure_ascii=False, separators=(",", ":"))
    return (
        f"{message.content}\n\n"
        "[구조화된 Conversation State: 사용자 확인값]\n"
        f"{state}"
    )


def _ollama_messages(messages: Sequence[Message]) -> list[JsonObject]:
    converted: list[JsonObject] = [
        {"role": "system", "content": OLLAMA_SYSTEM_PROMPT.strip()}
    ]
    for message in messages:
        item: JsonObject = {
            "role": message.role.value,
            "content": _message_content(message),
        }
        if message.tool_calls:
            item["tool_calls"] = [
                {
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": deepcopy(call.arguments),
                    },
                }
                for call in message.tool_calls
            ]
        if message.role is MessageRole.TOOL:
            item["tool_name"] = message.tool_name
        converted.append(item)
    return converted


class OllamaProvider:
    """Ollama의 공급자별 JSON과 HTTP 처리를 Agent Runtime에서 숨긴다."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        http_client=None,
        timeout_seconds: float = 120,
    ) -> None:
        self.base_url = _validate_base_url(
            _required_environment("OLLAMA_BASE_URL", base_url)
        )
        self.model = _required_environment("OLLAMA_MODEL", model)
        self.prompt_version = OLLAMA_PROMPT_VERSION
        if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds, (int, float)
        ) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds는 양수여야 합니다.")
        self.timeout_seconds = timeout_seconds
        self.http_client = http_client or requests.Session()
        self._request_count = 0

    def generate(
        self,
        messages: Sequence[Message],
        tools: Sequence[JsonObject],
    ) -> LLMResponse:
        if any(not isinstance(message, Message) for message in messages):
            raise TypeError("messages에는 Message만 포함할 수 있습니다.")

        ollama_tools = _ollama_tools(tools)
        payload: JsonObject = {
            "model": self.model,
            "messages": _ollama_messages(messages),
            "tools": ollama_tools,
            "stream": False,
            "think": False,
        }
        is_final_request = bool(messages) and messages[-1].role is MessageRole.TOOL
        if is_final_request:
            payload["format"] = deepcopy(FINAL_RESPONSE_SCHEMA)

        self._request_count += 1
        try:
            response = self.http_client.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            raw = response.json()
        except requests.RequestException as error:
            raise OllamaProviderError(
                "OLLAMA_REQUEST_FAILED",
                "Ollama 서버 요청에 실패했습니다.",
                {"error_type": type(error).__name__},
            ) from error
        except (TypeError, ValueError) as error:
            raise OllamaProviderError(
                "OLLAMA_RESPONSE_INVALID",
                "Ollama 응답을 JSON 객체로 읽을 수 없습니다.",
            ) from error

        return self._to_common_response(raw, tools)

    def _to_common_response(
        self,
        raw: JsonObject,
        tools: Sequence[JsonObject],
    ) -> LLMResponse:
        if not isinstance(raw, dict) or not isinstance(raw.get("message"), dict):
            raise OllamaProviderError(
                "OLLAMA_RESPONSE_INVALID",
                "Ollama 응답에 message 객체가 없습니다.",
            )

        message = raw["message"]
        raw_calls = message.get("tool_calls") or []
        if raw_calls:
            if not isinstance(raw_calls, list):
                raise OllamaProviderError(
                    "PROVIDER_INVALID_TOOL_CALL",
                    "Ollama tool_calls가 배열이 아닙니다.",
                )
            allowed_names = {
                tool.get("name")
                for tool in tools
                if isinstance(tool.get("name"), str)
            }
            calls = tuple(
                self._to_common_tool_call(raw_call, index, allowed_names)
                for index, raw_call in enumerate(raw_calls)
            )
            content = message.get("content")
            text = content if isinstance(content, str) and content.strip() else None
            return LLMResponse(text=text, tool_calls=calls)

        content = message.get("content")
        if not isinstance(content, str):
            raise OllamaProviderError(
                "OLLAMA_RESPONSE_INVALID",
                "Ollama 최종 응답 content가 문자열이 아닙니다.",
            )
        try:
            final = json.loads(content)
        except json.JSONDecodeError as error:
            raise OllamaProviderError(
                "OLLAMA_RESPONSE_INVALID",
                "Ollama 최종 응답이 구조화된 JSON이 아닙니다.",
            ) from error
        required = set(FINAL_RESPONSE_SCHEMA["required"])
        if not isinstance(final, dict) or set(final) != required:
            raise OllamaProviderError(
                "OLLAMA_RESPONSE_INVALID",
                "Ollama 최종 응답 필드가 공통 계약과 다릅니다.",
                {
                    "expected_fields": sorted(required),
                    "actual_fields": sorted(final) if isinstance(final, dict) else None,
                },
            )
        explanation = final["explanation"]
        next_action = final["next_action"]
        if not isinstance(explanation, str) or not explanation.strip():
            raise OllamaProviderError(
                "OLLAMA_RESPONSE_INVALID",
                "Ollama explanation이 비어 있습니다.",
            )
        if not isinstance(next_action, str) or not next_action.strip():
            raise OllamaProviderError(
                "OLLAMA_RESPONSE_INVALID",
                "Ollama next_action이 비어 있습니다.",
            )
        try:
            decision_claim = DecisionAction(final["decision_claim"])
        except (TypeError, ValueError) as error:
            raise OllamaProviderError(
                "OLLAMA_RESPONSE_INVALID",
                "Ollama decision_claim이 허용된 Decision이 아닙니다.",
            ) from error
        return LLMResponse(
            text=explanation.strip(),
            decision_claim=decision_claim,
            next_action=next_action.strip(),
        )

    def _to_common_tool_call(
        self,
        raw_call,
        index: int,
        allowed_names: set[str],
    ) -> ToolCall:
        function = raw_call.get("function") if isinstance(raw_call, dict) else None
        if not isinstance(function, dict):
            raise OllamaProviderError(
                "PROVIDER_INVALID_TOOL_CALL",
                "Ollama Tool Call에 function 객체가 없습니다.",
            )
        name = function.get("name")
        if not isinstance(name, str) or name not in allowed_names:
            raise OllamaProviderError(
                "PROVIDER_INVALID_TOOL_CALL",
                "Ollama가 등록되지 않은 Tool을 요청했습니다.",
                {"tool_name": name},
            )
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as error:
                raise OllamaProviderError(
                    "PROVIDER_INVALID_TOOL_CALL",
                    "Ollama Tool arguments가 올바른 JSON이 아닙니다.",
                    {"tool_name": name},
                ) from error
        if not isinstance(arguments, dict):
            raise OllamaProviderError(
                "PROVIDER_INVALID_TOOL_CALL",
                "Ollama Tool arguments는 JSON 객체여야 합니다.",
                {"tool_name": name},
            )
        raw_id = raw_call.get("id") if isinstance(raw_call, dict) else None
        call_id = (
            raw_id
            if isinstance(raw_id, str) and raw_id.strip()
            else f"ollama-{self._request_count}-{index}"
        )
        return ToolCall(call_id=call_id, name=name, arguments=arguments)
