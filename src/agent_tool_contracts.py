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


def _execution_properties(maximum_horizon=180):
    default_description = "생략하면 capabilities.execution_defaults의 해당 도구 값을 사용한다."
    return {
        "horizon_days": {
            "type": "integer",
            "minimum": 1,
            "maximum": maximum_horizon,
            "description": default_description,
        },
        "simulations": {
            "type": "integer",
            "minimum": 100,
            "maximum": 50000,
            "description": default_description,
        },
        "seed": {
            "type": "integer",
            "minimum": 0,
            "maximum": 4294967295,
            "description": default_description,
        },
    }


def _location_schema():
    return {
        "description": (
            "주소 검색, WGS84 좌표, KMA 격자 중 정확히 하나. "
            "주소 문자열과 정확한 위경도는 도구 응답·세션·감사 로그에 남기지 않는다."
        ),
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "address": {"type": "string", "minLength": 1, "maxLength": 200}
                },
                "required": ["address"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "latitude": {"type": "number", "exclusiveMinimum": -90, "exclusiveMaximum": 90},
                    "longitude": {"type": "number", "minimum": -180, "maximum": 180},
                },
                "required": ["latitude", "longitude"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "kma_nx": {"type": "integer", "minimum": 1, "maximum": 149},
                    "kma_ny": {"type": "integer", "minimum": 1, "maximum": 253},
                },
                "required": ["kma_nx", "kma_ny"],
                "additionalProperties": False,
            },
        ],
    }


