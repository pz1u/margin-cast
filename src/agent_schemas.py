"""MarginCast Agent와 LLM Provider가 공유하는 최소 데이터 계약."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


JsonObject = dict[str, Any]


class ValueSource(str, Enum):
    """사업 값과 설명이 처음 만들어진 위치."""

    USER = "USER"
    ENGINE = "ENGINE"
    DEFAULT = "DEFAULT"
    LLM = "LLM"


class AgentErrorCategory(str, Enum):
    MISSING_INPUT = "MISSING_INPUT"
    INVALID_INPUT = "INVALID_INPUT"
    UNSUPPORTED = "UNSUPPORTED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    ENGINE_ERROR = "ENGINE_ERROR"


class AgentResponseStatus(str, Enum):
    COMPLETED = "completed"
    NEEDS_INPUT = "needs_input"
    ERROR = "error"


class DecisionAction(str, Enum):
    RECOMMEND = "RECOMMEND"
    EXPERIMENT = "EXPERIMENT"
    HOLD = "HOLD"


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name}은 비어 있지 않은 문자열이어야 합니다.")


def _copy_object(value: dict[str, Any]) -> JsonObject:
    if not isinstance(value, dict):
        raise TypeError("JSON 객체는 dict여야 합니다.")
    return deepcopy(value)


def _copy_sources(value: dict[str, ValueSource]) -> dict[str, ValueSource]:
    if not isinstance(value, dict):
        raise TypeError("값 출처는 dict여야 합니다.")
    copied = dict(value)
    if any(not isinstance(source, ValueSource) for source in copied.values()):
        raise TypeError("값 출처는 ValueSource여야 합니다.")
    return copied


def _validate_value_provenance(
    values: JsonObject,
    sources: dict[str, ValueSource],
    source_refs: dict[str, str],
    *,
    allowed_sources: set[ValueSource],
) -> None:
    value_keys = set(values)
    if set(sources) != value_keys or set(source_refs) != value_keys:
        raise ValueError("모든 값에는 같은 이름의 source와 source_ref가 필요합니다.")
    if not set(sources.values()) <= allowed_sources:
        raise ValueError("이 객체에서 허용되지 않는 값 출처가 포함되어 있습니다.")
    if any(not isinstance(reference, str) or not reference.strip() for reference in source_refs.values()):
        raise ValueError("source_ref는 비어 있지 않은 문자열이어야 합니다.")


@dataclass(frozen=True)
class StaticCapabilities:
    """코드 배포 시 정해지는 도구와 기능 계약."""

    tool_schemas: tuple[JsonObject, ...]
    supported_features: tuple[str, ...]
    output_schemas: dict[str, JsonObject] = field(default_factory=dict)
    required_user_inputs: dict[str, tuple[str, ...]] = field(default_factory=dict)
    execution_defaults: dict[str, JsonObject] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "tool_schemas",
            tuple(_copy_object(schema) for schema in self.tool_schemas),
        )
        object.__setattr__(self, "supported_features", tuple(self.supported_features))
        object.__setattr__(
            self,
            "output_schemas",
            {name: _copy_object(schema) for name, schema in self.output_schemas.items()},
        )
        object.__setattr__(
            self,
            "required_user_inputs",
            {name: tuple(inputs) for name, inputs in self.required_user_inputs.items()},
        )
        object.__setattr__(
            self,
            "execution_defaults",
            {name: _copy_object(defaults) for name, defaults in self.execution_defaults.items()},
        )


@dataclass(frozen=True)
class DynamicCapabilities:
    """현재 데이터와 모델 상태에 따라 달라지는 ENGINE 응답."""

    supported_menus: tuple[JsonObject, ...]
    limits: JsonObject
    data: JsonObject
    limitations: tuple[str, ...] = ()
    data_sufficiency: JsonObject = field(default_factory=dict)
    model_readiness: JsonObject = field(default_factory=dict)
    data_version: str | None = None
    source: ValueSource = field(default=ValueSource.ENGINE, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "supported_menus",
            tuple(_copy_object(menu) for menu in self.supported_menus),
        )
        for name in ("limits", "data", "data_sufficiency", "model_readiness"):
            object.__setattr__(self, name, _copy_object(getattr(self, name)))
        object.__setattr__(self, "limitations", tuple(self.limitations))


@dataclass(frozen=True)
class AgentInput:
    """사용자 원문과 원문에서 확인 가능한 사업 입력."""

    message_id: str
    text: str
    business_inputs: JsonObject = field(default_factory=dict)
    sources: dict[str, ValueSource] = field(default_factory=dict)
    source_refs: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.message_id, "message_id")
        _require_text(self.text, "text")
        values = _copy_object(self.business_inputs)
        sources = _copy_sources(self.sources)
        refs = dict(self.source_refs)
        _validate_value_provenance(
            values,
            sources,
            refs,
            allowed_sources={ValueSource.USER},
        )
        object.__setattr__(self, "business_inputs", values)
        object.__setattr__(self, "sources", sources)
        object.__setattr__(self, "source_refs", refs)


@dataclass(frozen=True)
class Strategy:
    """아직 검토 중이거나 실행 준비가 끝난 사업 대안."""

    name: str
    operation: str
    business_inputs: JsonObject
    sources: dict[str, ValueSource]
    source_refs: dict[str, str]
    confirmed: bool = False

    def __post_init__(self) -> None:
        _require_text(self.name, "name")
        _require_text(self.operation, "operation")
        if not isinstance(self.confirmed, bool):
            raise TypeError("confirmed는 bool이어야 합니다.")
        values = _copy_object(self.business_inputs)
        sources = _copy_sources(self.sources)
        refs = dict(self.source_refs)
        allowed = {ValueSource.USER, ValueSource.ENGINE, ValueSource.DEFAULT, ValueSource.LLM}
        _validate_value_provenance(values, sources, refs, allowed_sources=allowed)
        if self.confirmed and ValueSource.LLM in sources.values():
            raise ValueError("확정된 전략에는 LLM 출처의 사업 입력을 사용할 수 없습니다.")
        object.__setattr__(self, "business_inputs", values)
        object.__setattr__(self, "sources", sources)
        object.__setattr__(self, "source_refs", refs)


@dataclass(frozen=True)
class ToolCall:
    """Provider가 반환하고 Agent Loop가 검증할 공통 도구 호출."""

    call_id: str
    name: str
    arguments: JsonObject
    argument_sources: dict[str, ValueSource] = field(default_factory=dict)
    source_refs: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.call_id, "call_id")
        _require_text(self.name, "name")
        sources = _copy_sources(self.argument_sources)
        refs = dict(self.source_refs)
        if set(sources) != set(refs):
            raise ValueError("도구 인자 출처에는 같은 경로의 source_ref가 필요합니다.")
        if any(not isinstance(reference, str) or not reference.strip() for reference in refs.values()):
            raise ValueError("도구 인자의 source_ref는 비어 있지 않은 문자열이어야 합니다.")
        object.__setattr__(self, "arguments", _copy_object(self.arguments))
        object.__setattr__(self, "argument_sources", sources)
        object.__setattr__(self, "source_refs", refs)


@dataclass(frozen=True)
class ToolResult:
    """Dispatcher가 반환한 성공 또는 오류 JSON 원본."""

    call_id: str
    tool_name: str
    raw: JsonObject
    error_category: AgentErrorCategory | None = None
    source: ValueSource = field(default=ValueSource.ENGINE, init=False)

    def __post_init__(self) -> None:
        _require_text(self.call_id, "call_id")
        _require_text(self.tool_name, "tool_name")
        raw = _copy_object(self.raw)
        if raw.get("status") not in {"ok", "error"}:
            raise ValueError("도구 결과 status는 ok 또는 error여야 합니다.")
        if raw["status"] == "ok" and self.error_category is not None:
            raise ValueError("성공한 도구 결과에는 오류 분류를 붙일 수 없습니다.")
        if raw["status"] == "error" and self.error_category is None:
            raise ValueError("실패한 도구 결과에는 오류 분류가 필요합니다.")
        object.__setattr__(self, "raw", raw)


@dataclass(frozen=True)
class MissingInput:
    """도구 실행 전에 사용자에게 확인해야 하는 사업 입력."""

    fields: tuple[str, ...]
    reason: str
    question: str
    strategy_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.fields or any(
            not isinstance(field_name, str) or not field_name.strip()
            for field_name in self.fields
        ):
            raise ValueError("fields에는 누락된 입력 이름이 하나 이상 필요합니다.")
        _require_text(self.reason, "reason")
        _require_text(self.question, "question")
        object.__setattr__(self, "fields", tuple(self.fields))


@dataclass(frozen=True)
class Decision:
    """계산 엔진이 반환한 실행 단계와 근거."""

    action: DecisionAction
    reason: str
    source_ref: str
    source: ValueSource = field(default=ValueSource.ENGINE, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.action, DecisionAction):
            raise TypeError("action은 DecisionAction이어야 합니다.")
        _require_text(self.reason, "reason")
        _require_text(self.source_ref, "source_ref")


@dataclass(frozen=True)
class Evidence:
    """설명에 사용할 엔진 근거와 사용자 가정의 원래 출처."""

    items: JsonObject
    sources: dict[str, ValueSource]
    source_refs: dict[str, str]
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        items = _copy_object(self.items)
        sources = _copy_sources(self.sources)
        refs = dict(self.source_refs)
        _validate_value_provenance(
            items,
            sources,
            refs,
            allowed_sources={ValueSource.USER, ValueSource.ENGINE, ValueSource.DEFAULT},
        )
        object.__setattr__(self, "items", items)
        object.__setattr__(self, "sources", sources)
        object.__setattr__(self, "source_refs", refs)
        object.__setattr__(self, "limitations", tuple(self.limitations))


@dataclass(frozen=True)
class AgentResponse:
    """UI용 원본 사실과 LLM 설명을 분리한 Agent 반환값."""

    status: AgentResponseStatus
    facts: JsonObject = field(default_factory=dict)
    fact_sources: dict[str, ValueSource] = field(default_factory=dict)
    fact_source_refs: dict[str, str] = field(default_factory=dict)
    decisions: tuple[Decision, ...] = ()
    evidence: Evidence | None = None
    explanation: str | None = None
    explanation_source: ValueSource = field(default=ValueSource.LLM, init=False)
    missing_input: MissingInput | None = None
    error: ToolResult | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, AgentResponseStatus):
            raise TypeError("status는 AgentResponseStatus여야 합니다.")
        facts = _copy_object(self.facts)
        sources = _copy_sources(self.fact_sources)
        refs = dict(self.fact_source_refs)
        _validate_value_provenance(
            facts,
            sources,
            refs,
            allowed_sources={ValueSource.ENGINE},
        )
        decisions = tuple(self.decisions)
        if self.explanation is not None:
            _require_text(self.explanation, "explanation")
        if self.status is AgentResponseStatus.NEEDS_INPUT and self.missing_input is None:
            raise ValueError("needs_input 응답에는 missing_input이 필요합니다.")
        if self.status is AgentResponseStatus.COMPLETED and self.missing_input is not None:
            raise ValueError("completed 응답에는 missing_input을 넣을 수 없습니다.")
        if self.status is AgentResponseStatus.ERROR:
            if self.error is None or self.error.raw.get("status") != "error":
                raise ValueError("error 응답에는 실패한 ToolResult가 필요합니다.")
            if facts or decisions:
                raise ValueError("error 응답에는 계산 사실이나 판단을 넣을 수 없습니다.")
        if self.status is not AgentResponseStatus.ERROR and self.error is not None:
            raise ValueError("error 상태가 아닌 응답에는 오류 결과를 넣을 수 없습니다.")
        object.__setattr__(self, "facts", facts)
        object.__setattr__(self, "fact_sources", sources)
        object.__setattr__(self, "fact_source_refs", refs)
        object.__setattr__(self, "decisions", decisions)


@dataclass(frozen=True)
class Message:
    """공급자와 무관한 대화 메시지."""

    role: MessageRole
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    tool_name: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.role, MessageRole):
            raise TypeError("role은 MessageRole이어야 합니다.")
        if not isinstance(self.content, str):
            raise TypeError("content는 문자열이어야 합니다.")
        calls = tuple(self.tool_calls)
        if calls and self.role is not MessageRole.ASSISTANT:
            raise ValueError("tool_calls는 assistant 메시지에만 포함할 수 있습니다.")
        if self.role is MessageRole.TOOL:
            _require_text(self.tool_call_id or "", "tool_call_id")
            _require_text(self.tool_name or "", "tool_name")
        object.__setattr__(self, "tool_calls", calls)


@dataclass(frozen=True)
class LLMResponse:
    """모든 LLM Provider가 반환하는 공통 응답."""

    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()

    def __post_init__(self) -> None:
        calls = tuple(self.tool_calls)
        if self.text is not None and not isinstance(self.text, str):
            raise TypeError("text는 문자열 또는 None이어야 합니다.")
        if (self.text is None or not self.text.strip()) and not calls:
            raise ValueError("LLMResponse에는 text 또는 tool_calls가 필요합니다.")
        object.__setattr__(self, "tool_calls", calls)
