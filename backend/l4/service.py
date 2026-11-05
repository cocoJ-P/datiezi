"""L4 API 服务层。"""

from __future__ import annotations

from typing import Any

from l4 import CAUSAL_KNOWLEDGE_VERSION, EQUIPMENT_MAPPING_VERSION, L4_SCHEMA_VERSION, __version__
from l4.causal_knowledge.loader import get_catalog
from l4.jobs import run_l4_for_analysis
from l4.mapping import mapping_version, resolve_equipment_mapping
from l4.store import L4_STORE


class L4ServiceError(Exception):
    def __init__(self, message: str, *, code: str = "l4_error", status_code: int = 400, details: Any = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details


def status() -> dict[str, Any]:
    catalog = get_catalog()
    return {
        "module": __version__,
        "l4_schema_version": L4_SCHEMA_VERSION,
        "causal_knowledge_version": catalog.get("causal_knowledge_version") or CAUSAL_KNOWLEDGE_VERSION,
        "equipment_mapping_version": mapping_version() or EQUIPMENT_MAPPING_VERSION,
        "causal_entry_count": len(catalog.get("entries") or []),
        "llm_enabled": False,
        "notes": [
            "L4 Core v1.2 为确定性引擎，不调用 LLM。",
            "根因确认状态恒为 unconfirmed。",
            "结构化证据匹配；Unknown≠Absent；supporting 仅含 cause_specific。",
        ],
    }


def create_run(
    *,
    analysis_run_id: str,
    record_ids: list[str] | None = None,
    force_new: bool = False,
    max_records: int | None = None,
    reasoning_run_id: str | None = None,
    enrichment_run_id: str | None = None,
) -> dict[str, Any]:
    try:
        return run_l4_for_analysis(
            analysis_run_id,
            record_ids=record_ids,
            force_new=force_new,
            max_records=max_records,
            reasoning_run_id=reasoning_run_id,
            enrichment_run_id=enrichment_run_id,
        )
    except RuntimeError as exc:
        raise L4ServiceError(str(exc), code="create_failed", status_code=400) from exc


def get_run(l4_run_id: str) -> dict[str, Any]:
    try:
        return L4_STORE.get(l4_run_id)
    except KeyError as exc:
        raise L4ServiceError(f"L4Run 不存在：{l4_run_id}", code="not_found", status_code=404) from exc


def get_by_analysis(analysis_run_id: str) -> dict[str, Any]:
    run = L4_STORE.get_by_analysis(analysis_run_id)
    return {"l4_run": run, "analysis_run_id": analysis_run_id}


def list_results(
    l4_run_id: str,
    *,
    offset: int = 0,
    limit: int = 50,
    record_id: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    get_run(l4_run_id)
    return L4_STORE.list_records(l4_run_id, offset=offset, limit=limit, record_id=record_id, status=status)


def get_record(l4_run_id: str, record_id: str) -> dict[str, Any]:
    get_run(l4_run_id)
    try:
        return L4_STORE.get_record(l4_run_id, record_id)
    except KeyError as exc:
        raise L4ServiceError(f"记录不存在：{record_id}", code="record_not_found", status_code=404) from exc


def overview(analysis_run_id: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": status()}
    if analysis_run_id:
        payload.update(get_by_analysis(analysis_run_id))
    return payload


def mapping_lookup(equipment_id: str | None, equipment_type: str | None) -> dict[str, Any]:
    return resolve_equipment_mapping(equipment_id=equipment_id, equipment_type=equipment_type)
