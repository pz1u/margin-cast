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


@dataclass(frozen=True)
class Provenance:
    """한 값의 출처와 원본 위치."""

    source: ValueSource
    ref: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, ValueSource):
            raise TypeError("source는 ValueSource여야 합니다.")
        if self.ref is not None:
            _require_text(self.ref, "ref")


class AgentErrorCategory(str, Enum):
    MISSING_INPUT = "MISSING_INPUT"
    INVALID_INPUT = "INVALID_INPUT"
    UNSUPPORTED = "UNSUPPORTED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    ENGINE_ERROR = "ENGINE_ERROR"


class AgentErrorOrigin(str, Enum):
    INPUT = "input"
    CAPABILITY = "capability"
    TOOL = "tool"
    PROVIDER = "provider"
    RUNTIME = "runtime"


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


def _copy_provenance(value: dict[str, Provenance]) -> dict[str, Provenance]:
    if not isinstance(value, dict):
        raise TypeError("provenance는 dict여야 합니다.")
    copied = dict(value)
    if any(not isinstance(item, Provenance) for item in copied.values()):
        raise TypeError("provenance 값은 Provenance여야 합니다.")
    return copied


def _validate_value_provenance(
    values: JsonObject,
    provenance: dict[str, Provenance],
    *,
    allowed_sources: set[ValueSource],
) -> None:
    value_keys = set(values)
    if set(provenance) != value_keys:
        raise ValueError("모든 값에는 같은 이름의 provenance가 필요합니다.")
    if not {item.source for item in provenance.values()} <= allowed_sources:
        raise ValueError("이 객체에서 허용되지 않는 값 출처가 포함되어 있습니다.")


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
    provenance: dict[str, Provenance] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.message_id, "message_id")
        _require_text(self.text, "text")
        values = _copy_object(self.business_inputs)
        provenance = _copy_provenance(self.provenance)
        _validate_value_provenance(
            values,
            provenance,
            allowed_sources={ValueSource.USER},
        )
        object.__setattr__(self, "business_inputs", values)
        object.__setattr__(self, "provenance", provenance)


@dataclass(frozen=True)
class Strategy:
    """아직 검토 중이거나 실행 준비가 끝난 사업 대안."""

    name: str
    operation: str
    business_inputs: JsonObject
    provenance: dict[str, Provenance]
    confirmed: bool = False

    def __post_init__(self) -> None:
        _require_text(self.name, "name")
        _require_text(self.operation, "operation")
        if not isinstance(self.confirmed, bool):
            raise TypeError("confirmed는 bool이어야 합니다.")
        values = _copy_object(self.business_inputs)
        provenance = _copy_provenance(self.provenance)
        allowed = {ValueSource.USER, ValueSource.ENGINE, ValueSource.DEFAULT, ValueSource.LLM}
        _validate_value_provenance(values, provenance, allowed_sources=allowed)
        if self.confirmed and any(
            item.source is ValueSource.LLM for item in provenance.values()
        ):
            raise ValueError("확정된 전략에는 LLM 출처의 사업 입력을 사용할 수 없습니다.")
        object.__setattr__(self, "business_inputs", values)
        object.__setattr__(self, "provenance", provenance)


@dataclass(frozen=True)
class ToolCall:
    """Provider가 반환하고 Agent Loop가 검증할 공통 도구 호출."""

    call_id: str
    name: str
    arguments: JsonObject
    argument_provenance: dict[str, Provenance] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.call_id, "call_id")
        _require_text(self.name, "name")
        object.__setattr__(self, "arguments", _copy_object(self.arguments))
        object.__setattr__(
            self,
            "argument_provenance",
            _copy_provenance(self.argument_provenance),
        )


@dataclass(frozen=True)
class ToolResult:
    """Dispatcher가 반환한 성공 또는 오류 JSON 원본."""

    call_id: str
    tool_name: str
    raw: JsonObject
    source: ValueSource = field(default=ValueSource.ENGINE, init=False)

    def __post_init__(self) -> None:
        _require_text(self.call_id, "call_id")
        _require_text(self.tool_name, "tool_name")
        raw = _copy_object(self.raw)
        if raw.get("status") not in {"ok", "error"}:
            raise ValueError("도구 결과 status는 ok 또는 error여야 합니다.")
        object.__setattr__(self, "raw", raw)