def _bundle_number_schema(*, value_type="number", minimum=0, maximum=1):
    return {
        "type": value_type,
        "minimum": minimum,
        "maximum": maximum,
        "x-margincast-source-policy": {
            "allowed_sources": ["USER", "ENGINE", "DEFAULT"],
            "planning_scenario_sources": ["ENGINE", "DEFAULT"],
            "llm_generation_allowed": False,
            "on_missing": "MISSING_INPUT",
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


def _interval_schema():
    return {
        "type": "object",
        "properties": {
            name: {"type": "number"} for name in ("mean", "p10", "p90")
        },
        "required": ["mean", "p10", "p90"],
        "additionalProperties": False,
    }


def _experiment_plan_schema():
    return {
        "type": "object",
        "properties": {
            "experiment_name": {
                "type": "string",
                "minLength": 1,
                "maxLength": 80,
            },
            "menu_id": {"type": "string", "minLength": 1, "maxLength": 20},
            "start_date": {"type": "string", "format": "date"},
            "end_date": {"type": "string", "format": "date"},
            "scenario": _price_scenario_schema(),
            "prediction": {
                "type": "object",
                "properties": {
                    "horizon_days": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 180,
                    },
                    "units": _interval_schema(),
                    "contribution_profit": _interval_schema(),
                    "profit_delta": _interval_schema(),
                },
                "required": [
                    "horizon_days",
                    "units",
                    "contribution_profit",
                    "profit_delta",
                ],
                "additionalProperties": False,
            },
            "decision_context": {
                "type": "object",
                "properties": {
                    "engine_version": {"type": "string"},
                    "operation": {"type": "string"},
                    "data_provenance": {
                        "type": "object",
                        "properties": {
                            "source_type": {"type": "string"},
                            "dataset_version": {"type": "string"},
                            "uses_actual_store_data": {"type": "boolean"},
                        },
                        "required": [
                            "source_type",
                            "dataset_version",
                            "uses_actual_store_data",
                        ],
                        "additionalProperties": False,
                    },
                    "weather_context": {
                        "type": "object",
                        "properties": {
                            "source": {"type": "string"},
                            "menu_specific_causal_effect_validated": {"type": "boolean"},
                        },
                        "required": [
                            "source",
                            "menu_specific_causal_effect_validated",
                        ],
                        "additionalProperties": False,
                    },
                    "evidence_quality": {
                        "type": "object",
                        "properties": {
                            "version": {"type": "string"},
                            "formula_fingerprint": {"type": "string"},
                            "label": {
                                "type": "string",
                                "enum": ["LOW", "MEDIUM", "HIGH"],
                            },
                            "score": {"type": "number", "minimum": 0, "maximum": 100},
                            "validation_status": {"type": "string"},
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
            },
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


def _menu_filter_schema():
    return {
        "type": "object",
        "properties": {
            "menu_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 20,
            }
        },
        "required": [],
        "additionalProperties": False,
    }


TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "get_margincast_capabilities",
        "description": (
            "지원 메뉴, 입력 한도, 데이터 범위, 해석 제약과 도구별 execution_defaults를 조회한다."
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
            "기대 판매량, 기대 기여이익, 80% 범위, 성공확률, 근거 품질과 실행 판단을 반환한다."
        ),
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
            "실제 기상청 단기예보를 적용해 가격·할인 대안을 비교한다. "
            "주소, WGS84 좌표, KMA 격자 중 정확히 한 위치 형식을 받는다."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                **PRICE_PROPERTIES,
                "location": _location_schema(),
                **_execution_properties(maximum_horizon=5),
            },
            "required": ["menu_id", "scenarios", "location"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "simulate_bundle_strategy",
        "description": (
            "치킨마요·콜라 세트의 Take Rate, 기존 동시구매 전환율, 신규 수요율과 "
            "다른 주메뉴 잠식률을 명시적으로 입력해 기여이익 분포와 실행 판단을 계산한다. "
            "근거 없는 숫자를 LLM이 만들 수 없으며 값이 없으면 MISSING_INPUT으로 처리한다."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "scenario": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "minLength": 1, "maxLength": 50},
                        "bundle_price": _bundle_number_schema(
                            value_type="integer", minimum=1000, maximum=100000
                        ),
                        "take_rate": _bundle_number_schema(),
                        "copurchase_take_rate": _bundle_number_schema(),
                        "incremental_demand_rate": _bundle_number_schema(),
                        "cannibalization_rate": _bundle_number_schema(),
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
            "required": ["scenario"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "create_experiment_plan",
        "description": "사용자가 실행 의사를 명시한 추천의 ENGINE 예측 스냅샷을 계획으로 저장한다.",
        "strict": True,
        "parameters": _experiment_plan_schema(),
    },
    {
        "type": "function",
        "name": "list_pending_experiments",
        "description": "실제 결과 입력을 기다리는 planned 실험을 조회한다.",
        "strict": True,
        "parameters": _menu_filter_schema(),
    },
    {
        "type": "function",
        "name": "record_experiment_result",
        "description": "사용자가 제공한 실제 결과를 기존 feedback_id의 실험 계획에 연결한다.",
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
                    "required": [
                        "units",
                        "contribution_profit",
                        "baseline_contribution_profit",
                    ],
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
        "description": "완료된 실험의 예측 오차와 보정 지표를 조회한다.",
        "strict": True,
        "parameters": _menu_filter_schema(),
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


def execute_tool(
    tool_name,
    arguments,
    service=None,
    forecast_service=None,
    feedback_store=None,
):
    """도구 이름과 JSON 인자를 받아 항상 JSON 직렬화 가능한 결과를 반환한다."""
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
            service = service or MarginCastDecisionService()
            if arguments:
                return error_result(
                    "INVALID_ARGUMENTS", "get_margincast_capabilities는 인자를 받지 않습니다."
                )
            return service.get_capabilities()
        if tool_name == "compare_price_strategies":
            service = service or MarginCastDecisionService()
            allowed = {"menu_id", "scenarios", "horizon_days", "simulations", "seed"}
            unknown = sorted(set(arguments) - allowed)
            if unknown:
                return error_result(
                    "INVALID_ARGUMENTS", "지원하지 않는 인자가 있습니다.", {"unknown_fields": unknown}
                )
            required = {"menu_id", "scenarios"}
            missing = sorted(required - set(arguments))
            if missing:
                return error_result(
                    "INVALID_ARGUMENTS", "필수 인자가 없습니다.", {"missing_fields": missing}
                )
            return service.compare_price_strategies(**arguments)
        if tool_name == "compare_price_strategies_with_forecast":
            service = service or MarginCastDecisionService()
            allowed = {
                "menu_id",
                "scenarios",
                "location",
                "horizon_days",
                "simulations",
                "seed",
            }
            unknown = sorted(set(arguments) - allowed)
            if unknown:
                return error_result(
                    "INVALID_ARGUMENTS", "지원하지 않는 인자가 있습니다.", {"unknown_fields": unknown}
                )
            missing = sorted({"menu_id", "scenarios", "location"} - set(arguments))
            if missing:
                return error_result(
                    "INVALID_ARGUMENTS", "필수 인자가 없습니다.", {"missing_fields": missing}
                )
            forecast_service = forecast_service or ForecastDecisionService(service)
            return forecast_service.compare_price_strategies(**arguments)
        if tool_name == "simulate_bundle_strategy":
            service = service or MarginCastDecisionService()
            allowed = {"scenario", "horizon_days", "simulations", "seed"}
            unknown = sorted(set(arguments) - allowed)
            if unknown:
                return error_result(
                    "INVALID_ARGUMENTS", "지원하지 않는 인자가 있습니다.", {"unknown_fields": unknown}
                )
            missing = sorted({"scenario"} - set(arguments))
            if missing:
                return error_result(
                    "MISSING_INPUT", "세트 계산에 필요한 입력이 없습니다.", {"missing_fields": missing}
                )
            scenario = arguments["scenario"]
            if isinstance(scenario, dict):
                required_numbers = {
                    "bundle_price",
                    "take_rate",
                    "copurchase_take_rate",
                    "incremental_demand_rate",
                    "cannibalization_rate",
                }
                missing_numbers = sorted(required_numbers - set(scenario))
                if missing_numbers:
                    return error_result(
                        "MISSING_INPUT",
                        "세트 계산에 필요한 숫자 입력의 출처를 확인해야 합니다.",
                        {
                            "missing_fields": [
                                f"scenario.{field_name}" for field_name in missing_numbers
                            ]
                        },
                    )
            return service.simulate_bundle_strategy(**arguments)
        if tool_name == "create_experiment_plan":
            feedback_store = feedback_store or ExperimentFeedbackStore()
            required = set(_experiment_plan_schema()["required"])
            missing = sorted(required - set(arguments))
            if missing:
                return error_result(
                    "MISSING_INPUT",
                    "실험 계획에 필요한 입력이 없습니다.",
                    {"missing_fields": missing},
                )
            return {"status": "ok", "plan": feedback_store.plan(arguments)}
        if tool_name == "list_pending_experiments":
            unknown = sorted(set(arguments) - {"menu_id"})
            if unknown:
                return error_result(
                    "INVALID_ARGUMENTS",
                    "지원하지 않는 인자가 있습니다.",
                    {"unknown_fields": unknown},
                )
            feedback_store = feedback_store or ExperimentFeedbackStore()
            return {
                "status": "ok",
                "plans": feedback_store.pending(arguments.get("menu_id")),
            }
        if tool_name == "record_experiment_result":
            required = {"feedback_id", "baseline_method", "actual"}
            missing = sorted(required - set(arguments))
            actual = arguments.get("actual")
            if isinstance(actual, dict):
                missing.extend(
                    f"actual.{name}"
                    for name in sorted(
                        {
                            "units",
                            "contribution_profit",
                            "baseline_contribution_profit",
                        }
                        - set(actual)
                    )
                )
            if missing:
                return error_result(
                    "MISSING_INPUT",
                    "실제 실험 결과에 필요한 입력이 없습니다.",
                    {"missing_fields": missing},
                )
            feedback_store = feedback_store or ExperimentFeedbackStore()
            record = feedback_store.complete(**arguments)
            return {
                "status": "ok",
                "record": record,
                "summary": feedback_store.summary(),
            }
        if tool_name == "get_feedback_summary":
            unknown = sorted(set(arguments) - {"menu_id"})
            if unknown:
                return error_result(
                    "INVALID_ARGUMENTS",
                    "지원하지 않는 인자가 있습니다.",
                    {"unknown_fields": unknown},
                )
            feedback_store = feedback_store or ExperimentFeedbackStore()
            return feedback_store.summary(arguments.get("menu_id"))
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
        return error_result(error.code, error.message, error.details, retryable=error.retryable)
    except ExperimentFeedbackError as error:
        return error_result(
            error.code,
            error.message,
            error.details,
            retryable=error.code == "FEEDBACK_STORE_UNAVAILABLE",
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
