"""L5b 阈值目录、冲突、适用性、测量语义与正式门控测试。"""

from __future__ import annotations

import copy
import unittest

from l5.alerts.applicability import assess_rule_applicability
from l5.alerts.approvals import is_formally_authorized
from l5.alerts.catalog import get_rule, load_catalog
from l5.alerts.conflict_detector import conflicting_rule_ids, detect_conflicts
from l5.alerts.evaluator import evaluate_record
from l5.alerts.semantics import semantics_for_feature


def _bundle(features: dict[str, tuple[float, str]]) -> dict:
    items = []
    for name, (val, unit) in features.items():
        items.append(
            {
                "evidence_id": f"e:{name}",
                "feature_name": name,
                "observed_value": val,
                "unit": unit,
            }
        )
    return {"schema": "L3EvidenceBundle", "evidence_items": items, "analyzer_results": []}


class CatalogAuditTests(unittest.TestCase):
    def test_catalog_loads_and_marks_motor_temp_conflict(self):
        cat = load_catalog()
        self.assertEqual(cat["catalog_version"], "alert_rule_catalog_v1_1")
        self.assertTrue(cat["rules"])
        yaml_de = get_rule("YAML-MM-BEARING-TEMP-DE-AS-ALARM", catalog=cat)
        self.assertEqual(yaml_de["review_status"], "conflict")
        self.assertEqual(yaml_de["threshold_value"], 85.0)
        kb_alarm = get_rule("KB03-MM-BEARING-TEMP-ALARM", catalog=cat)
        self.assertEqual(kb_alarm["threshold_value"], 75.0)
        self.assertEqual(kb_alarm["alert_level"], "alarm")
        kb_danger = get_rule("KB03-MM-BEARING-TEMP-DANGER", catalog=cat)
        self.assertEqual(kb_danger["threshold_value"], 85.0)
        self.assertEqual(kb_danger["alert_level"], "danger")

    def test_vib_rules_not_guessed_as_rms(self):
        cat = load_catalog()
        for rid in (
            "KB03-MM-VIB-XY-ALARM",
            "YAML-DP-VIB-X-ALARM",
            "YAML-MM-VIB-X-ALARM",
        ):
            rule = get_rule(rid, catalog=cat)
            self.assertEqual(rule["measurement_kind"], "vibration_velocity")
            self.assertEqual(rule["statistic"], "unresolved")
            self.assertNotEqual(rule["measurement_kind"], "vibration_rms")

    def test_conflicts_detected(self):
        conflicts = detect_conflicts()
        ids = {c["conflict_id"] for c in conflicts}
        self.assertIn("CONF-MM-BEARING-TEMP-YAML-MM-BEARING-TEMP-DE-AS-ALARM", ids)
        self.assertIn("NOTE-CWP-VS-DESCALING-NOT-EQUIVALENT", ids)
        blocked = conflicting_rule_ids()
        self.assertIn("YAML-MM-BEARING-TEMP-DE-AS-ALARM", blocked)
        self.assertIn("KB03-MM-BEARING-TEMP-ALARM", blocked)


class ApplicabilityTests(unittest.TestCase):
    def test_cooling_pump_not_on_descaling(self):
        rule = get_rule("KB03-CWP-VIB-ALARM")
        app = assess_rule_applicability(
            rule,
            equipment_type="high_pressure_descaling_pump",
            feature_name="X向振动(mm/s)",
            observed_unit="mm/s",
        )
        self.assertEqual(app["status"], "not_applicable")

    def test_gearbox_not_on_motor(self):
        rule = get_rule("KB03-GB-VIB-ALARM")
        app = assess_rule_applicability(
            rule,
            equipment_type="main_mill_motor",
            feature_name="X向振动(mm/s)",
            observed_unit="mm/s",
        )
        self.assertEqual(app["status"], "not_applicable")

    def test_unknown_equipment_unresolved(self):
        rule = get_rule("KB03-MM-VIB-XY-ALARM")
        app = assess_rule_applicability(
            rule,
            equipment_type=None,
            feature_name="X向振动(mm/s)",
            observed_unit="mm/s",
        )
        self.assertEqual(app["status"], "unresolved")

    def test_xy_vibration_statistic_unresolved(self):
        rule = get_rule("KB03-MM-VIB-XY-ALARM")
        sem = semantics_for_feature("X向振动(mm/s)")
        self.assertEqual(sem["dimension"], "velocity")
        self.assertEqual(sem["statistic"], "unresolved")
        app = assess_rule_applicability(
            rule,
            equipment_type="main_mill_motor",
            feature_name="X向振动(mm/s)",
            observed_unit="mm/s",
            observation_semantics=sem,
        )
        self.assertEqual(app["status"], "unresolved")
        self.assertTrue(any("statistic" in r for r in app["reasons"]))


