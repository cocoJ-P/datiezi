"""L4 Reasoning v1 单元与校验测试。"""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from l4.reasoning.input_adapter import build_l4_reasoning_input
from l4.reasoning.jobs import run_l4_reasoning
from l4.reasoning.orchestrator import build_deterministic_assessment, run_llm_tool_loop
from l4.reasoning.path_inspector import inspect_causal_path
from l4.reasoning.store import L4ReasoningStore
from l4.reasoning.tools import ToolContext, execute_tool
from l4.reasoning.validator import validate_causal_reasoning_assessment
from l4.store import L4Store


def _fake_core_assessment(record_id: str = "REC-T") -> dict:
    return {
        "schema": "RootCauseAssessment",
        "l4_run_id": "L4R-test",
        "analysis_run_id": "AR-test",
        "record_id": record_id,
        "input_fingerprint": "fp1",
        "causal_knowledge_version": "l4_causal_knowledge_v1_2",
        "applicability_version": "l4_applicability_v1_2",
        "rules_version": "l4_evidence_match_v1_2",
        "root_cause_confirmation": "unconfirmed",
        "equipment_type": "main_mill_motor",
        "equipment_id": "MM-01",
        "root_cause_candidates": [
            {
                "candidate_id": "RC-bearing",
                "cause_name": "滚动接触疲劳累积",
                "causal_path_id": "CP-bearing",
                "causal_knowledge_id": "CK-bearing-contact-fatigue",
                "related_fault_type": "轴承磨损",
                "applicability_status": "applicable",
                "same_layer_as_resulting_fault": True,
                "supporting_evidence_ids": [],
                "fault_manifestation_evidence_ids": ["mech:REC-T:bearing:BPFO幅值(g)"],
                "contradicting_evidence_ids": [],
                "missing_evidence": ["missing_feature:bpfo_amp"],
            },
            {
                "candidate_id": "RC-lube",
                "cause_name": "润滑量/质偏离设计（缺油、油品失效等）",
                "causal_path_id": "CP-lube",
                "causal_knowledge_id": "CK-lube-film-loss-bearing",
                "related_fault_type": "轴承磨损",
                "applicability_status": "applicable",
                "same_layer_as_resulting_fault": False,
                "supporting_evidence_ids": ["mech:REC-T:lubrication:驱动端轴承温度(°C)"],
                "fault_manifestation_evidence_ids": [],
                "contradicting_evidence_ids": [],
                "missing_evidence": [],
            },
        ],
        "causal_paths": [
            {
                "causal_path_id": "CP-bearing",
                "causal_knowledge_id": "CK-bearing-contact-fatigue",
                "cause": "滚动接触疲劳累积",
                "mechanism": "点蚀剥落",
                "resulting_fault": "轴承磨损",
                "related_fault_type": "轴承磨损",
                "evidence_matching": {"same_layer_as_resulting_fault": True},
                "source_document_locations": [{"document": "KB-02"}],
                "supporting_evidence_ids": [],
                "fault_manifestation_evidence_ids": ["mech:REC-T:bearing:BPFO幅值(g)"],
                "link_states": {"cause": "hypothesized", "mechanism": "knowledge_supported", "fault_to_evidence": "observed"},
            },
            {
                "causal_path_id": "CP-lube",
                "causal_knowledge_id": "CK-lube-film-loss-bearing",
                "cause": "润滑量/质偏离设计（缺油、油品失效等）",
                "mechanism": "油膜失效",
                "resulting_fault": "轴承磨损",
                "related_fault_type": "轴承磨损",
                "evidence_matching": {"same_layer_as_resulting_fault": False},
                "source_document_locations": [{"document": "KB-02"}],
                "supporting_evidence_ids": ["mech:REC-T:lubrication:驱动端轴承温度(°C)"],
                "fault_manifestation_evidence_ids": [],
                "link_states": {"cause": "hypothesized", "mechanism": "knowledge_supported", "fault_to_evidence": "unknown"},
            },
        ],
        "excluded_faults": [
            {
                "fault_type": "转子不平衡",
                "eligibility": "excluded_contradicted",
                "exclusion_reason": "CONTRADICTED",
                "contradicting_evidence_ids": ["mech:REC-T:unbalance_1x:1X转频幅值(mm/s)"],
            }
        ],
        "impact_assessment": {
            "propagation_status": "unknown",
            "upstream_dependencies": [{"graph_label": "稀油站"}],
            "downstream_connections": [{"graph_label": "齿轮箱"}],
            "potential_impact": {"reference_topology_only": True},
            "equipment_mapping": {"mapping_level": "equipment_type"},
            "impact_severity_level": None,
        },
        "limitations": ["测试夹具"],
    }


