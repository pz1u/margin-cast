"""AI 에이전트 등록 직전 단계의 MarginCast 함수 도구 계약과 디스패처."""

import argparse
import json
from pathlib import Path

try:
    from .decision_service import DecisionServiceError, MarginCastDecisionService
except ImportError:
    from decision_service import DecisionServiceError, MarginCastDecisionService


TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "get_margincast_capabilities",
        "description": (
            "MarginCast 계산 엔진이 전략 비교를 지원하는 메뉴, 입력 한도, 데이터 범위와 "
            "중요한 해석 제약을 조회한다. 전략 비교 전에 먼저 호출한다."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "compare_price_strategies",
        "description": (
            "한 메뉴의 가격 또는 할인 대안을 현재 가격과 Monte Carlo로 비교한다. "
            "기대 판매량, 기대 기여이익, 80% 범위, 성공확률, 신뢰도와 실행 판단을 반환한다."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "menu_id": {
                    "type": "string",
                    "description": "capabilities 응답의 supported_menus에 포함된 메뉴 ID",
                },
                "scenarios": {
                    "type": "array",
                    "description": "비교할 가격·할인 대안. 현재 가격은 생략하면 자동 추가된다.",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "minLength": 1, "maxLength": 50},
                            "list_price": {
                                "type": "integer",
                                "minimum": 1000,
                                "maximum": 100000,
                                "description": "원 단위 정가",
                            },
                            "discount": {
                                "type": "integer",
                                "minimum": 0,
                                "description": "원 단위 할인액. 정가보다 작아야 한다.",
                            },
                        },
                        "required": ["name", "list_price", "discount"],
                        "additionalProperties": False,
                    },
                },
                "horizon_days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 180,
                    "description": "비교 기간. 기본값은 14일",
                },
                "simulations": {
                    "type": "integer",
                    "minimum": 100,
                    "maximum": 50000,
                    "description": "Monte Carlo 반복 횟수. 기본값은 10000회",
                },
                "seed": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 4294967295,
                    "description": "재현성 seed. 기본값은 42",
                },
            },
            "required": ["menu_id", "scenarios", "horizon_days", "simulations", "seed"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "simulate_bundle_strategy",
        "description": (
            "치킨마요·콜라 세트의 Take Rate, 기존 동시구매 전환율, 신규 수요율과 "
            "다른 주메뉴 잠식률을 명시적으로 입력해 기여이익 분포와 실행 판단을 계산한다."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "scenario": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "minLength": 1, "maxLength": 50},
                        "bundle_price": {"type": "integer", "minimum": 1000, "maximum": 100000},
                        "take_rate": {"type": "number", "minimum": 0, "maximum": 1},
                        "copurchase_take_rate": {"type": "number", "minimum": 0, "maximum": 1},
                        "incremental_demand_rate": {"type": "number", "minimum": 0, "maximum": 1},
                        "cannibalization_rate": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": [
                        "name",
                        "bundle_price",
                        "take_rate",
                        "copurchase_take_rate",
                        "incremental_demand_rate",
                        "cannibalization_rate",
                    ],
                    "additionalProperties": False,
                },
                "horizon_days": {"type": "integer", "minimum": 1, "maximum": 180},
                "simulations": {"type": "integer", "minimum": 100, "maximum": 50000},
                "seed": {"type": "integer", "minimum": 0, "maximum": 4294967295},
            },
            "required": ["scenario", "horizon_days", "simulations", "seed"],
            "additionalProperties": False,
        },
    },
]


def error_result(code, message, details=None, retryable=False):
    return {
        "status": "error",
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": {} if details is None else details,
        },
    }


def execute_tool(tool_name, arguments, service=None):
    """도구 이름과 JSON 인자를 받아 항상 JSON 직렬화 가능한 결과를 반환한다."""
    service = service or MarginCastDecisionService()
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as error:
            return error_result(
                "INVALID_ARGUMENTS",
                "도구 인자가 올바른 JSON이 아닙니다.",
                {"position": error.pos},
            )
    if not isinstance(arguments, dict):
        return error_result("INVALID_ARGUMENTS", "도구 인자는 JSON 객체여야 합니다.")

    try:
        if tool_name == "get_margincast_capabilities":
            if arguments:
                return error_result(
                    "INVALID_ARGUMENTS", "get_margincast_capabilities는 인자를 받지 않습니다."
                )
            return service.get_capabilities()
        if tool_name == "compare_price_strategies":
            allowed = {"menu_id", "scenarios", "horizon_days", "simulations", "seed"}
            unknown = sorted(set(arguments) - allowed)
            if unknown:
                return error_result(
                    "INVALID_ARGUMENTS", "지원하지 않는 인자가 있습니다.", {"unknown_fields": unknown}
                )
            required = allowed
            missing = sorted(required - set(arguments))
            if missing:
                return error_result(
                    "INVALID_ARGUMENTS", "필수 인자가 없습니다.", {"missing_fields": missing}
                )
            return service.compare_price_strategies(**arguments)
        if tool_name == "simulate_bundle_strategy":
            allowed = {"scenario", "horizon_days", "simulations", "seed"}
            unknown = sorted(set(arguments) - allowed)
            if unknown:
                return error_result(
                    "INVALID_ARGUMENTS", "지원하지 않는 인자가 있습니다.", {"unknown_fields": unknown}
                )
            missing = sorted(allowed - set(arguments))
            if missing:
                return error_result(
                    "INVALID_ARGUMENTS", "필수 인자가 없습니다.", {"missing_fields": missing}
                )
            return service.simulate_bundle_strategy(**arguments)
        return error_result(
            "UNKNOWN_TOOL",
            f"등록되지 않은 도구입니다: {tool_name}",
            {"available_tools": [schema["name"] for schema in TOOL_SCHEMAS]},
        )
    except DecisionServiceError as error:
        return error_result(
            error.code,
            error.message,
            error.details,
            retryable=error.code in {"PANEL_NOT_FOUND", "INVALID_PANEL"},
        )
    except (TypeError, ValueError) as error:
        return error_result("INVALID_ARGUMENTS", str(error))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tool_name", choices=[schema["name"] for schema in TOOL_SCHEMAS])
    parser.add_argument("--arguments-file", type=Path)
    parser.add_argument("--panel", type=Path)
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    arguments = {}
    if args.arguments_file:
        arguments = json.loads(args.arguments_file.read_text(encoding="utf-8"))
    service = MarginCastDecisionService(args.panel, args.data_dir)
    print(json.dumps(execute_tool(args.tool_name, arguments, service), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
