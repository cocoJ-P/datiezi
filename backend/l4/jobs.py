"""L4Run 创建与执行（确定性，无 LLM；不改 L3）。"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

from l4 import CAUSAL_KNOWLEDGE_VERSION, EQUIPMENT_MAPPING_VERSION, __version__
from l4.causal_knowledge.loader import catalog_version
from l4.engine import analyze_record
from l4.input_adapter import build_l4_input
from l4.mapping import mapping_version
from l4.store import L4_STORE, L4Store
from l3.runtime.store import ANALYSIS_STORE, AnalysisStore

LOG = logging.getLogger("l4.jobs")

# 单次显式任务默认上限，防止误触发全量 1.9 万条
DEFAULT_MAX_RECORDS = int(os.getenv("L4_MAX_RECORDS_PER_RUN", "200"))


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def content_key_for(
    analysis_run_id: str,
    record_ids: list[str] | None,
    *,
    causal_version: str,
    mapping_version_str: str,
) -> str:
    ids = ",".join(sorted(record_ids or [])) or "*"
    raw = f"{analysis_run_id}|{ids}|{causal_version}|{mapping_version_str}|{__version__}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def enqueue_from_analysis(
    analysis_run_id: str,
    *,
    record_ids: list[str] | None = None,
    force_new: bool = False,
    max_records: int | None = None,
    reasoning_run_id: str | None = None,
    enrichment_run_id: str | None = None,
    store: L4Store | None = None,
    analysis_store: AnalysisStore | None = None,
) -> dict[str, Any]:
    astore = analysis_store or ANALYSIS_STORE
    lstore = store or L4_STORE
    try:
        run = astore.get_run(analysis_run_id)
    except KeyError as exc:
        raise RuntimeError(f"AnalysisRun 不存在：{analysis_run_id}") from exc

    limit = max_records if max_records is not None else DEFAULT_MAX_RECORDS
    if limit <= 0:
        raise RuntimeError("max_records 必须为正整数")

    if record_ids:
        targets = [str(r) for r in record_ids][:limit]
    else:
        # 默认只取有限分页，禁止无界扫全库
        page = astore.list_results(analysis_run_id, offset=0, limit=limit)
        targets = [str(i["record_id"]) for i in page["items"]]

    if not targets:
        raise RuntimeError("没有可分析的记录（请显式传入 record_ids，或确认 AnalysisRun 已有结果）")

    causal_v = catalog_version() or CAUSAL_KNOWLEDGE_VERSION
    map_v = mapping_version() or EQUIPMENT_MAPPING_VERSION
    key = content_key_for(analysis_run_id, targets, causal_version=causal_v, mapping_version_str=map_v)
    meta = {
        "source_analysis_run_id": analysis_run_id,
        "source_reasoning_run_id": reasoning_run_id,
        "source_enrichment_run_id": enrichment_run_id,
        "causal_knowledge_version": causal_v,
        "equipment_mapping_version": map_v,
        "selected_record_count": len(targets),
        "selection": {
            "record_ids": targets,
            "max_records": limit,
            "analysis_equipment_type": run.get("equipment_type"),
        },
    }
    l4_run, created = lstore.create_or_get(content_key=key, meta=meta, force_new=force_new)
    return {"l4_run": l4_run, "created": created, "selected_record_ids": targets}


def execute_l4_run(
    l4_run_id: str,
    *,
    store: L4Store | None = None,
) -> dict[str, Any]:
    lstore = store or L4_STORE
    run = lstore.get(l4_run_id)
    if run.get("status") == "completed":
        return run

    selection = run.get("selection") or {}
    record_ids = list(selection.get("record_ids") or [])
    analysis_run_id = run["source_analysis_run_id"]
    lstore.update(l4_run_id, status="running", started_at=_now())

    succeeded = 0
    failed = 0
    errors: list[str] = []

    for record_id in record_ids:
        try:
            ctx = build_l4_input(
                analysis_run_id=analysis_run_id,
                record_id=record_id,
                reasoning_run_id=run.get("source_reasoning_run_id"),
                enrichment_run_id=run.get("source_enrichment_run_id"),
            )
            assessment = analyze_record(
                l4_run_id=l4_run_id,
                analysis_run_id=analysis_run_id,
                record_id=record_id,
                ctx=ctx,
            )
            # 任务成功 ≠ 根因已确认
            status = "succeeded"
            if assessment.get("outcome") == "analysis_failed":
                status = "failed"
                failed += 1
            else:
                succeeded += 1
            lstore.save_record(
                l4_run_id,
                {
                    "record_id": record_id,
                    "equipment_id": assessment.get("equipment_id") or ctx.get("equipment_id"),
                    "status": status,
                    "outcome": assessment.get("outcome"),
                    "input_fingerprint": assessment.get("input_fingerprint"),
                    "assessment": assessment,
                },
            )
        except Exception as exc:  # noqa: BLE001 — 单条失败隔离
            failed += 1
            errors.append(f"{record_id}: {exc}")
            LOG.exception("L4 record failed %s", record_id)
            lstore.save_record(
                l4_run_id,
                {
                    "record_id": record_id,
                    "status": "failed",
                    "outcome": "analysis_failed",
                    "assessment": {
                        "schema": "RootCauseAssessment",
                        "outcome": "analysis_failed",
                        "root_cause_confirmation": "unconfirmed",
                        "error": str(exc),
                    },
                    "error_message": str(exc),
                },
            )

    return lstore.update(
        l4_run_id,
        status="completed",
        processed_record_count=len(record_ids),
        succeeded_record_count=succeeded,
        failed_record_count=failed,
        error_summary="; ".join(errors[:5]) if errors else None,
        finished_at=_now(),
    )


def run_l4_for_analysis(
    analysis_run_id: str,
    *,
    record_ids: list[str] | None = None,
    force_new: bool = False,
    max_records: int | None = None,
    reasoning_run_id: str | None = None,
    enrichment_run_id: str | None = None,
) -> dict[str, Any]:
    enqueued = enqueue_from_analysis(
        analysis_run_id,
        record_ids=record_ids,
        force_new=force_new,
        max_records=max_records,
        reasoning_run_id=reasoning_run_id,
        enrichment_run_id=enrichment_run_id,
    )
    l4_run = enqueued["l4_run"]
    if enqueued["created"] or l4_run.get("status") in {"queued", "interrupted"}:
        l4_run = execute_l4_run(l4_run["l4_run_id"])
    return {"l4_run": l4_run, "created": enqueued["created"], "selected_record_ids": enqueued["selected_record_ids"]}