class EvaluatorBoundaryTests(unittest.TestCase):
    def test_xy_vibration_no_comparison_when_statistic_unresolved(self):
        a = evaluate_record(
            record_id="T1",
            equipment_type="main_mill_motor",
            evidence_bundle=_bundle({"X向振动(mm/s)": (8.0, "mm/s")}),
        )
        vib_refs = [
            r
            for r in a["reference_comparisons"]
            if r["rule_id"] == "KB03-MM-VIB-XY-ALARM" and r["canonical_feature_id"] == "X向振动(mm/s)"
        ]
        self.assertTrue(vib_refs)
        self.assertEqual(vib_refs[0]["evaluation_status"], "indeterminate")
        self.assertIsNone(vib_refs[0].get("comparison_holds"))
        self.assertFalse(vib_refs[0]["formal"])
        self.assertEqual(a["formal_alert_count"], 0)

    def test_temperature_boundary_still_compares(self):
        # 温度 statistic=point，可比较；但冲突规则仍 rule_conflict
        a = evaluate_record(
            record_id="T-temp",
            equipment_type="main_mill_motor",
            evidence_bundle=_bundle({"驱动端轴承温度(°C)": (80.0, "°C")}),
        )
        conflict_rows = [r for r in a["reference_comparisons"] if r.get("evaluation_status") == "rule_conflict"]
        self.assertTrue(conflict_rows)
        self.assertTrue(
            any(r["rule_id"].startswith("KB03-MM-BEARING-TEMP") for r in conflict_rows)
            or any(r["rule_id"].startswith("YAML-MM-BEARING-TEMP") for r in conflict_rows)
        )

    def test_missing_obs_and_unit(self):
        a = evaluate_record(record_id="T5", equipment_type="main_mill_motor", evidence_bundle=_bundle({}))
        self.assertTrue(any(x.get("reason") == "missing_observation" for x in a["not_assessed"]))

        b = evaluate_record(
            record_id="T6",
            equipment_type="main_mill_motor",
            evidence_bundle=_bundle({"X向振动(mm/s)": (8.0, "g")}),
        )
        self.assertTrue(
            any(
                r.get("evaluation_status") in {"not_applicable", "indeterminate"}
                or (r.get("applicability") or {}).get("status") == "not_applicable"
                for r in b["not_assessed"] + b["reference_comparisons"]
            )
            or any("unit" in str(x) for x in b["not_assessed"])
        )

    def test_no_formal_without_approved(self):
        a = evaluate_record(
            record_id="T7",
            equipment_type="main_mill_motor",
            evidence_bundle=_bundle(
                {"X向振动(mm/s)": (9.0, "mm/s"), "驱动端轴承温度(°C)": (80.0, "°C")}
            ),
        )
        self.assertEqual(a["formal_alert_count"], 0)
        conflict_rows = [r for r in a["reference_comparisons"] if r.get("evaluation_status") == "rule_conflict"]
        self.assertTrue(conflict_rows)

    def test_descaling_does_not_use_cooling_rules(self):
        a = evaluate_record(
            record_id="T8",
            equipment_type="high_pressure_descaling_pump",
            evidence_bundle=_bundle({"X向振动(mm/s)": (5.0, "mm/s")}),
        )
        cooling = [r for r in a["reference_comparisons"] if str(r.get("rule_id", "")).startswith("KB03-CWP")]
        self.assertEqual(cooling, [])
        # X 振动统计量 unresolved → indeterminate，不得因字段名猜 RMS 而 triggered
        dp = [r for r in a["reference_comparisons"] if str(r.get("rule_id", "")) == "YAML-DP-VIB-X-ALARM"]
        self.assertTrue(dp)
        self.assertEqual(dp[0]["evaluation_status"], "indeterminate")
        self.assertFalse(dp[0]["formal"])