def _reasoning_input_from_core(core: dict) -> dict:
    return {
        "schema": "L4ReasoningInput",
        "source_l4_run_id": "L4R-test",
        "source_analysis_run_id": "AR-test",
        "record_id": core["record_id"],
        "equipment_type": core["equipment_type"],
        "equipment_id": core["equipment_id"],
        "input_fingerprint": core["input_fingerprint"],
        "causal_knowledge_version": core["causal_knowledge_version"],
        "applicability_version": core["applicability_version"],
        "evidence_match_version": core["rules_version"],
        "knowledge_snapshot_id": None,
        "root_cause_assessment": core,
        "evidence_bundle": {
            "evidence_items": [
                {
                    "evidence_id": "mech:REC-T:bearing:BPFO幅值(g)",
                    "analyzer_id": "mechanical_feature",
                    "feature_name": "BPFO幅值(g)",
                    "observed_value": 0.4,
                    "assessment": "SUPPORT",
                },
                {
                    "evidence_id": "mech:REC-T:lubrication:驱动端轴承温度(°C)",
                    "analyzer_id": "mechanical_feature",
                    "feature_name": "驱动端轴承温度(°C)",
                    "observed_value": 88,
                    "assessment": "SUPPORT",
                },
                {
                    "evidence_id": "mech:REC-T:unbalance_1x:1X转频幅值(mm/s)",
                    "analyzer_id": "mechanical_feature",
                    "feature_name": "1X转频幅值(mm/s)",
                    "observed_value": 0,
                    "assessment": "CONTRADICT",
                    "related_fault_mode": "转子不平衡",
                },
            ]
        },
        "allowed_evidence_ids": [
            "mech:REC-T:bearing:BPFO幅值(g)",
            "mech:REC-T:lubrication:驱动端轴承温度(°C)",
            "mech:REC-T:unbalance_1x:1X转频幅值(mm/s)",
        ],
        "allowed_causal_knowledge_ids": [
            "CK-bearing-contact-fatigue",
            "CK-lube-film-loss-bearing",
        ],
        "allowed_candidate_ids": ["RC-bearing", "RC-lube"],
        "excluded_fault_types": ["转子不平衡"],
        "excluded_faults": core["excluded_faults"],
        "constraints": {
            "root_cause_confirmation_fixed": "unconfirmed",
            "may_not_restore_excluded": True,
        },
    }


class PathInspectorTests(unittest.TestCase):
    def test_same_layer_not_observed_cause(self):
        core = _fake_core_assessment()
        out = inspect_causal_path(core, causal_path_id="CP-bearing")
        self.assertTrue(out["ok"])
        steps = {s["step"]: s for s in out["reviews"][0]["steps"]}
        self.assertTrue(out["reviews"][0]["same_layer_as_resulting_fault"])
        self.assertIn(steps["cause"]["status"], {"knowledge_backed", "unresolved"})
        self.assertNotEqual(steps["cause"]["status"], "observed")


class DeterministicReasoningTests(unittest.TestCase):
    def test_bearing_lube_competition(self):
        inp = _reasoning_input_from_core(_fake_core_assessment())
        assessment = build_deterministic_assessment(inp, reasoning_run_id="L4RR-t")
        self.assertEqual(assessment["root_cause_confirmation"], "unconfirmed")
        by_id = {r["candidate_id"]: r for r in assessment["candidate_reviews"]}
        self.assertEqual(by_id["RC-bearing"]["validity_status"], "mechanism_only")
        self.assertIn(by_id["RC-lube"]["validity_status"], {
            "insufficient_upstream_evidence",
            "indistinguishable",
        })
        self.assertTrue(by_id["RC-lube"]["alternative_explanations"])
        self.assertIsNone(assessment["impact_interpretation"]["impact_degree"])
        self.assertEqual(assessment["impact_interpretation"]["propagation_status"], "unknown")

    def test_dry_run_tools(self):
        inp = _reasoning_input_from_core(_fake_core_assessment())
        result = run_llm_tool_loop(inp, reasoning_run_id="L4RR-dry", dry_run=True)
        self.assertEqual(result["mode"], "dry_run")
        tools = {h["tool"] for h in result["tool_history"]}
        self.assertIn("get_l4_candidates", tools)
        self.assertIn("inspect_causal_path", tools)
        self.assertEqual(result["assessment"]["root_cause_confirmation"], "unconfirmed")


