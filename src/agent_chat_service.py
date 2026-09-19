"""가격 질문 한 흐름을 메모리 세션과 Agent Runtime에 연결한다."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
import re
import threading
from uuid import uuid4

from .agent_audit import AgentAuditStore
from .agent_result_router import AgentResultRouter
from .agent_runtime import AgentRuntime, ToolExecutor
from .agent_schemas import (
    AgentInput,
    AgentResponse,
    AgentResponseStatus,
    JsonObject,
    MissingInput,
    Provenance,
    ValueSource,
)
from .agent_web_contract import (
    AgentWebResult,
    AgentWebStatus,
    map_agent_result,
    map_runtime_error,
)
from .execution_defaults import get_execution_defaults
from .response_policy import PriceResponsePolicy


PRICE_PATTERN = re.compile(r"(?<!\d)(\d{1,3}(?:,\d{3})+|\d{4,7})(?:\s*원)?")


class AgentChatRequestError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ConversationState:
    """원문 대화 대신 다음 실행에 필요한 구조화된 값만 보존한다."""

    session_id: str
    capabilities: JsonObject
    selected_menu: JsonObject | None = None
    draft_scenario: JsonObject | None = None
    pending_question: JsonObject | None = None
    recommendation_id: str | None = None
    recommendation_context: JsonObject | None = None

    def copy(self) -> "ConversationState":
        return ConversationState(
            session_id=self.session_id,
            capabilities=deepcopy(self.capabilities),
            selected_menu=deepcopy(self.selected_menu),
            draft_scenario=deepcopy(self.draft_scenario),
            pending_question=deepcopy(self.pending_question),
            recommendation_id=self.recommendation_id,
            recommendation_context=deepcopy(self.recommendation_context),
        )


class MemorySessionStore:
    """단일 프로세스용 메모리 세션 저장소."""

    def __init__(self) -> None:
        self._states: dict[str, ConversationState] = {}
        self._lock = threading.RLock()

    def get(self, session_id: str) -> ConversationState | None:
        with self._lock:
            state = self._states.get(session_id)
            return None if state is None else state.copy()

    def save(self, state: ConversationState) -> None:
        with self._lock:
            self._states[state.session_id] = state.copy()


def _serialize_web_result(session_id: str, result: AgentWebResult) -> JsonObject:
    payload: JsonObject = {
        "session_id": session_id,
        "status": result.status.value,
        "execution_id": result.execution_id,
        "recommendation_id": result.recommendation_id,
    }
    response = result.agent_response
    if result.status is AgentWebStatus.COMPLETED and response is not None:
        payload.update(
            {
                "facts": deepcopy(response.facts),
                "presentation": {
                    "explanation": response.explanation,
                    "next_action": response.presentation.next_action,
                    "source": response.presentation.source.value,
                },
                "notices": list(response.notices),
            }
        )
    elif result.status is AgentWebStatus.NEEDS_INPUT and response is not None:
        missing = response.missing_input
        payload.update(
            {
                "question": missing.question,
                "missing_input": {
                    "fields": list(missing.fields),
                    "source_requirement": [
                        source.value for source in missing.source_requirement
                    ],
                },
            }
        )
    elif result.status is AgentWebStatus.REJECTED:
        payload["policy_validation"] = {
            "status": "REJECTED",
            "violation_codes": list(
                result.policy_validation.get("violations", [])
            ),
        }
    else:
        error = response.error if response is not None else None
        payload["error"] = {
            "code": (
                error.code.value
                if error is not None
                else result.policy_validation.get("reason", "AGENT_ERROR")
            ),
            "message": "Agent 요청을 처리하지 못했습니다.",
            "retryable": error.retryable if error is not None else False,
        }
    return payload


class AgentChatService:
    """G1 가격 대화 한 건을 세션 상태와 기존 Agent 경계로 실행한다."""

    def __init__(
        self,
        *,
        provider_factory: Callable[[], object],
        tool_executor: ToolExecutor,
        capabilities_loader: Callable[[], JsonObject],
        audit_store: AgentAuditStore,
        session_store: MemorySessionStore | None = None,
        session_id_factory: Callable[[], str] | None = None,
        execution_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.provider_factory = provider_factory
        self.tool_executor = tool_executor
        self.capabilities_loader = capabilities_loader
        self.audit_store = audit_store
        self.session_store = session_store or MemorySessionStore()
        self.session_id_factory = session_id_factory or (lambda: uuid4().hex)
        self.execution_id_factory = execution_id_factory or (lambda: uuid4().hex)

    @staticmethod
    def _validate_text(value, name: str, maximum: int) -> str:
        if not isinstance(value, str) or not value.strip():
            raise AgentChatRequestError(
                "INVALID_ARGUMENTS",
                f"{name}은 비어 있지 않은 문자열이어야 합니다.",
            )
        value = value.strip()
        if len(value) > maximum:
            raise AgentChatRequestError(
                "INVALID_ARGUMENTS",
                f"{name} 길이가 허용 범위를 넘었습니다.",
            )
        return value

    def _session_id(self, value) -> str:
        if value is None:
            value = self.session_id_factory()
        return self._validate_text(value, "session_id", 128)

    def _new_state(self, session_id: str) -> ConversationState:
        capabilities = self.capabilities_loader()
        if not isinstance(capabilities, dict) or capabilities.get("status") != "ok":
            raise RuntimeError("Agent capability를 준비할 수 없습니다.")
        return ConversationState(
            session_id=session_id,
            capabilities=deepcopy(capabilities),
        )

    @staticmethod
    def _find_menu(message: str, state: ConversationState) -> JsonObject | None:
        menus = state.capabilities.get("supported_menus", [])
        for menu in menus if isinstance(menus, list) else []:
            if not isinstance(menu, dict):
                continue
            menu_id = menu.get("menu_id")
            menu_name = menu.get("menu_name")
            matches_name = isinstance(menu_name, str) and menu_name in message
            matches_id = (
                isinstance(menu_id, str) and menu_id.lower() in message.lower()
            )
            if matches_name or matches_id:
                return deepcopy(menu)
        return deepcopy(state.selected_menu)

    @staticmethod
    def _find_price(message: str) -> int | None:
        candidates = [
            int(match.group(1).replace(",", ""))
            for match in PRICE_PATTERN.finditer(message)
        ]
        return next((value for value in reversed(candidates) if value >= 1_000), None)

    @staticmethod
    def _needs_input(
        execution_id: str,
        fields: tuple[str, ...],
        question: str,
    ) -> AgentWebResult:
        response = AgentResponse(
            status=AgentResponseStatus.NEEDS_INPUT,
            missing_input=MissingInput(
                fields=fields,
                reason="가격 비교에 필요한 사용자 입력이 없습니다.",
                question=question,
                source_requirement=(ValueSource.USER,),
            ),
        )
        return AgentWebResult(
            execution_id=execution_id,
            status=AgentWebStatus.NEEDS_INPUT,
            recommendation_id=None,
            agent_response=response,
            policy_validation={"status": "NOT_RUN", "reason": "MISSING_INPUT"},
            timings_ms={},
        )

    @staticmethod
    def _recommendation_snapshot(run_result, response) -> JsonObject:
        raw = run_result.tool_result.raw
        selected_id = response.facts["selected_scenario_id"]["value"]
        selected = next(
            (
                deepcopy(strategy)
                for strategy in raw.get("strategies", [])
                if strategy.get("scenario_id") == selected_id
            ),
            None,
        )
        return {
            "execution_id": run_result.execution_id,
            "recommendation_id": response.recommendation_id,
            "selected_scenario_id": selected_id,
            "request": deepcopy(raw.get("request", {})),
            "selected_strategy": selected,
            "recommended_action": deepcopy(raw.get("recommended_action", {})),
            "engine_version": raw.get("engine_version"),
            "data_provenance": deepcopy(raw.get("data_provenance", {})),
            "weather_context": deepcopy(raw.get("weather_context", {})),
        }

    def chat(self, session_id, message) -> tuple[int, JsonObject]:
        session_id = self._session_id(session_id)
        message = self._validate_text(message, "message", 2_000)
        execution_id = self.execution_id_factory()
        execution_id = self._validate_text(execution_id, "execution_id", 128)

        try:
            state = self.session_store.get(session_id) or self._new_state(session_id)
            menu = self._find_menu(message, state)
            if menu is not None:
                state.selected_menu = menu
            if state.selected_menu is None:
                state.pending_question = {
                    "fields": ["menu_id"],
                    "question": "분석할 메뉴를 알려주세요.",
                }
                self.session_store.save(state)
                result = self._needs_input(
                    execution_id,
                    ("menu_id",),
                    state.pending_question["question"],
                )
                return 200, _serialize_web_result(session_id, result)

            price = self._find_price(message)
            if price is None and state.draft_scenario is not None:
                draft_price = state.draft_scenario.get("list_price")
                price = draft_price if isinstance(draft_price, int) else None
            if price is None:
                state.pending_question = {
                    "fields": ["list_price"],
                    "question": "변경할 가격은 얼마로 생각하고 계신가요?",
                }
                self.session_store.save(state)
                result = self._needs_input(
                    execution_id,
                    ("list_price",),
                    state.pending_question["question"],
                )
                return 200, _serialize_web_result(session_id, result)

            state.draft_scenario = {
                "name": "가격 변경",
                "list_price": price,
                "discount": 0,
            }
            state.pending_question = None
            state.recommendation_id = None
            state.recommendation_context = None
            self.session_store.save(state)

            business_inputs = {
                "menu_id": state.selected_menu["menu_id"],
                "scenarios": [deepcopy(state.draft_scenario)],
            }
            agent_input = AgentInput(
                message_id=f"{execution_id}:user",
                text=message,
                business_inputs=business_inputs,
                provenance={
                    name: Provenance(ValueSource.USER, f"{execution_id}:{name}")
                    for name in business_inputs
                },
            )
            runtime = AgentRuntime(
                self.provider_factory(),
                tool_executor=self.tool_executor,
                execution_id_factory=lambda: execution_id,
            )
            run_result = runtime.run(agent_input)
            routed = AgentResultRouter(
                PriceResponsePolicy(),
                execution_defaults=get_execution_defaults(),
                audit_recorder=self.audit_store,
            ).route(run_result)
            web_result = map_agent_result(run_result, routed)

            if (
                web_result.status is AgentWebStatus.COMPLETED
                and web_result.agent_response is not None
            ):
                state.recommendation_id = web_result.recommendation_id
                state.recommendation_context = self._recommendation_snapshot(
                    run_result,
                    web_result.agent_response,
                )
                self.session_store.save(state)
            return (
                500 if web_result.status is AgentWebStatus.ERROR else 200,
                _serialize_web_result(session_id, web_result),
            )
        except Exception as error:
            try:
                error.execution_id = execution_id
            except Exception:
                pass
            try:
                web_result = map_runtime_error(error)
            except Exception:
                web_result = AgentWebResult(
                    execution_id=execution_id,
                    status=AgentWebStatus.ERROR,
                    recommendation_id=None,
                    agent_response=None,
                    policy_validation={"status": "NOT_RUN", "reason": "AGENT_ERROR"},
                    timings_ms={},
                )
            return 500, _serialize_web_result(session_id, web_result)
