"""L4 Core v1.2：结构化匹配、适用性降级、排除追溯与扰动测试。"""

from __future__ import annotations

import copy
import unittest

from l4.applicability import evaluate_applicability
from l4.candidate_builder import build_root_cause_candidates
from l4.causal_knowledge.loader import catalog_version, rules_version
from l4.engine import analyze_record
from l4.evidence_metadata import adapt_evidence_item, parse_evidence_id
from l4.fault_eligibility import evaluate_fault_eligibility, filter_faults_for_root_cause
from l4.path_validator import validate_candidates
from l4.schema import ROOT_CAUSE_CONFIRMATION


def _ctx_base(**kwargs):
    base = {
        "record_id": "REC-AUDIT",
        "source_analysis_run_id": "AR-AUDIT",
        "source_reasoning_run_id": None,
        "equipment_type": "main_mill_motor",
        "equipment_id": "MM-AUDIT",
        "l3_fault_candidates": [
            {"fault_type": "轴承磨损", "verdict": "SUPPORTED", "source": "l3_fault_type_review"}
        ],
        "support_evidence": [],
        "contradict_evidence": [],
        "evidence_references": [],
        "evidence_bundle": {"evidence_items": [], "support_evidence": [], "contradict_evidence": []},
    }
    base.update(kwargs)
    return base


class VersionTests(unittest.TestCase):
    def test_versions(self):
        self.assertEqual(catalog_version(), "l4_causal_knowledge_v1_2")
        self.assertEqual(rules_version(), "l4_evidence_match_v1_2")


class StructuredMetadataTests(unittest.TestCase):
    def test_mech_prefix_alone_does_not_resolve(self):
        item = {
            "evidence_id": "mech:spoof:lubrication:bearing:not-a-real-feature",
            "analyzer_id": "mechanical_feature",
            "feature_name": "随机噪声",
            "observed_value": 1.0,
            "assessment": "SUPPORT",
        }
        meta = adapt_evidence_item(item)
        self.assertEqual(meta["metadata_status"], "unresolved")
        self.assertIsNone(meta["canonical_feature_id"])

    def test_parse_rejects_unknown_group(self):
        parsed = parse_evidence_id("mech:R:not_a_group:BPFO幅值(g)")
        self.assertFalse(parsed["parse_ok"])


class EvidenceIdNoiseTests(unittest.TestCase):
    def test_irrelevant_id_text_does_not_change_role(self):
        real = {
            "evidence_id": "mech:REC-AUDIT:bearing:BPFO幅值(g)",
            "analyzer_id": "mechanical_feature",
            "feature_name": "BPFO幅值(g)",
            "observed_value": 0.5,
            "assessment": "SUPPORT",
        }
        noisy = dict(real)
        noisy["evidence_id"] = "mech:REC-AUDIT:bearing:BPFO幅值(g)|noise-lubrication-bearing-mech"
        # 非法格式 → unresolved → contextual，不得 cause_specific
        ctx = _ctx_base(
            equipment_type="high_pressure_descaling_pump",
            l3_fault_candidates=[
                {"fault_type": "轴承磨损", "verdict": "SUPPORTED", "source": "l3_fault_type_review"}
            ],
            evidence_bundle={"evidence_items": [noisy]},
            support_evidence=[noisy],
        )
        built = build_root_cause_candidates(ctx)
        seal = [c for c in built["root_cause_candidates"] if c["causal_knowledge_id"] == "CK-seal-leak-bearing-lube-fail"]
        validated = validate_candidates(ctx, candidates=seal, paths=built["causal_paths"])
        for c in validated["root_cause_candidates"]:
            self.assertNotIn(noisy["evidence_id"], c.get("supporting_evidence_ids") or [])

        # 合法 ID + 同 feature：分类稳定（manif for seal）
        ctx2 = _ctx_base(
            equipment_type="high_pressure_descaling_pump",
            l3_fault_candidates=[
                {"fault_type": "轴承磨损", "verdict": "SUPPORTED", "source": "l3_fault_type_review"}
            ],
            evidence_bundle={"evidence_items": [real]},
            support_evidence=[real],
        )
        built2 = build_root_cause_candidates(ctx2)
        seal2 = [c for c in built2["root_cause_candidates"] if c["causal_knowledge_id"] == "CK-seal-leak-bearing-lube-fail"]
        v2 = validate_candidates(ctx2, candidates=seal2, paths=built2["causal_paths"])
        for c in v2["root_cause_candidates"]:
            self.assertEqual(c.get("supporting_evidence_ids") or [], [])
            self.assertIn(real["evidence_id"], c.get("fault_manifestation_evidence_ids") or [])


