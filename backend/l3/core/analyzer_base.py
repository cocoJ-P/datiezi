"""分析器统一契约。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from l3.core.context import AnalysisContext

STATUS_SUPPORT = "SUPPORT"
STATUS_CONTRADICT = "CONTRADICT"
STATUS_INCONCLUSIVE = "INCONCLUSIVE"
STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass
class AnalyzerResult:
    analyzer_id: str
    version: str
    status: str
    observations: list[dict[str, Any]] = field(default_factory=list)
    assessments: list[dict[str, Any]] = field(default_factory=list)
    evidence_items: list[dict[str, Any]] = field(default_factory=list)
    missing_inputs: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "analyzer_id": self.analyzer_id,
            "version": self.version,
            "status": self.status,
            "observations": self.observations,
            "assessments": self.assessments,
            "evidence_items": self.evidence_items,
            "missing_inputs": self.missing_inputs,
            "warnings": self.warnings,
        }


class Analyzer(ABC):
    analyzer_id: str = "base"
    analyzer_version: str = "1.0.0"
    required_features: tuple[str, ...] = ()
    optional_features: tuple[str, ...] = ()
    supported_equipment_types: tuple[str, ...] | None = None  # None = all
    supported_fault_modes: tuple[str, ...] | None = None

    def applicability_check(self, context: AnalysisContext) -> tuple[bool, str]:
        if self.supported_equipment_types is not None and context.equipment_type not in self.supported_equipment_types:
            return False, f"设备类型 {context.equipment_type} 不在适用列表"
        missing = [f for f in self.required_features if f not in context.features or context.features.get(f) is None]
        if missing:
            return False, f"缺少必要特征：{', '.join(missing)}"
        return True, "ok"

    @abstractmethod
    def analyze(self, context: AnalysisContext) -> AnalyzerResult:
        raise NotImplementedError
