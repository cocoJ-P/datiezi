"""ReasoningRun 创建、选择、执行（不覆盖 Core/Knowledge）。"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from l3.knowledge.enrichment import ENRICHMENT_STORE
from l3.knowledge.snapshot import load_snapshot
from l3.reasoning import PROMPT_VERSION, REASONING_POLICY_VERSION, TOOL_REGISTRY_VERSION
from l3.reasoning.database import connect, default_db_path, init_db
from l3.reasoning.input_adapter import build_reasoning_input
from l3.reasoning.orchestrator import run_llm_tool_loop
from l3.reasoning.policy import apply_selection, policy_config
from l3.reasoning.trace_overlay import attach_reasoning_nodes
from l3.runtime.store import ANALYSIS_STORE


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(raw: str | None) -> Any:
    if not raw:
        return {}
    return json.loads(raw)


class ReasoningStore:
    def __init__(self, db_path=None):
        self.db_path = db_path or default_db_path()
        init_db(self.db_path)

    def create_or_get(self, *, content_key: str, meta: dict[str, Any], force_new: bool = False) -> tuple[dict[str, Any], bool]:
        connection = connect(self.db_path)
        try:
            if not force_new:
                row = connection.execute(
                    "SELECT * FROM reasoning_runs WHERE content_key = ?",
                    (content_key,),
                ).fetchone()
                if row:
                    return self._public(dict(row)), False
            rid = f"RR-{uuid.uuid4().hex[:12]}"
            key = content_key if not force_new else f"{content_key}|force|{rid}"
            connection.execute(
                """
                INSERT INTO reasoning_runs (
                    reasoning_run_id, source_analysis_run_id, knowledge_enrichment_run_id, knowledge_snapshot_id,
                    llm_model, prompt_version, tool_registry_version, reasoning_policy_version,
                    content_key, status, selection_json, selected_record_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (
                    rid,
                    meta["source_analysis_run_id"],
                    meta.get("knowledge_enrichment_run_id"),
                    meta.get("knowledge_snapshot_id"),
                    meta.get("llm_model"),
                    PROMPT_VERSION,
                    TOOL_REGISTRY_VERSION,
                    REASONING_POLICY_VERSION,
                    key,
                    _dumps(meta.get("selection") or {}),
                    int(meta.get("selected_record_count") or 0),
                    _now(),
                ),
            )
            connection.commit()
            row = connection.execute("SELECT * FROM reasoning_runs WHERE reasoning_run_id = ?", (rid,)).fetchone()
            return self._public(dict(row)), True
        finally:
            connection.close()

    def update(self, reasoning_run_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {
            "status",
            "processed_record_count",
            "succeeded_record_count",
            "insufficient_record_count",
            "failed_record_count",
            "validation_failed_record_count",
            "token_usage_json",
            "error_summary",
            "finished_at",
            "llm_model",
            "selection_json",
        }
        sets = []
        values = []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "selection_json" and not isinstance(v, str):
                v = _dumps(v)
            if k == "token_usage_json" and not isinstance(v, str):
                v = _dumps(v)
            sets.append(f"{k} = ?")
            values.append(v)
        if not sets:
            return self.get(reasoning_run_id)
        connection = connect(self.db_path)
        try:
            connection.execute(
                f"UPDATE reasoning_runs SET {', '.join(sets)} WHERE reasoning_run_id = ?",
                (*values, reasoning_run_id),
            )
            connection.commit()
        finally:
            connection.close()
        return self.get(reasoning_run_id)

    def get(self, reasoning_run_id: str) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT * FROM reasoning_runs WHERE reasoning_run_id = ?",
                (reasoning_run_id,),
            ).fetchone()
            if not row:
                raise KeyError(reasoning_run_id)
            return self._public(dict(row))
        finally:
            connection.close()

    def get_by_analysis(self, analysis_run_id: str) -> dict[str, Any] | None:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                """
                SELECT * FROM reasoning_runs
                WHERE source_analysis_run_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (analysis_run_id,),
            ).fetchone()
            return self._public(dict(row)) if row else None
        finally:
            connection.close()

    def save_record(self, reasoning_run_id: str, record: dict[str, Any]) -> None:
        connection = connect(self.db_path)
        try:
            assessment = record.get("assessment") or {}
            outcome_class = record.get("mode") or assessment.get("outcome_class") or record.get("status")
            audit = record.get("audit") or assessment.get("audit") or {}
            connection.execute(
                """
                INSERT INTO reasoning_records (
                    reasoning_run_id, record_id, status, selection_reason,
                    assessment_json, trace_overlay_json, tool_history_json, mode, error_message,
                    audit_json, outcome_class
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(reasoning_run_id, record_id) DO UPDATE SET
                    status = excluded.status,
                    selection_reason = excluded.selection_reason,
                    assessment_json = excluded.assessment_json,
                    trace_overlay_json = excluded.trace_overlay_json,
                    tool_history_json = excluded.tool_history_json,
                    mode = excluded.mode,
                    error_message = excluded.error_message,
                    audit_json = excluded.audit_json,
                    outcome_class = excluded.outcome_class
                """,
                (
                    reasoning_run_id,
                    record["record_id"],
                    record.get("status") or outcome_class or "ok",
                    record.get("selection_reason"),
                    _dumps(assessment),
                    _dumps(record.get("trace_overlay")),
                    _dumps(record.get("tool_history") or []),
                    record.get("mode") or outcome_class,
                    record.get("error_message"),
                    _dumps(audit),
                    outcome_class,
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def get_record(self, reasoning_run_id: str, record_id: str) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT * FROM reasoning_records WHERE reasoning_run_id = ? AND record_id = ?",
                (reasoning_run_id, record_id),
            ).fetchone()
            if not row:
                raise KeyError(record_id)
            keys = set(row.keys())
            return {
                "record_id": row["record_id"],
                "status": row["status"],
                "selection_reason": row["selection_reason"],
                "assessment": _loads(row["assessment_json"]),
                "trace_overlay": _loads(row["trace_overlay_json"]),
                "tool_history": _loads(row["tool_history_json"]),
                "mode": row["mode"],
                "error_message": row["error_message"],
                "audit": _loads(row["audit_json"]) if "audit_json" in keys else {},
                "outcome_class": row["outcome_class"] if "outcome_class" in keys else row["mode"],
            }
        finally:
            connection.close()

    def list_records(self, reasoning_run_id: str, *, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            total = connection.execute(
                "SELECT COUNT(*) AS c FROM reasoning_records WHERE reasoning_run_id = ?",
                (reasoning_run_id,),
            ).fetchone()["c"]
            rows = connection.execute(
                """
                SELECT * FROM reasoning_records
                WHERE reasoning_run_id = ?
                ORDER BY record_id LIMIT ? OFFSET ?
                """,
                (reasoning_run_id, limit, offset),
            ).fetchall()
            items = []
            for r in rows:
                keys = set(r.keys())
                items.append(
                    {
                        "record_id": r["record_id"],
                        "status": r["status"],
                        "selection_reason": r["selection_reason"],
                        "assessment": _loads(r["assessment_json"]),
                        "trace_overlay": _loads(r["trace_overlay_json"]),
                        "tool_history": _loads(r["tool_history_json"]),
                        "mode": r["mode"],
                        "error_message": r["error_message"],
                        "audit": _loads(r["audit_json"]) if "audit_json" in keys else {},
                        "outcome_class": r["outcome_class"] if "outcome_class" in keys else r["mode"],
                    }
                )
            return {"total": int(total), "offset": offset, "limit": limit, "items": items}
        finally:
            connection.close()

    def _public(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "reasoning_run_id": row["reasoning_run_id"],
            "source_analysis_run_id": row["source_analysis_run_id"],
            "knowledge_enrichment_run_id": row.get("knowledge_enrichment_run_id"),
            "knowledge_snapshot_id": row.get("knowledge_snapshot_id"),
            "llm_model": row.get("llm_model"),
            "prompt_version": row.get("prompt_version"),
            "tool_registry_version": row.get("tool_registry_version"),
            "reasoning_policy_version": row.get("reasoning_policy_version"),
            "content_key": row.get("content_key"),
            "status": row.get("status"),
            "selection": _loads(row.get("selection_json")),
            "selected_record_count": int(row.get("selected_record_count") or 0),
            "processed_record_count": int(row.get("processed_record_count") or 0),
            "succeeded_record_count": int(row.get("succeeded_record_count") or 0),
            "insufficient_record_count": int(row.get("insufficient_record_count") or 0),
            "failed_record_count": int(row.get("failed_record_count") or 0),
            "validation_failed_record_count": int(row.get("validation_failed_record_count") or 0),
            "token_usage": _loads(row.get("token_usage_json")),
            "error_summary": row.get("error_summary"),
            "created_at": row.get("created_at"),
            "finished_at": row.get("finished_at"),
        }


REASONING_STORE = ReasoningStore()


def run_reasoning_for_analysis(
    analysis_run_id: str,
    *,
    record_ids: list[str] | None = None,
    automatic_selection: bool = True,
    manual_request: bool = False,
    limit_scan: int = 200,
    force_new: bool = False,
    dry_run: bool = False,
    store: ReasoningStore | None = None,
) -> dict[str, Any]:
    estore = store or REASONING_STORE
    cfg = policy_config()
    page = ANALYSIS_STORE.list_results(analysis_run_id, offset=0, limit=limit_scan)
    rows = page["items"]
    if record_ids is not None:
        wanted = set(record_ids)
        rows = [r for r in rows if r["record_id"] in wanted]
        # 手动指定记录视为 manual
        manual_ids = list(wanted) if manual_request or record_ids else []
    else:
        manual_ids = list(record_ids or []) if manual_request else []

    selection = apply_selection(
        rows,
        manual_record_ids=manual_ids or (record_ids if manual_request else None),
        automatic_selection=automatic_selection if record_ids is None else True,
        max_auto=cfg["max_auto_records_per_run"] if record_ids is None else max(len(rows), cfg["max_auto_records_per_run"]),
    )
    # 若显式传入 record_ids，强制这些进入 selected
    if record_ids is not None:
        selected_ids = {e["record_id"] for e in selection["selected"]}
        for rid in record_ids:
            if rid not in selected_ids:
                selection["selected"].append(
                    {
                        "record_id": rid,
                        "screening_result": None,
                        "selection_reason": "manual_request" if manual_request else "explicit_record_ids",
                        "manual": True,
                    }
                )

    try:
        snap = load_snapshot()
        snapshot_id = snap.get("knowledge_snapshot_id")
    except Exception:  # noqa: BLE001
        snapshot_id = None
    enrich = ENRICHMENT_STORE.get_by_analysis(analysis_run_id)
    enrich_id = enrich["enrichment_run_id"] if enrich else None

    selected = selection["selected"]
    key_raw = (
        f"{analysis_run_id}|{snapshot_id}|{PROMPT_VERSION}|{TOOL_REGISTRY_VERSION}|"
        f"{sorted(e['record_id'] for e in selected)}|dry={dry_run}"
    )
    content_key = hashlib.sha256(key_raw.encode("utf-8")).hexdigest()
    run, created = estore.create_or_get(
        content_key=content_key,
        meta={
            "source_analysis_run_id": analysis_run_id,
            "knowledge_enrichment_run_id": enrich_id,
            "knowledge_snapshot_id": snapshot_id,
            "llm_model": "dry_run" if dry_run else None,
            "selection": selection,
            "selected_record_count": len(selected),
        },
        force_new=force_new,
    )
    if not created and run["status"] in {"succeeded", "partial", "insufficient"} and not force_new:
        return {**run, "reused": True, "created": False}

    estore.update(run["reasoning_run_id"], status="running", selection_json=selection)
    ok = 0
    failed = 0
    insufficient = 0
    validation_failed = 0
    llm_model = None
    usage_agg: dict[str, Any] = {
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "llm_call_count": 0,
        "tool_call_count": 0,
        "execution_duration": 0.0,
        "records": [],
    }

    by_id = {r["record_id"]: r for r in rows}
    for entry in selected:
        rid = entry["record_id"]
        try:
            if rid not in by_id:
                ANALYSIS_STORE.get_record(analysis_run_id, rid)
            rin = build_reasoning_input(analysis_run_id, rid)
            outcome = run_llm_tool_loop(
                rin,
                reasoning_run_id=run["reasoning_run_id"],
                dry_run=dry_run,
            )
            assessment = outcome["assessment"]
            llm_model = assessment.get("llm_model") or llm_model
            mode = str(outcome.get("mode") or assessment.get("outcome_class") or "validation_failed")
            status = str(assessment.get("status") or mode)
            audit = outcome.get("audit") or assessment.get("audit") or {}
            overlay = attach_reasoning_nodes(
                (rin.get("_evidence_bundle") or {}).get("reasoning_trace"),
                assessment,
                outcome.get("tool_history"),
            )
            estore.save_record(
                run["reasoning_run_id"],
                {
                    "record_id": rid,
                    "status": status,
                    "selection_reason": entry.get("selection_reason"),
                    "assessment": assessment,
                    "trace_overlay": overlay,
                    "tool_history": outcome.get("tool_history") or [],
                    "mode": mode,
                    "error_message": outcome.get("error"),
                    "audit": audit,
                },
            )
            # 聚合审计（usage 缺失保持 null，不伪造）
            usage_agg["llm_call_count"] += int(audit.get("llm_call_count") or 0)
            usage_agg["tool_call_count"] += int(audit.get("tool_call_count") or 0)
            if audit.get("execution_duration") is not None:
                usage_agg["execution_duration"] = float(usage_agg["execution_duration"] or 0) + float(
                    audit["execution_duration"]
                )
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                if audit.get(key) is not None:
                    usage_agg[key] = int(usage_agg[key] or 0) + int(audit[key])
            usage_agg["records"].append({"record_id": rid, "outcome_class": mode, "audit": audit})

            if mode in {"live_llm_success", "dry_run"}:
                ok += 1
            elif mode == "insufficient_evidence":
                insufficient += 1
            elif mode == "validation_failed":
                validation_failed += 1
            else:
                # llm_invalid_json_fallback / llm_failed_fallback
                failed += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            estore.save_record(
                run["reasoning_run_id"],
                {
                    "record_id": rid,
                    "status": "llm_failed_fallback",
                    "selection_reason": entry.get("selection_reason"),
                    "assessment": {"status": "llm_failed_fallback", "error": str(exc)},
                    "trace_overlay": None,
                    "tool_history": [],
                    "mode": "llm_failed_fallback",
                    "error_message": str(exc),
                    "audit": {"error_type": type(exc).__name__},
                },
            )

    processed = ok + failed + insufficient + validation_failed
    if failed == 0 and validation_failed == 0 and insufficient == 0:
        final_status = "succeeded"
    elif ok or insufficient:
        final_status = "partial"
    else:
        final_status = "failed"

    return estore.update(
        run["reasoning_run_id"],
        status=final_status,
        processed_record_count=processed,
        succeeded_record_count=ok,
        insufficient_record_count=insufficient,
        failed_record_count=failed,
        validation_failed_record_count=validation_failed,
        finished_at=_now(),
        llm_model=llm_model,
        token_usage_json=usage_agg,
        error_summary=None
        if failed == 0 and validation_failed == 0
        else f"failed={failed}, validation_failed={validation_failed}",
    )
