"""Reasoning Trace v1 结构与可追溯性测试。"""

from __future__ import annotations

import json
from copy import deepcopy

from l3.core.registry import AnalyzerRegistry
from l3.evidence.builder import build_evidence_bundle
from l3.evidence.reasoning_trace import (
    NODE_CONCLUSION,
    NODE_EVIDENCE,
    NODE_OBSERVATION,
    NODE_RULE,
    TRACE_VERSION,
    build_reasoning_trace,
    validate_trace,
)
from l3.tests.conftest import make_analysis_context


def test_trace_nodes_link_to_real_observations_and_evidence():
    ctx = make_analysis_context()
    results, review = AnalyzerRegistry().run_all(ctx)
    trace = build_reasoning_trace(ctx, results, review)
    validate_trace(trace)
    assert trace["trace_version"] == TRACE_VERSION
    assert trace["build_status"] == "ok"

    by_id = {n["node_id"]: n for n in trace["nodes"]}
    obs = [n for n in trace["nodes"] if n["node_type"] == NODE_OBSERVATION]
    assert any(n["feature_name"] == "BPFO幅值(g)" and n["value"] == 0.8 for n in obs)

    evidence_ids = {e["evidence_id"] for e in review["evidence_items"]}
    ev_nodes = [n for n in trace["nodes"] if n["node_type"] == NODE_EVIDENCE]
    assert ev_nodes
    for node in ev_nodes:
        assert node["evidence_id"] in evidence_ids

    for cid in trace["conclusion_node_ids"]:
        node = by_id[cid]
        assert node["node_type"] == NODE_CONCLUSION
        for eid in node.get("evidence_ids") or []:
            assert any(n.get("evidence_id") == eid for n in ev_nodes)


def test_no_fake_rule_when_thresholds_unused_on_pump_missing_feature():
    ctx = make_analysis_context(threshold_rules=[], equipment_type="unknown_device_no_rules")
    # 清掉限值后不应出现 RULE 节点
    results, review = AnalyzerRegistry().run_all(ctx)
    trace = build_reasoning_trace(ctx, results, review)
    assert not any(n["node_type"] == NODE_RULE for n in trace["nodes"])


def test_bundle_persists_trace_and_is_deterministic():
    ctx = make_analysis_context()
    results, review = AnalyzerRegistry().run_all(ctx)
    b1 = build_evidence_bundle(ctx, results, review)
    b2 = build_evidence_bundle(ctx, deepcopy(results), deepcopy(review))
    assert "reasoning_trace" in b1
    assert b1["reasoning_trace"]["build_status"] == "ok"
    # 稳定节点集合
    ids1 = sorted(n["node_id"] for n in b1["reasoning_trace"]["nodes"])
    ids2 = sorted(n["node_id"] for n in b2["reasoning_trace"]["nodes"])
    assert ids1 == ids2
    # EvidenceItem 与 Trace 引用一致
    item_ids = {e["evidence_id"] for e in b1["evidence_items"]}
    for node in b1["reasoning_trace"]["nodes"]:
        if node["node_type"] == NODE_EVIDENCE:
            assert node["evidence_id"] in item_ids
    # 可序列化重载
    raw = json.dumps(b1, ensure_ascii=False)
    loaded = json.loads(raw)
    validate_trace(loaded["reasoning_trace"])


def test_no_fft_or_harmonic_nodes_without_spectrum_array():
    ctx = make_analysis_context()
    results, review = AnalyzerRegistry().run_all(ctx)
    trace = build_reasoning_trace(ctx, results, review)
    blob = json.dumps(trace, ensure_ascii=False)
    assert "FFT" not in blob
    assert "谐波阶次验证" not in blob
    assert "l2_spectral_reference_is_reference_only_not_verified_evidence" in trace["limitations"]


def test_history_reference_not_future():
    ctx = make_analysis_context()
    results, review = AnalyzerRegistry().run_all(ctx)
    trace = build_reasoning_trace(ctx, results, review)
    for node in trace["nodes"]:
        if node["node_type"] != NODE_OBSERVATION:
            continue
        src = node.get("source_reference") or {}
        if src.get("kind") == "historical_observation_reference":
            assert src.get("not_future") is True
            assert src.get("window") == "records_before_current_collection_time"
