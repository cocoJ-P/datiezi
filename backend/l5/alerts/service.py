"""L5b Alert 服务门面。"""

from __future__ import annotations

from typing import Any

from l5.alerts import CATALOG_VERSION, POLICY_VERSION, __version__
from l5.alerts.catalog import get_rule, list_rules, load_catalog
from l5.alerts.conflict_detector import detect_conflicts
from l5.alerts.jobs import run_alerts
from l5.alerts.store import ALERT_STORE


class AlertServiceError(Exception):
    def __init__(self, message: str, *, code: str = "alert_error", status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


def status() -> dict[str, Any]:
    cat = load_catalog()
    conflicts = detect_conflicts(cat)
    approved = list_rules(review_status="approved", catalog=cat)
    return {
        "layer": "l5b",
        "version": __version__,
        "catalog_version": cat.get("catalog_version") or CATALOG_VERSION,
        "policy_version": POLICY_VERSION,
        "approved_rule_count": len(approved),
        "rule_count": len(cat.get("rules") or []),
        "confirmed_conflict_count": len([c for c in conflicts if c.get("status") == "confirmed"]),
        "llm_enabled": False,
        "formal_alerts_require_approved_rules": True,
        "note": "无 approved 规则时 formal_alerts 为空是正确行为",
    }


def catalog_view() -> dict[str, Any]:
    cat = load_catalog()
    return {
        "catalog": cat,
        "conflicts": detect_conflicts(cat),
    }


def create_run(**kwargs: Any) -> dict[str, Any]:
    try:
        return run_alerts(**kwargs)
    except KeyError as exc:
        raise AlertServiceError(f"来源不存在：{exc}", code="not_found", status_code=404) from exc
    except RuntimeError as exc:
        raise AlertServiceError(str(exc), code="create_failed", status_code=400) from exc


def get_run(alert_run_id: str) -> dict[str, Any]:
    try:
        return {"alert_run": ALERT_STORE.get(alert_run_id)}
    except KeyError as exc:
        raise AlertServiceError(f"AlertRun 不存在：{alert_run_id}", code="not_found", status_code=404) from exc


def list_records(alert_run_id: str, *, offset: int = 0, limit: int = 50) -> dict[str, Any]:
    get_run(alert_run_id)
    return ALERT_STORE.list_records(alert_run_id, offset=offset, limit=limit)


def get_record(alert_run_id: str, record_id: str) -> dict[str, Any]:
    try:
        ALERT_STORE.get(alert_run_id)
        return ALERT_STORE.get_record(alert_run_id, record_id)
    except KeyError as exc:
        raise AlertServiceError(f"记录不存在：{record_id}", code="not_found", status_code=404) from exc


def get_by_l5(l5_run_id: str) -> dict[str, Any]:
    run = ALERT_STORE.get_by_l5(l5_run_id)
    return {"alert_run": run}


def get_rule_detail(rule_id: str) -> dict[str, Any]:
    rule = get_rule(rule_id)
    if not rule:
        raise AlertServiceError(f"规则不存在：{rule_id}", code="not_found", status_code=404)
    related = [c for c in detect_conflicts() if rule_id in (c.get("rule_ids") or [])]
    return {"rule": rule, "conflicts": related}