class ValidatorRejectionTests(unittest.TestCase):
    def test_fake_evidence_and_restore_rejected(self):
        inp = _reasoning_input_from_core(_fake_core_assessment())
        assessment = build_deterministic_assessment(inp, reasoning_run_id="L4RR-v")
        bad = copy.deepcopy(assessment)
        bad["candidate_reviews"][0]["upstream_cause_evidence"] = ["fake-ev"]
        bad["root_cause_confirmation"] = "confirmed"
        bad["impact_interpretation"]["impact_degree"] = "high"
        bad["impact_interpretation"]["propagation_status"] = "confirmed"
        # restore excluded as active candidate id not in allowed
        bad["candidate_reviews"].append(
            {
                "candidate_id": "RC-restored",
                "cause_name": "假恢复",
                "validity_status": "consistent_with_observations",
                "source_causal_knowledge_ids": ["CK-nope"],
                "upstream_cause_evidence": [],
                "fault_manifestation_evidence": [],
                "contradicting_evidence": [],
            }
        )
        result = validate_causal_reasoning_assessment(bad, reasoning_input=inp)
        self.assertFalse(result["ok"])
        joined = " ".join(result["errors"])
        self.assertIn("unknown_evidence_id", joined)
        self.assertIn("root_cause_confirmation_must_remain_unconfirmed", joined)
        self.assertIn("impact_degree_must_be_null", joined)
        self.assertIn("invalid_propagation_claim", joined)
        self.assertIn("unknown_candidate_id", joined)

    def test_excluded_auto_restore_action_rejected(self):
        inp = _reasoning_input_from_core(_fake_core_assessment())
        assessment = build_deterministic_assessment(inp, reasoning_run_id="L4RR-v2")
        assessment["excluded_review_recommendations"] = [
            {"fault_type": "转子不平衡", "action": "restore_active"}
        ]
        result = validate_causal_reasoning_assessment(assessment, reasoning_input=inp)
        self.assertFalse(result["ok"])
        self.assertTrue(any("excluded_auto_restore" in e for e in result["errors"]))


class ToolSafetyTests(unittest.TestCase):
    def test_unknown_tool_forbidden(self):
        ctx = ToolContext(_reasoning_input_from_core(_fake_core_assessment()))
        result = execute_tool("run_shell", {}, ctx)
        self.assertFalse(result["ok"])

    def test_tool_result_cache_same_args(self):
        from l4.reasoning.tools import budget_used

        ctx = ToolContext(_reasoning_input_from_core(_fake_core_assessment()))
        a = execute_tool("get_l4_candidates", {}, ctx)
        b = execute_tool("get_l4_candidates", {}, ctx)
        self.assertTrue(a.get("ok"))
        self.assertTrue(b.get("cached"))
        self.assertEqual(ctx.unique_exec_count, 1)
        self.assertEqual(ctx.cache_hits, 1)
        self.assertEqual(budget_used(ctx), 1)
        self.assertEqual(ctx.call_count, 2)


class JsonFailureClassificationTests(unittest.TestCase):
    def test_classify_empty_vs_prose_vs_malformed(self):
        from l4.reasoning.orchestrator import _classify_json_failure

        self.assertEqual(_classify_json_failure("", finish_reason="stop"), "empty_final_content")
        self.assertEqual(_classify_json_failure("请继续调用工具", finish_reason="stop"), "non_json_prose")
        self.assertEqual(_classify_json_failure('{"a":', finish_reason="stop"), "malformed_json")
        self.assertEqual(_classify_json_failure('{"a":1}', finish_reason="length"), "output_truncated")

    def test_normalize_evidence_objects_to_ids(self):
        from l4.reasoning.orchestrator import _normalize_llm_assessment

        obj = _normalize_llm_assessment(
            {
                "candidate_reviews": [
                    {
                        "candidate_id": "RC-bearing",
                        "upstream_cause_evidence": [
                            {"evidence_id": "mech:REC-T:lubrication:驱动端轴承温度(°C)", "note": "温升"}
                        ],
                        "fault_manifestation_evidence": ["mech:REC-T:bearing:BPFO幅值(g)"],
                        "source_causal_knowledge_ids": ["CK-lube-film-loss-bearing"],
                    }
                ],
                "supporting_evidence_ids": [
                    {"evidence_id": "mech:REC-T:lubrication:驱动端轴承温度(°C)", "note": "x"}
                ],
            }
        )
        self.assertEqual(
            obj["candidate_reviews"][0]["upstream_cause_evidence"],
            ["mech:REC-T:lubrication:驱动端轴承温度(°C)"],
        )
        self.assertEqual(
            obj["supporting_evidence_ids"],
            ["mech:REC-T:lubrication:驱动端轴承温度(°C)"],
        )


