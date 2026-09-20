"""OpenAI Responses API를 MarginCast 공통 LLM 계약으로 변환한다."""

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
from .llm_prompt import FINAL_RESPONSE_SCHEMA, MARGINCAST_SYSTEM_PROMPT


OPENAI_PROMPT_VERSION = "openai-price-weather-v1"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


class OpenAIProviderError(RuntimeError):
    """OpenAI 설정, 통신 또는 공통 계약 변환 실패."""

    def __init__(self, code: str, message: str, details: JsonObject | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = deepcopy(details or {})


def _required_environment(name: str, explicit_value: str | None) -> str:
    value = explicit_value if explicit_value is not None else os.getenv(name)
    if not isinstance(value, str) or not value.strip():
        raise OpenAIProviderError(
            "OPENAI_CONFIGURATION_ERROR",
            f"{name} 환경변수가 필요합니다.",
            {"field": name},
        )
    return value.strip()


def _validate_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise OpenAIProviderError(
            "OPENAI_CONFIGURATION_ERROR",
            "OpenAI API URL은 유효한 HTTPS URL이어야 합니다.",
            {"field": "OPENAI_BASE_URL"},
        )
    if parsed.query or parsed.fragment:
        raise OpenAIProviderError(
            "OPENAI_CONFIGURATION_ERROR",
            "OpenAI API URL에는 query나 fragment를 사용할 수 없습니다.",
            {"field": "OPENAI_BASE_URL"},
        )
    return value.rstrip("/")


def _message_content(message: Message) -> str:
    if not message.context:
        return message.content
    state = json.dumps(message.context, ensure_ascii=False, separators=(",", ":"))
    return (
        f"{message.content}\n\n"
        "[구조화된 Conversation State: 사용자 확인값]\n"
        f"{state}"
    )


def _openai_tools(tools: Sequence[JsonObject]) -> list[JsonObject]:
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
            raise OpenAIProviderError(
                "OPENAI_TOOL_SCHEMA_ERROR",
                "공통 Tool 스키마를 OpenAI 형식으로 변환할 수 없습니다.",
            )
        converted.append(
            {
                "type": "function",
                "name": name,
                "description": description,
                "parameters": deepcopy(parameters),
                # 현재 공통 Schema에는 실행 기본값처럼 선택 필드가 있다.
                # OpenAI strict function schema는 모든 속성을 required로 요구하므로
                # Runtime의 JSON Schema 검증을 최종 경계로 유지한다.
                "strict": False,
            }
        )
    return converted


def _openai_input(
    messages: Sequence[Message],
    prior_output_items: Sequence[JsonObject] = (),
) -> list[JsonObject]:
    converted: list[JsonObject] = []
    prior_inserted = False
    for message in messages:
        if message.role is MessageRole.TOOL:
            converted.append(
                {
                    "type": "function_call_output",
                    "call_id": message.tool_call_id,
                    "output": message.content,
                }
            )
            continue
        if message.role is MessageRole.ASSISTANT and message.tool_calls:
            if prior_output_items and not prior_inserted:
                converted.extend(deepcopy(tuple(prior_output_items)))
                prior_inserted = True
            else:
                if message.content.strip():
                    converted.append({"role": "assistant", "content": message.content})
                converted.extend(
                    {
                        "type": "function_call",
                        "call_id": call.call_id,
                        "name": call.name,
                        "arguments": json.dumps(
                            call.arguments,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    }
                    for call in message.tool_calls
                )
            continue
        converted.append(
            {
                "role": message.role.value,
                "content": _message_content(message),
            }
        )
    return converted


def _structured_output_schema() -> JsonObject:
    schema = deepcopy(FINAL_RESPONSE_SCHEMA)
    for field in schema["properties"].values():
        if isinstance(field, dict):
            field.pop("minLength", None)
    return schema


def _safe_provider_error(raw: object) -> JsonObject:
    if not isinstance(raw, dict):
        return {}
    error = raw.get("error")
    if not isinstance(error, dict):
        return {}
    safe = {}
    for source_name, target_name in (
        ("code", "provider_error_code"),
        ("type", "provider_error_type"),
    ):
        value = error.get(source_name)
        if isinstance(value, str) and value:
            safe[target_name] = value
    return safe


def _http_error_code(status: int, details: JsonObject) -> str:
    provider_code = str(details.get("provider_error_code", "")).lower()
    if status in {401, 403}:
        return "OPENAI_AUTHENTICATION_ERROR"
    if status == 429:
        return "OPENAI_RATE_LIMIT"
    if status == 404 or provider_code in {"model_not_found", "unsupported_model"}:
        return "OPENAI_UNSUPPORTED_MODEL"
    return "OPENAI_REQUEST_FAILED"


def _output_text(raw: JsonObject) -> str:
    direct = raw.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    parts = []
    output = raw.get("output")
    if not isinstance(output, list):
        return ""
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "refusal":
                raise OpenAIProviderError(
                    "OPENAI_RESPONSE_REFUSED",
                    "OpenAI 모델이 요청 처리를 거부했습니다.",
                )
            text = part.get("text")
            if part.get("type") == "output_text" and isinstance(text, str):
                parts.append(text)
    return "".join(parts).strip()


class OpenAIProvider:
    """OpenAI Responses API 세부 형식을 Agent Runtime에서 숨긴다."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str = DEFAULT_OPENAI_BASE_URL,
        http_client=None,
        timeout_seconds: float = 120,
    ) -> None:
        self.api_key = _required_environment("OPENAI_API_KEY", api_key)
        self.model = _required_environment("OPENAI_MODEL", model)
        self.base_url = _validate_base_url(base_url)
        self.prompt_version = OPENAI_PROMPT_VERSION
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds는 양수여야 합니다.")
        self.timeout_seconds = timeout_seconds
        self.http_client = http_client or requests.Session()
        self.usage_records: list[JsonObject] = []
        self._pending_output_items: tuple[JsonObject, ...] = ()

    @property
    def usage_totals(self) -> JsonObject:
        totals = {
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
        for usage in self.usage_records:
            for name in totals:
                value = usage.get(name, 0)
                if isinstance(value, int) and not isinstance(value, bool):
                    totals[name] += value
        return totals

    def _record_usage(self, raw: JsonObject) -> None:
        usage = raw.get("usage")
        if not isinstance(usage, dict):
            return
        details = usage.get("input_tokens_details")
        cached = details.get("cached_tokens", 0) if isinstance(details, dict) else 0
        self.usage_records.append(
            {
                "input_tokens": usage.get("input_tokens", 0),
                "cached_input_tokens": cached,
                "output_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            }
        )

    def _post(self, payload: JsonObject) -> JsonObject:
        try:
            response = self.http_client.post(
                f"{self.base_url}/responses",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.Timeout as error:
            raise OpenAIProviderError(
                "OPENAI_TIMEOUT",
                "OpenAI 응답 대기 시간이 초과되었습니다.",
                {"error_type": type(error).__name__},
            ) from error
        except requests.RequestException as error:
            raise OpenAIProviderError(
                "OPENAI_NETWORK_ERROR",
                "OpenAI API 요청에 실패했습니다.",
                {"error_type": type(error).__name__},
            ) from error

        status = getattr(response, "status_code", 200)
        try:
            raw = response.json()
        except (TypeError, ValueError) as error:
            raise OpenAIProviderError(
                "OPENAI_RESPONSE_INVALID",
                "OpenAI 응답을 JSON 객체로 읽을 수 없습니다.",
                {"http_status": status},
            ) from error
        if not isinstance(raw, dict):
            raise OpenAIProviderError(
                "OPENAI_RESPONSE_INVALID",
                "OpenAI 응답은 JSON 객체여야 합니다.",
                {"http_status": status},
            )
        if status >= 400:
            details = {"http_status": status, **_safe_provider_error(raw)}
            raise OpenAIProviderError(
                _http_error_code(status, details),
                "OpenAI API가 요청을 처리하지 못했습니다.",
                details,
            )
        if raw.get("status") in {"failed", "cancelled"}:
            raise OpenAIProviderError(
                "OPENAI_REQUEST_FAILED",
                "OpenAI 응답 생성이 실패했습니다.",
                _safe_provider_error(raw),
            )
        if raw.get("status") == "incomplete":
            details = raw.get("incomplete_details")
            raise OpenAIProviderError(
                "OPENAI_RESPONSE_INCOMPLETE",
                "OpenAI 응답이 완료되지 않았습니다.",
                deepcopy(details) if isinstance(details, dict) else {},
            )
        self._record_usage(raw)
        return raw

    def generate(
        self,
        messages: Sequence[Message],
        tools: Sequence[JsonObject],
    ) -> LLMResponse:
        if any(not isinstance(message, Message) for message in messages):
            raise TypeError("messages에는 Message만 포함할 수 있습니다.")

        tool_definitions = _openai_tools(tools)
        is_final_request = bool(messages) and messages[-1].role is MessageRole.TOOL
        payload: JsonObject = {
            "model": self.model,
            "instructions": MARGINCAST_SYSTEM_PROMPT.strip(),
            "input": _openai_input(messages, self._pending_output_items),
            "tools": tool_definitions,
            "parallel_tool_calls": False,
            "store": False,
        }
        if is_final_request:
            payload["tool_choice"] = "none"
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "margincast_agent_response",
                    "schema": _structured_output_schema(),
                    "strict": True,
                }
            }
        else:
            payload["tool_choice"] = "required"

        raw = self._post(payload)
        if not is_final_request:
            output = raw.get("output")
            calls = (
                [
                    item
                    for item in output
                    if isinstance(item, dict) and item.get("type") == "function_call"
                ]
                if isinstance(output, list)
                else []
            )
            if len(calls) != 1:
                raise OpenAIProviderError(
                    "PROVIDER_INVALID_TOOL_CALL",
                    "OpenAI 응답에는 정확히 하나의 Tool Call이 필요합니다.",
                    {"tool_call_count": len(calls)},
                )
            call = calls[0]
            name = call.get("name")
            call_id = call.get("call_id")
            if (
                not isinstance(name, str)
                or name not in {tool["name"] for tool in tools}
                or not isinstance(call_id, str)
                or not call_id.strip()
            ):
                raise OpenAIProviderError(
                    "PROVIDER_INVALID_TOOL_CALL",
                    "OpenAI가 등록되지 않았거나 식별할 수 없는 Tool을 호출했습니다.",
                )
            arguments_text = call.get("arguments")
            if not isinstance(arguments_text, str):
                raise OpenAIProviderError(
                    "PROVIDER_INVALID_TOOL_CALL",
                    "OpenAI Tool arguments는 JSON 문자열이어야 합니다.",
                )
            try:
                arguments = json.loads(arguments_text)
            except json.JSONDecodeError as error:
                raise OpenAIProviderError(
                    "PROVIDER_INVALID_TOOL_CALL",
                    "OpenAI Tool arguments를 JSON으로 읽을 수 없습니다.",
                ) from error
            if not isinstance(arguments, dict):
                raise OpenAIProviderError(
                    "PROVIDER_INVALID_TOOL_CALL",
                    "OpenAI Tool arguments는 JSON 객체여야 합니다.",
                )
            self._pending_output_items = tuple(
                deepcopy(item)
                for item in output
                if isinstance(item, dict)
            )
            return LLMResponse(
                tool_calls=(
                    ToolCall(
                        call_id=call_id,
                        name=name,
                        arguments=arguments,
                    ),
                )
            )

        self._pending_output_items = ()
        content = _output_text(raw)
        if not content:
            raise OpenAIProviderError(
                "OPENAI_RESPONSE_INVALID",
                "OpenAI 최종 응답이 비어 있습니다.",
            )
        try:
            structured = json.loads(content)
        except json.JSONDecodeError as error:
            raise OpenAIProviderError(
                "OPENAI_RESPONSE_INVALID",
                "OpenAI 최종 응답이 JSON이 아닙니다.",
            ) from error
        if not isinstance(structured, dict) or set(structured) != {
            "decision_claim",
            "explanation",
            "next_action",
        }:
            raise OpenAIProviderError(
                "OPENAI_RESPONSE_INVALID",
                "OpenAI 최종 응답이 공통 응답 계약과 다릅니다.",
            )
        try:
            return LLMResponse(
                text=structured["explanation"],
                decision_claim=DecisionAction(structured["decision_claim"]),
                next_action=structured["next_action"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise OpenAIProviderError(
                "OPENAI_RESPONSE_INVALID",
                "OpenAI 최종 응답 필드가 공통 응답 계약과 다릅니다.",
            ) from error