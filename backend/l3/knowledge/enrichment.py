"""KnowledgeEnrichmentRun：独立版本化知识增强，不覆盖历史 EvidenceBundle。"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from l3.knowledge import RETRIEVER_VERSION
from l3.knowledge.context import attach_knowledge_nodes_to_trace, build_knowledge_context
from l3.knowledge.database import connect, default_db_path, init_db
from l3.knowledge.snapshot import load_snapshot
from l3.runtime.store import ANALYSIS_STORE


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(raw: str | None) -> Any:
    if not raw:
        return {}
    return json.loads(raw)


def _minimal_trace_from_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    """历史 AnalysisRun 若未落盘 Trace，仅根据已有 EvidenceItem 组装轻量 DAG（不重算数值）。"""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    conclusions: list[str] = []
    rid = evidence.get("record_id") or "R"
    for item in evidence.get("evidence_items") or []:
        eid = str(item.get("evidence_id") or item.get("feature_name") or len(nodes))
        fname = str(item.get("feature_name") or "")
        obs_id = f"obs:{rid}:{fname}"
        ev_id = f"ev:{eid}"
        nodes.append(
            {
                "node_id": obs_id,
                "node_type": "OBSERVATION",
                "feature_name": fname,
                "value": item.get("observed_value"),
            }
        )
        nodes.append(
            {
                "node_id": ev_id,
                "node_type": "EVIDENCE",
                "evidence_id": eid,
                "assessment": item.get("assessment"),
                "related_fault_mode": item.get("related_fault_mode"),
            }
        )
        edges.append({"from": obs_id, "to": ev_id, "relation": "DERIVED_FROM"})
    for row in evidence.get("fault_type_review") or []:
        mode = row.get("fault_mode")
        if not mode:
            continue
        cid = f"concl:fault_review:{mode}"
        conclusions.append(cid)
        nodes.append(
            {
                "node_id": cid,
                "node_type": "CONCLUSION",
                "conclusion_type": "fault_type_review",
                "assessment": row.get("verdict"),
                "evidence_ids": [],
            }
        )
        for item in evidence.get("evidence_items") or []:
            if item.get("related_fault_mode") == mode:
                edges.append(
                    {
                        "from": f"ev:{item.get('evidence_id')}",
                        "to": cid,
                        "relation": "SUPPORTS" if item.get("assessment") == "SUPPORT" else "CONTRIBUTES_TO",
                    }
                )
    # 去重节点
    by_id = {n["node_id"]: n for n in nodes}
    return {
        "trace_id": f"TRACE-min-{rid}",
        "trace_version": "reasoning_trace_v1",
        "build_status": "ok",
        "nodes": list(by_id.values()),
        "edges": edges,
        "conclusion_node_ids": conclusions,
        "limitations": ["trace_reconstructed_from_evidence_items_for_knowledge_overlay"],
        "note": "历史结果无 Trace 时由 Knowledge 层轻量重建，不修改数值 EvidenceItem",
    }


def resolve_evidence_bundle(analysis_run_id: str, record_id: str, evidence: dict[str, Any]) -> dict[str, Any]:
    """优先使用已有 Trace；否则尝试结果目录；仍缺失则轻量重建。"""
    if evidence.get("reasoning_trace"):
        return evidence
    try:
        run = ANALYSIS_STORE.get_run(analysis_run_id)
        result_dir = run.get("result_dir")
        if result_dir:
            path = Path(result_dir) / f"{record_id}.json"
            if path.is_file():
                disk = json.loads(path.read_text(encoding="utf-8"))
                if disk.get("reasoning_trace"):
                    return {**evidence, "reasoning_trace": disk["reasoning_trace"]}
                # 磁盘 bundle 也可能缺 Trace，但可能更完整
                if not evidence.get("evidence_items") and disk.get("evidence_items"):
                    evidence = {**disk, **evidence}
    except Exception:  # noqa: BLE001
        pass
    return {**evidence, "reasoning_trace": _minimal_trace_from_evidence(evidence)}


class EnrichmentStore:
    def __init__(self, db_path=None):
        self.db_path = db_path or default_db_path()
        init_db(self.db_path)

    def create_or_get(
        self,
        *,
        content_key: str,
        source_analysis_run_id: str,
        knowledge_snapshot_id: str,
        selected_record_count: int,
        force_new: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        connection = connect(self.db_path)
        try:
            if not force_new:
                row = connection.execute(
                    "SELECT * FROM knowledge_enrichment_runs WHERE content_key = ?",
                    (content_key,),
                ).fetchone()
                if row:
                    return self._public(dict(row)), False
            rid = f"KE-{uuid.uuid4().hex[:12]}"
            key = content_key if not force_new else f"{content_key}|force|{rid}"
            connection.execute(
                """
                INSERT INTO knowledge_enrichment_runs (
                    enrichment_run_id, source_analysis_run_id, knowledge_snapshot_id, retriever_version,
                    content_key, status, selected_record_count, created_at
                ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)
                """,
                (rid, source_analysis_run_id, knowledge_snapshot_id, RETRIEVER_VERSION, key, selected_record_count, _now()),
            )
            connection.commit()
            row = connection.execute(
                "SELECT * FROM knowledge_enrichment_runs WHERE enrichment_run_id = ?",
                (rid,),
            ).fetchone()
            return self._public(dict(row)), True
        finally:
            connection.close()

    def update(self, enrichment_run_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {
            "status",
            "processed_record_count",
            "succeeded_record_count",
            "failed_record_count",
            "error_summary",
            "finished_at",
        }
        sets = []
        values = []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k} = ?")
                values.append(v)
        if not sets:
            return self.get(enrichment_run_id)
        connection = connect(self.db_path)
        try:
            connection.execute(
                f"UPDATE knowledge_enrichment_runs SET {', '.join(sets)} WHERE enrichment_run_id = ?",
                (*values, enrichment_run_id),
            )
            connection.commit()
        finally:
            connection.close()
        return self.get(enrichment_run_id)

    def get(self, enrichment_run_id: str) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT * FROM knowledge_enrichment_runs WHERE enrichment_run_id = ?",
                (enrichment_run_id,),
            ).fetchone()
            if not row:
                raise KeyError(enrichment_run_id)
            return self._public(dict(row))
        finally:
            connection.close()

    def get_by_analysis(self, analysis_run_id: str) -> dict[str, Any] | None:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                """
                SELECT * FROM knowledge_enrichment_runs
                WHERE source_analysis_run_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (analysis_run_id,),
            ).fetchone()
            return self._public(dict(row)) if row else None
        finally:
            connection.close()

    def save_record(self, enrichment_run_id: str, record: dict[str, Any]) -> None:
        connection = connect(self.db_path)
        try:
            connection.execute(
                """
                INSERT INTO knowledge_enrichment_records (
                    enrichment_run_id, record_id, status, context_json, trace_overlay_json
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(enrichment_run_id, record_id) DO UPDATE SET
                    status = excluded.status,
                    context_json = excluded.context_json,
                    trace_overlay_json = excluded.trace_overlay_json
                """,
                (
                    enrichment_run_id,
                    record["record_id"],
                    record.get("status") or "ok",
                    _dumps(record.get("context") or {}),
                    _dumps(record.get("trace_overlay")),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def get_record(self, enrichment_run_id: str, record_id: str) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                """
                SELECT * FROM knowledge_enrichment_records
                WHERE enrichment_run_id = ? AND record_id = ?
                """,
                (enrichment_run_id, record_id),
            ).fetchone()
            if not row:
                raise KeyError(record_id)
            return {
                "record_id": row["record_id"],
                "status": row["status"],
                "context": _loads(row["context_json"]),
                "trace_overlay": _loads(row["trace_overlay_json"]),
            }
        finally:
            connection.close()

    def list_records(self, enrichment_run_id: str, *, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            total = connection.execute(
                "SELECT COUNT(*) AS c FROM knowledge_enrichment_records WHERE enrichment_run_id = ?",
                (enrichment_run_id,),
            ).fetchone()["c"]
            rows = connection.execute(
                """
                SELECT * FROM knowledge_enrichment_records
                WHERE enrichment_run_id = ?
                ORDER BY record_id LIMIT ? OFFSET ?
                """,
                (enrichment_run_id, limit, offset),
            ).fetchall()
            items = [
                {
                    "record_id": r["record_id"],
                    "status": r["status"],
                    "context": _loads(r["context_json"]),
                    "trace_overlay": _loads(r["trace_overlay_json"]),
                }
                for r in rows
            ]
            return {"total": int(total), "offset": offset, "limit": limit, "items": items}
        finally:
            connection.close()

    def _public(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "enrichment_run_id": row["enrichment_run_id"],
            "source_analysis_run_id": row["source_analysis_run_id"],
            "knowledge_snapshot_id": row["knowledge_snapshot_id"],
            "retriever_version": row["retriever_version"],
            "content_key": row["content_key"],
            "status": row["status"],
            "selected_record_count": int(row.get("selected_record_count") or 0),
            "processed_record_count": int(row.get("processed_record_count") or 0),
            "succeeded_record_count": int(row.get("succeeded_record_count") or 0),
            "failed_record_count": int(row.get("failed_record_count") or 0),
            "error_summary": row.get("error_summary"),
            "created_at": row.get("created_at"),
            "finished_at": row.get("finished_at"),
        }


ENRICHMENT_STORE = EnrichmentStore()


def run_enrichment_for_analysis(
    analysis_run_id: str,
    *,
    record_ids: list[str] | None = None,
    limit: int | None = 50,
    force_new: bool = False,
    store: EnrichmentStore | None = None,
) -> dict[str, Any]:
    """对 AnalysisRun 的结果做知识增强。默认最多 50 条，避免无条件全量重复检索。"""
    estore = store or ENRICHMENT_STORE
    snap = load_snapshot()
    page = ANALYSIS_STORE.list_results(analysis_run_id, offset=0, limit=limit or 50)
    items = page["items"]
    if record_ids is not None:
        wanted = set(record_ids)
        items = [i for i in items if i["record_id"] in wanted]

    # 按 equipment_type + preliminary_fault 缓存检索上下文的重查询由 build_knowledge_context 内部完成；
    # 这里额外做简单缓存键复用 knowledge refs。
    cache: dict[str, dict[str, Any]] = {}

    key_raw = f"{analysis_run_id}|{snap['knowledge_snapshot_id']}|{RETRIEVER_VERSION}|{len(items)}"
    content_key = hashlib.sha256(key_raw.encode("utf-8")).hexdigest()
    run, created = estore.create_or_get(
        content_key=content_key,
        source_analysis_run_id=analysis_run_id,
        knowledge_snapshot_id=snap["knowledge_snapshot_id"],
        selected_record_count=len(items),
        force_new=force_new,
    )
    if not created and run["status"] in {"succeeded", "partial"} and not force_new:
        return {**run, "reused": True, "created": False}

    estore.update(run["enrichment_run_id"], status="running")
    ok = 0
    failed = 0
    for row in items:
        try:
            evidence = resolve_evidence_bundle(analysis_run_id, row["record_id"], row.get("evidence") or {})
            equipment_type = str(evidence.get("equipment_type") or ANALYSIS_STORE.get_run(analysis_run_id).get("equipment_type") or "")
            l2 = evidence.get("l2_context") or {}
            cache_key = f"{equipment_type}|{l2.get('preliminary_fault')}|{l2.get('screening_result')}"
            # 检索可复用；证据验证仍按记录独立构建 context（含本记录 observed features）
            ctx = build_knowledge_context(
                analysis_run_id=analysis_run_id,
                record_id=row["record_id"],
                equipment_type=equipment_type,
                evidence_bundle=evidence,
                knowledge_snapshot_id=snap["knowledge_snapshot_id"],
            )
            # 若同组已有检索结果，复用 applicable_knowledge 列表但仍保留本记录 observed_features
            if cache_key in cache:
                reused_refs = cache[cache_key]
                ctx = dict(ctx)
                ctx["applicable_knowledge"] = reused_refs
                ctx["knowledge_references"] = reused_refs
                ctx["retrieval"] = {**(ctx.get("retrieval") or {}), "group_cache_hit": True}
            else:
                cache[cache_key] = ctx.get("knowledge_references") or []

            overlay = attach_knowledge_nodes_to_trace(evidence.get("reasoning_trace"), ctx)
            estore.save_record(
                run["enrichment_run_id"],
                {
                    "record_id": row["record_id"],
                    "status": "ok",
                    "context": ctx,
                    "trace_overlay": overlay,
                },
            )
            ok += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            estore.save_record(
                run["enrichment_run_id"],
                {
                    "record_id": row["record_id"],
                    "status": "failed",
                    "context": {"error": str(exc)},
                    "trace_overlay": None,
                },
            )
    status = "succeeded" if failed == 0 else ("partial" if ok else "failed")
    return estore.update(
        run["enrichment_run_id"],
        status=status,
        processed_record_count=ok + failed,
        succeeded_record_count=ok,
        failed_record_count=failed,
        finished_at=_now(),
        error_summary=None if failed == 0 else f"{failed} records failed",
    )
