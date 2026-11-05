"""L5 服务门面。"""

from __future__ import annotations

from typing import Any

from l5 import AGGREGATION_SCHEMA_VERSION, POLICY_VERSION, RECOMMENDATION_RULE_VERSION, __version__
from l5.jobs import run_l5
from l5.provenance import build_field_provenance
from l5.store import L5_STORE
from l5.input_adapter import build_l5_input_context


class L5ServiceError(Exception):
    def __init__(self, message: str, *, code: str = "l5_error", status_code: int = 400, details: Any = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details


def status() -> dict[str, Any]:
    return {
        "layer": "l5a",
        "version": __version__,
        "aggregation_schema_version": AGGREGATION_SCHEMA_VERSION,
        "recommendation_rule_version": RECOMMENDATION_RULE_VERSION,
        "policy_version": POLICY_VERSION,
        "llm_enabled": False,
        "risk_grading_enabled": False,
        "priority_engine_enabled": False,
        "features": [
            "final_diagnosis_assessment",
            "verification_recommendations",
            "evidence_provenance",
            "explicit_run_binding",
        ],
    }


def create_run(**kwargs: Any) -> dict[str, Any]:
    try:
        return run_l5(**kwargs)
    except KeyError as exc:
        raise L5ServiceError(f"来源运行不存在：{exc}", code="source_not_found", status_code=404) from exc
    except RuntimeError as exc:
        raise L5ServiceError(str(exc), code="create_failed", status_code=400) from exc


def get_run(l5_run_id: str) -> dict[str, Any]:
    try:
        return {"l5_run": L5_STORE.get(l5_run_id)}
    except KeyError as exc:
        raise L5ServiceError(f"L5Run 不存在：{l5_run_id}", code="not_found", status_code=404) from exc


def list_runs(*, offset: int = 0, limit: int = 50) -> dict[str, Any]:
    return L5_STORE.list_runs(offset=offset, limit=limit)


def list_results(
    l5_run_id: str,
    *,
    offset: int = 0,
    limit: int = 50,
    record_id: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    try:
        L5_STORE.get(l5_run_id)
    except KeyError as exc:
        raise L5ServiceError(f"L5Run 不存在：{l5_run_id}", code="not_found", status_code=404) from exc
    return L5_STORE.list_records(l5_run_id, offset=offset, limit=limit, record_id=record_id, status=status)


def get_record(l5_run_id: str, record_id: str) -> dict[str, Any]:
    try:
        L5_STORE.get(l5_run_id)
        return L5_STORE.get_record(l5_run_id, record_id)
    except KeyError as exc:
        raise L5ServiceError(f"记录不存在：{record_id}", code="not_found", status_code=404) from exc


def get_provenance(l5_run_id: str, record_id: str, field: str | None = None) -> dict[str, Any]:
    rec = get_record(l5_run_id, record_id)
    assessment = rec.get("assessment") or {}
    stored = rec.get("provenance") or {}
    if field:
        # 优先用存储的全量；若无则尝试重建（需要上下文）
        fields = (stored.get("fields") if isinstance(stored, dict) else None) or {}
        if field in fields:
            return fields[field]
        run = L5_STORE.get(l5_run_id)
        try:
            ctx = build_l5_input_context(
                record_id=record_id,
                diagnosis_run_id=run.get("diagnosis_run_id"),
                analysis_run_id=run.get("analysis_run_id"),
                l4_run_id=run.get("l4_run_id"),
                l3_reasoning_run_id=run.get("l3_reasoning_run_id"),
                l4_reasoning_run_id=run.get("l4_reasoning_run_id"),
                enrichment_run_id=run.get("enrichment_run_id"),
            )
            return build_field_provenance(assessment, ctx, field)
        except Exception as exc:  # noqa: BLE001
            return {
                "field_key": field,
                "untraceable_reason": str(exc),
                "field_slot": (assessment.get("fields") or {}).get(field),
            }
    return stored or {"fields": {}, "note": "provenance_empty"}
