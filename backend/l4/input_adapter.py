"""L4 InputAdapter：只读聚合 L3 已保存结果，不重跑分析器/LLM。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from l3.knowledge.enrichment import ENRICHMENT_STORE
from l3.reasoning.jobs import ReasoningStore
from l3.runtime.store import ANALYSIS_STORE


class L4InputError(Exception):
    def __init__(self, message: str, *, code: str = "l4_input_error"):
        super().__init__(message)
        self.message = message
        self.code = code


def _stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _fault_candidates_from_bundle(bundle: dict[str, Any], assessment: dict[str, Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    skip_names = {"正常", "normal", "none", "无"}
    for row in bundle.get("fault_type_review") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("fault_mode") or "").strip()
        if not name or name in seen or name.lower() in skip_names or name in skip_names:
            continue
        seen.add(name)
        out.append(
            {
                "fault_type": name,
                "source": "l3_fault_type_review",
                "verdict": row.get("verdict"),
                "support_count": row.get("support_count"),
                "contradict_count": row.get("contradict_count"),
                "note": row.get("note"),
            }
        )
    if assessment:
        for hyp in assessment.get("candidate_hypotheses") or []:
            if not isinstance(hyp, dict):
                continue
            name = str(hyp.get("fault_type") or "").strip()
            # 去掉鉴别项里的长尾巴以便匹配因果知识
            base = name.split("（")[0].split("/")[0].strip()
            key = base or name
            if not key or key in seen or key in skip_names or key.lower() in skip_names:
                continue
            # 仅当基础名尚未收录时追加；保留原始名称
            if base and base not in seen:
                seen.add(base)
                out.append(
                    {
                        "fault_type": base,
                        "source": "l3_reasoning_hypothesis",
                        "verdict": hyp.get("assessment"),
                        "hypothesis_origin": hyp.get("hypothesis_origin"),
                        "raw_fault_type": name,
                    }
                )
            elif name not in seen and name not in skip_names:
                seen.add(name)
                out.append(
                    {
                        "fault_type": name,
                        "source": "l3_reasoning_hypothesis",
                        "verdict": hyp.get("assessment"),
                        "hypothesis_origin": hyp.get("hypothesis_origin"),
                    }
                )
    l2 = bundle.get("l2_context") or {}
    prelim = str(l2.get("preliminary_fault") or "").strip()
    if prelim and prelim not in seen and prelim not in skip_names and prelim.lower() not in skip_names:
        out.append({"fault_type": prelim, "source": "l2_preliminary_fault", "verdict": None})
    return out


def _evidence_index(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for item in bundle.get("evidence_items") or []:
        if not isinstance(item, dict):
            continue
        refs.append(
            {
                "evidence_id": item.get("evidence_id"),
                "feature_name": item.get("feature_name"),
                "assessment": item.get("assessment"),
                "related_fault_mode": item.get("related_fault_mode"),
                "observed_value": item.get("observed_value"),
                "analyzer_id": item.get("analyzer_id"),
            }
        )
    return refs


def build_l4_input(
    *,
    analysis_run_id: str,
    record_id: str,
    reasoning_run_id: str | None = None,
    enrichment_run_id: str | None = None,
    analysis_store=None,
    reasoning_store=None,
    enrichment_store=None,
) -> dict[str, Any]:
    astore = analysis_store or ANALYSIS_STORE
    rstore = reasoning_store or ReasoningStore()
    estore = enrichment_store or ENRICHMENT_STORE

    try:
        run = astore.get_run(analysis_run_id)
    except KeyError as exc:
        raise L4InputError(f"AnalysisRun 不存在：{analysis_run_id}", code="analysis_run_not_found") from exc

    try:
        record = astore.get_record(analysis_run_id, record_id)
    except KeyError as exc:
        raise L4InputError(f"Analysis 记录不存在：{record_id}", code="record_not_found") from exc

    bundle = record.get("evidence") or {}
    if not isinstance(bundle, dict) or bundle.get("schema") != "L3EvidenceBundle":
        # 部分失败记录可能只有 error；仍允许生成失败态输入骨架
        bundle = bundle if isinstance(bundle, dict) else {}

    assessment = None
    resolved_rr = None
    if reasoning_run_id:
        try:
            rr = rstore.get(reasoning_run_id)
            resolved_rr = rr.get("reasoning_run_id")
            rec = rstore.get_record(reasoning_run_id, record_id)
            assessment = rec.get("assessment") if rec else None
        except KeyError:
            assessment = None
    else:
        latest = rstore.get_by_analysis(analysis_run_id)
        if latest:
            resolved_rr = latest.get("reasoning_run_id")
            try:
                rec = rstore.get_record(resolved_rr, record_id)
                assessment = rec.get("assessment") if rec else None
            except Exception:
                assessment = None

    enrichment_ctx = None
    resolved_er = enrichment_run_id
    if not resolved_er:
        latest_e = estore.get_by_analysis(analysis_run_id) if hasattr(estore, "get_by_analysis") else None
        if latest_e:
            resolved_er = latest_e.get("enrichment_run_id")
    if resolved_er:
        try:
            erec = estore.get_record(resolved_er, record_id)
            enrichment_ctx = erec.get("context") if erec else None
        except Exception:
            enrichment_ctx = None

    knowledge_snapshot_id = None
    if isinstance(assessment, dict):
        knowledge_snapshot_id = assessment.get("knowledge_snapshot_id")
    if not knowledge_snapshot_id and isinstance(enrichment_ctx, dict):
        knowledge_snapshot_id = enrichment_ctx.get("knowledge_snapshot_id")

    fault_candidates = _fault_candidates_from_bundle(bundle, assessment if isinstance(assessment, dict) else None)
    evidence_refs = _evidence_index(bundle)
    missing = list(bundle.get("missing_evidence") or [])
    if isinstance(assessment, dict):
        for item in assessment.get("missing_evidence") or []:
            if item not in missing:
                missing.append(item)

    from l4 import (
        APPLICABILITY_VERSION,
        CAUSAL_KNOWLEDGE_VERSION,
        RULES_VERSION,
        __version__ as L4_CORE_VERSION,
    )

    fingerprint_payload = {
        "analysis_run_id": analysis_run_id,
        "record_id": record_id,
        "reasoning_run_id": resolved_rr,
        "enrichment_run_id": resolved_er,
        "knowledge_snapshot_id": knowledge_snapshot_id,
        "evidence_sufficiency": bundle.get("evidence_sufficiency"),
        "fault_candidates": [c.get("fault_type") for c in fault_candidates],
        "support_ids": [e.get("evidence_id") for e in (bundle.get("support_evidence") or []) if isinstance(e, dict)],
        "l2_screening": (bundle.get("l2_context") or {}).get("screening_result"),
        "l2_preliminary": (bundle.get("l2_context") or {}).get("preliminary_fault"),
        "l4_core_version": L4_CORE_VERSION,
        "causal_knowledge_version": CAUSAL_KNOWLEDGE_VERSION,
        "rules_version": RULES_VERSION,
        "applicability_version": APPLICABILITY_VERSION,
    }

    return {
        "schema": "L4InputContext",
        "schema_version": "l4_input_v1_2",
        "source_analysis_run_id": analysis_run_id,
        "source_diagnosis_run_id": run.get("source_diagnosis_run_id") or bundle.get("diagnosis_run_id"),
        "source_reasoning_run_id": resolved_rr,
        "source_enrichment_run_id": resolved_er,
        "record_id": record_id,
        "equipment_id": bundle.get("equipment_id") or record.get("equipment_id"),
        "equipment_type": bundle.get("equipment_type") or run.get("equipment_type"),
        "prepared_dataset_id": bundle.get("prepared_dataset_id") or run.get("prepared_dataset_id"),
        "knowledge_snapshot_id": knowledge_snapshot_id,
        "input_fingerprint": _stable_hash(fingerprint_payload),
        "l3_fault_candidates": fault_candidates,
        "evidence_references": evidence_refs,
        "support_evidence": bundle.get("support_evidence") or [],
        "contradict_evidence": bundle.get("contradict_evidence") or [],
        "missing_evidence": missing,
        "evidence_sufficiency": bundle.get("evidence_sufficiency"),
        "l2_context": bundle.get("l2_context") or {},
        "evidence_bundle": bundle,
        "reasoning_assessment": assessment if isinstance(assessment, dict) else None,
        "knowledge_context": enrichment_ctx if isinstance(enrichment_ctx, dict) else None,
        "analysis_record_status": record.get("status"),
        "limitations": list(bundle.get("analysis_limitations") or []),
    }
