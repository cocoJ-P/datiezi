"""Synthesis Agent：按需 DeepSeek 结构化复核（失败可回退）。"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

LOG = logging.getLogger("l5.synthesis.agent")

SYSTEM = (
    "你是钢铁设备诊断综合复核助手（Synthesis Agent）。"
    "你将收到：L5 监督预测十字段、Diagnosis Agent（L3）摘要、Causal Agent（L4）摘要、确定性对照结果。"
    "任务：评价一致性，列出冲突与证据不足，不得编造测量值或伪造 evidence/knowledge ID。"
    "不得把 L5 模板根因写成已确认根因；不得修改 L4 root_cause_confirmation。"
    "L5 根因模板 + L4 unconfirmed 且无明确反证时，应倾向 insufficient_evidence，不要仅因文本不重叠就标 conflicting。"
    "不得使用或索要数据集真实答案标签。"
    "只输出一个 JSON 对象，字段："
    "overall_consistency(consistent|partially_consistent|conflicting|insufficient_evidence),"
    "field_notes(array of {field, note}), unresolved_conflicts(array of string),"
    "evidence_references(array), knowledge_references(array), limitations(array), explanation(string)。"
)


def _extract_json(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None


def validate_synthesis_llm_output(obj: dict[str, Any] | None) -> tuple[bool, str]:
    if not isinstance(obj, dict):
        return False, "not_object"
    allowed = {
        "consistent",
        "partially_consistent",
        "conflicting",
        "insufficient_evidence",
        "unavailable",
    }
    if obj.get("overall_consistency") not in allowed:
        return False, "bad_overall_consistency"
    for key in ("evidence_references", "knowledge_references"):
        refs = obj.get(key) or []
        if not isinstance(refs, list):
            return False, f"{key}_not_list"
        for r in refs:
            if not isinstance(r, str) or not r.strip():
                return False, f"{key}_invalid_id"
            # 拒绝明显伪造前缀
            if str(r).lower().startswith("fake") or str(r).startswith("FORGED"):
                return False, f"{key}_forged"
    return True, "ok"


def run_synthesis_agent(
    payload: dict[str, Any],
    *,
    allow_llm: bool = False,
    budget_tokens: int = 2500,
) -> dict[str, Any]:
    """返回 agent_audit + optional llm_synthesis。allow_llm=False 时不调用模型。"""
    audit = {
        "agent_role": "synthesis",
        "invoked": False,
        "model_id": None,
        "tokens": None,
        "duration_ms": None,
        "outcome": "skipped",
        "validation_result": None,
        "tool_calls": [],
        "source_run_id": None,
    }
    if not allow_llm:
        return {"agent_audit": audit, "llm_synthesis": None}

    t0 = time.time()
    try:
        from app.services import deepseek
    except Exception as exc:  # noqa: BLE001
        audit.update({"outcome": "import_failed", "error": str(exc)})
        return {"agent_audit": audit, "llm_synthesis": None}

    diag = payload.get("diagnosis_agent_summary") or {}
    causal = payload.get("causal_agent_summary") or {}
    det = payload.get("deterministic_comparison") or {}
    compact = {
        "l5_predictions": (payload.get("l5_prediction") or {}).get("predictions"),
        "diagnosis_agent": {
            "status": diag.get("status"),
            "fault_type_review": (diag.get("fault_type_review") or [])[:4],
            "evidence_sufficiency": diag.get("evidence_sufficiency"),
            "evidence_references": (diag.get("evidence_references") or [])[:8],
            "knowledge_references": (diag.get("knowledge_references") or [])[:8],
            "explanation": str(diag.get("explanation") or "")[:300],
        },
        "causal_agent": {
            "status": causal.get("status"),
            "root_cause_confirmation": causal.get("root_cause_confirmation"),
            "ranked_candidates": (causal.get("ranked_candidates") or [])[:4],
            "evidence_references": (causal.get("evidence_references") or [])[:8],
            "explanation": str(causal.get("explanation") or "")[:300],
        },
        "deterministic_summary": det.get("consistency_summary"),
        "unresolved_conflicts": [
            {"field": r.get("field"), "consistency": r.get("consistency"), "detail": r.get("detail")}
            for r in (det.get("unresolved_conflicts") or [])[:8]
        ],
        "instruction": "只输出合法 JSON 对象，explanation 不超过 200 字。",
    }
    user = json.dumps(compact, ensure_ascii=False)[:8000]

    try:
        text = deepseek.complete_chat(SYSTEM, user)
        if isinstance(text, dict):
            obj = text
        else:
            obj = _extract_json(text if isinstance(text, str) else str(text))
        ok, why = validate_synthesis_llm_output(obj)
        audit.update(
            {
                "invoked": True,
                "model_id": getattr(deepseek, "MODEL", "deepseek"),
                "duration_ms": int((time.time() - t0) * 1000),
                "outcome": "ok" if ok else "rejected_invalid_output",
                "validation_result": why,
                "budget_tokens": budget_tokens,
            }
        )
        if not ok:
            preview = (text if isinstance(text, str) else str(text))[:240]
            audit["raw_preview"] = preview
            return {"agent_audit": audit, "llm_synthesis": None, "raw_rejected": True}
        return {"agent_audit": audit, "llm_synthesis": obj}
    except Exception as exc:  # noqa: BLE001
        LOG.exception("synthesis agent failed")
        audit.update(
            {
                "invoked": True,
                "duration_ms": int((time.time() - t0) * 1000),
                "outcome": "failed",
                "error": str(exc),
            }
        )
        return {"agent_audit": audit, "llm_synthesis": None}
