"""规则批准审计日志（正式告警门控）。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

APPROVALS_PATH = Path(__file__).resolve().parent / "catalog" / "approvals_v1.json"


@lru_cache(maxsize=4)
def load_approvals(path: str | None = None) -> dict[str, Any]:
    target = Path(path) if path else APPROVALS_PATH
    if not target.is_file():
        return {"schema": "AlertRuleApprovalLog", "version": "approvals_v1", "approvals": []}
    return json.loads(target.read_text(encoding="utf-8"))


def clear_approvals_cache() -> None:
    load_approvals.cache_clear()


def find_approval(
    rule_id: str,
    rule_version: str | None,
    *,
    approvals: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    payload = approvals if approvals is not None else load_approvals()
    for row in payload.get("approvals") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("rule_id")) != str(rule_id):
            continue
        if rule_version is not None and str(row.get("rule_version")) != str(rule_version):
            continue
        if not row.get("authorized_by"):
            continue
        if not row.get("authorized_at"):
            continue
        if str(row.get("decision") or "").lower() != "approved":
            continue
        return row
    return None


def is_formally_authorized(rule: dict[str, Any], *, approvals: dict[str, Any] | None = None) -> tuple[bool, str]:
    if str(rule.get("review_status") or "") != "approved":
        return False, "review_status_not_approved"
    hit = find_approval(str(rule.get("rule_id")), rule.get("rule_version"), approvals=approvals)
    if not hit:
        return False, "missing_approval_audit_record"
    return True, f"authorized_by:{hit.get('authorized_by')}"