class StructuredFeatureChangeTests(unittest.TestCase):
    def test_only_dependent_path_changes(self):
        temp = {
            "evidence_id": "mech:REC-AUDIT:lubrication:驱动端轴承温度(°C)",
            "analyzer_id": "mechanical_feature",
            "feature_name": "驱动端轴承温度(°C)",
            "observed_value": 85.0,
            "assessment": "SUPPORT",
        }
        bpfo = {
            "evidence_id": "mech:REC-AUDIT:bearing:BPFO幅值(g)",
            "analyzer_id": "mechanical_feature",
            "feature_name": "BPFO幅值(g)",
            "observed_value": 0.4,
            "assessment": "SUPPORT",
        }
        ctx = _ctx_base(
            evidence_bundle={"evidence_items": [temp, bpfo]},
            support_evidence=[temp, bpfo],
            l3_fault_candidates=[
                {"fault_type": "轴承磨损", "verdict": "SUPPORTED", "source": "l3_fault_type_review"},
                {"fault_type": "润滑故障", "verdict": "SUPPORTED", "source": "l3_fault_type_review"},
            ],
        )
        built = build_root_cause_candidates(ctx)
        validated = validate_candidates(
            ctx, candidates=built["root_cause_candidates"], paths=built["causal_paths"]
        )
        by_ck = {c["causal_knowledge_id"]: c for c in validated["root_cause_candidates"]}
        # 同层疲劳：BPFO 仅 manif
        self.assertEqual(by_ck["CK-bearing-contact-fatigue"].get("supporting_evidence_ids") or [], [])
        self.assertIn(bpfo["evidence_id"], by_ck["CK-bearing-contact-fatigue"].get("fault_manifestation_evidence_ids") or [])
        # 润滑：温度 cause_specific；BPFO 不应成为润滑 cause_specific
        self.assertIn(temp["evidence_id"], by_ck["CK-lube-film-loss-bearing"].get("supporting_evidence_ids") or [])
        self.assertNotIn(bpfo["evidence_id"], by_ck["CK-lube-film-loss-bearing"].get("supporting_evidence_ids") or [])

        # 去掉温度，仅 BPFO：润滑 cause_specific 消失
        ctx2 = copy.deepcopy(ctx)
        ctx2["evidence_bundle"]["evidence_items"] = [bpfo]
        ctx2["support_evidence"] = [bpfo]
        built2 = build_root_cause_candidates(ctx2)
        v2 = validate_candidates(ctx2, candidates=built2["root_cause_candidates"], paths=built2["causal_paths"])
        lube = next(c for c in v2["root_cause_candidates"] if c["causal_knowledge_id"] == "CK-lube-film-loss-bearing")
        self.assertEqual(lube.get("supporting_evidence_ids") or [], [])


class UnknownEquipmentTests(unittest.TestCase):
    def test_unknown_type_is_unresolved_not_not_applicable(self):
        entry = {
            "equipment_scope": ["main_mill_motor"],
            "required_components": ["轴承"],
            "not_applicable_equipment_types": [],
        }
        appl = evaluate_applicability(entry, equipment_type="unknown_widget_xyz")
        self.assertEqual(appl["applicability_status"], "unresolved")
        self.assertFalse(appl["expand_as_active_candidate"])
        self.assertNotEqual(appl["applicability_status"], "not_applicable")


class BodyVsAssociatedTests(unittest.TestCase):
    def test_gear_on_motor_associated_not_body_applicable(self):
        ctx = _ctx_base(
            l3_fault_candidates=[
                {"fault_type": "齿轮异常", "verdict": "SUPPORTED", "source": "l3_fault_type_review"}
            ],
            evidence_bundle={
                "evidence_items": [
                    {
                        "evidence_id": "mech:REC-AUDIT:gear_mesh:啮合频率幅值(g)",
                        "analyzer_id": "mechanical_feature",
                        "feature_name": "啮合频率幅值(g)",
                        "observed_value": 1.2,
                        "assessment": "SUPPORT",
                    }
                ]
            },
        )
        built = build_root_cause_candidates(ctx)
        gear = [c for c in built["root_cause_candidates"] if c["causal_knowledge_id"] == "CK-gear-mesh-abnormality"]
        self.assertTrue(gear)
        self.assertEqual(gear[0]["applicability_status"], "associated_unconfirmed")
        self.assertEqual(gear[0].get("component_binding"), "associated_drive_train")
        validated = validate_candidates(ctx, candidates=gear, paths=built["causal_paths"])
        # 同层：manif，非 cause_specific
        self.assertEqual(validated["root_cause_candidates"][0].get("supporting_evidence_ids") or [], [])
        self.assertTrue(validated["root_cause_candidates"][0].get("fault_manifestation_evidence_ids"))

    def test_gear_not_on_pump(self):
        ctx = _ctx_base(
            equipment_type="high_pressure_descaling_pump",
            l3_fault_candidates=[
                {"fault_type": "齿轮异常", "verdict": "SUPPORTED", "source": "l3_fault_type_review"}
            ],
        )
        built = build_root_cause_candidates(ctx)
        self.assertEqual(
            [c for c in built["root_cause_candidates"] if "gear" in c["causal_knowledge_id"]],
            [],
        )


