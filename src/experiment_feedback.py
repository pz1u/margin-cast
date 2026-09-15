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
        return {"record_count": 0, "calibration": None}

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

    return {"record_count": len(records), "calibration": calibration}


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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=f".{self.path.stem}-", suffix=".tmp"
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(records, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    def record(self, value):
        normalized = normalize_feedback(value)
        record = {
            "feedback_id": uuid4().hex,
            "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            **normalized,
            "evaluation": evaluate_feedback(normalized),
        }
        with self._lock:
            records = self._read()
            records.append(record)
            self._write(records)
        return record

    def list(self, menu_id=None):
        with self._lock:
            records = self._read()
        if menu_id is not None:
            records = [row for row in records if row.get("menu_id") == menu_id]
        return records

    def summary(self, menu_id=None):
        records = self.list(menu_id)
        return {
            "status": "ok",
            "menu_id": menu_id,
            **summarize_feedback(records),
            "interpretation_notes": [
                "80% 구간의 실제 포함률은 실험이 쌓인 뒤 약 80%에 가까운지 확인한다.",
                "matched_period 기준선은 날씨·요일·추세 교란이 남아 있어 인과효과의 확정값이 아니다.",
                "피드백은 보정 근거이며 자동 재학습에 바로 사용하지 않는다.",
            ],
        }
