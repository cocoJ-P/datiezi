"""Reasoning Input Adapter：读取已保存 L2/L3/Knowledge，不重跑分析。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from l3.knowledge.enrichment import ENRICHMENT_STORE, resolve_evidence_bundle
from l3.knowledge.snapshot import load_snapshot
from l3.runtime.store import ANALYSIS_STORE


def _summarize_evidence(evidence: dict[str, Any], *, max_items: int = 12) -> dict[str, Any]:
    items = evidence.get("evidence_items") or []
    compact = []
    for item in items[:max_items]:
        compact.append(
            {
                "evidence_id": item.get("evidence_id"),
                "feature_name": item.get("feature_name"),
                "observed_value": item.get("observed_value"),
                "assessment": item.get("assessment"),
                "related_fault_mode": item.get("related_fault_mode"),
                "calculation_description": (item.get("calculation_description") or "")[:160],
            }
        )
    l2 = evidence.get("l2_context") or {}
    return {
        "equipment_type": evidence.get("equipment_type"),
        "equipment_id": evidence.get("equipment_id"),
        "record_id": evidence.get("record_id"),
        "l2_context": {
            "screening_result": l2.get("screening_result"),
            "preliminary_fault": l2.get("preliminary_fault"),
            "route": l2.get("route"),
            "binary_probability": l2.get("binary_probability"),
            "fault_probabilities": l2.get("fault_probabilities"),
            "consistency": l2.get("consistency"),
            "trigger_reason": l2.get("trigger_reason") or evidence.get("trigger_reason"),
        },
        "fault_type_review": evidence.get("fault_type_review") or [],
        "evidence_sufficiency": evidence.get("evidence_sufficiency"),
        "support_evidence_ids": [i.get("evidence_id") for i in (evidence.get("support_evidence") or [])][:20],
        "contradict_evidence_ids": [i.get("evidence_id") for i in (evidence.get("contradict_evidence") or [])][:20],
        "missing_evidence": evidence.get("missing_evidence") or [],
        "analysis_limitations": evidence.get("analysis_limitations") or [],
        "severity_assessment": evidence.get("severity_assessment"),
        "location_candidates": evidence.get("location_candidates"),
        "spectral_feature_assessment": evidence.get("spectral_feature_assessment"),
        "evidence_items": compact,
        "evidence_item_count": len(items),
        "has_reasoning_trace": bool(evidence.get("reasoning_trace")),
    }


def _summarize_knowledge(ctx: dict[str, Any] | None) -> dict[str, Any] | None:
    if not ctx:
        return None
    refs = ctx.get("knowledge_references") or []
    return {
        "knowledge_snapshot_id": ctx.get("knowledge_snapshot_id"),
        "fault_candidates": ctx.get("fault_candidates") or [],
        "observed_features": ctx.get("observed_features") or [],
        "missing_knowledge": ctx.get("missing_knowledge") or [],
        "knowledge_references": [
            {
                "knowledge_id": r.get("knowledge_id"),
                "canonical_name": r.get("canonical_name"),
                "knowledge_type": r.get("knowledge_type"),
                "applicability_status": r.get("applicability_status"),
                "source_document_id": r.get("source_document_id"),
                "content_summary": (r.get("content_summary") or "")[:180],
            }
            for r in refs[:8]
        ],
        "note": "知识摘要仅供引用查询；案例结论不是当前记录真值",
    }


def build_reasoning_input(
    analysis_run_id: str,
    record_id: str,
    *,
    include_full_trace: bool = False,
) -> dict[str, Any]:
    run = ANALYSIS_STORE.get_run(analysis_run_id)
    row = ANALYSIS_STORE.get_record(analysis_run_id, record_id)
    evidence = resolve_evidence_bundle(analysis_run_id, record_id, row.get("evidence") or {})

    # 尝试磁盘完整 bundle（含 Trace）
    result_dir = run.get("result_dir")
    if result_dir:
        path = Path(result_dir) / f"{record_id}.json"
        if path.is_file():
            try:
                disk = json.loads(path.read_text(encoding="utf-8"))
                if disk.get("evidence_items") and not evidence.get("evidence_items"):
                    evidence = {**disk, **evidence}
                if disk.get("reasoning_trace") and not evidence.get("reasoning_trace"):
                    evidence = {**evidence, "reasoning_trace": disk["reasoning_trace"]}
            except Exception:  # noqa: BLE001
                pass

    enrich_run = ENRICHMENT_STORE.get_by_analysis(analysis_run_id)
    knowledge_ctx = None
    enrichment_run_id = None
    if enrich_run:
        enrichment_run_id = enrich_run["enrichment_run_id"]
        try:
            rec = ENRICHMENT_STORE.get_record(enrichment_run_id, record_id)
            knowledge_ctx = rec.get("context")
        except KeyError:
            knowledge_ctx = None

    try:
        snap = load_snapshot()
        snapshot_id = snap.get("knowledge_snapshot_id")
        valid_knowledge_ids = {i["knowledge_id"] for i in snap.get("items") or []}
    except Exception:  # noqa: BLE001
        snapshot_id = None
        valid_knowledge_ids = set()

    evidence_ids = {
        str(i.get("evidence_id"))
        for i in (evidence.get("evidence_items") or [])
        if i.get("evidence_id")
    }

    payload = {
        "schema": "L3ReasoningInput",
        "schema_version": "l3_reasoning_input_v1",
        "analysis_run_id": analysis_run_id,
        "record_id": record_id,
        "equipment_type": evidence.get("equipment_type") or run.get("equipment_type"),
        "equipment_id": evidence.get("equipment_id") or row.get("equipment_id"),
        "diagnosis_run_id": evidence.get("diagnosis_run_id") or run.get("source_diagnosis_run_id"),
        "model_bundle_id": evidence.get("model_bundle_id") or run.get("model_bundle_id"),
        "knowledge_snapshot_id": snapshot_id,
        "knowledge_enrichment_run_id": enrichment_run_id,
        "evidence_summary": _summarize_evidence(evidence),
        "knowledge_summary": _summarize_knowledge(knowledge_ctx),
        "allowed_evidence_ids": sorted(evidence_ids),
        "allowed_knowledge_ids": sorted(valid_knowledge_ids),
        "immutable_note": "不得重跑分析器/重训 L2/读取 labels；不得改写 EvidenceBundle",
    }
    if include_full_trace:
        payload["reasoning_trace"] = evidence.get("reasoning_trace")
    # 内部保留完整 evidence 供工具使用，不整包塞进 LLM 首轮提示
    payload["_evidence_bundle"] = evidence
    payload["_knowledge_context"] = knowledge_ctx
    return payload
