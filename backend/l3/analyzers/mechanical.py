"""基于标量机械特征的故障模式复核。"""

from __future__ import annotations

from typing import Any

import numpy as np

from l3.core.analyzer_base import (
    STATUS_CONTRADICT,
    STATUS_INCONCLUSIVE,
    STATUS_NOT_APPLICABLE,
    STATUS_SUPPORT,
    Analyzer,
    AnalyzerResult,
)
from l3.core.context import AnalysisContext

BEARING_FEATURES = ("BPFO幅值(g)", "BPFI幅值(g)", "BSF幅值(g)", "包络谱峰值(g)")
UNBALANCE_FEATURES = ("1X转频幅值(mm/s)", "X向振动(mm/s)", "Y向振动(mm/s)", "转速(rpm)")
GEAR_FEATURES = ("啮合频率幅值(g)",)
LUBE_FEATURES = ("包络谱峰值(g)", "驱动端轴承温度(°C)", "非驱动端轴承温度(°C)", "X向振动(mm/s)")


def _hist_median(context: AnalysisContext, name: str) -> float | None:
    vals = [float(pt.features[name]) for pt in context.history_before if name in pt.features]
    if len(vals) < 3:
        return None
    return float(np.median(np.asarray(vals, dtype=float)))


class MechanicalFeatureAnalyzer(Analyzer):
    analyzer_id = "mechanical_feature"
    analyzer_version = "1.0.0"
    required_features = ()

    def applicability_check(self, context: AnalysisContext) -> tuple[bool, str]:
        keys = set(context.features)
        if keys.intersection(BEARING_FEATURES + UNBALANCE_FEATURES + GEAR_FEATURES + LUBE_FEATURES):
            return True, "ok"
        return False, "缺少轴承/不平衡/齿轮/润滑相关标量特征"

    def analyze(self, context: AnalysisContext) -> AnalyzerResult:
        observations: list[dict[str, Any]] = []
        assessments: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        missing: list[str] = []
        warnings: list[str] = []
        statuses: list[str] = []

        candidates = context.candidate_faults or ([context.preliminary_fault] if context.preliminary_fault else [])
        modes = [m for m in candidates if m and m != "正常"]
        if not modes:
            modes = ["未指定故障候选"]

        for mode in modes:
            if "轴承" in mode:
                statuses.append(self._review_bearing(context, mode, observations, assessments, evidence, missing, warnings))
            elif "不平衡" in mode:
                statuses.append(self._review_unbalance(context, mode, observations, assessments, evidence, missing, warnings))
            elif "齿轮" in mode:
                statuses.append(self._review_gear(context, mode, observations, assessments, evidence, missing, warnings))
            elif "润滑" in mode:
                statuses.append(self._review_lube(context, mode, observations, assessments, evidence, missing, warnings))
            else:
                warnings.append(f"无专用机理规则：{mode}")
                statuses.append(STATUS_INCONCLUSIVE)

        # 无明确候选时做通用轴承/1X 扫描
        if modes == ["未指定故障候选"]:
            if any(f in context.features for f in BEARING_FEATURES):
                statuses.append(self._review_bearing(context, None, observations, assessments, evidence, missing, warnings))
            if "1X转频幅值(mm/s)" in context.features:
                statuses.append(self._review_unbalance(context, None, observations, assessments, evidence, missing, warnings))

        if not observations and not evidence:
            return AnalyzerResult(
                analyzer_id=self.analyzer_id,
                version=self.analyzer_version,
                status=STATUS_NOT_APPLICABLE if missing else STATUS_INCONCLUSIVE,
                observations=[],
                assessments=[],
                evidence_items=[],
                missing_inputs=missing,
                warnings=warnings or ["无可计算的机械特征复核"],
            )

        if STATUS_SUPPORT in statuses and STATUS_CONTRADICT in statuses:
            final = STATUS_INCONCLUSIVE
            warnings.append("存在支持与反对并存的机械特征证据，保留冲突")
        elif STATUS_SUPPORT in statuses:
            final = STATUS_SUPPORT
        elif STATUS_CONTRADICT in statuses:
            final = STATUS_CONTRADICT
        else:
            final = STATUS_INCONCLUSIVE

        return AnalyzerResult(
            analyzer_id=self.analyzer_id,
            version=self.analyzer_version,
            status=final,
            observations=observations,
            assessments=assessments,
            evidence_items=evidence,
            missing_inputs=missing,
            warnings=warnings,
        )

    def _elevated(
        self,
        context: AnalysisContext,
        name: str,
        *,
        mode: str | None,
        observations: list,
        assessments: list,
        evidence: list,
        missing: list,
        tag: str,
    ) -> str:
        if name not in context.features:
            missing.append(name)
            return STATUS_NOT_APPLICABLE
        value = float(context.features[name])
        ref = _hist_median(context, name)
        observations.append(
            {
                "feature_name": name,
                "observed_value": value,
                "history_median": ref,
                "fault_mode": mode,
                "check": tag,
            }
        )
        if ref is None:
            return STATUS_INCONCLUSIVE
        ratio = value / ref if abs(ref) > 1e-12 else float("inf")
        if ratio >= 1.5:
            assessment = STATUS_SUPPORT
        elif ratio <= 0.8:
            assessment = STATUS_CONTRADICT
        else:
            assessment = STATUS_INCONCLUSIVE
        assessments.append(
            {
                "feature_name": name,
                "assessment": assessment,
                "related_fault_mode": mode,
                "ratio_to_history_median": None if ratio == float("inf") else ratio,
            }
        )
        evidence.append(
            {
                "evidence_id": f"mech:{context.record_id}:{tag}:{name}",
                "analyzer_id": self.analyzer_id,
                "analyzer_version": self.analyzer_version,
                "feature_name": name,
                "observed_value": value,
                "unit": None,
                "reference_value": ref,
                "reference_source": context.history_note,
                "assessment": assessment,
                "related_fault_mode": mode,
                "calculation_description": f"{tag}: value/history_median={ratio if ratio != float('inf') else 'inf'}",
                "provenance": {"equipment_id": context.equipment_id, "history_note": context.history_note},
            }
        )
        return assessment

    def _review_bearing(self, context, mode, observations, assessments, evidence, missing, warnings) -> str:
        warnings.append("仅基于标量幅值复核；未验证多阶谐波或完整包络谱形状")
        results = []
        for n in BEARING_FEATURES:
            if n in context.features:
                results.append(
                    self._elevated(
                        context,
                        n,
                        mode=mode,
                        observations=observations,
                        assessments=assessments,
                        evidence=evidence,
                        missing=missing,
                        tag="bearing",
                    )
                )
            else:
                missing.append(n)
        usable = [r for r in results if r != STATUS_NOT_APPLICABLE]
        if not usable:
            return STATUS_NOT_APPLICABLE
        if STATUS_SUPPORT in usable:
            return STATUS_SUPPORT
        if STATUS_CONTRADICT in usable:
            return STATUS_CONTRADICT
        return STATUS_INCONCLUSIVE

    def _review_unbalance(self, context, mode, observations, assessments, evidence, missing, warnings) -> str:
        warnings.append("缺少相位与 2X 幅值时无法完成完整不平衡确证")
        if "1X转频幅值(mm/s)" not in context.features:
            missing.append("1X转频幅值(mm/s)")
            return STATUS_NOT_APPLICABLE
        return self._elevated(
            context,
            "1X转频幅值(mm/s)",
            mode=mode,
            observations=observations,
            assessments=assessments,
            evidence=evidence,
            missing=missing,
            tag="unbalance_1x",
        )

    def _review_gear(self, context, mode, observations, assessments, evidence, missing, warnings) -> str:
        warnings.append("无完整频谱时不验证边带分布")
        if "啮合频率幅值(g)" not in context.features:
            missing.append("啮合频率幅值(g)")
            return STATUS_NOT_APPLICABLE
        return self._elevated(
            context,
            "啮合频率幅值(g)",
            mode=mode,
            observations=observations,
            assessments=assessments,
            evidence=evidence,
            missing=missing,
            tag="gear_mesh",
        )

    def _review_lube(self, context, mode, observations, assessments, evidence, missing, warnings) -> str:
        warnings.append("润滑复核仅反映温度/振动/包络变化迹象，不能确认油量或油品问题")
        results = []
        for name in LUBE_FEATURES:
            if name in context.features:
                results.append(
                    self._elevated(
                        context,
                        name,
                        mode=mode,
                        observations=observations,
                        assessments=assessments,
                        evidence=evidence,
                        missing=missing,
                        tag="lubrication",
                    )
                )
            else:
                missing.append(name)
        usable = [r for r in results if r != STATUS_NOT_APPLICABLE]
        if not usable:
            return STATUS_NOT_APPLICABLE
        if STATUS_SUPPORT in usable:
            return STATUS_SUPPORT
        return STATUS_INCONCLUSIVE
