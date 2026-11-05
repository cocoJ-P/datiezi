"""统计与趋势分析器。"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from l3.core.analyzer_base import (
    STATUS_INCONCLUSIVE,
    STATUS_NOT_APPLICABLE,
    STATUS_SUPPORT,
    Analyzer,
    AnalyzerResult,
)
from l3.core.context import AnalysisContext

MIN_HISTORY = 5
VIB_KEYS = ("X向振动(mm/s)", "Y向振动(mm/s)", "最大振动(mm/s)", "1X转频幅值(mm/s)", "包络谱峰值(g)")


def _mad(values: np.ndarray) -> float:
    if values.size == 0:
        return float("nan")
    med = float(np.median(values))
    return float(np.median(np.abs(values - med)))


class StatisticalAnalyzer(Analyzer):
    analyzer_id = "statistical"
    analyzer_version = "1.0.0"
    required_features = ()
    optional_features = VIB_KEYS

    def applicability_check(self, context: AnalysisContext) -> tuple[bool, str]:
        if not context.features:
            return False, "无可用观测特征"
        return True, "ok"

    def analyze(self, context: AnalysisContext) -> AnalyzerResult:
        observations: list[dict[str, Any]] = []
        assessments: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        warnings: list[str] = []
        status = STATUS_INCONCLUSIVE

        if context.history_note in {"insufficient_history", "invalid_or_missing_collection_time", "no_equipment_id_single_record_only"}:
            warnings.append(context.history_note)

        hist_n = len(context.history_before)
        for name in context.feature_names:
            if name not in context.features:
                continue
            current = float(context.features[name])
            hist_vals = []
            for pt in context.history_before:
                if name in pt.features:
                    hist_vals.append(float(pt.features[name]))
            obs: dict[str, Any] = {
                "feature_name": name,
                "observed_value": current,
                "history_sample_count": len(hist_vals),
                "baseline_kind": context.history_note,
            }
            if len(hist_vals) >= MIN_HISTORY:
                arr = np.asarray(hist_vals, dtype=float)
                med = float(np.median(arr))
                q75 = float(np.percentile(arr, 75))
                q90 = float(np.percentile(arr, 90))
                mad = _mad(arr)
                rel = (current - med) / med if abs(med) > 1e-12 else float("nan")
                robust_z = (current - med) / (1.4826 * mad) if mad > 1e-12 else float("nan")
                # 简单趋势：历史后半 vs 前半
                mid = len(arr) // 2
                slope_dir = "flat"
                if mid >= 2:
                    early = float(np.median(arr[:mid]))
                    late = float(np.median(arr[mid:]))
                    if late > early * 1.1:
                        slope_dir = "rising"
                    elif late < early * 0.9:
                        slope_dir = "falling"
                obs.update(
                    {
                        "history_median": med,
                        "history_p75": q75,
                        "history_p90": q90,
                        "mad": mad,
                        "relative_change": None if math.isnan(rel) else rel,
                        "robust_z": None if math.isnan(robust_z) else robust_z,
                        "trend_direction": slope_dir,
                    }
                )
                if name in VIB_KEYS and not math.isnan(robust_z) and abs(robust_z) >= 3.0:
                    assessment = STATUS_SUPPORT if robust_z > 0 else STATUS_INCONCLUSIVE
                    assessments.append(
                        {
                            "feature_name": name,
                            "assessment": assessment,
                            "related_fault_mode": context.preliminary_fault,
                            "note": "相对历史参考显著偏离（非故障定论）",
                        }
                    )
                    evidence.append(
                        {
                            "evidence_id": f"stat:{context.record_id}:{name}",
                            "analyzer_id": self.analyzer_id,
                            "analyzer_version": self.analyzer_version,
                            "feature_name": name,
                            "observed_value": current,
                            "unit": None,
                            "reference_value": med,
                            "reference_source": context.history_note,
                            "assessment": assessment,
                            "related_fault_mode": context.preliminary_fault,
                            "calculation_description": f"robust_z={robust_z:.3f} vs history_median",
                            "provenance": {
                                "history_sample_count": len(hist_vals),
                                "equipment_id": context.equipment_id,
                            },
                        }
                    )
                    status = STATUS_SUPPORT
            else:
                obs["note"] = "insufficient_history_for_trend"
            observations.append(obs)

        if hist_n < MIN_HISTORY and status == STATUS_INCONCLUSIVE:
            status = STATUS_INCONCLUSIVE if observations else STATUS_NOT_APPLICABLE

        return AnalyzerResult(
            analyzer_id=self.analyzer_id,
            version=self.analyzer_version,
            status=status,
            observations=observations,
            assessments=assessments,
            evidence_items=evidence,
            missing_inputs=[],
            warnings=warnings,
        )
