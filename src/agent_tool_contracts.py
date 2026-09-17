"""AI 에이전트 등록 직전 단계의 MarginCast 함수 도구 계약과 디스패처."""

import argparse
import json
from pathlib import Path

try:
    from .decision_service import DecisionServiceError, MarginCastDecisionService
    from .experiment_feedback import ExperimentFeedbackError, ExperimentFeedbackStore
    from .forecast_decision_service import ForecastDecisionError, ForecastDecisionService
except ImportError:
    from decision_service import DecisionServiceError, MarginCastDecisionService
    from experiment_feedback import ExperimentFeedbackError, ExperimentFeedbackStore
    from forecast_decision_service import ForecastDecisionError, ForecastDecisionService


def _price_scenario_schema():
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": 50},
            "list_price": {"type": "integer", "minimum": 1000, "maximum": 100000},
            "discount": {"type": "integer", "minimum": 0},
        },
        "required": ["name", "list_price", "discount"],
        "additionalProperties": False,
    }


def _interval_schema(*, nonnegative=False):
    number = {"type": "number"}
    if nonnegative:
        number["minimum"] = 0
    return {
        "type": "object",
        "properties": {name: dict(number) for name in ("mean", "p10", "p90")},
        "required": ["mean", "p10", "p90"],
        "additionalProperties": False,
    }


def _prediction_schema():
    return {
        "type": "object",
        "properties": {
            "horizon_days": {"type": "integer", "minimum": 1, "maximum": 180},
            "units": _interval_schema(nonnegative=True),
            "contribution_profit": _interval_schema(),
            "profit_delta": _interval_schema(),
        },
        "required": ["horizon_days", "units", "contribution_profit", "profit_delta"],
        "additionalProperties": False,
    }


def _decision_context_schema():
    return {
        "type": "object",
        "properties": {
            "engine_version": {"type": "string", "minLength": 1, "maxLength": 30},
            "operation": {"type": "string", "minLength": 1, "maxLength": 60},
            "data_provenance": {
                "type": "object",
                "properties": {
                    "source_type": {"type": "string", "minLength": 1, "maxLength": 40},
                    "dataset_version": {"type": "string", "minLength": 1, "maxLength": 60},
                    "uses_actual_store_data": {"type": "boolean"},
                },
                "required": ["source_type", "dataset_version", "uses_actual_store_data"],
                "additionalProperties": False,
            },
            "weather_context": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "minLength": 1, "maxLength": 40},
                    "menu_specific_causal_effect_validated": {"type": "boolean"},
                },
                "required": ["source", "menu_specific_causal_effect_validated"],
                "additionalProperties": False,
            },
            "evidence_quality": {
                "type": "object",
                "properties": {
                    "version": {"type": "string", "minLength": 1, "maxLength": 40},
                    "formula_fingerprint": {"type": "string", "minLength": 1, "maxLength": 64},
                    "label": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
                    "score": {"type": "number", "minimum": 0, "maximum": 100},
                    "validation_status": {"type": "string", "minLength": 1, "maxLength": 60},
                },
                "required": [
                    "version",
                    "formula_fingerprint",
                    "label",
                    "score",
                    "validation_status",
                ],
                "additionalProperties": False,
            },
            "decision_action": {
                "type": "string",
                "enum": ["RECOMMEND", "EXPERIMENT", "HOLD"],
            },
        },
        "required": [
            "engine_version",
            "operation",
            "data_provenance",
            "weather_context",
            "evidence_quality",
            "decision_action",
        ],
        "additionalProperties": False,
    }


def _experiment_plan_parameters():
    return {
        "type": "object",
        "properties": {
            "experiment_name": {"type": "string", "minLength": 1, "maxLength": 80},
            "menu_id": {"type": "string", "minLength": 1, "maxLength": 20},
            "start_date": {"type": "string", "format": "date"},
            "end_date": {"type": "string", "format": "date"},
            "scenario": _price_scenario_schema(),
            "prediction": _prediction_schema(),
            "decision_context": _decision_context_schema(),
        },
        "required": [
            "experiment_name",
            "menu_id",
            "start_date",
            "end_date",
            "scenario",
            "prediction",
            "decision_context",
        ],
        "additionalProperties": False,
    }


