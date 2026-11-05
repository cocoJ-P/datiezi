"""确定性证据整理（无 LLM）。"""

from __future__ import annotations

from typing import Any

from l3.core.analyzer_base import (
    STATUS_CONTRADICT,
    STATUS_INCONCLUSIVE,
    STATUS_NOT_APPLICABLE,
    STATUS_SUPPORT,
    AnalyzerResult,
)
from l3.core.context import AnalysisContext


class EvidenceReviewer:
    def review(
        self,
        context: AnalysisContext,
        results: list[AnalyzerResult],
        *,
        skipped: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        support: list[dict[str, Any]] = []
        contradict: list[dict[str, Any]] = []
        inconclusive: list[dict[str, Any]] = []
        all_evidence: list[dict[str, Any]] = []
        missing: list[str] = []
        limitations: list[str] = []
        dependencies: list[dict[str, Any]] = []

        for result in results:
            missing.extend(result.missing_inputs)
            limitations.extend(result.warnings)
            for item in result.evidence_items:
                all_evidence.append(item)
                assessment = item.get("assessment")
                if assessment == STATUS_SUPPORT:
                    support.append(item)
                elif assessment == STATUS_CONTRADICT:
                    contradict.append(item)
                else:
                    inconclusive.append(item)

        # 相关振动特征依赖标记（避免 X/Y/RMS 被当作独立强证据）
        vib_names = {"X向振动(mm/s)", "Y向振动(mm/s)", "最大振动(mm/s)", "振动均方根"}
        vib_ids = [e["evidence_id"] for e in all_evidence if e.get("feature_name") in vib_names]
        if len(vib_ids) >= 2:
            dependencies.append(
                {
                    "dependency_type": "common_source",
                    "evidence_ids": vib_ids,
                    "note": "振动相关特征可能同源，不应当作相互独立的多条强证据",
                }
            )

        covering_analyzers = {r.analyzer_id for r in results if r.status != STATUS_NOT_APPLICABLE}
        feature_coverage = len([f for f in context.feature_names if f in context.features]) / max(len(context.feature_names), 1)
        if len(support) >= 2 and feature_coverage >= 0.5:
            sufficiency = "sufficient"
        elif support or contradict:
            sufficiency = "limited"
        else:
            sufficiency = "insufficient"

        fault_reviews = []
        for mode in context.candidate_faults or [context.preliminary_fault]:
            if not mode or mode == "正常":
                continue
            s = [e for e in support if e.get("related_fault_mode") == mode]
            c = [e for e in contradict if e.get("related_fault_mode") == mode]
            if s and c:
                verdict = STATUS_INCONCLUSIVE
            elif s:
                verdict = STATUS_SUPPORT
            elif c:
                verdict = STATUS_CONTRADICT
            else:
                verdict = STATUS_INCONCLUSIVE
            fault_reviews.append(
                {
                    "fault_mode": mode,
                    "verdict": verdict,
                    "support_count": len(s),
                    "contradict_count": len(c),
                    "note": "L3 复核不改写 L2 preliminary_fault",
                }
            )

        # 严重度：仅征兆，无正式分级规则时 severity_level=null
        threshold_exceedances = []
        for result in results:
            if result.analyzer_id != "threshold":
                continue
            for obs in result.observations:
                if obs.get("exceedance_state") == "exceeded":
                    threshold_exceedances.append(obs)

        trend_notes = []
        for result in results:
            if result.analyzer_id != "statistical":
                continue
            for obs in result.observations:
                if obs.get("trend_direction") in {"rising", "falling"}:
                    trend_notes.append(
                        {
                            "feature_name": obs.get("feature_name"),
                            "trend_direction": obs.get("trend_direction"),
                            "robust_z": obs.get("robust_z"),
                        }
                    )

        severity = {
            "observed_risk_indicators": [
                {"type": "threshold_exceedance", "count": len(threshold_exceedances)},
                {"type": "trend_signals", "count": len(trend_notes)},
            ],
            "threshold_exceedances": threshold_exceedances,
            "trend_assessment": trend_notes,
            "severity_level": None,
            "note": "无正式 1–5 级严重度规则时不强制分级",
        }

        location = {
            "candidates": [],
            "limitations": ["传感器-部件映射不足，不能仅凭 X/Y 方向推断驱动端/非驱动端"],
        }

        spectral = {
            "observed_scalar_features": [
                name
                for name in ("BPFO幅值(g)", "BPFI幅值(g)", "BSF幅值(g)", "1X转频幅值(mm/s)", "啮合频率幅值(g)", "包络谱峰值(g)")
                if name in context.features
            ],
            "completed_calculations": [r.analyzer_id for r in results],
            "typical_pattern_reference": context.l2_spectral_reference,
            "unverified": [
                "完整频谱曲线",
                "包络谱形状",
                "边带分布",
                "相位",
                "2X 幅值",
            ],
            "note": "L2 spectral_reference 仅为 reference_only，不作为已验证频谱证据",
        }

        return {
            "fault_type_review": fault_reviews,
            "severity_assessment": severity,
            "location_candidates": location,
            "spectral_feature_assessment": spectral,
            "evidence_sufficiency": sufficiency,
            "evidence_items": all_evidence,
            "support_evidence": support,
            "contradict_evidence": contradict,
            "inconclusive_evidence": inconclusive,
            "missing_evidence": sorted(set(missing)),
            "analysis_limitations": sorted(set(limitations)),
            "skipped_analyzers": skipped or [],
            "evidence_dependencies": dependencies,
            "covering_analyzers": sorted(covering_analyzers),
            "knowledge_extension_hooks": {
                "allow_knowledge_citations": True,
                "allow_unverified_hypotheses": True,
                "llm_reasoning": False,
            },
        }