@dataclass(frozen=True)
class AgentError:
    """Agent가 사용자에게 전달하는 표준화된 실패 상태."""

    code: AgentErrorCategory
    message: str
    origin: AgentErrorOrigin
    retryable: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.code, AgentErrorCategory):
            raise TypeError("code는 AgentErrorCategory여야 합니다.")
        if not isinstance(self.origin, AgentErrorOrigin):
            raise TypeError("origin은 AgentErrorOrigin이어야 합니다.")
        _require_text(self.message, "message")
        if not isinstance(self.retryable, bool):
            raise TypeError("retryable은 bool이어야 합니다.")


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
    provenance: dict[str, Provenance]
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        items = _copy_object(self.items)
        provenance = _copy_provenance(self.provenance)
        _validate_value_provenance(
            items,
            provenance,
            allowed_sources={ValueSource.USER, ValueSource.ENGINE, ValueSource.DEFAULT},
        )
        object.__setattr__(self, "items", items)
        object.__setattr__(self, "provenance", provenance)
        object.__setattr__(self, "limitations", tuple(self.limitations))


@dataclass(frozen=True)
class AgentResponse:
    """UI용 원본 사실과 LLM 설명을 분리한 Agent 반환값."""

    status: AgentResponseStatus
    facts: JsonObject = field(default_factory=dict)
    fact_provenance: dict[str, Provenance] = field(default_factory=dict)
    decision: Decision | None = None
    evidence: Evidence | None = None
    explanation: str | None = None
    explanation_source: ValueSource = field(default=ValueSource.LLM, init=False)
    notices: tuple[str, ...] = ()
    policy_validation: JsonObject = field(default_factory=dict)
    missing_input: MissingInput | None = None
    error: AgentError | None = None
    tool_results: tuple[ToolResult, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, AgentResponseStatus):
            raise TypeError("status는 AgentResponseStatus여야 합니다.")
        facts = _copy_object(self.facts)
        provenance = _copy_provenance(self.fact_provenance)
        _validate_value_provenance(
            facts,
            provenance,
            allowed_sources={ValueSource.ENGINE},
        )
        tool_results = tuple(self.tool_results)
        notices = tuple(self.notices)
        if any(not isinstance(notice, str) or not notice.strip() for notice in notices):
            raise ValueError("notices는 비어 있지 않은 문자열이어야 합니다.")
        policy_validation = _copy_object(self.policy_validation)
        if policy_validation and policy_validation.get("status") not in {"PASS", "REJECTED"}:
            raise ValueError("policy_validation.status는 PASS 또는 REJECTED여야 합니다.")
        if self.explanation is not None:
            _require_text(self.explanation, "explanation")
        if self.status is AgentResponseStatus.NEEDS_INPUT and self.missing_input is None:
            raise ValueError("needs_input 응답에는 missing_input이 필요합니다.")
        if self.status is AgentResponseStatus.COMPLETED and self.missing_input is not None:
            raise ValueError("completed 응답에는 missing_input을 넣을 수 없습니다.")
        if self.status is AgentResponseStatus.ERROR:
            if self.error is None:
                raise ValueError("error 응답에는 AgentError가 필요합니다.")
            if facts or self.decision is not None:
                raise ValueError("error 응답에는 계산 사실이나 판단을 넣을 수 없습니다.")
        if self.status is not AgentResponseStatus.ERROR and self.error is not None:
            raise ValueError("error 상태가 아닌 응답에는 AgentError를 넣을 수 없습니다.")
        object.__setattr__(self, "facts", facts)
        object.__setattr__(self, "fact_provenance", provenance)
        object.__setattr__(self, "notices", notices)
        object.__setattr__(self, "policy_validation", policy_validation)
        object.__setattr__(self, "tool_results", tool_results)


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
    missing_input: MissingInput | None = None
    decision_claim: DecisionAction | None = None

    def __post_init__(self) -> None:
        calls = tuple(self.tool_calls)
        if self.text is not None and not isinstance(self.text, str):
            raise TypeError("text는 문자열 또는 None이어야 합니다.")
        decision_claim = self.decision_claim
        if decision_claim is not None and not isinstance(decision_claim, DecisionAction):
            decision_claim = DecisionAction(decision_claim)
        if (self.text is None or not self.text.strip()) and not calls and self.missing_input is None:
            raise ValueError("LLMResponse에는 text, tool_calls 또는 missing_input이 필요합니다.")
        object.__setattr__(self, "tool_calls", calls)
        object.__setattr__(self, "decision_claim", decision_claim)