def _execution_properties(maximum_horizon=180):
    return {
        "horizon_days": {
            "type": "integer",
            "minimum": 1,
            "maximum": maximum_horizon,
            "description": "생략하면 capabilities.execution_defaults의 해당 도구 값을 사용한다.",
        },
        "simulations": {
            "type": "integer",
            "minimum": 100,
            "maximum": 50000,
            "description": "생략하면 capabilities.execution_defaults의 해당 도구 값을 사용한다.",
        },
        "seed": {
            "type": "integer",
            "minimum": 0,
            "maximum": 4294967295,
            "description": "생략하면 capabilities.execution_defaults의 해당 도구 값을 사용한다.",
        },
    }


PRICE_PROPERTIES = {
    "menu_id": {
        "type": "string",
        "description": "capabilities 응답의 supported_menus에 포함된 메뉴 ID",
    },
    "scenarios": {
        "type": "array",
        "description": "비교할 가격·할인 대안. 현재 가격은 생략하면 자동 추가된다.",
        "minItems": 1,
        "maxItems": 8,
        "items": _price_scenario_schema(),
    },
}

TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "get_margincast_capabilities",
        "description": (
            "지원 메뉴, 입력 한도, 데이터 범위, 해석 제약과 도구별 execution_defaults를 조회한다. "
            "전략 비교 전에 먼저 호출한다."
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
        "description": "한 메뉴의 가격 또는 할인 대안을 현재 가격과 비교한다.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {**PRICE_PROPERTIES, **_execution_properties()},
            "required": ["menu_id", "scenarios"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "compare_price_strategies_with_forecast",
        "description": (
            "주소, WGS84 위경도 또는 KMA 격자 중 하나로 단기예보를 조회해 가격·할인 대안을 비교한다. "
            "응답에는 주소 원문과 정확한 위경도를 포함하지 않는다."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                **PRICE_PROPERTIES,
                "location": {
                    "type": "object",
                    "properties": {
                        "address": {"type": "string", "minLength": 1, "maxLength": 200},
                        "latitude": {"type": "number", "minimum": -90, "maximum": 90},
                        "longitude": {"type": "number", "minimum": -180, "maximum": 180},
                        "nx": {"type": "integer", "minimum": 1},
                        "ny": {"type": "integer", "minimum": 1},
                    },
                    "oneOf": [
                        {"required": ["address"]},
                        {"required": ["latitude", "longitude"]},
                        {"required": ["nx", "ny"]},
                    ],
                    "additionalProperties": False,
                },
                **_execution_properties(maximum_horizon=5),
            },
            "required": ["location", "menu_id", "scenarios"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "simulate_bundle_strategy",
        "description": (
            "주메뉴와 한 개 이상의 구성 메뉴로 세트를 정의하고 전환·신규 수요·잠식 가정을 계산한다."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "main_menu_id": {"type": "string", "minLength": 1, "maxLength": 20},
                "component_menu_ids": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 4,
                    "uniqueItems": True,
                    "items": {"type": "string", "minLength": 1, "maxLength": 20},
                },
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
                **_execution_properties(),
            },
            "required": ["main_menu_id", "component_menu_ids", "scenario"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "create_experiment_plan",
        "description": "사용자가 실행하기로 한 전략의 예측 스냅샷과 실험 기간을 저장한다.",
        "strict": True,
        "parameters": _experiment_plan_parameters(),
    },
    {
        "type": "function",
        "name": "list_pending_experiments",
        "description": "아직 실제 결과가 연결되지 않은 실험 계획을 조회한다.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {"menu_id": {"type": "string", "minLength": 1, "maxLength": 20}},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "record_experiment_result",
        "description": "대기 중인 실험 계획에 사용자가 제공한 실제 결과를 연결한다.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "feedback_id": {"type": "string", "minLength": 1},
                "baseline_method": {
                    "type": "string",
                    "enum": ["matched_period", "parallel_control"],
                },
                "actual": {
                    "type": "object",
                    "properties": {
                        "units": {"type": "integer", "minimum": 0},
                        "contribution_profit": {"type": "number"},
                        "baseline_contribution_profit": {"type": "number"},
                    },
                    "required": ["units", "contribution_profit", "baseline_contribution_profit"],
                    "additionalProperties": False,
                },
            },
            "required": ["feedback_id", "baseline_method", "actual"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_feedback_summary",
        "description": "완료된 실험의 오차, 80% 구간 포함률과 방향 정확도를 조회한다.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {"menu_id": {"type": "string", "minLength": 1, "maxLength": 20}},
            "required": [],
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


def _validate_fields(arguments, *, allowed, required):
    unknown = sorted(set(arguments) - set(allowed))
    if unknown:
        return error_result(
            "INVALID_ARGUMENTS", "지원하지 않는 인자가 있습니다.", {"unknown_fields": unknown}
        )
    missing = sorted(set(required) - set(arguments))
    if missing:
        return error_result(
            "INVALID_ARGUMENTS", "필수 인자가 없습니다.", {"missing_fields": missing}
        )
    return None


def execute_tool(
    tool_name,
    arguments,
    service=None,
    *,
    forecast_service=None,
    feedback_store=None,
):
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
            invalid = _validate_fields(
                arguments,
                allowed={"menu_id", "scenarios", "horizon_days", "simulations", "seed"},
                required={"menu_id", "scenarios"},
            )
            return invalid or service.compare_price_strategies(**arguments)

        if tool_name == "compare_price_strategies_with_forecast":
            invalid = _validate_fields(
                arguments,
                allowed={"location", "menu_id", "scenarios", "horizon_days", "simulations", "seed"},
                required={"location", "menu_id", "scenarios"},
            )
            if invalid:
                return invalid
            forecast_service = forecast_service or ForecastDecisionService(service)
            return forecast_service.compare_price_strategies(**arguments)

        if tool_name == "simulate_bundle_strategy":
            invalid = _validate_fields(
                arguments,
                allowed={
                    "main_menu_id",
                    "component_menu_ids",
                    "scenario",
                    "horizon_days",
                    "simulations",
                    "seed",
                },
                required={"main_menu_id", "component_menu_ids", "scenario"},
            )
            return invalid or service.simulate_bundle_strategy(**arguments)

        if tool_name == "create_experiment_plan":
            plan_schema = _experiment_plan_parameters()
            invalid = _validate_fields(
                arguments,
                allowed=set(plan_schema["properties"]),
                required=set(plan_schema["required"]),
            )
            if invalid:
                return invalid
            feedback_store = feedback_store or ExperimentFeedbackStore()
            return {"status": "ok", "plan": feedback_store.plan(arguments)}

        if tool_name in {"list_pending_experiments", "get_feedback_summary"}:
            invalid = _validate_fields(arguments, allowed={"menu_id"}, required=set())
            if invalid:
                return invalid
            feedback_store = feedback_store or ExperimentFeedbackStore()
            if tool_name == "list_pending_experiments":
                return {"status": "ok", "plans": feedback_store.pending(**arguments)}
            return feedback_store.summary(**arguments)

        if tool_name == "record_experiment_result":
            invalid = _validate_fields(
                arguments,
                allowed={"feedback_id", "baseline_method", "actual"},
                required={"feedback_id", "baseline_method", "actual"},
            )
            if invalid:
                return invalid
            feedback_store = feedback_store or ExperimentFeedbackStore()
            record = feedback_store.complete(**arguments)
            return {
                "status": "ok",
                "record": record,
                "summary": feedback_store.summary(record["menu_id"]),
            }

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
    except ForecastDecisionError as error:
        return error_result(error.code, error.message, error.details, error.retryable)
    except ExperimentFeedbackError as error:
        return error_result(error.code, error.message, error.details)
    except (TypeError, ValueError) as error:
        return error_result("INVALID_ARGUMENTS", str(error))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tool_name", choices=[schema["name"] for schema in TOOL_SCHEMAS])
    parser.add_argument("--arguments-file", type=Path)
    parser.add_argument("--panel", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--feedback-store", type=Path)
    args = parser.parse_args()
    arguments = {}
    if args.arguments_file:
        arguments = json.loads(args.arguments_file.read_text(encoding="utf-8"))
    service = MarginCastDecisionService(args.panel, args.data_dir)
    feedback_store = ExperimentFeedbackStore(args.feedback_store) if args.feedback_store else None
    print(
        json.dumps(
            execute_tool(
                args.tool_name,
                arguments,
                service,
                feedback_store=feedback_store,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
