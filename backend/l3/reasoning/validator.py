"""程序化校验 ReasoningAssessment：格式 / 引用 / 语义 / 证据关联。"""

from __future__ import annotations

from typing import Any

from l3.reasoning.schema import (
    EVIDENCE_ROLE_ANOMALY,
    EVIDENCE_ROLE_FAULT_SPECIFIC,
    EVIDENCE_ROLE_KNOWLEDGE,
    FORBIDDEN_FIELDS,
    VALID_HYPOTHESIS_ORIGINS,
    classify_evidence_role,
    normalize_hypothesis_origin,
)


def _collect_forbidden(obj: Any, found: list[str], path: str = "") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in FORBIDDEN_FIELDS:
                found.append(f"{path}.{k}" if path else k)
            _collect_forbidden(v, found, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _collect_forbidden(v, found, f"{path}[{i}]")


def _evidence_catalog(reasoning_input: dict[str, Any]) -> dict[str, dict[str, Any]]:
    items = []
    summary = reasoning_input.get("evidence_summary") or {}
    items.extend(summary.get("evidence_items") or [])
    bundle = reasoning_input.get("_evidence_bundle") or {}
    items.extend(bundle.get("evidence_items") or [])
    out: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        eid = item.get("evidence_id")
        if eid:
            out[str(eid)] = item
    return out


def _role_for(eid: str, catalog: dict[str, dict[str, Any]]) -> str:
    item = catalog.get(str(eid)) or {}
    return classify_evidence_role(
        evidence_id=str(eid),
        feature_name=item.get("feature_name"),
        related_fault_mode=item.get("related_fault_mode"),
    )


def apply_semantic_guards(
    assessment: dict[str, Any],
    *,
    reasoning_input: dict[str, Any],
) -> list[str]:
    """就地修正不合理的 SUPPORTED/CONTRADICTED；返回 demotion 说明。兼容旧字段，不覆盖落盘历史。"""
    notes: list[str] = []
    catalog = _evidence_catalog(reasoning_input)
    for hyp in assessment.get("candidate_hypotheses") or []:
        if not isinstance(hyp, dict):
            continue
        fault = str(hyp.get("fault_type") or "")
        support = [str(x) for x in (hyp.get("supporting_evidence_ids") or [])]
        contradict = [str(x) for x in (hyp.get("contradicting_evidence_ids") or [])]
        knowledge = [str(x) for x in (hyp.get("knowledge_reference_ids") or [])]
        roles_support = [_role_for(e, catalog) for e in support]
        # 仅通用异常 + 无故障特异证据 → 不得 SUPPORTED
        if str(hyp.get("assessment") or "") == "SUPPORTED":
            has_specific = any(r == EVIDENCE_ROLE_FAULT_SPECIFIC for r in roles_support)
            only_anomaly = bool(support) and all(r == EVIDENCE_ROLE_ANOMALY for r in roles_support)
            knowledge_only = (not support) and bool(knowledge)
            if only_anomaly or knowledge_only or (not support and not knowledge):
                hyp["assessment"] = "INCONCLUSIVE"
                hyp.setdefault("missing_evidence", [])
                if "fault_specific_evidence" not in hyp["missing_evidence"]:
                    hyp["missing_evidence"].append("fault_specific_evidence")
                notes.append(f"demote_supported_anomaly_only:{fault}")
            elif not has_specific and support:
                hyp["assessment"] = "INCONCLUSIVE"
                notes.append(f"demote_supported_without_fault_specific:{fault}")
        # 轴承类：仅个别特征反对时不得整体 CONTRADICTED
        if str(hyp.get("assessment") or "") == "CONTRADICTED" and ("轴承" in fault or "bearing" in fault.lower()):
            bearing_keys = ("bpfo", "bpfi", "bsf", "包络")
            contradicted_markers = set()
            for eid in contradict:
                item = catalog.get(eid) or {}
                blob = f"{eid} {item.get('feature_name') or ''}".lower()
                for k in bearing_keys:
                    if k in blob:
                        contradicted_markers.add(k)
            # 若反对面过窄（少于 2 类轴承特征）且支持侧几乎没有特异反对全集
            if len(contradicted_markers) <= 1:
                hyp["assessment"] = "INCONCLUSIVE"
                notes.append(f"demote_bearing_contradicted_incomplete:{fault}")
        # 标注证据角色（供前端/审计，不改变 EvidenceItem）
        hyp["evidence_roles"] = {
            "supporting": {eid: _role_for(eid, catalog) for eid in support},
            "contradicting": {eid: _role_for(eid, catalog) for eid in contradict},
            "knowledge": {kid: EVIDENCE_ROLE_KNOWLEDGE for kid in knowledge},
        }
    if notes:
        assessment["limitations"] = list(assessment.get("limitations") or []) + notes
    return notes


def sanitize_origins(assessment: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for hyp in assessment.get("candidate_hypotheses") or []:
        if not isinstance(hyp, dict):
            continue
        normalized, err = normalize_hypothesis_origin(hyp.get("hypothesis_origin"))
        if err:
            errors.append(err)
            hyp["hypothesis_origin"] = "exploratory_hypothesis"
            hyp["origin_sanitized"] = True
            assessment.setdefault("limitations", []).append(err)
        else:
            hyp["hypothesis_origin"] = normalized
    return errors


def validate_assessment(
    assessment: dict[str, Any],
    *,
    reasoning_input: dict[str, Any],
    apply_guards: bool = False,
) -> dict[str, Any]:
    format_errors: list[str] = []
    reference_errors: list[str] = []
    semantic_errors: list[str] = []
    association_errors: list[str] = []

    if not isinstance(assessment, dict):
        return {
            "ok": False,
            "errors": ["assessment_not_object"],
            "format_errors": ["assessment_not_object"],
            "reference_errors": [],
            "semantic_errors": [],
            "association_errors": [],
            "status": "validation_failed",
        }

    if apply_guards:
        apply_semantic_guards(assessment, reasoning_input=reasoning_input)
        sanitize_origins(assessment)

    if assessment.get("record_id") != reasoning_input.get("record_id"):
        format_errors.append("record_id_mismatch")
    if assessment.get("analysis_run_id") != reasoning_input.get("analysis_run_id"):
        format_errors.append("analysis_run_id_mismatch")
    eq = assessment.get("equipment_type")
    if eq and eq != reasoning_input.get("equipment_type"):
        format_errors.append("equipment_type_mismatch")

    allowed_ev = set(reasoning_input.get("allowed_evidence_ids") or [])
    allowed_kn = set(reasoning_input.get("allowed_knowledge_ids") or [])
    catalog = _evidence_catalog(reasoning_input)

    def check_ev_ids(ids: list[Any], field: str) -> None:
        for eid in ids or []:
            if str(eid) not in allowed_ev:
                reference_errors.append(f"unknown_evidence_id:{field}:{eid}")

    def check_kn_ids(ids: list[Any], field: str) -> None:
        for kid in ids or []:
            if allowed_kn and str(kid) not in allowed_kn:
                reference_errors.append(f"unknown_knowledge_id:{field}:{kid}")

    for hyp in assessment.get("candidate_hypotheses") or []:
        if not isinstance(hyp, dict):
            format_errors.append("hypothesis_not_object")
            continue
        origin = hyp.get("hypothesis_origin")
        if origin not in VALID_HYPOTHESIS_ORIGINS:
            semantic_errors.append(f"invalid_hypothesis_origin:{origin}")
        check_ev_ids(hyp.get("supporting_evidence_ids") or [], "hypothesis.support")
        check_ev_ids(hyp.get("contradicting_evidence_ids") or [], "hypothesis.contradict")
        check_kn_ids(hyp.get("knowledge_reference_ids") or [], "hypothesis.knowledge")
        status = str(hyp.get("assessment") or "")
        if status not in {"SUPPORTED", "CONTRADICTED", "INCONCLUSIVE", "NOT_APPLICABLE"}:
            format_errors.append(f"invalid_hypothesis_assessment:{status}")

        support = [str(x) for x in (hyp.get("supporting_evidence_ids") or [])]
        roles = [_role_for(e, catalog) for e in support]
        if status == "SUPPORTED":
            if support and all(r == EVIDENCE_ROLE_ANOMALY for r in roles):
                association_errors.append(f"supported_by_anomaly_only:{hyp.get('fault_type')}")
            if not support and (hyp.get("knowledge_reference_ids") or []):
                association_errors.append(f"supported_by_knowledge_only:{hyp.get('fault_type')}")

    check_ev_ids(assessment.get("evidence_references") or [], "evidence_references")
    # knowledge_references 允许 id 列表或对象列表
    kn_refs = assessment.get("knowledge_references") or []
    kn_ids = []
    for ref in kn_refs:
        if isinstance(ref, dict):
            if str(ref.get("as_measured_evidence") or "").lower() in {"1", "true", "yes"}:
                association_errors.append("knowledge_masquerading_as_measured_evidence")
            kid = ref.get("knowledge_id")
            if kid:
                kn_ids.append(kid)
        else:
            kn_ids.append(ref)
    check_kn_ids(kn_ids, "knowledge_references")

    forbidden: list[str] = []
    _collect_forbidden(assessment, forbidden)
    for f in forbidden:
        semantic_errors.append(f"forbidden_field:{f}")

    errors = format_errors + reference_errors + semantic_errors + association_errors
    ok = not errors
    return {
        "ok": ok,
        "errors": errors,
        "format_errors": format_errors,
        "reference_errors": reference_errors,
        "semantic_errors": semantic_errors,
        "association_errors": association_errors,
        "status": "validated" if ok else "validation_failed",
    }