class GearAndPumpFixtures(unittest.TestCase):
    def test_gear_same_layer_and_associated(self):
        core = _fake_core_assessment()
        core["root_cause_candidates"] = [
            {
                "candidate_id": "RC-gear",
                "cause_name": "齿轮副啮合状态异常",
                "causal_path_id": "CP-gear",
                "causal_knowledge_id": "CK-gear-mesh-abnormality",
                "related_fault_type": "齿轮异常",
                "applicability_status": "associated_unconfirmed",
                "component_binding": "associated_drive_train",
                "same_layer_as_resulting_fault": True,
                "supporting_evidence_ids": [],
                "fault_manifestation_evidence_ids": ["mech:REC-T:gear_mesh:啮合频率幅值(g)"],
                "contradicting_evidence_ids": [],
                "missing_evidence": [],
            }
        ]
        core["causal_paths"] = [
            {
                "causal_path_id": "CP-gear",
                "causal_knowledge_id": "CK-gear-mesh-abnormality",
                "cause": "齿轮副啮合状态异常",
                "mechanism": "啮合能量上升",
                "resulting_fault": "齿轮异常",
                "evidence_matching": {"same_layer_as_resulting_fault": True},
                "source_document_locations": [{"document": "KB-02"}],
                "fault_manifestation_evidence_ids": ["mech:REC-T:gear_mesh:啮合频率幅值(g)"],
                "link_states": {},
            }
        ]
        inp = _reasoning_input_from_core(core)
        inp["allowed_candidate_ids"] = ["RC-gear"]
        inp["allowed_causal_knowledge_ids"] = ["CK-gear-mesh-abnormality"]
        inp["allowed_evidence_ids"] = ["mech:REC-T:gear_mesh:啮合频率幅值(g)"]
        assessment = build_deterministic_assessment(inp, reasoning_run_id="L4RR-g")
        rev = assessment["candidate_reviews"][0]
        self.assertEqual(rev["validity_status"], "mechanism_only")
        self.assertEqual(assessment["root_cause_confirmation"], "unconfirmed")


@unittest.skipUnless(
    Path(__file__).resolve().parents[3].joinpath("data", "l4_analysis.sqlite3").exists()
    or Path(__file__).resolve().parents[3].joinpath("data", "l3_analysis.sqlite3").exists(),
    "需要本机 L4/L3 sqlite",
)
class LiveDryRunTests(unittest.TestCase):
    def test_dry_run_on_existing_l4_if_any(self):
        from l4.store import L4_STORE

        # 找一条已有 L4 run
        try:
            # 使用已知回归 run
            run = L4_STORE.get("L4R-6819e31506c8")
        except KeyError:
            self.skipTest("无可用 L4 run")
        page = L4_STORE.list_records(run["l4_run_id"], offset=0, limit=1)
        if not page.get("items"):
            self.skipTest("L4 run 无记录")
        rid = page["items"][0]["record_id"]
        result = run_l4_reasoning(run["l4_run_id"], record_ids=[rid], force_new=True, dry_run=True, max_records=1)
        self.assertEqual(result["reasoning_run"]["status"], "completed")
        from l4.reasoning.store import L4_REASONING_STORE

        item = L4_REASONING_STORE.get_record(result["reasoning_run"]["reasoning_run_id"], rid)
        self.assertEqual(item["assessment"].get("root_cause_confirmation"), "unconfirmed")
        self.assertIn("inspect_causal_path", {h.get("tool") for h in item.get("tool_history") or []})


if __name__ == "__main__":
    unittest.main()
