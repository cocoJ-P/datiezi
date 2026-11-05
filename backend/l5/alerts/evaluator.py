"""AlertEvaluationEngine：确定性阈值比较（无 LLM）。"""

from __future__ import annotations

from typing import Any

from l5.alerts import CATALOG_VERSION, EVALUATION_SCHEMA_VERSION
from l5.alerts.applicability import assess_rule_applicability
from l5.alerts.approvals import is_formally_authorized, load_approvals
from l5.alerts.catalog import list_rules, load_catalog
from l5.alerts.conflict_detector import conflicting_rule_ids, detect_conflicts
from l5.alerts.observations import extract_observations
from l5.alerts.schema import empty_alert_assessment


def _compare(value: float, operator: str, threshold: float) -> bool:
    if operator == ">":
        return value > threshold
    if operator == ">=":
        return value >= threshold
    if operator == "<":
        return value < threshold
    if operator == "<=":
        return value <= threshold
    raise ValueError(f"unsupported_operator:{operator}")


def evaluate_record(
    *,
    record_id: str,
    equipment_type: str | None,
    equipment_id: str | None = None,
    evidence_bundle: dict[str, Any] | None = None,
    alert_run_id: str | None = None,
    measurement_timestamp: str | None = None,
    catalog: dict[str, Any] | None = None,
    approvals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cat = catalog if catalog is not None else load_catalog()
    approval_log = approvals if approvals is not None else load_approvals()
    conflicts = detect_conflicts(cat)
    blocked = conflicting_rule_ids(cat)
    observations = extract_observations(evidence_bundle)
    assessment = empty_alert_assessment(record_id=record_id, alert_run_id=alert_run_id)
    assessment["catalog_version"] = cat.get("catalog_version") or CATALOG_VERSION
    assessment["schema_version"] = EVALUATION_SCHEMA_VERSION
    assessment["conflicts"] = conflicts
    assessment["equipment_type"] = equipment_type
    assessment["equipment_id"] = equipment_id

    limitations = list(cat.get("notes") or [])
    if not equipment_type:
        limitations.append("equipment_type_unknown_unresolved_applicability")
    if not observations:
        limitations.append("no_numeric_observations_extracted")

    rules = list_rules(catalog=cat)
    for rule in rules:
        feats = [str(f) for f in (rule.get("canonical_feature_ids") or [])]
        if not feats:
            assessment["not_assessed"].append(
                {
                    "rule_id": rule.get("rule_id"),
                    "evaluation_status": "not_assessed",
                    "reason": "rule_missing_canonical_feature_ids",
                }
            )
            continue

        matched_any_feature = False
        for feat in feats:
            obs = observations.get(feat)
            app = assess_rule_applicability(
                rule,
                equipment_type=equipment_type,
                equipment_id=equipment_id,
                feature_name=feat if obs else None,
                observed_unit=(obs or {}).get("unit") if obs else None,
                observation_semantics=(obs or {}).get("semantics") if obs else None,
            )
            if app["status"] == "not_applicable":
                reasons = list(app.get("reasons") or [])
                joined = " ".join(reasons)
                if "equipment_type_mismatch" in joined or any(
                    x.startswith("cooling_pump") or x.startswith("gearbox_rule") for x in reasons
                ):
                    assessment["not_assessed"].append(
                        {
                            "rule_id": rule.get("rule_id"),
                            "evaluation_status": "not_applicable",
                            "applicability": app,
                            "feature": feat,
                        }
                    )
                    break
                if any(
                    x.startswith("unit_")
                    or x.startswith("feature_not_in_rule")
                    or "mismatch" in x
                    for x in reasons
                ):
                    assessment["not_assessed"].append(
                        {
                            "rule_id": rule.get("rule_id"),
                            "canonical_feature_id": feat,
                            "evaluation_status": "not_applicable",
                            "applicability": app,
                            "reason": reasons[0] if reasons else "not_applicable",
                        }
                    )
                continue

            if not obs:
                assessment["not_assessed"].append(
                    {
                        "rule_id": rule.get("rule_id"),
                        "canonical_feature_id": feat,
                        "evaluation_status": "indeterminate",
                        "reason": "missing_observation",
                        "applicability": app,
                    }
                )
                continue

            matched_any_feature = True
            rid = str(rule.get("rule_id"))
            review = str(rule.get("review_status") or "needs_review")
            op = str(rule.get("comparison_operator") or ">")
            thr = float(rule["threshold_value"])
            value = float(obs["value"])
            authorized, auth_reason = is_formally_authorized(rule, approvals=approval_log)

            base = {
                "record_id": record_id,
                "equipment_id": equipment_id,
                "equipment_type": equipment_type,
                "canonical_feature_id": feat,
                "observed_value": value,
                "observed_unit": obs.get("unit"),
                "measurement_kind": rule.get("measurement_kind"),
                "statistic": rule.get("statistic"),
                "observation_semantics": obs.get("semantics"),
                "measurement_timestamp": measurement_timestamp,
                "rule_id": rid,
                "rule_version": rule.get("rule_version"),
                "threshold_value": thr,
                "threshold_unit": rule.get("unit"),
                "comparison_operator": op,
                "alert_level": rule.get("alert_level"),
                "applicability_status": app["status"],
                "review_status": review,
                "authorization": auth_reason,
                "source_document_id": rule.get("source_document_id"),
                "source_locator": rule.get("source_locator"),
                "source_type": rule.get("source_type"),
                "source_evidence_ids": list(obs.get("evidence_ids") or []),
                "limitations": list(rule.get("limitations") or []),
            }

            # 测量语义未确认：禁止比较，不得 triggered/formal
            if app["status"] == "unresolved":
                base["evaluation_status"] = "indeterminate"
                base["formal"] = False
                base["channel"] = "reference_semantics_unresolved"
                base["comparison_holds"] = None
                base["limitations"] = list(base["limitations"]) + list(app.get("reasons") or []) + [
                    "statistic_or_measurement_kind_unresolved_no_comparison"
                ]
                if app.get("note"):
                    base["limitations"].append(str(app["note"]))
                assessment["reference_comparisons"].append(base)
                continue

            if rid in blocked or review == "conflict":
                base["evaluation_status"] = "rule_conflict"
                base["formal"] = False
                base["channel"] = "blocked_rule_conflict"
                base["limitations"] = list(base["limitations"]) + ["blocked_due_to_rule_conflict"]
                # 冲突时仍可做数值审计比较，但不可正式告警；approved 也不能绕开
                try:
                    base["comparison_holds"] = _compare(value, op, thr)
                except ValueError as exc:
                    base["evaluation_status"] = "indeterminate"
                    base["comparison_holds"] = None
                    base["limitations"].append(str(exc))
                assessment["reference_comparisons"].append(base)
                continue

            try:
                holds = _compare(value, op, thr)
            except ValueError as exc:
                base["evaluation_status"] = "indeterminate"
                base["formal"] = False
                base["comparison_holds"] = None
                base["limitations"] = list(base["limitations"]) + [str(exc)]
                assessment["reference_comparisons"].append(base)
                continue

            base["comparison_holds"] = holds
            base["evaluation_status"] = "triggered" if holds else "within_limit"

            # 正式告警：适用 + 语义完整 + 无冲突 + review_status=approved + 可追溯授权记录
            if authorized and holds:
                base["formal"] = True
                base["channel"] = "formal_approved_rule"
                assessment["formal_alerts"].append(base)
            else:
                base["formal"] = False
                if review == "approved" and not authorized:
                    base["channel"] = "reference_approved_without_audit"
                    base["limitations"] = list(base["limitations"]) + [
                        "review_status=approved 但缺少授权审计记录；非正式告警",
                        auth_reason,
                    ]
                elif review != "approved":
                    base["channel"] = "reference_unapproved_rule"
                    base["limitations"] = list(base["limitations"]) + [
                        f"review_status={review}; 仅参考比对，非正式安全告警"
                    ]
                else:
                    base["channel"] = "reference_authorized_within_limit"
                assessment["reference_comparisons"].append(base)

        if not matched_any_feature and equipment_type and str(rule.get("equipment_type")) == str(equipment_type):
            pass

    seen = set()
    uniq = []
    for row in assessment["not_assessed"]:
        key = (row.get("rule_id"), row.get("feature") or row.get("canonical_feature_id"), row.get("evaluation_status"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(row)
    assessment["not_assessed"] = uniq

    assessment["formal_alert_count"] = len(assessment["formal_alerts"])
    assessment["reference_count"] = len(assessment["reference_comparisons"])
    if assessment["formal_alert_count"] == 0:
        limitations.append("no_approved_rules_or_no_formal_triggers; formal_alerts_empty_is_valid")
    limitations.append("within_limit≠设备整体安全；triggered≠已确认根因或综合风险等级")
    assessment["limitations"] = list(dict.fromkeys(limitations))
    return assessment


def evaluate_from_l5_context(
    context: dict[str, Any],
    *,
    alert_run_id: str | None = None,
    catalog: dict[str, Any] | None = None,
    approvals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bundle = context.get("evidence_bundle") or {}
    return evaluate_record(
        record_id=str(context.get("record_id")),
        equipment_type=context.get("equipment_type"),
        equipment_id=context.get("equipment_id"),
        evidence_bundle=bundle,
        alert_run_id=alert_run_id,
        measurement_timestamp=bundle.get("collection_time"),
        catalog=catalog,
        approvals=approvals,
    )
