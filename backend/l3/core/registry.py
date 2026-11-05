"""分析器注册与选择。"""

from __future__ import annotations

from typing import Iterable

from l3.analyzers.evidence_review import EvidenceReviewer
from l3.analyzers.mechanical import MechanicalFeatureAnalyzer
from l3.analyzers.statistical import StatisticalAnalyzer
from l3.analyzers.threshold import ThresholdAnalyzer
from l3.core.analyzer_base import Analyzer, AnalyzerResult
from l3.core.context import AnalysisContext


class AnalyzerRegistry:
    def __init__(self, analyzers: Iterable[Analyzer] | None = None):
        self._analyzers = list(analyzers) if analyzers is not None else [
            StatisticalAnalyzer(),
            ThresholdAnalyzer(),
            MechanicalFeatureAnalyzer(),
        ]
        self._reviewer = EvidenceReviewer()

    def select(self, context: AnalysisContext) -> list[tuple[Analyzer, bool, str]]:
        selected: list[tuple[Analyzer, bool, str]] = []
        for analyzer in self._analyzers:
            ok, reason = analyzer.applicability_check(context)
            selected.append((analyzer, ok, reason))
        return selected

    def run_all(self, context: AnalysisContext) -> tuple[list[AnalyzerResult], dict]:
        results: list[AnalyzerResult] = []
        skipped: list[dict] = []
        for analyzer, ok, reason in self.select(context):
            if not ok:
                skipped.append(
                    {
                        "analyzer_id": analyzer.analyzer_id,
                        "analyzer_version": analyzer.analyzer_version,
                        "reason": reason,
                    }
                )
                continue
            results.append(analyzer.analyze(context))
        review = self._reviewer.review(context, results, skipped=skipped)
        return results, review


DEFAULT_REGISTRY = AnalyzerRegistry()
