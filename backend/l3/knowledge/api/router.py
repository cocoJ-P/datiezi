"""L3 Knowledge 只读 / 受控增强 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from l3.knowledge.enrichment import ENRICHMENT_STORE, run_enrichment_for_analysis
from l3.knowledge.graph_query import (
    graph_status,
    query_equipment_components,
    query_fault_cases,
    query_fault_features,
    query_rules_for_equipment,
)
from l3.knowledge.import_docs import inspect_sources
from l3.knowledge.retrieval import hybrid_search
from l3.knowledge.snapshot import build_snapshot, list_snapshots, load_snapshot

router = APIRouter(prefix="/l3/knowledge", tags=["l3-knowledge"])


@router.get("/status")
def knowledge_status() -> dict[str, Any]:
    sources = inspect_sources()
    try:
        snap = load_snapshot()
        snap_id = snap["knowledge_snapshot_id"]
        item_count = snap.get("item_count")
        conflicts = snap.get("conflicts") or []
    except Exception:  # noqa: BLE001
        snap_id = None
        item_count = 0
        conflicts = []
    g = graph_status()
    from pathlib import Path

    from l3.knowledge.paths import RAG_INDEX_PATH, VECTOR_DIR

    vector_metas = list(VECTOR_DIR.glob("*.meta.json")) if VECTOR_DIR.is_dir() else []
    return {
        "module_version": "l3_knowledge_v1",
        "sources": sources,
        "knowledge_snapshot_id": snap_id,
        "item_count": item_count,
        "conflicts": conflicts,
        "graph": g,
        "bm25_index": {
            "path": str(RAG_INDEX_PATH),
            "available": RAG_INDEX_PATH.is_file(),
            "note": "L3 Hybrid 复用 jieba+BM25 算法；canonical item 索引在快照内构建",
        },
        "vector_index": {
            "available": bool(vector_metas),
            "backend": "tfidf_sklearn",
            "metas": [str(p.name) for p in vector_metas[:5]],
            "note": "sentence-transformers 未安装时使用 TF-IDF 向量后端；不可用时降级 bm25_only",
        },
        "snapshots": list_snapshots()[-5:],
    }


@router.post("/snapshots")
def create_snapshot(force_new: bool = False) -> dict[str, Any]:
    body = build_snapshot(force_new=force_new)
    return {
        "knowledge_snapshot_id": body["knowledge_snapshot_id"],
        "item_count": body["item_count"],
        "duplicate_alias_count": body.get("duplicate_alias_count"),
        "conflicts": body.get("conflicts"),
        "pending_documents": body.get("pending_documents"),
        "created_at": body.get("created_at"),
    }


@router.get("/search")
def search_knowledge(
    query: str = Query(..., min_length=1),
    equipment_type: str | None = None,
    fault_mode: str | None = None,
    top_k: int = Query(8, ge=1, le=20),
) -> dict[str, Any]:
    return hybrid_search(query=query, equipment_type=equipment_type, fault_mode=fault_mode, top_k=top_k)


@router.get("/graph/status")
def get_graph_status() -> dict[str, Any]:
    return graph_status()


@router.get("/graph/equipment/{equipment_type}/components")
def equipment_components(equipment_type: str) -> dict[str, Any]:
    return query_equipment_components(equipment_type)


@router.get("/graph/faults/{fault_mode}/features")
def fault_features(fault_mode: str) -> dict[str, Any]:
    return query_fault_features(fault_mode)


@router.get("/graph/faults/{fault_mode}/cases")
def fault_cases(fault_mode: str) -> dict[str, Any]:
    return query_fault_cases(fault_mode)


@router.get("/graph/equipment/{equipment_type}/rules")
def equipment_rules(equipment_type: str) -> dict[str, Any]:
    return query_rules_for_equipment(equipment_type)


class EnrichBody(BaseModel):
    analysis_run_id: str
    record_ids: list[str] | None = None
    limit: int | None = 50
    force_new: bool = False


@router.post("/enrichment-runs")
def create_enrichment(body: EnrichBody) -> dict[str, Any]:
    try:
        return run_enrichment_for_analysis(
            body.analysis_run_id,
            record_ids=body.record_ids,
            limit=body.limit,
            force_new=body.force_new,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"error_code": "NOT_FOUND", "message": str(exc)}) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail={"error_code": "ENRICH_FAILED", "message": str(exc)}) from exc


@router.get("/enrichment-runs/{enrichment_run_id}")
def get_enrichment(enrichment_run_id: str) -> dict[str, Any]:
    try:
        return ENRICHMENT_STORE.get(enrichment_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"error_code": "NOT_FOUND", "message": str(exc)}) from exc


@router.get("/by-analysis/{analysis_run_id}")
def enrichment_by_analysis(analysis_run_id: str) -> dict[str, Any]:
    run = ENRICHMENT_STORE.get_by_analysis(analysis_run_id)
    return {"analysis_run_id": analysis_run_id, "enrichment_run": run}


@router.get("/enrichment-runs/{enrichment_run_id}/records/{record_id}")
def enrichment_record(enrichment_run_id: str, record_id: str) -> dict[str, Any]:
    try:
        return ENRICHMENT_STORE.get_record(enrichment_run_id, record_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"error_code": "NOT_FOUND", "message": str(exc)}) from exc


@router.get("/enrichment-runs/{enrichment_run_id}/results")
def enrichment_results(
    enrichment_run_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    try:
        ENRICHMENT_STORE.get(enrichment_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"error_code": "NOT_FOUND", "message": str(exc)}) from exc
    return ENRICHMENT_STORE.list_records(enrichment_run_id, offset=offset, limit=limit)
