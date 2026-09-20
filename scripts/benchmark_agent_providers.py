"""실제 Provider의 가격 Agent 성공률, latency, token/cost를 비교한다."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import statistics
import tempfile

from src.agent_audit import AgentAuditStore
from src.agent_chat_service import AgentChatService, MemorySessionStore
from src.agent_tool_contracts import execute_tool
from src.decision_service import MarginCastDecisionService
from src.llm_provider_factory import create_llm_provider


PRICE_QUESTION = "치킨마요를 9,500원으로 올리면 어때?"
LUNA_PRICING_USD_PER_MILLION = {
    "input_tokens": 0.20,
    "cached_input_tokens": 0.02,
    "output_tokens": 1.20,
}
LUNA_PRICING_SOURCE = "https://developers.openai.com/api/docs/models/gpt-5.6-luna"


class RecordingExecutor:
    def __init__(self, service):
        self.service = service
        self.calls = []

    def __call__(self, tool_name, arguments):
        copied = deepcopy(arguments)
        result = execute_tool(tool_name, copied, service=self.service)
        self.calls.append((tool_name, copied, deepcopy(result)))
        return result


class ProviderCollector:
    def __init__(self):
        self.providers = []

    def __call__(self):
        provider = create_llm_provider()
        self.providers.append(provider)
        return provider


def percentile(values: Sequence[float], ratio: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = max(0, math.ceil(len(ordered) * ratio) - 1)
    return round(ordered[position], 3)


def aggregate_usage(providers) -> dict[str, int] | None:
    totals = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }
    found = False
    for provider in providers:
        usage = getattr(provider, "usage_totals", None)
        if not isinstance(usage, dict):
            continue
        found = True
        for name in totals:
            value = usage.get(name, 0)
            if isinstance(value, int) and not isinstance(value, bool):
                totals[name] += value
    return totals if found else None


def estimate_luna_cost(model: str, usage: dict[str, int] | None) -> float | None:
    if model != "gpt-5.6-luna" or usage is None:
        return None
    cached = usage["cached_input_tokens"]
    uncached = max(0, usage["input_tokens"] - cached)
    cost = (
        uncached * LUNA_PRICING_USD_PER_MILLION["input_tokens"]
        + cached * LUNA_PRICING_USD_PER_MILLION["cached_input_tokens"]
        + usage["output_tokens"] * LUNA_PRICING_USD_PER_MILLION["output_tokens"]
    ) / 1_000_000
    return round(cost, 8)


def run(provider_name: str, runs: int) -> dict:
    os.environ["LLM_PROVIDER"] = provider_name
    engine = MarginCastDecisionService()
    executor = RecordingExecutor(engine)
    providers = ProviderCollector()

    with tempfile.TemporaryDirectory() as directory:
        audit_path = Path(directory) / "audit.json"
        service = AgentChatService(
            provider_factory=providers,
            tool_executor=executor,
            capabilities_loader=lambda: execute_tool(
                "get_margincast_capabilities",
                {},
                service=engine,
            ),
            audit_store=AgentAuditStore(audit_path),
            session_store=MemorySessionStore(),
        )
        results = []
        for position in range(runs):
            status, payload = service.chat(
                f"benchmark-{provider_name}-{position}",
                PRICE_QUESTION,
            )
            results.append(
                {
                    "http_status": status,
                    "status": payload.get("status"),
                    "presentation_source": payload.get("presentation", {}).get("source"),
                    "decision": payload.get("facts", {})
                    .get("engine_decision", {})
                    .get("value"),
                    "output_chars": len(
                        payload.get("presentation", {}).get("explanation", "")
                        + payload.get("presentation", {}).get("next_action", "")
                    ),
                }
            )
        audit = (
            json.loads(audit_path.read_text(encoding="utf-8"))
            if audit_path.exists()
            else []
        )

    timings = [
        record.get("timings_ms", {}).get("total_ms")
        for record in audit
        if isinstance(record.get("timings_ms", {}).get("total_ms"), (int, float))
    ]
    first = [
        record.get("timings_ms", {}).get("first_provider_ms")
        for record in audit
        if isinstance(
            record.get("timings_ms", {}).get("first_provider_ms"), (int, float)
        )
    ]
    second = [
        record.get("timings_ms", {}).get("second_provider_ms")
        for record in audit
        if isinstance(
            record.get("timings_ms", {}).get("second_provider_ms"), (int, float)
        )
    ]
    usage = aggregate_usage(providers.providers)
    model = (
        str(getattr(providers.providers[0], "model", "unknown"))
        if providers.providers
        else "not-created"
    )
    expected_arguments = all(
        name == "compare_price_strategies"
        and arguments.get("menu_id") == "M01"
        and arguments.get("scenarios", [{}])[0].get("list_price") == 9500
        for name, arguments, _ in executor.calls
    )
    return {
        "provider": provider_name,
        "model": model,
        "runs": runs,
        "completed": sum(item["status"] == "COMPLETED" for item in results),
        "tool_selection_success": sum(
            name == "compare_price_strategies" for name, _, _ in executor.calls
        ),
        "tool_arguments_valid": len(executor.calls) == runs and expected_arguments,
        "initial_policy_pass": sum(
            record.get("initial_llm_policy_status") == "PASS" for record in audit
        ),
        "fallback_used": sum(bool(record.get("fallback_used")) for record in audit),
        "rejected": sum(item["status"] == "REJECTED" for item in results),
        "latency_ms": {
            "first_provider_average": round(statistics.fmean(first), 3) if first else None,
            "second_provider_average": round(statistics.fmean(second), 3)
            if second
            else None,
            "total_average": round(statistics.fmean(timings), 3) if timings else None,
            "total_p50": percentile(timings, 0.50),
            "total_p95": percentile(timings, 0.95),
        },
        "token_usage": usage,
        "output_chars": sum(item["output_chars"] for item in results),
        "estimated_cost_usd": estimate_luna_cost(model, usage),
        "pricing_source": (
            LUNA_PRICING_SOURCE if model == "gpt-5.6-luna" else None
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("ollama", "openai"), required=True)
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs는 한 번 이상이어야 합니다.")
    print(json.dumps(run(args.provider, args.runs), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()