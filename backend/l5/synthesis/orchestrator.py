"""DiagnosisSynthesisOrchestrator：三角色信息交接（默认不触发 LLM）。"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from l5.synthesis import SYNTHESIS_SCHEMA_VERSION, __version__
from l5.synthesis.comparison import run_deterministic_comparison
from l5.synthesis.synthesis_agent import run_synthesis_agent


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _diag_summary(l3: dict[str, Any] | None) -> dict[str, Any]:
    if not l3:
        return {"role": "diagnosis_agent", "status": "unavailable", "source": None}
    return {
        "role": "diagnosis_agent",
        "status": "available",
        "source": "l3_reasoning_assessment",
        "fault_type_review": (l3.get("fault_type_review") or [])[:6],
        "evidence_sufficiency": l3.get("evidence_sufficiency"),
        "location_interpretation": l3.get("location_interpretation"),
        "spectral_interpretation": str(l3.get("spectral_interpretation") or "")[:400],
        "explanation": str(l3.get("explanation") or "")[:500],
        "evidence_references": (l3.get("evidence_references") or [])[:12],
        "knowledge_references": (l3.get("knowledge_references") or [])[:12],
        "reasoning_run_id": l3.get("reasoning_run_id") or l3.get("run_id"),
    }


def _causal_summary(l4: dict[str, Any] | None) -> dict[str, Any]:
    if not l4:
        return {"role": "causal_agent", "status": "unavailable", "source": None}
    ranked = l4.get("ranked_candidates") or l4.get("candidates") or l4.get("candidate_reviews") or []
    return {
        "role": "causal_agent",
        "status": "available",
        "source": "l4_causal_reasoning_assessment",
        "root_cause_confirmation": l4.get("root_cause_confirmation") or "unconfirmed",
        "ranked_candidates": ranked[:6] if isinstance(ranked, list) else [],
        "propagation_status": l4.get("propagation_status")
        or (l4.get("impact_assessment") or {}).get("propagation_status"),
        "explanation": str(l4.get("explanation") or l4.get("summary") or "")[:500],
        "evidence_references": (l4.get("evidence_references") or l4.get("supporting_evidence_ids") or [])[:12],
        "knowledge_references": (l4.get("knowledge_references") or [])[:12],
        "reasoning_run_id": l4.get("reasoning_run_id") or l4.get("run_id"),
    }


def synthesize_record(
    *,
    record_id: str,
    synthesis_run_id: str,
    prediction: dict[str, Any] | None,
    l2_record: dict[str, Any] | None = None,
    evidence_bundle: dict[str, Any] | None = None,
    l3_reasoning: dict[str, Any] | None = None,
    l4_reasoning: dict[str, Any] | None = None,
    source_run_ids: dict[str, Any] | None = None,
    allow_llm_synthesis: bool = False,
    prediction_run_id: str | None = None,
) -> dict[str, Any]:
    """
    角色交接：
    1) Diagnosis Agent ← 已有 L3 Reasoning（不默认重跑）
    2) Causal Agent ← 已有 L4 Reasoning（不默认重跑）
    3) 确定性对照
    4) Synthesis Agent ← 仅 allow_llm_synthesis 时调用
    """
    diagnosis = _diag_summary(l3_reasoning)
    causal = _causal_summary(l4_reasoning)
    # 结构化交接对象（真实字段，非改名空壳）
    handoff = {
        "from_diagnosis_to_causal": {
            "fault_signals": diagnosis.get("fault_type_review"),
            "evidence_references": diagnosis.get("evidence_references"),
            "location_clues": diagnosis.get("location_interpretation"),
        },
        "from_agents_to_synthesis": {
            "diagnosis_status": diagnosis.get("status"),
            "causal_status": causal.get("status"),
            "l5_has_prediction": bool(prediction),
        },
    }

    det = run_deterministic_comparison(
        prediction=prediction,
        l2_record=l2_record,
        evidence_bundle=evidence_bundle,
        l3_reasoning=l3_reasoning,
        l4_reasoning=l4_reasoning,
    )

    agent_payload = {
        "l5_prediction": prediction,
        "diagnosis_agent_summary": diagnosis,
        "causal_agent_summary": causal,
        "deterministic_comparison": det,
    }
    syn = run_synthesis_agent(agent_payload, allow_llm=allow_llm_synthesis)

    # 收集可验证引用：只保留上游给出的 ID
    allowed_e = set(diagnosis.get("evidence_references") or []) | set(causal.get("evidence_references") or [])
    allowed_k = set(diagnosis.get("knowledge_references") or []) | set(causal.get("knowledge_references") or [])
    llm = syn.get("llm_synthesis") or {}
    ev_refs = [r for r in (llm.get("evidence_references") or []) if r in allowed_e]
    kn_refs = [r for r in (llm.get("knowledge_references") or []) if r in allowed_k]

    # L4 confirmation 快照（不可被改写）
    l4_conf = causal.get("root_cause_confirmation") or "unconfirmed"

    assessment = {
        "schema": "SynthesisAssessment",
        "schema_version": SYNTHESIS_SCHEMA_VERSION,
        "synthesis_run_id": synthesis_run_id,
        "prediction_run_id": prediction_run_id or (prediction or {}).get("model_versions", {}).get("model_id"),
        "record_id": record_id,
        "source_l2_run_id": (source_run_ids or {}).get("diagnosis_run_id"),
        "source_l3_run_id": (source_run_ids or {}).get("analysis_run_id"),
        "source_l4_run_id": (source_run_ids or {}).get("l4_run_id"),
        "source_reasoning_run_ids": {
            "l3_reasoning_run_id": diagnosis.get("reasoning_run_id"),
            "l4_reasoning_run_id": causal.get("reasoning_run_id"),
        },
        "model_versions": {
            "synthesis_engine": __version__,
            "l5_prediction": (prediction or {}).get("model_versions"),
        },
        "roles": {
            "diagnosis_agent": diagnosis,
            "causal_agent": causal,
            "synthesis_agent": {
                **(syn.get("agent_audit") or {}),
                "status": (
                    "available"
                    if (syn.get("agent_audit") or {}).get("outcome") == "ok"
                    else (
                        "skipped"
                        if (syn.get("agent_audit") or {}).get("outcome") == "skipped"
                        else "unavailable"
                    )
                ),
            },
        },
        "handoff": handoff,
        "field_comparisons": det.get("field_comparisons"),
        "consistency_summary": det.get("consistency_summary"),
        "unresolved_conflicts": det.get("unresolved_conflicts"),
        "llm_synthesis": llm or None,
        "evidence_references": ev_refs,
        "causal_knowledge_references": kn_refs,
        "agent_audit": [syn.get("agent_audit")],
        "l4_root_cause_confirmation_snapshot": l4_conf,
        "l5_predictions_unchanged": True,
        "used_ground_truth_labels": False,
        "limitations": [
            "default_path_is_prediction_plus_deterministic_comparison",
            "llm_synthesis_only_when_allow_llm_synthesis",
            "missing_l3_l4_marked_unavailable_not_prediction_failure",
            "dataset_predicted_root_cause_separate_from_l4_confirmation",
        ],
        "created_at": _now(),
    }
    if diagnosis["status"] == "unavailable" and causal["status"] == "unavailable":
        assessment["limitations"].append("knowledge_review_unavailable_for_this_record")
    return assessment


def content_key_for(record_ids: list[str], *, l5_run_id: str | None, allow_llm: bool) -> str:
    raw = f"{l5_run_id}|{','.join(sorted(record_ids))}|llm={int(allow_llm)}|{__version__}"
    return hashlib.sha256(raw.encode()).hexdigest()
