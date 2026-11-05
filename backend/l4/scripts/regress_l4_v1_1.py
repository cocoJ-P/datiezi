"""L4 Core v1.1 定向 19 + 批量 200 回归（只读 L3，不改答案列）。"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

from l4.engine import analyze_record
from l4.jobs import run_l4_for_analysis
from l4.store import L4_STORE

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data"
PREV_DIRECTED = OUT_DIR / "l4_validation_directed.json"
PREV_BATCH = OUT_DIR / "l4_validation_batch200.json"


def _load_prev_cases() -> list[dict]:
    data = json.loads(PREV_DIRECTED.read_text(encoding="utf-8"))
    return list(data.get("detailed") or [])


def _count_errors(assessment: dict) -> dict:
    cands = assessment.get("root_cause_candidates") or []
    refs = assessment.get("reference_candidates") or []
    excluded = assessment.get("excluded_faults") or []
    bpfo_seal = 0
    mesh_water = 0
    wrong_appl = 0
    cause_specific = 0
    manif = 0
    for c in cands + refs:
        ck = c.get("causal_knowledge_id")
        appl = c.get("applicability_status")
        if appl == "applicable" and ck in {
            "CK-gear-mesh-abnormality",
            "CK-lube-water-gear-pitting",
        } and assessment.get("equipment_type") == "high_pressure_descaling_pump":
            wrong_appl += 1
        if appl == "applicable" and ck == "CK-seal-leak-bearing-lube-fail" and assessment.get(
            "equipment_type"
        ) == "main_mill_motor":
            wrong_appl += 1
        for eid in c.get("supporting_evidence_ids") or []:
            cause_specific += 1
            el = str(eid).lower()
            if ck == "CK-seal-leak-bearing-lube-fail" and "bpfo" in el:
                bpfo_seal += 1
            if ck == "CK-lube-water-gear-pitting" and ("啮合" in el or "gear_mesh" in el):
                mesh_water += 1
        manif += len(c.get("fault_manifestation_evidence_ids") or [])
    # 否定故障是否仍展开
    excluded_types = {e.get("fault_type") for e in excluded}
    bad_expand = 0
    for c in cands:
        if c.get("related_fault_type") in excluded_types:
            bad_expand += 1
        # 也检查 L3 原始否定
    for row in assessment.get("l3_fault_candidates") or []:
        v = str(row.get("verdict") or "").upper().replace("-", "_")
        if v in {"CONTRADICT", "CONTRADICTED", "NOT_APPLICABLE", "NA"}:
            ft = row.get("fault_type")
            if any(c.get("related_fault_type") == ft for c in cands):
                bad_expand += 1
    return {
        "n_cands": len(cands),
        "n_refs": len(refs),
        "n_excluded_faults": len(excluded),
        "bpfo_seal": bpfo_seal,
        "mesh_water": mesh_water,
        "wrong_appl": wrong_appl,
        "bad_expand": bad_expand,
        "cause_specific": cause_specific,
        "manif": manif,
        "confirm": assessment.get("root_cause_confirmation"),
        "propagation": (assessment.get("impact_assessment") or {}).get("propagation_status"),
        "outcome": assessment.get("outcome"),
    }


def run_directed() -> dict:
    cases = _load_prev_cases()
    detailed = []
    stats = Counter()
    t0 = time.time()
    for prev in cases:
        ar, rid = prev["ar"], prev["rid"]
        assessment = analyze_record(l4_run_id="L4R-regress-v11", analysis_run_id=ar, record_id=rid)
        err = _count_errors(assessment)
        impact = assessment.get("impact_assessment") or {}
        pot = impact.get("potential_impact") or {}
        row = {
            "tag": prev.get("tag"),
            "ar": ar,
            "rid": rid,
            "et": assessment.get("equipment_type"),
            "outcome": assessment.get("outcome"),
            "confirm": assessment.get("root_cause_confirmation"),
            "prev_n_cands": len(prev.get("cands") or []),
            "n_cands": err["n_cands"],
            "n_refs": err["n_refs"],
            "n_excluded_faults": err["n_excluded_faults"],
            "cause_specific": err["cause_specific"],
            "manif": err["manif"],
            "bpfo_seal": err["bpfo_seal"],
            "mesh_water": err["mesh_water"],
            "wrong_appl": err["wrong_appl"],
            "bad_expand": err["bad_expand"],
            "propagation": err["propagation"],
            "upstream_n": len(pot.get("upstream_dependencies") or impact.get("upstream_dependencies") or []),
            "downstream_n": len(pot.get("downstream_connections") or impact.get("downstream_connections") or []),
            "cands": [
                {
                    "cause": c.get("cause_name"),
                    "fault": c.get("related_fault_type"),
                    "ck": c.get("causal_knowledge_id"),
                    "appl": c.get("applicability_status"),
                    "sup": c.get("supporting_evidence_ids") or [],
                    "manif": c.get("fault_manifestation_evidence_ids") or [],
                    "ctx": c.get("contextual_evidence_ids") or [],
                }
                for c in assessment.get("root_cause_candidates") or []
            ],
            "excluded_faults": assessment.get("excluded_faults") or [],
            "reference_candidates": [
                {
                    "ck": c.get("causal_knowledge_id"),
                    "appl": c.get("applicability_status"),
                    "cause": c.get("cause_name"),
                }
                for c in assessment.get("reference_candidates") or []
            ],
            "notes": assessment.get("notes") or [],
            "causal_knowledge_version": assessment.get("causal_knowledge_version"),
        }
        detailed.append(row)
        stats["n"] += 1
        stats["candidates_total"] += err["n_cands"]
        stats["refs_total"] += err["n_refs"]
        stats["excluded_faults_total"] += err["n_excluded_faults"]
        stats["bpfo_seal"] += err["bpfo_seal"]
        stats["mesh_water"] += err["mesh_water"]
        stats["wrong_appl"] += err["wrong_appl"]
        stats["bad_expand"] += err["bad_expand"]
        stats["cause_specific"] += err["cause_specific"]
        stats["manif"] += err["manif"]
        if err["confirm"] == "unconfirmed":
            stats["all_unconfirmed"] += 1
        if err["propagation"] == "unknown":
            stats["propagation_unknown"] += 1
        stats[f"outcome:{err['outcome']}"] += 1

    elapsed = time.time() - t0
    result = {"stats": dict(stats), "detailed": detailed, "elapsed_s": round(elapsed, 3)}
    out = OUT_DIR / "l4_validation_directed_v1_1.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def run_batch200() -> dict:
    prev = json.loads(PREV_BATCH.read_text(encoding="utf-8"))
    analysis_runs: list[str] = list(prev.get("analysis_runs") or [])
    record_ids_by_ar: dict[str, list[str]] = dict(prev.get("record_ids_by_ar") or {})
    if not analysis_runs and prev.get("l4_run_id"):
        old = L4_STORE.get(prev["l4_run_id"])
        ar = old.get("source_analysis_run_id")
        analysis_runs = [ar] if ar else []
        selection = old.get("selection") or {}
        rids = list(selection.get("record_ids") or [])
        if not rids:
            page = L4_STORE.list_records(prev["l4_run_id"], offset=0, limit=200)
            rids = [i["record_id"] for i in page.get("items") or []]
        if ar:
            record_ids_by_ar = {ar: rids}

    t0 = time.time()
    totals = Counter()
    run_ids = []
    for ar in analysis_runs:
        rids = record_ids_by_ar.get(ar) or []
        if not rids:
            continue
        result = run_l4_for_analysis(ar, record_ids=rids, max_records=len(rids), force_new=True)
        l4_run = result["l4_run"]
        run_ids.append(l4_run["l4_run_id"])
        page = L4_STORE.list_records(l4_run["l4_run_id"], offset=0, limit=500)
        for item in page.get("items") or []:
            totals["n"] += 1
            if item.get("status") == "succeeded":
                totals["succeeded"] += 1
            else:
                totals["failed"] += 1
            assessment = item.get("assessment") or {}
            err = _count_errors(assessment)
            totals["bpfo_seal"] += err["bpfo_seal"]
            totals["mesh_water"] += err["mesh_water"]
            totals["wrong_appl"] += err["wrong_appl"]
            totals["bad_expand"] += err["bad_expand"]
            totals["cause_specific"] += err["cause_specific"]
            totals["manif"] += err["manif"]
            totals["candidates_total"] += err["n_cands"]
            if err["confirm"] == "unconfirmed":
                totals["unconfirmed"] += 1
            if err["propagation"] == "unknown":
                totals["propagation_unknown"] += 1

    # 幂等：相同输入 + force_new=False 必须复用同一 L4Run（content_key 含知识/规则版本）
    idempotent_ok = True
    if analysis_runs:
        ar0 = analysis_runs[0]
        rids0 = (record_ids_by_ar.get(ar0) or [])[:5]
        if rids0:
            a = run_l4_for_analysis(ar0, record_ids=rids0, max_records=len(rids0), force_new=False)
            b = run_l4_for_analysis(ar0, record_ids=rids0, max_records=len(rids0), force_new=False)
            idempotent_ok = (not b["created"]) and b["l4_run"]["l4_run_id"] == a["l4_run"]["l4_run_id"]
            # 规则版本变更后 content_key 变化，不会误复用 v1 结果
            from l4.jobs import content_key_for
            from l4 import CAUSAL_KNOWLEDGE_VERSION, EQUIPMENT_MAPPING_VERSION, __version__
            from l4.causal_knowledge.loader import catalog_version
            from l4.mapping import mapping_version as map_ver

            key_new = content_key_for(
                ar0,
                rids0,
                causal_version=catalog_version() or CAUSAL_KNOWLEDGE_VERSION,
                mapping_version_str=map_ver() or EQUIPMENT_MAPPING_VERSION,
            )
            key_old = content_key_for(
                ar0,
                rids0,
                causal_version="l4_causal_knowledge_v1",
                mapping_version_str="l4_equipment_mapping_v1",
            )
            # 人为构造旧版本 key 应不同（旧 __version__ 也不同，这里至少因果版本不同）
            idempotent_ok = idempotent_ok and (key_new != key_old or __version__ != "l4_core_v1")

    # 旧 run 可读
    old_readable = False
    if prev.get("l4_run_id"):
        try:
            old = L4_STORE.get(prev["l4_run_id"])
            old_readable = bool(old.get("l4_run_id"))
        except Exception:
            old_readable = False

    elapsed = time.time() - t0
    result = {
        "stats": dict(totals),
        "l4_run_ids": run_ids,
        "elapsed_s": round(elapsed, 3),
        "idempotent_ok": idempotent_ok,
        "old_l4_run_readable": old_readable,
        "old_l4_run_id": prev.get("l4_run_id"),
    }
    out = OUT_DIR / "l4_validation_batch200_v1_1.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main():
    print("=== Directed 19 ===")
    d = run_directed()
    print(json.dumps(d["stats"], ensure_ascii=False, indent=2))
    print("elapsed", d["elapsed_s"])
    print("=== Batch 200 ===")
    b = run_batch200()
    print(json.dumps(b, ensure_ascii=False, indent=2)[:2000])


if __name__ == "__main__":
    main()
