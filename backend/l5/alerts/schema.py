"""AlertRule / AlertEvaluation Schema。"""

from __future__ import annotations

from typing import Any

REVIEW_STATUSES = {"draft", "needs_review", "conflict", "approved", "deprecated"}
ALERT_LEVELS = {"alarm", "danger", "good", "other"}
EVAL_STATUSES = {
    "triggered",
    "within_limit",
    "indeterminate",
    "not_applicable",
    "rule_conflict",
    "not_assessed",
}
OPERATORS = {">", ">=", "<", "<="}


def empty_alert_assessment(*, record_id: str, alert_run_id: str | None = None) -> dict[str, Any]:
    return {
        "schema": "AlertAssessment",
        "schema_version": "alert_evaluation_v1",
        "alert_run_id": alert_run_id,
        "record_id": record_id,
        "catalog_version": None,
        "formal_alerts": [],
        "reference_comparisons": [],
        "conflicts": [],
        "not_assessed": [],
        "limitations": [],
        "formal_alert_count": 0,
        "reference_count": 0,
        "note": "AlertAssessment 为辅助模块，不改写十字段业务字段；不生成 risk_level/P0–P3。",
    }