class MissingCauseSpecificTests(unittest.TestCase):
    def test_remove_temp_removes_lube_cause_specific(self):
        items = [
            {
                "evidence_id": "mech:R:lubrication:驱动端轴承温度(°C)",
                "analyzer_id": "mechanical_feature",
                "feature_name": "驱动端轴承温度(°C)",
                "observed_value": 90,
                "assessment": "SUPPORT",
            }
        ]
        ctx = _ctx_base(
            l3_fault_candidates=[
                {"fault_type": "润滑故障", "verdict": "SUPPORTED", "source": "l3_fault_type_review"}
            ],
            evidence_bundle={"evidence_items": items},
            support_evidence=items,
        )
        built = build_root_cause_candidates(ctx)
        v = validate_candidates(ctx, candidates=built["root_cause_candidates"], paths=built["causal_paths"])
        self.assertTrue(any(c.get("supporting_evidence_ids") for c in v["root_cause_candidates"]))

        ctx2 = copy.deepcopy(ctx)
        ctx2["evidence_bundle"]["evidence_items"] = []
        ctx2["support_evidence"] = []
        built2 = build_root_cause_candidates(ctx2)
        v2 = validate_candidates(ctx2, candidates=built2["root_cause_candidates"], paths=built2["causal_paths"])
        for c in v2["root_cause_candidates"]:
            self.assertEqual(c.get("supporting_evidence_ids") or [], [])
            self.assertEqual(c["assessment_status"], ROOT_CAUSE_CONFIRMATION)


class ExclusionTraceTests(unittest.TestCase):
    def test_contradicted_keeps_evidence_ids(self):
        contra = {
            "evidence_id": "mech:R:unbalance_1x:1X转频幅值(mm/s)",
            "analyzer_id": "mechanical_feature",
            "feature_name": "1X转频幅值(mm/s)",
            "observed_value": 0.0,
            "assessment": "CONTRADICT",
            "related_fault_mode": "转子不平衡",
        }
        ctx = _ctx_base(
            l3_fault_candidates=[
                {
                    "fault_type": "转子不平衡",
                    "verdict": "CONTRADICTED",
                    "source": "l3_fault_type_review",
                    "contradict_count": 1,
                    "support_count": 0,
                }
            ],
            contradict_evidence=[contra],
            evidence_bundle={"evidence_items": [contra], "contradict_evidence": [contra]},
        )
        filtered = filter_faults_for_root_cause(ctx)
        self.assertEqual(filtered["excluded_count"], 1)
        ex = filtered["excluded_faults"][0]
        self.assertEqual(ex["eligibility"], "excluded_contradicted")
        self.assertIn(contra["evidence_id"], ex["contradicting_evidence_ids"])
        self.assertFalse(ex["evidence_citation_missing"])
        self.assertEqual(ex["l3_core_verdict"], "CONTRADICTED")
        self.assertIn("exclusion_reason", ex)

    def test_conflict_needs_review_and_missing_citation_flag(self):
        ctx = {
            "source_analysis_run_id": "AR-X",
            "l3_fault_candidates": [
                {"fault_type": "轴承磨损", "verdict": "SUPPORTED", "source": "l3_fault_type_review"}
            ],
            "reasoning_assessment": {
                "candidate_hypotheses": [{"fault_type": "轴承磨损", "assessment": "CONTRADICTED"}]
            },
            "evidence_bundle": {"evidence_items": []},
        }
        d = evaluate_fault_eligibility(ctx["l3_fault_candidates"][0], ctx)
        self.assertEqual(d["eligibility"], "excluded_core_reasoning_conflict")
        self.assertTrue(d["needs_review"])
        self.assertTrue(d["evidence_citation_missing"])


class NoiseFieldTests(unittest.TestCase):
    def test_non_business_fields_do_not_change_semantics(self):
        items = [
            {
                "evidence_id": "mech:R:bearing:BPFO幅值(g)",
                "analyzer_id": "mechanical_feature",
                "feature_name": "BPFO幅值(g)",
                "observed_value": 0.3,
                "assessment": "SUPPORT",
            }
        ]
        ctx = _ctx_base(evidence_bundle={"evidence_items": items}, support_evidence=items)
        a1 = analyze_record(l4_run_id="a", analysis_run_id="AR", record_id="R", ctx=ctx)
        ctx2 = copy.deepcopy(ctx)
        ctx2["limitations"] = ["noise"]
        ctx2["analysis_record_status"] = "whatever"
        a2 = analyze_record(l4_run_id="a", analysis_run_id="AR", record_id="R", ctx=ctx2)
        k = lambda a: [
            (c["causal_knowledge_id"], c.get("supporting_evidence_ids"), c.get("fault_manifestation_evidence_ids"))
            for c in a["root_cause_candidates"]
        ]
        self.assertEqual(k(a1), k(a2))


if __name__ == "__main__":
    unittest.main()