class FormalGatingFixtureTests(unittest.TestCase):
    """隔离夹具：模拟批准规则，不修改生产目录。"""

    def _fixture_temp_rule(self, **overrides) -> dict:
        base = {
            "rule_id": "FIX-MM-BEARING-TEMP-ALARM",
            "rule_version": "fixture_v1",
            "source_document_id": "FIXTURE",
            "source_locator": "test-only",
            "source_type": "test_fixture",
            "equipment_type": "main_mill_motor",
            "equipment_id": None,
            "canonical_feature_ids": ["驱动端轴承温度(°C)"],
            "measurement_kind": "temperature",
            "statistic": "point",
            "unit": "°C",
            "comparison_operator": ">",
            "threshold_value": 75.0,
            "alert_level": "alarm",
            "review_status": "approved",
            "limitations": ["test fixture only"],
        }
        base.update(overrides)
        return base

    def _fixture_catalog(self, rules: list[dict]) -> dict:
        return {
            "schema": "AlertRuleCatalog",
            "catalog_version": "fixture_formal_gate_v1",
            "notes": ["test-only catalog; not production"],
            "rules": rules,
        }

    def _fixture_approvals(self, rule: dict) -> dict:
        return {
            "schema": "AlertRuleApprovalLog",
            "version": "approvals_fixture_v1",
            "approvals": [
                {
                    "rule_id": rule["rule_id"],
                    "rule_version": rule["rule_version"],
                    "decision": "approved",
                    "authorized_by": "test_authority:fixture_operator",
                    "authorized_at": "2026-11-06T00:00:00Z",
                    "rationale": "isolated formal-gate test only",
                }
            ],
        }

    def test_full_match_forms_formal_alert(self):
        rule = self._fixture_temp_rule()
        cat = self._fixture_catalog([rule])
        approvals = self._fixture_approvals(rule)
        ok, _ = is_formally_authorized(rule, approvals=approvals)
        self.assertTrue(ok)
        a = evaluate_record(
            record_id="FG1",
            equipment_type="main_mill_motor",
            evidence_bundle=_bundle({"驱动端轴承温度(°C)": (80.0, "°C")}),
            catalog=cat,
            approvals=approvals,
        )
        self.assertEqual(a["formal_alert_count"], 1)
        formal = a["formal_alerts"][0]
        self.assertEqual(formal["rule_id"], "FIX-MM-BEARING-TEMP-ALARM")
        self.assertEqual(formal["evaluation_status"], "triggered")
        self.assertTrue(formal["formal"])

    def test_equipment_mismatch_no_formal(self):
        rule = self._fixture_temp_rule()
        cat = self._fixture_catalog([rule])
        approvals = self._fixture_approvals(rule)
        a = evaluate_record(
            record_id="FG2",
            equipment_type="high_pressure_descaling_pump",
            evidence_bundle=_bundle({"驱动端轴承温度(°C)": (80.0, "°C")}),
            catalog=cat,
            approvals=approvals,
        )
        self.assertEqual(a["formal_alert_count"], 0)

    def test_unit_mismatch_no_formal(self):
        rule = self._fixture_temp_rule()
        cat = self._fixture_catalog([rule])
        approvals = self._fixture_approvals(rule)
        a = evaluate_record(
            record_id="FG3",
            equipment_type="main_mill_motor",
            evidence_bundle=_bundle({"驱动端轴承温度(°C)": (80.0, "K")}),
            catalog=cat,
            approvals=approvals,
        )
        self.assertEqual(a["formal_alert_count"], 0)

    def test_measurement_kind_mismatch_no_formal(self):
        rule = self._fixture_temp_rule(measurement_kind="vibration_velocity")
        cat = self._fixture_catalog([rule])
        approvals = self._fixture_approvals(rule)
        a = evaluate_record(
            record_id="FG4",
            equipment_type="main_mill_motor",
            evidence_bundle=_bundle({"驱动端轴承温度(°C)": (80.0, "°C")}),
            catalog=cat,
            approvals=approvals,
        )
        self.assertEqual(a["formal_alert_count"], 0)

    def test_conflict_blocks_even_if_approved(self):
        # 使用生产冲突规则对 + 夹具批准，验证 approved 不能绕开冲突拦截
        prod = load_catalog()
        kb = copy.deepcopy(get_rule("KB03-MM-BEARING-TEMP-ALARM", catalog=prod))
        yaml_r = copy.deepcopy(get_rule("YAML-MM-BEARING-TEMP-DE-AS-ALARM", catalog=prod))
        kb["review_status"] = "approved"
        yaml_r["review_status"] = "approved"
        cat = self._fixture_catalog([kb, yaml_r])
        approvals = {
            "schema": "AlertRuleApprovalLog",
            "version": "approvals_fixture_v1",
            "approvals": [
                {
                    "rule_id": kb["rule_id"],
                    "rule_version": kb["rule_version"],
                    "decision": "approved",
                    "authorized_by": "test_authority",
                    "authorized_at": "2026-11-06T00:00:00Z",
                },
                {
                    "rule_id": yaml_r["rule_id"],
                    "rule_version": yaml_r["rule_version"],
                    "decision": "approved",
                    "authorized_by": "test_authority",
                    "authorized_at": "2026-11-06T00:00:00Z",
                },
            ],
        }
        a = evaluate_record(
            record_id="FG5",
            equipment_type="main_mill_motor",
            evidence_bundle=_bundle({"驱动端轴承温度(°C)": (80.0, "°C")}),
            catalog=cat,
            approvals=approvals,
        )
        self.assertEqual(a["formal_alert_count"], 0)
        self.assertTrue(
            any(r.get("evaluation_status") == "rule_conflict" for r in a["reference_comparisons"])
        )

    def test_unapproved_only_reference(self):
        rule = self._fixture_temp_rule(review_status="needs_review")
        cat = self._fixture_catalog([rule])
        a = evaluate_record(
            record_id="FG6",
            equipment_type="main_mill_motor",
            evidence_bundle=_bundle({"驱动端轴承温度(°C)": (80.0, "°C")}),
            catalog=cat,
            approvals={"approvals": []},
        )
        self.assertEqual(a["formal_alert_count"], 0)
        refs = [r for r in a["reference_comparisons"] if r["rule_id"] == rule["rule_id"]]
        self.assertTrue(refs)
        self.assertEqual(refs[0]["evaluation_status"], "triggered")
        self.assertFalse(refs[0]["formal"])

    def test_approved_status_without_audit_not_formal(self):
        rule = self._fixture_temp_rule(review_status="approved")
        cat = self._fixture_catalog([rule])
        a = evaluate_record(
            record_id="FG7",
            equipment_type="main_mill_motor",
            evidence_bundle=_bundle({"驱动端轴承温度(°C)": (80.0, "°C")}),
            catalog=cat,
            approvals={"approvals": []},
        )
        self.assertEqual(a["formal_alert_count"], 0)
        refs = [r for r in a["reference_comparisons"] if r["rule_id"] == rule["rule_id"]]
        self.assertTrue(refs)
        self.assertEqual(refs[0]["channel"], "reference_approved_without_audit")

    def test_semantics_missing_no_formal(self):
        rule = self._fixture_temp_rule(
            canonical_feature_ids=["X向振动(mm/s)"],
            measurement_kind="vibration_velocity",
            statistic="unresolved",
            unit="mm/s",
            threshold_value=4.5,
        )
        cat = self._fixture_catalog([rule])
        approvals = self._fixture_approvals(rule)
        a = evaluate_record(
            record_id="FG8",
            equipment_type="main_mill_motor",
            evidence_bundle=_bundle({"X向振动(mm/s)": (9.0, "mm/s")}),
            catalog=cat,
            approvals=approvals,
        )
        self.assertEqual(a["formal_alert_count"], 0)
        refs = [r for r in a["reference_comparisons"] if r["rule_id"] == rule["rule_id"]]
        self.assertTrue(refs)
        self.assertEqual(refs[0]["evaluation_status"], "indeterminate")


if __name__ == "__main__":
    unittest.main()
