"""현재 execute_tool 결과로 Agent 개발용 fixture를 재생성한다."""

import json
import sys
from pathlib import Path


FIXTURE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = FIXTURE_DIR.parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agent_tool_contracts import execute_tool  # noqa: E402
from src.decision_service import MarginCastDecisionService  # noqa: E402


FIXTURE_CALLS = {
    "capabilities.json": {
        "tool_name": "get_margincast_capabilities",
        "arguments": {},
    },
    "price_recommend.json": {
        "tool_name": "compare_price_strategies",
        "arguments": {
            "menu_id": "M01",
            "scenarios": [
                {"name": "500원 인상", "list_price": 9500, "discount": 0},
                {"name": "1,000원 인상", "list_price": 10000, "discount": 0},
                {"name": "2,000원 인상", "list_price": 11000, "discount": 0},
            ],
        },
    },
    "price_experiment.json": {
        "tool_name": "compare_price_strategies",
        "arguments": {
            "menu_id": "M02",
            "scenarios": [
                {"name": "500원 인상", "list_price": 10500, "discount": 0},
                {"name": "1,000원 인상", "list_price": 11000, "discount": 0},
                {"name": "1,500원 인상", "list_price": 11500, "discount": 0},
            ],
        },
    },
    "price_hold.json": {
        "tool_name": "compare_price_strategies",
        "arguments": {
            "menu_id": "M01",
            "scenarios": [
                {"name": "1,000원 인하", "list_price": 8000, "discount": 0},
                {"name": "500원 인하", "list_price": 8500, "discount": 0},
            ],
        },
    },
    "unsupported_menu.json": {
        "tool_name": "compare_price_strategies",
        "arguments": {
            "menu_id": "M04",
            "scenarios": [
                {"name": "500원 인상", "list_price": 8500, "discount": 0}
            ],
        },
    },
    "invalid_scenario.json": {
        "tool_name": "compare_price_strategies",
        "arguments": {
            "menu_id": "M01",
            "scenarios": [
                {
                    "name": "할인액이 정가와 같은 잘못된 입력",
                    "list_price": 9000,
                    "discount": 9000,
                }
            ],
        },
    },
}

EXPECTED_OUTCOMES = {
    "price_recommend.json": ("ok", "RECOMMEND"),
    "price_experiment.json": ("ok", "EXPERIMENT"),
    "price_hold.json": ("ok", "HOLD"),
    "unsupported_menu.json": ("error", "UNSUPPORTED_MENU"),
    "invalid_scenario.json": ("error", "INVALID_SCENARIO"),
}


def validate_fixture(name, result):
    if name == "capabilities.json":
        defaults = result["execution_defaults"]
        assert defaults["compare_price_strategies"] == {
            "horizon_days": 14,
            "simulations": 10000,
            "seed": 42,
        }
        assert defaults["compare_price_strategies_with_forecast"] == {
            "horizon_days": 4,
            "simulations": 10000,
            "seed": 42,
        }
        assert defaults["simulate_bundle_strategy"] == {
            "horizon_days": 14,
            "simulations": 10000,
            "seed": 42,
        }
        return

    expected_status, expected_value = EXPECTED_OUTCOMES[name]
    assert result["status"] == expected_status
    if expected_status == "ok":
        assert result["recommended_action"]["action"] == expected_value
    else:
        assert result["error"]["code"] == expected_value


def main():
    service = MarginCastDecisionService()
    manifest = {}
    for filename, call in FIXTURE_CALLS.items():
        result = execute_tool(call["tool_name"], call["arguments"], service)
        validate_fixture(filename, result)
        (FIXTURE_DIR / filename).write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest[filename] = call

    (FIXTURE_DIR / "fixture-inputs.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
