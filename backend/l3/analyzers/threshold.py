"""设备限值检查分析器。"""

from __future__ import annotations

from typing import Any

from l3.core.analyzer_base import (
    STATUS_INCONCLUSIVE,
    STATUS_NOT_APPLICABLE,
    STATUS_SUPPORT,
    Analyzer,
    AnalyzerResult,
)
from l3.core.context import AnalysisContext


class ThresholdAnalyzer(Analyzer):
    analyzer_id = "threshold"
    analyzer_version = "1.0.0"
    required_features = ()

    def applicability_check(self, context: AnalysisContext) -> tuple[bool, str]:
        if not context.threshold_rules:
            return False, "当前设备无已配置限值规则"
        return True, "ok"

    def analyze(self, context: AnalysisContext) -> AnalyzerResult:
        observations: list[dict[str, Any]] = []
        assessments: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        missing: list[str] = []
        exceeded = 0

        for rule in context.threshold_rules:
            param = str(rule.get("parameter") or "")
            if param not in context.features:
                missing.append(param)
                continue
            value = float(context.features[param])
            thr = float(rule["threshold"])
            thr_type = str(rule.get("threshold_type") or "upper_alarm")
            over = False
            margin = value - thr
            if thr_type.startswith("upper"):
                over = value > thr
            elif thr_type.startswith("lower"):
                over = value < thr
                margin = thr - value
            state = "exceeded" if over else "within"
            obs = {
                "rule_id": rule.get("rule_id"),
                "parameter": param,
                "observed_value": value,
                "threshold": thr,
                "unit": rule.get("unit"),
                "threshold_type": thr_type,
                "exceedance_state": state,
                "exceedance_margin": margin if over else 0.0,
                "source_reference": rule.get("source_reference"),
                "rule_version": rule.get("rule_version"),
                "applicability": "applicable",
            }
            observations.append(obs)
            if over:
                exceeded += 1
                assessments.append(
                    {
                        "feature_name": param,
                        "assessment": STATUS_SUPPORT,
                        "related_fault_mode": context.preliminary_fault,
                        "note": "越限为风险征兆，不等于正式严重度等级",
                    }
                )
                evidence.append(
                    {
                        "evidence_id": f"thr:{context.record_id}:{rule.get('rule_id')}",
                        "analyzer_id": self.analyzer_id,
                        "analyzer_version": self.analyzer_version,
                        "feature_name": param,
                        "observed_value": value,
                        "unit": rule.get("unit"),
                        "reference_value": thr,
                        "reference_source": rule.get("source_reference"),
                        "assessment": STATUS_SUPPORT,
                        "related_fault_mode": context.preliminary_fault,
                        "calculation_description": f"{param} {state} vs {thr_type} {thr}",
                        "provenance": {
                            "rule_id": rule.get("rule_id"),
                            "rule_version": rule.get("rule_version"),
                        },
                    }
                )

        if not observations and missing:
            return AnalyzerResult(
                analyzer_id=self.analyzer_id,
                version=self.analyzer_version,
                status=STATUS_NOT_APPLICABLE,
                observations=[],
                assessments=[],
                evidence_items=[],
                missing_inputs=missing,
                warnings=["规则参数在当前观测中均不可用"],
            )

        status = STATUS_SUPPORT if exceeded else STATUS_INCONCLUSIVE
        return AnalyzerResult(
            analyzer_id=self.analyzer_id,
            version=self.analyzer_version,
            status=status,
            observations=observations,
            assessments=assessments,
            evidence_items=evidence,
            missing_inputs=missing,
            warnings=[],
        )
