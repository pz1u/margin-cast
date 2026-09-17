"""MarginCast Agent Loop를 실제 도구 계약과 계산 서비스에 연결한다."""

from __future__ import annotations

from copy import deepcopy

from .agent_runtime import MarginCastAgentLoop
from .agent_schemas import DynamicCapabilities, StaticCapabilities
from .agent_tool_contracts import TOOL_SCHEMAS, execute_tool
from .decision_service import MarginCastDecisionService
from .llm_provider import LLMProvider


CAPABILITY_INVALIDATING_ERRORS = {
    "INSUFFICIENT_DATA",
    "INVALID_PANEL",
    "PANEL_NOT_FOUND",
    "UNSUPPORTED_MENU",
}
SUPPORTED_FEATURES = (
    "capability_lookup",
    "price_strategy",
    "bundle_strategy",
)
REQUIRED_USER_INPUTS = {
    "get_margincast_capabilities": (),
    "compare_price_strategies": (
        "menu_id",
        "scenarios[].list_price",
        "scenarios[].discount",
    ),
    "simulate_bundle_strategy": (
        "scenario.bundle_price",
        "scenario.take_rate",
        "scenario.copurchase_take_rate",
        "scenario.incremental_demand_rate",
        "scenario.cannibalization_rate",
    ),
}
EXECUTION_DEFAULTS = {
    "get_margincast_capabilities": {},
    "compare_price_strategies": {
        "horizon_days": 14,
        "simulations": 10_000,
        "seed": 42,
    },
    "simulate_bundle_strategy": {
        "horizon_days": 14,
        "simulations": 10_000,
        "seed": 42,
    },
}


def _agent_tool_schemas() -> tuple[dict, ...]:
    schemas = deepcopy(TOOL_SCHEMAS)
    for schema in schemas:
        defaults = EXECUTION_DEFAULTS.get(schema["name"], {})
        required = schema["parameters"].get("required", [])
        schema["parameters"]["required"] = [
            field_name for field_name in required if field_name not in defaults
        ]
    return tuple(schemas)


def build_static_capabilities() -> StaticCapabilities:
    """현재 코드에 등록된 도구 계약과 엔진 실행 기본값을 반환한다."""
    return StaticCapabilities(
        tool_schemas=_agent_tool_schemas(),
        supported_features=SUPPORTED_FEATURES,
        required_user_inputs=REQUIRED_USER_INPUTS,
        execution_defaults=EXECUTION_DEFAULTS,
    )


def _dynamic_capabilities(result: dict) -> DynamicCapabilities:
    return DynamicCapabilities(
        supported_menus=tuple(result["supported_menus"]),
        limits=result["limits"],
        data=result["data"],
        limitations=tuple(result.get("limitations", ())),
    )


class MarginCastToolExecutor:
    """Agent 호출을 Dispatcher에 전달하고 동적 지원 상태를 세션에 보관한다."""

    def __init__(self, service: MarginCastDecisionService | None = None) -> None:
        self.service = service or MarginCastDecisionService()
        self._capabilities: DynamicCapabilities | None = None
        self._capability_result: dict | None = None

    @property
    def cached_capabilities(self) -> DynamicCapabilities | None:
        return deepcopy(self._capabilities)

    def invalidate_capabilities(self) -> None:
        self._capabilities = None
        self._capability_result = None

    def __call__(self, tool_name: str, arguments: dict) -> dict:
        if (
            tool_name == "get_margincast_capabilities"
            and not arguments
            and self._capability_result is not None
        ):
            return deepcopy(self._capability_result)

        result = execute_tool(tool_name, arguments, self.service)
        if tool_name == "get_margincast_capabilities" and result.get("status") == "ok":
            try:
                self._capabilities = _dynamic_capabilities(result)
                self._capability_result = deepcopy(result)
            except (KeyError, TypeError, ValueError):
                self.invalidate_capabilities()
        elif result.get("status") == "error":
            error = result.get("error") if isinstance(result.get("error"), dict) else {}
            if error.get("code") in CAPABILITY_INVALIDATING_ERRORS:
                self.invalidate_capabilities()
        return result


def create_margincast_agent_loop(
    provider: LLMProvider,
    service: MarginCastDecisionService | None = None,
    max_provider_calls: int = 4,
) -> MarginCastAgentLoop:
    """기본 계약과 실제 Dispatcher가 연결된 Agent Loop를 생성한다."""
    return MarginCastAgentLoop(
        provider=provider,
        static_capabilities=build_static_capabilities(),
        tool_executor=MarginCastToolExecutor(service),
        max_provider_calls=max_provider_calls,
    )
