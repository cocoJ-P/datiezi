"""L3 API 业务服务。"""

from __future__ import annotations

from typing import Any

from l3.runtime.jobs import enqueue_from_diagnosis_run
from l3.runtime.store import ANALYSIS_STORE


class L3ServiceError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


def create_analysis_run(
    *,
    diagnosis_run_id: str,
    record_ids: list[str] | None = None,
    manual_request: bool = False,
    force_new: bool = False,
) -> dict[str, Any]:
    try:
        body = enqueue_from_diagnosis_run(
            diagnosis_run_id,
            record_ids=record_ids,
            manual=manual_request,
            force_new=force_new,
        )
    except KeyError as exc:
        raise L3ServiceError("DIAGNOSIS_RUN_NOT_FOUND", f"找不到 DiagnosisRun：{diagnosis_run_id}", 404) from exc
    except RuntimeError as exc:
        raise L3ServiceError("DIAGNOSIS_RUN_NOT_READY", str(exc), 409) from exc
    if body.get("skipped"):
        raise L3ServiceError("NO_L3_TARGETS", "没有需要进入 L3 的记录。", 400, body)
    return body


def get_analysis_run(analysis_run_id: str) -> dict[str, Any]:
    try:
        return ANALYSIS_STORE.get_run(analysis_run_id)
    except KeyError as exc:
        raise L3ServiceError("ANALYSIS_RUN_NOT_FOUND", f"找不到 AnalysisRun：{analysis_run_id}", 404) from exc


def get_analysis_run_for_diagnosis(diagnosis_run_id: str) -> dict[str, Any] | None:
    return ANALYSIS_STORE.get_run_by_diagnosis(diagnosis_run_id)


def list_results(
    analysis_run_id: str,
    *,
    offset: int = 0,
    limit: int = 50,
    equipment_id: str | None = None,
    record_id: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    get_analysis_run(analysis_run_id)
    return ANALYSIS_STORE.list_results(
        analysis_run_id,
        offset=offset,
        limit=min(limit, 500),
        equipment_id=equipment_id,
        record_id=record_id,
        status=status,
    )


def get_record_evidence(analysis_run_id: str, record_id: str) -> dict[str, Any]:
    get_analysis_run(analysis_run_id)
    try:
        return ANALYSIS_STORE.get_record(analysis_run_id, record_id)
    except KeyError as exc:
        raise L3ServiceError("RECORD_NOT_FOUND", f"找不到记录：{record_id}", 404) from exc
