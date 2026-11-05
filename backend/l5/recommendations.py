"""VerificationRecommendationBuilder：仅生成 verification 类型建议。"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from l5 import RECOMMENDATION_RULE_VERSION

# 标准化条件 ID：用于去重（不同测量要求不得仅因措辞相近合并）
_CONDITION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("phase_measurement", re.compile(r"相位|phase", re.I)),
    ("amp_2x", re.compile(r"\b2[xX]\b|二倍频|amp_2x|missing_feature:amp_2x", re.I)),
    ("amp_1x", re.compile(r"\b1[xX]\b|转频|amp_1x|missing_feature:amp_1x", re.I)),
    ("oil_analysis", re.compile(r"油液|油品|油位|含水|lubric|oil_water|oil_", re.I)),
    ("bearing_temp", re.compile(r"轴承温度|bearing_temp|missing_feature:bearing_temp", re.I)),
    ("bpfo", re.compile(r"BPFO|bpfo_amp", re.I)),
    ("bpfi", re.compile(r"BPFI|bpfi_amp", re.I)),
    ("bsf", re.compile(r"\bBSF\b|bsf_amp", re.I)),
    ("gear_mesh", re.compile(r"啮合|gear_mesh", re.I)),
    ("maintenance_history", re.compile(r"维修|拆检|检修记录|解体", re.I)),
    ("equipment_bom", re.compile(r"部件结构|BOM|设备型号|机封渗漏|资产", re.I)),
    ("full_spectrum", re.compile(r"完整频谱|包络谱形状|边带", re.I)),
]


def _norm_condition_id(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return "unknown_condition"
    # 已是结构化 missing_feature:* 则直接用
    if raw.startswith("missing_feature:"):
        return raw.lower()
    for cid, pat in _CONDITION_PATTERNS:
        if pat.search(raw):
            return f"condition:{cid}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    return f"condition:other:{digest}"


def _desc_for(condition_id: str, samples: list[str]) -> str:
    sample = samples[0] if samples else condition_id
    mapping = {
        "condition:phase_measurement": "需要进一步获得相位观测，以区分不平衡/对中等竞争解释。",
        "condition:amp_2x": "需要补充 2X 幅值相关观测。",
        "condition:amp_1x": "需要核实 1X 转频幅值及相关测量条件。",
        "condition:oil_analysis": "需要补充润滑油检测/油位油品或含水率记录。",
        "condition:bearing_temp": "需要核实轴承温度测点与规程可比条件。",
        "condition:maintenance_history": "需要核实设备维修/拆检历史记录。",
        "condition:equipment_bom": "需要确认设备型号与实际部件结构（含机封等）。",
        "condition:full_spectrum": "需要完整频谱/包络形状等更完整的频域证据（当前多为标量特征）。",
    }
    if condition_id in mapping:
        return mapping[condition_id]
    if condition_id.startswith("missing_feature:"):
        return f"需要补充结构化缺失特征：{condition_id.split(':', 1)[-1]}。"
    return f"需要进一步验证：{sample}"


def build_verification_recommendations(context: dict[str, Any]) -> list[dict[str, Any]]:
    bundle = context.get("evidence_bundle") or {}
    rca = context.get("root_cause_assessment") or {}
    cra = context.get("causal_reasoning_assessment") or {}

    buckets: dict[str, dict[str, Any]] = {}

    def _add(
        raw: str,
        *,
        layer: str,
        fault_ids: list[str] | None = None,
        rc_ids: list[str] | None = None,
        knowledge_id: str | None = None,
    ) -> None:
        text = str(raw or "").strip()
        if not text:
            return
        cid = _norm_condition_id(text)
        slot = buckets.setdefault(
            cid,
            {
                "condition_id": cid,
                "samples": [],
                "layers": set(),
                "fault_ids": set(),
                "rc_ids": set(),
                "knowledge_ids": set(),
            },
        )
        if text not in slot["samples"] and len(slot["samples"]) < 4:
            slot["samples"].append(text)
        slot["layers"].add(layer)
        for f in fault_ids or []:
            if f:
                slot["fault_ids"].add(str(f))
        for r in rc_ids or []:
            if r:
                slot["rc_ids"].add(str(r))
        if knowledge_id:
            slot["knowledge_ids"].add(str(knowledge_id))

    for item in bundle.get("missing_evidence") or []:
        if isinstance(item, dict):
            _add(
                str(item.get("feature_name") or item.get("evidence_id") or item.get("note") or item),
                layer="l3",
                fault_ids=[item.get("related_fault_mode")] if item.get("related_fault_mode") else None,
            )
        else:
            _add(str(item), layer="l3")

    spectral = bundle.get("spectral_feature_assessment") or {}
    for u in spectral.get("unverified") or []:
        _add(str(u), layer="l3_spectral")

    for cand in rca.get("root_cause_candidates") or []:
        if not isinstance(cand, dict):
            continue
        rc_id = cand.get("candidate_id")
        for m in cand.get("missing_evidence") or []:
            _add(
                str(m),
                layer="l4",
                fault_ids=[cand.get("related_fault_type")],
                rc_ids=[rc_id],
                knowledge_id=cand.get("causal_knowledge_id"),
            )

    for path in rca.get("causal_paths") or []:
        if not isinstance(path, dict):
            continue
        for obs in path.get("required_observations") or []:
            _add(
                str(obs),
                layer="l4_path",
                fault_ids=[path.get("related_fault_type")],
                knowledge_id=path.get("causal_knowledge_id"),
            )

    for rev in cra.get("candidate_reviews") or []:
        if not isinstance(rev, dict):
            continue
        for link in rev.get("unresolved_links") or []:
            _add(
                str(link),
                layer="l4_reasoning",
                rc_ids=[rev.get("candidate_id")],
            )
    for m in cra.get("missing_evidence") or []:
        _add(str(m), layer="l4_reasoning")

    # 故障候选 id（L3 review）
    fault_candidates = []
    for row in bundle.get("fault_type_review") or []:
        if isinstance(row, dict) and row.get("fault_mode"):
            fault_candidates.append(str(row.get("fault_mode")))

    out: list[dict[str, Any]] = []
    for idx, (cid, slot) in enumerate(sorted(buckets.items(), key=lambda x: x[0])):
        rid = f"VR-{hashlib.sha1(cid.encode('utf-8')).hexdigest()[:10]}"
        related_faults = sorted(slot["fault_ids"]) or fault_candidates[:3]
        out.append(
            {
                "recommendation_id": rid,
                "recommendation_type": "verification",
                "condition_id": cid,
                "description": _desc_for(cid, slot["samples"]),
                "related_fault_candidate_ids": related_faults,
                "related_root_cause_candidate_ids": sorted(slot["rc_ids"]),
                "missing_evidence_reference": list(slot["samples"]),
                "knowledge_source_id": next(iter(sorted(slot["knowledge_ids"])), None),
                "source_layers": sorted(slot["layers"]),
                "applicability_status": "needs_verification",
                "requires_human_review": True,
                "authorization_note": "涉及现场测试、拆检或工况变更时，须由授权人员按规程执行；本条仅为验证建议，不是维修/停机指令。",
                "rule_version": RECOMMENDATION_RULE_VERSION,
                "rank_hint": idx,
            }
        )
    return out
