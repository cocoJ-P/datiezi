"""L3 故障候选资格过滤 v1.2：排除原因可追溯至真实 evidence_id。"""

from __future__ import annotations

from typing import Any

ELIGIBLE = "eligible"
ELIGIBLE_CONSERVATIVE = "eligible_conservative"
EXCLUDED_CONTRADICTED = "excluded_contradicted"
EXCLUDED_NOT_APPLICABLE = "excluded_not_applicable"
EXCLUDED_CONFLICT = "excluded_core_reasoning_conflict"
EXCLUDED_UNKNOWN = "excluded_unknown_or_unusable"


def normalize_verdict(raw: Any) -> str:
    text = str(raw or "").strip().upper().replace("-", "_")
    aliases = {
        "SUPPORT": "SUPPORTED",
        "SUPPORTED": "SUPPORTED",
        "INCONCLUSIVE": "INCONCLUSIVE",
        "LIMITED": "INCONCLUSIVE",
        "CONTRADICT": "CONTRADICTED",
        "CONTRADICTED": "CONTRADICTED",
        "NOT_APPLICABLE": "NOT_APPLICABLE",
        "NA": "NOT_APPLICABLE",
        "N/A": "NOT_APPLICABLE",
    }
    return aliases.get(text, "UNKNOWN" if not text else "UNKNOWN")


def _core_and_reasoning_verdicts(fault_row: dict[str, Any], ctx: dict[str, Any]) -> tuple[str | None, str | None, list[str]]:
    fault = str(fault_row.get("fault_type") or "").strip()
    core_v: str | None = None
    reasoning_v: str | None = None
    all_v: list[str] = []

    src = str(fault_row.get("source") or "")
    row_v = normalize_verdict(fault_row.get("verdict") or fault_row.get("assessment"))
    if row_v != "UNKNOWN":
        all_v.append(row_v)
        if src in {"l3_fault_type_review", "l2_preliminary_fault"} or not src.startswith("l3_reasoning"):
            core_v = row_v
        if src == "l3_reasoning_hypothesis":
            reasoning_v = row_v

    assessment = ctx.get("reasoning_assessment") if isinstance(ctx.get("reasoning_assessment"), dict) else None
    if assessment and fault:
        for hyp in assessment.get("candidate_hypotheses") or []:
            if not isinstance(hyp, dict):
                continue
            name = str(hyp.get("fault_type") or "").strip()
            base = name.split("（")[0].split("/")[0].strip()
            if name == fault or base == fault:
                hv = normalize_verdict(hyp.get("assessment"))
                if hv != "UNKNOWN":
                    all_v.append(hv)
                    reasoning_v = hv
    # 若 core 仍空但 row 来自 review
    if core_v is None and src == "l3_fault_type_review" and row_v != "UNKNOWN":
        core_v = row_v
    return core_v, reasoning_v, list(dict.fromkeys(all_v))


