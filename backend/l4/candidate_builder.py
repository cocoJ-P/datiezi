"""RootCauseCandidateBuilder：Fault Eligibility → 适用性 → 因果知识候选。"""

from __future__ import annotations

import hashlib
from typing import Any

from l4.applicability import evaluate_applicability
from l4.causal_knowledge.loader import catalog_version, list_entries_for_fault, rules_version
from l4.fault_eligibility import filter_faults_for_root_cause
from l4.schema import ROOT_CAUSE_CONFIRMATION


def _cid(*parts: str) -> str:
    raw = "|".join(parts)
    return "RC-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _path_id(*parts: str) -> str:
    raw = "|".join(parts)
    return "CP-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def build_root_cause_candidates(ctx: dict[str, Any]) -> dict[str, Any]:
    equipment_type = ctx.get("equipment_type")
    equipment_id = ctx.get("equipment_id")
    notes: list[str] = []

    eligibility = filter_faults_for_root_cause(ctx)
    eligible_faults = eligibility.get("eligible_faults") or []
    excluded_faults = eligibility.get("excluded_faults") or []

    if excluded_faults:
        notes.append(
            f"已排除 {len(excluded_faults)} 个 L3 否定/不适用/冲突故障，不进入有效根因展开。"
        )

    candidates: list[dict[str, Any]] = []
    reference_candidates: list[dict[str, Any]] = []
    excluded_by_applicability: list[dict[str, Any]] = []
    paths: list[dict[str, Any]] = []

    if not (ctx.get("l3_fault_candidates") or []):
        notes.append("L3 未提供故障候选；无法生成根因候选。")
        return {
            "root_cause_candidates": [],
            "reference_candidates": [],
            "excluded_faults": excluded_faults,
            "excluded_by_applicability": [],
            "causal_paths": [],
            "notes": notes,
            "causal_knowledge_version": catalog_version(),
            "rules_version": rules_version(),
            "eligibility_summary": eligibility,
            "empty_reason": "no_l3_fault_candidates",
        }

    if not eligible_faults:
        notes.append("全部故障候选经 Fault Eligibility 过滤后均不可展开根因。")
        return {
            "root_cause_candidates": [],
            "reference_candidates": [],
            "excluded_faults": excluded_faults,
            "excluded_by_applicability": [],
            "causal_paths": [],
            "notes": notes,
            "causal_knowledge_version": catalog_version(),
            "rules_version": rules_version(),
            "eligibility_summary": eligibility,
            "empty_reason": "all_faults_excluded_by_eligibility",
        }

    matched_any = False
    for fault_row in eligible_faults:
        fault = str(fault_row.get("fault_type") or "").strip()
        if not fault:
            continue
        elig = fault_row.get("_eligibility") or {}
        entries = list_entries_for_fault(fault, equipment_type=None)  # 全量候选，由适用性判定
        if not entries:
            notes.append(f"故障「{fault}」无 curated 因果知识。")
            continue
        matched_any = True
        for entry in entries:
            appl = evaluate_applicability(
                entry, equipment_type=equipment_type, equipment_id=equipment_id
            )
            status = appl.get("applicability_status") or "unresolved"
            path_id = _path_id(entry["causal_knowledge_id"], fault, ctx["record_id"])
            cand_id = _cid(entry["causal_knowledge_id"], fault, ctx["record_id"])
            matching = entry.get("evidence_matching") or {}
            path = {
                "causal_path_id": path_id,
                "causal_knowledge_id": entry["causal_knowledge_id"],
                "cause": entry["cause"],
                "mechanism": entry["mechanism"],
                "resulting_fault": entry["resulting_fault"],
                "related_fault_type": fault,
                "validation_status": entry.get("validation_status") or "candidate_mechanism",
                "source_knowledge_ids": entry.get("source_knowledge_ids") or [],
                "source_document_locations": entry.get("source_document_locations") or [],
                "required_observations": entry.get("required_observations") or [],
                "supporting_patterns": entry.get("supporting_patterns") or [],
                "contradicting_patterns": entry.get("contradicting_patterns") or [],
                "evidence_matching": matching,
                "steps": [
                    {"step_id": "cause", "label": entry["cause"], "role": "ROOT_CAUSE_CANDIDATE"},
                    {"step_id": "mechanism", "label": entry["mechanism"], "role": "CAUSAL_MECHANISM"},
                    {"step_id": "fault", "label": fault, "role": "FAULT_REFERENCE"},
                ],
                "link_states": {},
            }
            cand = {
                "candidate_id": cand_id,
                "cause_name": entry["cause"],
                "related_fault_type": fault,
                "causal_path_id": path_id,
                "causal_knowledge_id": entry["causal_knowledge_id"],
                "source_knowledge_ids": entry.get("source_knowledge_ids") or [],
                "source_document_locations": entry.get("source_document_locations") or [],
                "supporting_evidence_ids": [],
                "fault_manifestation_evidence_ids": [],
                "contextual_evidence_ids": [],
                "contradicting_evidence_ids": [],
                "missing_evidence": list(entry.get("required_observations") or []),
                "applicability_status": status,
                "applicability_level": appl.get("applicability_level") or status,
                "applicability_notes": list(appl.get("applicability_notes") or []),
                "active_candidate": bool(appl.get("expand_as_active_candidate")),
                "assessment_status": ROOT_CAUSE_CONFIRMATION,
                "support_summary": "待 CausalPathValidator 对照本机证据",
                "l3_fault_source": fault_row.get("source"),
                "l3_eligibility": elig.get("eligibility"),
                "l3_eligibility_reason": elig.get("reason"),
                "evidence_insufficient_from_l3": elig.get("eligibility")
                == "eligible_conservative",
            }

            cand["structure_source"] = appl.get("structure_source")
            cand["component_binding"] = appl.get("component_binding")

            if status == "not_applicable":
                excluded_by_applicability.append(
                    {
                        "causal_knowledge_id": entry["causal_knowledge_id"],
                        "related_fault_type": fault,
                        "applicability_status": status,
                        "reason": "; ".join(appl.get("applicability_notes") or []),
                    }
                )
                continue

            if appl.get("expand_as_active_candidate"):
                # applicable 或 associated_unconfirmed（关联传动未确认但仍可保留候选）
                paths.append(path)
                candidates.append(cand)
            else:
                # reference_only / unresolved：可追溯，不进入有效根因列表
                reference_candidates.append(cand)
                paths.append(path)

    if not matched_any and not candidates and not reference_candidates:
        return {
            "root_cause_candidates": [],
            "reference_candidates": [],
            "excluded_faults": excluded_faults,
            "excluded_by_applicability": excluded_by_applicability,
            "causal_paths": [],
            "notes": notes or ["无适用因果知识"],
            "causal_knowledge_version": catalog_version(),
            "rules_version": rules_version(),
            "eligibility_summary": eligibility,
            "empty_reason": "no_applicable_causal_knowledge",
        }

    if not candidates and (reference_candidates or excluded_by_applicability):
        notes.append("无 applicable 根因候选；仅有 reference_only/unresolved 或 not_applicable 排除项。")

    return {
        "root_cause_candidates": candidates,
        "reference_candidates": reference_candidates,
        "excluded_faults": excluded_faults,
        "excluded_by_applicability": excluded_by_applicability,
        "causal_paths": paths,
        "notes": notes,
        "causal_knowledge_version": catalog_version(),
        "rules_version": rules_version(),
        "eligibility_summary": eligibility,
        "empty_reason": None if candidates else (
            "no_applicable_causal_knowledge" if not reference_candidates else "only_reference_candidates"
        ),
    }
