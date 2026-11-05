"""L2→L3 统一分流策略（单一口径，供 decision / routing-summary / L3 选记录复用）。"""

from __future__ import annotations

from typing import Iterable

# 与 decide() 当前规则绑定；历史 DiagnosisRun 保留原始 route，执行 L3 时记录本版本。
ROUTING_POLICY_VERSION = "l2_l3_routing_v2"

L3_AUTO_SCREENINGS = frozenset({"ABNORMAL", "CONFLICT", "UNCERTAIN"})
L2_COMPLETE_SCREENINGS = frozenset({"NORMAL"})

TRIGGER_BY_SCREENING = {
    "ABNORMAL": "fault_detected",
    "CONFLICT": "model_conflict",
    "UNCERTAIN": "uncertain_prediction",
}


def is_l3_auto_target(screening_result: str | None) -> bool:
    return str(screening_result or "").upper() in L3_AUTO_SCREENINGS


def effective_route(screening_result: str | None) -> str:
    """按当前策略计算有效 route（不改写已持久化的 historical decision.route）。"""
    screening = str(screening_result or "").upper()
    if screening in L3_AUTO_SCREENINGS:
        return "l3_review_recommended"
    if screening == "NORMAL":
        return "l2_complete"
    return "failed"


def trigger_reason_for(screening_result: str | None, *, manual: bool = False) -> str:
    if manual:
        return "manual_request"
    return TRIGGER_BY_SCREENING.get(str(screening_result or "").upper(), "manual_request")


def filter_l3_record_ids(rows: Iterable[dict]) -> list[str]:
    """从 diagnosis_records 行中选出全部需自动进入 L3 的 record_id（不受前端截断影响）。"""
    out: list[str] = []
    for row in rows:
        screening = row.get("screening_result") if isinstance(row, dict) else None
        if hasattr(row, "keys") and not isinstance(row, dict):
            try:
                screening = row["screening_result"]  # type: ignore[index]
                rid = row["record_id"]  # type: ignore[index]
            except Exception:  # noqa: BLE001
                continue
        else:
            rid = (row or {}).get("record_id")  # type: ignore[union-attr]
        if is_l3_auto_target(str(screening) if screening is not None else None):
            out.append(str(rid))
    return out