def _known_evidence_ids(ctx: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for key in ("support_evidence", "contradict_evidence", "evidence_references"):
        for row in ctx.get(key) or []:
            if isinstance(row, dict) and row.get("evidence_id"):
                ids.add(str(row["evidence_id"]))
    bundle = ctx.get("evidence_bundle") or {}
    for row in bundle.get("evidence_items") or []:
        if isinstance(row, dict) and row.get("evidence_id"):
            ids.add(str(row["evidence_id"]))
    return ids


def _contradicting_evidence_ids_for_fault(fault: str, ctx: dict[str, Any]) -> tuple[list[str], bool]:
    """返回经验证存在的反对证据 ID；若无可用 ID 则 citation_missing=True。"""
    known = _known_evidence_ids(ctx)
    found: list[str] = []
    bundle = ctx.get("evidence_bundle") or {}
    pools = []
    for key in ("contradict_evidence", "evidence_items", "support_evidence"):
        pools.extend(bundle.get(key) or [])
    for key in ("contradict_evidence", "evidence_references"):
        pools.extend(ctx.get(key) or [])

    for row in pools:
        if not isinstance(row, dict):
            continue
        mode = str(row.get("related_fault_mode") or "").strip()
        assessment = str(row.get("assessment") or "").upper()
        eid = str(row.get("evidence_id") or "")
        if not eid or eid not in known:
            continue
        if mode == fault and "CONTRADICT" in assessment:
            if eid not in found:
                found.append(eid)

    # Reasoning hypothesis 引用
    assessment = ctx.get("reasoning_assessment") if isinstance(ctx.get("reasoning_assessment"), dict) else None
    if assessment:
        for hyp in assessment.get("candidate_hypotheses") or []:
            if not isinstance(hyp, dict):
                continue
            name = str(hyp.get("fault_type") or "").strip()
            base = name.split("（")[0].split("/")[0].strip()
            if name != fault and base != fault:
                continue
            for eid in hyp.get("contradicting_evidence_ids") or []:
                eid_s = str(eid)
                if eid_s in known and eid_s not in found:
                    found.append(eid_s)

    citation_missing = len(found) == 0
    return found, citation_missing


def _base_exclusion(
    *,
    fault: str,
    eligibility: str,
    reason: str,
    core_v: str | None,
    reasoning_v: str | None,
    unique: list[str],
    ctx: dict[str, Any],
    fault_row: dict[str, Any],
    needs_review: bool,
) -> dict[str, Any]:
    contra_ids, citation_missing = _contradicting_evidence_ids_for_fault(fault, ctx)
    return {
        "fault_type": fault,
        "eligibility": eligibility,
        "exclusion_reason": reason,
        "reason": reason,
        "expand_root_cause": False,
        "normalized_verdicts": unique,
        "l3_core_verdict": core_v,
        "l3_reasoning_verdict": reasoning_v,
        "contradicting_evidence_ids": contra_ids,
        "evidence_citation_missing": citation_missing,
        "source_analysis_run_id": ctx.get("source_analysis_run_id"),
        "source_reasoning_run_id": ctx.get("source_reasoning_run_id"),
        "needs_review": needs_review,
        "evidence_hint": {
            "contradict_count": fault_row.get("contradict_count"),
            "support_count": fault_row.get("support_count"),
        },
        "source_row": {
            "source": fault_row.get("source"),
            "verdict": fault_row.get("verdict") or fault_row.get("assessment"),
        },
    }


def evaluate_fault_eligibility(fault_row: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    fault = str(fault_row.get("fault_type") or "").strip()
    core_v, reasoning_v, unique = _core_and_reasoning_verdicts(fault_row, ctx)

    positive = {"SUPPORTED", "INCONCLUSIVE"}
    negative = {"CONTRADICTED", "NOT_APPLICABLE"}
    has_pos = any(v in positive for v in unique)
    has_neg = any(v in negative for v in unique)

    if has_pos and has_neg and "CONTRADICTED" in unique:
        return _base_exclusion(
            fault=fault,
            eligibility=EXCLUDED_CONFLICT,
            reason="L3 Core 与 Reasoning 对同一故障结论冲突；不自动展开有效根因，保留待复核。",
            core_v=core_v,
            reasoning_v=reasoning_v,
            unique=unique,
            ctx=ctx,
            fault_row=fault_row,
            needs_review=True,
        )
    if has_pos and "NOT_APPLICABLE" in unique and "SUPPORTED" not in unique:
        return _base_exclusion(
            fault=fault,
            eligibility=EXCLUDED_NOT_APPLICABLE,
            reason="存在 NOT_APPLICABLE 结论，禁止生成适用根因。",
            core_v=core_v,
            reasoning_v=reasoning_v,
            unique=unique,
            ctx=ctx,
            fault_row=fault_row,
            needs_review=False,
        )
    if "NOT_APPLICABLE" in unique:
        return _base_exclusion(
            fault=fault,
            eligibility=EXCLUDED_NOT_APPLICABLE,
            reason="L3 判定 NOT_APPLICABLE，禁止生成适用根因。",
            core_v=core_v,
            reasoning_v=reasoning_v,
            unique=unique,
            ctx=ctx,
            fault_row=fault_row,
            needs_review=False,
        )
    if "CONTRADICTED" in unique and "SUPPORTED" not in unique:
        return _base_exclusion(
            fault=fault,
            eligibility=EXCLUDED_CONTRADICTED,
            reason="L3 判定 CONTRADICTED/CONTRADICT，默认不展开有效根因。",
            core_v=core_v,
            reasoning_v=reasoning_v,
            unique=unique,
            ctx=ctx,
            fault_row=fault_row,
            needs_review=False,
        )
    if "SUPPORTED" in unique:
        return {
            "fault_type": fault,
            "eligibility": ELIGIBLE,
            "expand_root_cause": True,
            "normalized_verdicts": unique,
            "l3_core_verdict": core_v,
            "l3_reasoning_verdict": reasoning_v,
            "reason": "L3 支持该故障表现，允许生成上游根因候选（仍 unconfirmed）。",
            "source_row": {
                "source": fault_row.get("source"),
                "verdict": fault_row.get("verdict") or fault_row.get("assessment"),
            },
        }
    if "INCONCLUSIVE" in unique:
        return {
            "fault_type": fault,
            "eligibility": ELIGIBLE_CONSERVATIVE,
            "expand_root_cause": True,
            "normalized_verdicts": unique,
            "l3_core_verdict": core_v,
            "l3_reasoning_verdict": reasoning_v,
            "reason": "L3 结论不充分（INCONCLUSIVE），允许保守生成根因候选并标记证据不足。",
            "source_row": {
                "source": fault_row.get("source"),
                "verdict": fault_row.get("verdict") or fault_row.get("assessment"),
            },
        }

    source = str(fault_row.get("source") or "")
    if source in {"l3_fault_type_review", "l2_preliminary_fault", "l3_reasoning_hypothesis"} and fault:
        return {
            "fault_type": fault,
            "eligibility": ELIGIBLE_CONSERVATIVE,
            "expand_root_cause": True,
            "normalized_verdicts": unique or ["UNKNOWN"],
            "l3_core_verdict": core_v,
            "l3_reasoning_verdict": reasoning_v,
            "reason": "无明确 SUPPORTED/CONTRADICTED 枚举；按现有候选保守展开，不视为已支持。",
            "source_row": {
                "source": fault_row.get("source"),
                "verdict": fault_row.get("verdict") or fault_row.get("assessment"),
            },
        }

    return _base_exclusion(
        fault=fault,
        eligibility=EXCLUDED_UNKNOWN,
        reason="缺少可用故障判断，不自动展开根因。",
        core_v=core_v,
        reasoning_v=reasoning_v,
        unique=unique or ["UNKNOWN"],
        ctx=ctx,
        fault_row=fault_row,
        needs_review=True,
    )


def filter_faults_for_root_cause(ctx: dict[str, Any]) -> dict[str, Any]:
    eligible_rows: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for row in ctx.get("l3_fault_candidates") or []:
        if not isinstance(row, dict):
            continue
        decision = evaluate_fault_eligibility(row, ctx)
        if decision.get("expand_root_cause"):
            enriched = dict(row)
            enriched["_eligibility"] = decision
            eligible_rows.append(enriched)
        else:
            excluded.append(decision)
    return {
        "eligible_faults": eligible_rows,
        "excluded_faults": excluded,
        "eligible_count": len(eligible_rows),
        "excluded_count": len(excluded),
    }
