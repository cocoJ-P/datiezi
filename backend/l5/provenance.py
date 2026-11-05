"""FinalEvidenceProvenance：按业务字段返回只读追溯视图。"""

from __future__ import annotations

from typing import Any

from l5 import PROVENANCE_VERSION
from l5.schema import BUSINESS_FIELD_LABELS_ZH


def build_field_provenance(assessment: dict[str, Any], context: dict[str, Any], field_key: str) -> dict[str, Any]:
    fields = assessment.get("fields") or {}
    slot = fields.get(field_key) or {}
    bundle = context.get("evidence_bundle") or {}
    rca = context.get("root_cause_assessment") or {}
    evidence_items = {
        str(i.get("evidence_id")): {
            "evidence_id": i.get("evidence_id"),
            "feature_name": i.get("feature_name"),
            "observed_value": i.get("observed_value"),
            "assessment": i.get("assessment"),
            "analyzer_id": i.get("analyzer_id"),
        }
        for i in (bundle.get("evidence_items") or [])
        if isinstance(i, dict) and i.get("evidence_id")
    }

    refs = []
    for eid in slot.get("evidence_references") or []:
        item = evidence_items.get(str(eid))
        refs.append(item or {"evidence_id": eid, "traceable": False, "reason": "evidence_id_not_in_bundle"})

    knowledge_refs = []
    for kid in slot.get("knowledge_references") or []:
        # 仅返回 ID；不伪造 KnowledgeReference 正文
        knowledge_refs.append({"causal_knowledge_id": kid, "traceable": True, "note": "详见 L4 CausalKnowledge 目录"})

    chain: list[dict[str, Any]] = [
        {
            "step": "final_field",
            "field": field_key,
            "label_zh": BUSINESS_FIELD_LABELS_ZH.get(field_key, field_key),
            "value": slot.get("value"),
            "null_reason": slot.get("null_reason"),
            "uncertainty": slot.get("uncertainty"),
        }
    ]
    if slot.get("source_layer"):
        chain.append(
            {
                "step": "source_layer",
                "layer": slot.get("source_layer"),
                "run_id": slot.get("source_run_id"),
            }
        )

    if field_key == "root_cause_analysis":
        for c in rca.get("root_cause_candidates") or []:
            if not isinstance(c, dict):
                continue
            chain.append(
                {
                    "step": "root_cause_candidate",
                    "candidate_id": c.get("candidate_id"),
                    "causal_path_id": c.get("causal_path_id"),
                    "causal_knowledge_id": c.get("causal_knowledge_id"),
                    "confirmation": "unconfirmed",
                }
            )
    if field_key == "fault_type":
        for row in bundle.get("fault_type_review") or []:
            if isinstance(row, dict):
                chain.append(
                    {
                        "step": "l3_fault_type_review",
                        "fault_mode": row.get("fault_mode"),
                        "verdict": row.get("verdict"),
                    }
                )
    if field_key == "recommended_actions":
        for v in assessment.get("verification_recommendations") or []:
            chain.append(
                {
                    "step": "verification_recommendation",
                    "recommendation_id": v.get("recommendation_id"),
                    "condition_id": v.get("condition_id"),
                    "missing_evidence_reference": v.get("missing_evidence_reference"),
                }
            )

    versions = context.get("source_versions") or {}
    return {
        "schema": "FinalEvidenceProvenance",
        "provenance_version": PROVENANCE_VERSION,
        "record_id": assessment.get("record_id"),
        "field_key": field_key,
        "label_zh": BUSINESS_FIELD_LABELS_ZH.get(field_key, field_key),
        "field_slot": {
            "value": slot.get("value"),
            "display": slot.get("display"),
            "null_reason": slot.get("null_reason"),
            "uncertainty": slot.get("uncertainty"),
            "source_layer": slot.get("source_layer"),
            "source_run_id": slot.get("source_run_id"),
        },
        "chain": chain,
        "evidence_items": refs,
        "knowledge_references": knowledge_refs,
        "reasoning_trace_ref": {
            "available": bool((bundle.get("reasoning_trace") or {}).get("nodes") or bundle.get("reasoning_trace")),
            "note": "完整 ReasoningTrace 位于 L3 EvidenceBundle，不在此复制",
        },
        "ped_versions": {
            "prepared_dataset_id": versions.get("prepared_dataset_id") or versions.get("l2_prepared_dataset_id"),
            "dataset_revision_id": versions.get("dataset_revision_id") or versions.get("l2_dataset_revision_id"),
            "model_bundle_id": versions.get("model_bundle_id") or versions.get("l2_model_bundle_id"),
            "knowledge_snapshot_id": versions.get("knowledge_snapshot_id"),
            "causal_knowledge_version": versions.get("causal_knowledge_version"),
        },
        "source_run_ids": context.get("source_run_ids") or assessment.get("source_run_ids"),
        "untraceable_reason": None
        if (slot.get("value") is not None or slot.get("null_reason") or slot.get("extras"))
        else "field_not_materialized",
    }


def build_full_provenance(assessment: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "FinalEvidenceProvenanceBundle",
        "record_id": assessment.get("record_id"),
        "fields": {
            k: build_field_provenance(assessment, context, k) for k in (assessment.get("fields") or {}).keys()
        },
    }
