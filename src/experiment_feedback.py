"""시뮬레이션 예측과 실제 실험 결과를 보존하고 보정 지표를 계산한다."""

import json
import math
import os
import tempfile
import threading
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4


BASELINE_METHODS = {"matched_period", "parallel_control"}
INTERVAL_FIELDS = {"mean", "p10", "p90"}
DECISION_ACTIONS = {"RECOMMEND", "EXPERIMENT", "HOLD"}


class ExperimentFeedbackError(ValueError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = {} if details is None else details


def _require_exact_fields(value, expected, label):
    if not isinstance(value, dict):
        raise ExperimentFeedbackError("INVALID_FEEDBACK", f"{label}은 객체여야 합니다.")
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing or unknown:
        raise ExperimentFeedbackError(
            "INVALID_FEEDBACK",
            f"{label}의 필드를 확인하세요.",
            {"field": label, "missing_fields": missing, "unknown_fields": unknown},
        )


def _finite_number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExperimentFeedbackError("INVALID_FEEDBACK", f"{label}은 숫자여야 합니다.")
    number = float(value)
    if not math.isfinite(number):
        raise ExperimentFeedbackError("INVALID_FEEDBACK", f"{label}은 유한한 숫자여야 합니다.")
    return number


def _parse_date(value, label):
    if not isinstance(value, str):
        raise ExperimentFeedbackError("INVALID_FEEDBACK", f"{label}은 YYYY-MM-DD 형식이어야 합니다.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ExperimentFeedbackError(
            "INVALID_FEEDBACK", f"{label}은 YYYY-MM-DD 형식이어야 합니다."
        ) from None
    if parsed.isoformat() != value:
        raise ExperimentFeedbackError("INVALID_FEEDBACK", f"{label}은 YYYY-MM-DD 형식이어야 합니다.")
    return parsed


def _normalize_interval(value, label, *, nonnegative=False):
    _require_exact_fields(value, INTERVAL_FIELDS, label)
    normalized = {
        field: _finite_number(value[field], f"{label}.{field}")
        for field in ("mean", "p10", "p90")
    }
    if not normalized["p10"] <= normalized["mean"] <= normalized["p90"]:
        raise ExperimentFeedbackError(
            "INVALID_FEEDBACK", f"{label}은 p10 <= mean <= p90 순서여야 합니다."
        )
    if nonnegative and normalized["p10"] < 0:
        raise ExperimentFeedbackError("INVALID_FEEDBACK", f"{label}은 음수일 수 없습니다.")
    return normalized


def _required_string(value, label, maximum=80):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
        raise ExperimentFeedbackError(
            "INVALID_FEEDBACK", f"{label}은 1~{maximum}자의 문자열이어야 합니다."
        )
    return value.strip()


def _normalize_decision_context(value):
    fields = {
        "engine_version",
        "operation",
        "data_provenance",
        "weather_context",
        "evidence_quality",
        "decision_action",
    }
    _require_exact_fields(value, fields, "decision_context")

    provenance_fields = {"source_type", "dataset_version", "uses_actual_store_data"}
    provenance = value["data_provenance"]
    _require_exact_fields(provenance, provenance_fields, "decision_context.data_provenance")
    if not isinstance(provenance["uses_actual_store_data"], bool):
        raise ExperimentFeedbackError(
            "INVALID_FEEDBACK",
            "decision_context.data_provenance.uses_actual_store_data는 boolean이어야 합니다.",
        )

    weather_fields = {"source", "menu_specific_causal_effect_validated"}
    weather = value["weather_context"]
    _require_exact_fields(weather, weather_fields, "decision_context.weather_context")
    if not isinstance(weather["menu_specific_causal_effect_validated"], bool):
        raise ExperimentFeedbackError(
            "INVALID_FEEDBACK",
            "decision_context.weather_context.menu_specific_causal_effect_validated는 boolean이어야 합니다.",
        )

    quality_fields = {"version", "label", "score", "validation_status"}
    quality = value["evidence_quality"]
    _require_exact_fields(quality, quality_fields, "decision_context.evidence_quality")
    if quality["label"] not in {"LOW", "MEDIUM", "HIGH"}:
        raise ExperimentFeedbackError(
            "INVALID_FEEDBACK", "decision_context.evidence_quality.label을 확인하세요."
        )
    score = _finite_number(quality["score"], "decision_context.evidence_quality.score")
    if not 0 <= score <= 100:
        raise ExperimentFeedbackError(
            "INVALID_FEEDBACK", "decision_context.evidence_quality.score는 0~100이어야 합니다."
        )
    if value["decision_action"] not in DECISION_ACTIONS:
        raise ExperimentFeedbackError(
            "INVALID_FEEDBACK", "decision_context.decision_action을 확인하세요."
        )

    return {
        "engine_version": _required_string(value["engine_version"], "decision_context.engine_version", 30),
        "operation": _required_string(value["operation"], "decision_context.operation", 60),
        "data_provenance": {
            "source_type": _required_string(
                provenance["source_type"], "decision_context.data_provenance.source_type", 40
            ),
            "dataset_version": _required_string(
                provenance["dataset_version"],
                "decision_context.data_provenance.dataset_version",
                60,
            ),
            "uses_actual_store_data": provenance["uses_actual_store_data"],
        },
        "weather_context": {
            "source": _required_string(
                weather["source"], "decision_context.weather_context.source", 40
            ),
            "menu_specific_causal_effect_validated": weather[
                "menu_specific_causal_effect_validated"
            ],
        },
        "evidence_quality": {
            "version": _required_string(
                quality["version"], "decision_context.evidence_quality.version", 40
            ),
            "label": quality["label"],
            "score": score,
            "validation_status": _required_string(
                quality["validation_status"],
                "decision_context.evidence_quality.validation_status",
                60,
            ),
        },
        "decision_action": value["decision_action"],
    }


def normalize_feedback(value):
    """저장 전에 실험 기간·시나리오·예측·실제 결과의 계약을 검증한다."""
    expected = {
        "experiment_name",
        "menu_id",
        "start_date",
        "end_date",
        "baseline_method",
        "scenario",
        "prediction",
        "decision_context",
        "actual",
    }
    _require_exact_fields(value, expected, "feedback")

    name = value["experiment_name"]
    menu_id = value["menu_id"]
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ExperimentFeedbackError(
            "INVALID_FEEDBACK", "experiment_name은 1~80자의 문자열이어야 합니다."
        )
    if not isinstance(menu_id, str) or not 1 <= len(menu_id.strip()) <= 20:
        raise ExperimentFeedbackError(
            "INVALID_FEEDBACK", "menu_id는 1~20자의 문자열이어야 합니다."
        )

    start = _parse_date(value["start_date"], "start_date")
    end = _parse_date(value["end_date"], "end_date")
    observed_days = (end - start).days + 1
    if observed_days < 1:
        raise ExperimentFeedbackError(
            "INVALID_FEEDBACK", "end_date는 start_date보다 빠를 수 없습니다."
        )
    baseline_method = value["baseline_method"]
    if not isinstance(baseline_method, str) or baseline_method not in BASELINE_METHODS:
        raise ExperimentFeedbackError(
            "INVALID_FEEDBACK",
            "baseline_method는 matched_period 또는 parallel_control이어야 합니다.",
        )

    scenario_fields = {"name", "list_price", "discount"}
    _require_exact_fields(value["scenario"], scenario_fields, "scenario")
    scenario = value["scenario"]
    scenario_name = scenario["name"]
    if not isinstance(scenario_name, str) or not 1 <= len(scenario_name.strip()) <= 50:
        raise ExperimentFeedbackError("INVALID_FEEDBACK", "scenario.name은 1~50자여야 합니다.")
    list_price = scenario["list_price"]
    discount = scenario["discount"]
    if any(isinstance(item, bool) or not isinstance(item, int) for item in (list_price, discount)):
        raise ExperimentFeedbackError(
            "INVALID_FEEDBACK", "scenario의 정가와 할인액은 원 단위 정수여야 합니다."
        )
    if not 1_000 <= list_price <= 100_000 or not 0 <= discount < list_price:
        raise ExperimentFeedbackError(
            "INVALID_FEEDBACK", "정가는 1,000~100,000원, 할인액은 0 이상 정가 미만이어야 합니다."
        )

    prediction_fields = {
        "horizon_days",
        "units",
        "contribution_profit",
        "profit_delta",
    }
    _require_exact_fields(value["prediction"], prediction_fields, "prediction")
    prediction = value["prediction"]
    horizon = prediction["horizon_days"]
    if isinstance(horizon, bool) or not isinstance(horizon, int) or not 1 <= horizon <= 180:
        raise ExperimentFeedbackError(
            "INVALID_FEEDBACK", "prediction.horizon_days는 1~180의 정수여야 합니다."
        )
    if horizon != observed_days:
        raise ExperimentFeedbackError(
            "PERIOD_MISMATCH",
            "실제 실험 기간과 예측 기간이 일치해야 합니다.",
            {"observed_days": observed_days, "horizon_days": horizon},
        )

    actual_fields = {"units", "contribution_profit", "baseline_contribution_profit"}
    _require_exact_fields(value["actual"], actual_fields, "actual")
    actual = value["actual"]
    actual_units = actual["units"]
    if isinstance(actual_units, bool) or not isinstance(actual_units, int) or actual_units < 0:
        raise ExperimentFeedbackError("INVALID_FEEDBACK", "actual.units는 0 이상의 정수여야 합니다.")

    return {
        "experiment_name": name.strip(),
        "menu_id": menu_id.strip(),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "baseline_method": baseline_method,
        "scenario": {
            "name": scenario_name.strip(),
            "list_price": list_price,
            "discount": discount,
        },
        "prediction": {
            "horizon_days": horizon,
            "units": _normalize_interval(prediction["units"], "prediction.units", nonnegative=True),
            "contribution_profit": _normalize_interval(
                prediction["contribution_profit"], "prediction.contribution_profit"
            ),
            "profit_delta": _normalize_interval(prediction["profit_delta"], "prediction.profit_delta"),
        },
        "decision_context": _normalize_decision_context(value["decision_context"]),
        "actual": {
            "units": actual_units,
            "contribution_profit": _finite_number(
                actual["contribution_profit"], "actual.contribution_profit"
            ),
            "baseline_contribution_profit": _finite_number(
                actual["baseline_contribution_profit"],
                "actual.baseline_contribution_profit",
            ),
        },
    }


def normalize_experiment_plan(value):
    """실행 전에 예측 분포를 고정하며 실제 기준선 방식과 결과는 받지 않는다."""
    expected = {
        "experiment_name",
        "menu_id",
        "start_date",
        "end_date",
        "scenario",
        "prediction",
        "decision_context",
    }
    _require_exact_fields(value, expected, "plan")
    normalized = normalize_feedback(
        {
            **value,
            "baseline_method": "matched_period",
            "actual": {
                "units": 0,
                "contribution_profit": 0,
                "baseline_contribution_profit": 0,
            },
        }
    )
    normalized.pop("baseline_method")
    normalized.pop("actual")
    return normalized


def _evaluate_interval(prediction, actual):
    error = actual - prediction["mean"]
    return {
        "error": float(error),
        "absolute_error": float(abs(error)),
        "absolute_percentage_error": (
            None if actual == 0 else float(abs(error) / abs(actual))
        ),
        "within_80_interval": bool(prediction["p10"] <= actual <= prediction["p90"]),
    }


def evaluate_feedback(feedback):
    prediction = feedback["prediction"]
    actual = feedback["actual"]
    actual_profit_delta = (
        actual["contribution_profit"] - actual["baseline_contribution_profit"]
    )
    predicted_delta = prediction["profit_delta"]["mean"]
    return {
        "units": _evaluate_interval(prediction["units"], actual["units"]),
        "contribution_profit": _evaluate_interval(
            prediction["contribution_profit"], actual["contribution_profit"]
        ),
        "profit_delta": {
            **_evaluate_interval(prediction["profit_delta"], actual_profit_delta),
            "actual": float(actual_profit_delta),
            "direction_correct": bool((predicted_delta > 0) == (actual_profit_delta > 0)),
        },
    }


def _mean(values):
    return None if not values else float(sum(values) / len(values))


def summarize_feedback(records):
    """저장된 실험의 예측 오차와 80% 구간 보정 상태를 집계한다."""
    if not records:
        return {
            "record_count": 0,
            "calibration": None,
            "evidence_quality_calibration": [],
        }

    calibration = {}
    for metric in ("units", "contribution_profit", "profit_delta"):
        evaluations = [record["evaluation"][metric] for record in records]
        percentage_errors = [
            row["absolute_percentage_error"]
            for row in evaluations
            if row["absolute_percentage_error"] is not None
        ]
        summary = {
            "mean_error": _mean([row["error"] for row in evaluations]),
            "mean_absolute_error": _mean([row["absolute_error"] for row in evaluations]),
            "mean_absolute_percentage_error": _mean(percentage_errors),
            "p80_coverage": _mean(
                [float(row["within_80_interval"]) for row in evaluations]
            ),
        }
        if metric == "profit_delta":
            summary["direction_accuracy"] = _mean(
                [float(row["direction_correct"]) for row in evaluations]
            )
        calibration[metric] = summary

    quality_groups = {}
    for record in records:
        quality = record.get("decision_context", {}).get("evidence_quality")
        if not quality:
            continue
        key = (quality["version"], quality["label"])
        quality_groups.setdefault(key, []).append(record["evaluation"]["profit_delta"])
    quality_calibration = []
    for (version, label), evaluations in sorted(quality_groups.items()):
        quality_calibration.append(
            {
                "version": version,
                "label": label,
                "record_count": len(evaluations),
                "profit_delta_mean_absolute_error": _mean(
                    [row["absolute_error"] for row in evaluations]
                ),
                "profit_delta_p80_coverage": _mean(
                    [float(row["within_80_interval"]) for row in evaluations]
                ),
                "profit_delta_direction_accuracy": _mean(
                    [float(row["direction_correct"]) for row in evaluations]
                ),
            }
        )

    return {
        "record_count": len(records),
        "calibration": calibration,
        "evidence_quality_calibration": quality_calibration,
    }


class ExperimentFeedbackStore:
    def __init__(self, path=None):
        root = Path(__file__).resolve().parents[1]
        self.path = Path(path or root / "data" / "feedback" / "experiment_feedback.json")
        self._lock = threading.RLock()

    def _read(self):
        if not self.path.exists():
            return []
        try:
            records = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ExperimentFeedbackError(
                "FEEDBACK_STORE_CORRUPT", "실험 피드백 저장소를 읽을 수 없습니다."
            ) from error
        if not isinstance(records, list) or not all(isinstance(row, dict) for row in records):
            raise ExperimentFeedbackError(
                "FEEDBACK_STORE_CORRUPT", "실험 피드백 저장소 형식이 올바르지 않습니다."
            )
        return records

    def _write(self, records):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                dir=self.path.parent, prefix=f".{self.path.stem}-", suffix=".tmp"
            )
        except OSError as error:
            raise ExperimentFeedbackError(
                "FEEDBACK_STORE_UNAVAILABLE", "실험 피드백 저장소를 준비할 수 없습니다."
            ) from error
        temporary_path = Path(temporary_name)
        try:
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                    json.dump(records, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary_path, self.path)
            except OSError as error:
                raise ExperimentFeedbackError(
                    "FEEDBACK_STORE_UNAVAILABLE", "실험 피드백을 저장할 수 없습니다."
                ) from error
        finally:
            if temporary_path.exists():
                try:
                    temporary_path.unlink()
                except OSError:
                    pass

    def record(self, value):
        normalized = normalize_feedback(value)
        recorded_at = datetime.now().astimezone().isoformat(timespec="seconds")
        record = {
            "feedback_id": uuid4().hex,
            "status": "completed",
            "recorded_at": recorded_at,
            "planned_at": recorded_at,
            "completed_at": recorded_at,
            **normalized,
            "evaluation": evaluate_feedback(normalized),
        }
        with self._lock:
            records = self._read()
            records.append(record)
            self._write(records)
        return record

    def plan(self, value):
        normalized = normalize_experiment_plan(value)
        record = {
            "feedback_id": uuid4().hex,
            "status": "planned",
            "planned_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            **normalized,
        }
        with self._lock:
            records = self._read()
            records.append(record)
            self._write(records)
        return record

    def complete(self, feedback_id, baseline_method, actual):
        if not isinstance(feedback_id, str) or not feedback_id.strip():
            raise ExperimentFeedbackError(
                "INVALID_FEEDBACK", "feedback_id는 비어 있지 않은 문자열이어야 합니다."
            )
        with self._lock:
            records = self._read()
            position = next(
                (
                    index
                    for index, row in enumerate(records)
                    if row.get("feedback_id") == feedback_id
                ),
                None,
            )
            if position is None:
                raise ExperimentFeedbackError(
                    "FEEDBACK_NOT_FOUND", "해당 실험 계획을 찾을 수 없습니다."
                )
            existing = records[position]
            if existing.get("status", "completed") != "planned":
                raise ExperimentFeedbackError(
                    "FEEDBACK_ALREADY_COMPLETED", "이미 실제 결과가 기록된 실험입니다."
                )

            complete_value = {
                field: existing[field]
                for field in (
                    "experiment_name",
                    "menu_id",
                    "start_date",
                    "end_date",
                    "scenario",
                    "prediction",
                    "decision_context",
                )
            }
            complete_value["baseline_method"] = baseline_method
            complete_value["actual"] = actual
            normalized = normalize_feedback(complete_value)
            completed_at = datetime.now().astimezone().isoformat(timespec="seconds")
            record = {
                "feedback_id": existing["feedback_id"],
                "status": "completed",
                "recorded_at": completed_at,
                "planned_at": existing["planned_at"],
                "completed_at": completed_at,
                **normalized,
                "evaluation": evaluate_feedback(normalized),
            }
            records[position] = record
            self._write(records)
        return record

    def list(self, menu_id=None):
        with self._lock:
            records = self._read()
        if menu_id is not None:
            records = [row for row in records if row.get("menu_id") == menu_id]
        return records

    def pending(self, menu_id=None):
        return [
            row
            for row in self.list(menu_id)
            if row.get("status", "completed") == "planned"
        ]

    def summary(self, menu_id=None):
        all_records = self.list(menu_id)
        records = [row for row in all_records if row.get("evaluation") is not None]
        return {
            "status": "ok",
            "menu_id": menu_id,
            "planned_count": len(all_records) - len(records),
            **summarize_feedback(records),
            "interpretation_notes": [
                "80% 구간의 실제 포함률은 실험이 쌓인 뒤 약 80%에 가까운지 확인한다.",
                "matched_period 기준선은 날씨·요일·추세 교란이 남아 있어 인과효과의 확정값이 아니다.",
                "피드백은 보정 근거이며 자동 재학습에 바로 사용하지 않는다.",
            ],
        }
