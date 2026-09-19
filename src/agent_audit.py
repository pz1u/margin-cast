"""추천과 실험 피드백의 최소 감사 추적을 민감 원문 없이 저장한다."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading

from .agent_runtime import AgentRunResult
from .agent_schemas import AgentResponse, AgentResponseStatus, JsonObject


class AgentAuditError(ValueError):
    pass


def tool_result_reference(raw: JsonObject) -> str:
    canonical = json.dumps(
        raw,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


class AgentAuditStore:
    """추천 상태만 저장하며 프롬프트·설명·민감 Tool 인자는 저장하지 않는다."""

    def __init__(self, path=None) -> None:
        root = Path(__file__).resolve().parents[1]
        self.path = Path(path or root / "data" / "audit" / "agent_audit.json")
        self._lock = threading.RLock()

    def _read(self) -> list[JsonObject]:
        if not self.path.exists():
            return []
        try:
            records = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AgentAuditError("Agent 감사 로그를 읽을 수 없습니다.") from error
        if not isinstance(records, list) or not all(
            isinstance(record, dict) for record in records
        ):
            raise AgentAuditError("Agent 감사 로그 형식이 올바르지 않습니다.")
        return records

    def _write(self, records: list[JsonObject]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                dir=self.path.parent,
                prefix=f".{self.path.stem}-",
                suffix=".tmp",
            )
        except OSError as error:
            raise AgentAuditError("Agent 감사 로그 저장소를 준비할 수 없습니다.") from error
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(records, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
        except OSError as error:
            raise AgentAuditError("Agent 감사 로그를 저장할 수 없습니다.") from error
        finally:
            if temporary_path.exists():
                try:
                    temporary_path.unlink()
                except OSError:
                    pass

    def record_recommendation(
        self,
        run_result: AgentRunResult,
        agent_response: AgentResponse,
        policy_validation: JsonObject,
    ) -> JsonObject:
        if agent_response.status is not AgentResponseStatus.COMPLETED:
            raise AgentAuditError("완료된 추천만 감사 로그에 기록할 수 있습니다.")
        recommendation_id = agent_response.recommendation_id
        if not recommendation_id:
            raise AgentAuditError("recommendation_id가 필요합니다.")
        selected = agent_response.facts.get("selected_scenario_id", {}).get("value")
        record = {
            "recommendation_id": recommendation_id,
            "prompt_version": run_result.prompt_version,
            "model_identifier": run_result.model_identifier,
            "called_tool_names": [run_result.tool_call.name],
            "selected_scenario_id": selected,
            "engine_decision": policy_validation.get("engine_decision"),
            "presented_decision": policy_validation.get("presented_decision"),
            "response_policy_status": policy_validation.get("status"),
            "violation_codes": list(policy_validation.get("violations", [])),
            "feedback_id": None,
            "feedback_status": "not_planned",
            "tool_result_ref": tool_result_reference(run_result.tool_result.raw),
        }
        with self._lock:
            records = self._read()
            if any(
                item.get("recommendation_id") == recommendation_id
                for item in records
            ):
                raise AgentAuditError("이미 기록된 recommendation_id입니다.")
            records.append(record)
            self._write(records)
        return deepcopy(record)

    def update_feedback(
        self,
        recommendation_id: str,
        *,
        feedback_id: str,
        feedback_status: str,
        called_tool_name: str,
    ) -> JsonObject:
        if feedback_status not in {"planned", "completed"}:
            raise AgentAuditError("feedback_status 전이를 확인하세요.")
        if not isinstance(feedback_id, str) or not feedback_id.strip():
            raise AgentAuditError("feedback_id가 필요합니다.")
        if feedback_id == recommendation_id:
            raise AgentAuditError("recommendation_id와 feedback_id는 달라야 합니다.")
        with self._lock:
            records = self._read()
            position = next(
                (
                    index
                    for index, record in enumerate(records)
                    if record.get("recommendation_id") == recommendation_id
                ),
                None,
            )
            if position is None:
                raise AgentAuditError("추천 감사 기록을 찾을 수 없습니다.")
            record = records[position]
            current_status = record.get("feedback_status")
            if feedback_status == "planned" and current_status != "not_planned":
                raise AgentAuditError("실험 계획 상태 전이가 올바르지 않습니다.")
            if feedback_status == "completed" and (
                current_status != "planned" or record.get("feedback_id") != feedback_id
            ):
                raise AgentAuditError("실험 완료 상태 전이가 올바르지 않습니다.")
            updated = deepcopy(record)
            updated["feedback_id"] = feedback_id
            updated["feedback_status"] = feedback_status
            updated["called_tool_names"] = [
                *updated.get("called_tool_names", []),
                called_tool_name,
            ]
            records[position] = updated
            self._write(records)
        return deepcopy(updated)

    def get(self, recommendation_id: str) -> JsonObject | None:
        with self._lock:
            record = next(
                (
                    row
                    for row in self._read()
                    if row.get("recommendation_id") == recommendation_id
                ),
                None,
            )
        return None if record is None else deepcopy(record)

    def list(self) -> list[JsonObject]:
        with self._lock:
            return deepcopy(self._read())
