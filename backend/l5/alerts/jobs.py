"""AlertRun 创建与执行。"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

from l3.runtime.store import ANALYSIS_STORE
from l4.store import L4_STORE
from l5.alerts import CATALOG_VERSION, __version__
from l5.alerts.catalog import load_catalog
from l5.alerts.evaluator import evaluate_from_l5_context
from l5.alerts.store import ALERT_STORE, AlertStore
from l5.input_adapter import L5InputError, build_l5_input_context
from l5.store import L5_STORE

LOG = logging.getLogger("l5.alerts.jobs")
DEFAULT_MAX = int(os.getenv("L5_ALERT_MAX_RECORDS", "200"))


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def content_key_for(
    *,
    analysis_run_id: str | None,
    l5_run_id: str | None,
    catalog_version: str,
    record_ids: list[str],
) -> str:
    ids = ",".join(sorted(record_ids))
    raw = f"{analysis_run_id}|{l5_run_id}|{ids}|{catalog_version}|{__version__}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def enqueue_alert_run(
    *,
    analysis_run_id: str | None = None,
    diagnosis_run_id: str | None = None,
    l4_run_id: str | None = None,
    source_l5_run_id: str | None = None,
    record_ids: list[str] | None = None,
    force_new: bool = False,
    max_records: int | None = None,
    store: AlertStore | None = None,
) -> dict[str, Any]:
    if source_l5_run_id:
        l5 = L5_STORE.get(source_l5_run_id)
        analysis_run_id = analysis_run_id or l5.get("analysis_run_id")
        diagnosis_run_id = diagnosis_run_id or l5.get("diagnosis_run_id")
        l4_run_id = l4_run_id or l5.get("l4_run_id")
        if not record_ids:
            page = L5_STORE.list_records(source_l5_run_id, offset=0, limit=max_records or DEFAULT_MAX)
            record_ids = [i["record_id"] for i in page.get("items") or []]

    if l4_run_id and not analysis_run_id:
        analysis_run_id = L4_STORE.get(l4_run_id).get("source_analysis_run_id")
    if analysis_run_id and not diagnosis_run_id:
        diagnosis_run_id = ANALYSIS_STORE.get_run(analysis_run_id).get("source_diagnosis_run_id")
    if not analysis_run_id:
        raise RuntimeError("AlertRun 需要 analysis_run_id 或 source_l5_run_id / l4_run_id")

    # 校验 analysis 存在
    ANALYSIS_STORE.get_run(analysis_run_id)

    limit = max(1, min(int(max_records or DEFAULT_MAX), 200))
    if record_ids:
        targets = [str(r) for r in record_ids][:limit]
    else:
        page = ANALYSIS_STORE.list_results(analysis_run_id, offset=0, limit=limit)
        targets = [str(i["record_id"]) for i in page.get("items") or []]
    if not targets:
        raise RuntimeError("没有可评估的记录")

    catalog = load_catalog()
    cat_v = catalog.get("catalog_version") or CATALOG_VERSION
    key = content_key_for(
        analysis_run_id=analysis_run_id,
        l5_run_id=source_l5_run_id,
        catalog_version=cat_v,
        record_ids=targets,
    )
    meta = {
        "source_l5_run_id": source_l5_run_id,
        "diagnosis_run_id": diagnosis_run_id,
        "analysis_run_id": analysis_run_id,
        "l4_run_id": l4_run_id,
        "catalog_version": cat_v,
        "selected_record_count": len(targets),
        "selection": {"record_ids": targets, "max_records": limit},
    }
    astore = store or ALERT_STORE
    run, created = astore.create_or_get(content_key=key, meta=meta, force_new=force_new)
    return {"alert_run": run, "created": created, "selected_record_ids": targets}


def execute_alert_run(alert_run_id: str, *, store: AlertStore | None = None) -> dict[str, Any]:
    astore = store or ALERT_STORE
    run = astore.get(alert_run_id)
    if run.get("status") == "completed":
        return {"alert_run": run, "reused": True}

    targets = list((run.get("selection") or {}).get("record_ids") or [])
    astore.update(alert_run_id, status="running", started_at=_now())
    ok = failed = formal_total = 0
    errors: list[str] = []

    for rid in targets:
        try:
            ctx = build_l5_input_context(
                record_id=rid,
                diagnosis_run_id=run.get("diagnosis_run_id"),
                analysis_run_id=run.get("analysis_run_id"),
                l4_run_id=run.get("l4_run_id"),
            )
            assessment = evaluate_from_l5_context(ctx, alert_run_id=alert_run_id)
            formal_total += int(assessment.get("formal_alert_count") or 0)
            astore.save_record(
                alert_run_id,
                {"record_id": rid, "status": "succeeded", "assessment": assessment},
            )
            ok += 1
        except L5InputError as exc:
            failed += 1
            errors.append(f"{rid}:{exc.code}")
            astore.save_record(
                alert_run_id,
                {
                    "record_id": rid,
                    "status": "failed",
                    "error_message": f"{exc.code}:{exc.message}",
                    "assessment": {"schema": "AlertAssessment", "record_id": rid, "error": exc.message},
                },
            )
        except Exception as exc:  # noqa: BLE001
            LOG.exception("alert eval failed %s", rid)
            failed += 1
            errors.append(f"{rid}:{exc}")
            astore.save_record(
                alert_run_id,
                {
                    "record_id": rid,
                    "status": "failed",
                    "error_message": str(exc),
                    "assessment": {"schema": "AlertAssessment", "record_id": rid, "error": str(exc)},
                },
            )

    status = "completed" if not failed else ("failed" if not ok else "partial")
    run = astore.update(
        alert_run_id,
        status=status,
        finished_at=_now(),
        processed_record_count=len(targets),
        succeeded_record_count=ok,
        failed_record_count=failed,
        formal_alert_total=formal_total,
        error_summary="; ".join(errors[:8]) if errors else None,
    )
    return {"alert_run": run, "reused": False}


def run_alerts(**kwargs: Any) -> dict[str, Any]:
    enq = enqueue_alert_run(**kwargs)
    result = execute_alert_run(enq["alert_run"]["alert_run_id"])
    return {**result, "created": enq["created"], "selected_record_ids": enq["selected_record_ids"]}
