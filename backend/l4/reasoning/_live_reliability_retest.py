"""Live retest for L4 Reasoning reliability (limited records)."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

# ensure backend on path
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from l4.reasoning.jobs import run_l4_reasoning
from l4.reasoning.store import L4_REASONING_STORE
from l4.store import L4_STORE

TARGET_ORIGINAL = [
    "REC-MA-20260406-00399",
    "REC-MA-20260314-00008",
    "REC-DP-20260314-00021",
    "REC-DP-20260314-00162",
]
# Extra records not specially tuned for JSON repair
TARGET_EXTRA = [
    "REC-MA-20260406-00392",
    "REC-MA-20260314-00011",
]


def _find_l4_run_for(record_id: str) -> tuple[str, dict] | None:
    # Prefer completed runs that contain the record
    runs = L4_STORE.list_runs(limit=50) if hasattr(L4_STORE, "list_runs") else None
    if runs is None:
        # fallback: scan known recent from sqlite
        import sqlite3
        from l4.database import default_db_path

        con = sqlite3.connect(default_db_path())
        con.row_factory = sqlite3.Row
        row = con.execute(
            """
            SELECT l4_run_id FROM l4_records
            WHERE record_id=? AND status='succeeded'
            ORDER BY rowid DESC LIMIT 1
            """,
            (record_id,),
        ).fetchone()
        con.close()
        if not row:
            return None
        l4_run_id = row["l4_run_id"]
        return l4_run_id, L4_STORE.get_record(l4_run_id, record_id)
    return None


def find_record(record_id: str) -> tuple[str, dict]:
    import sqlite3
    from l4.database import default_db_path

    con = sqlite3.connect(default_db_path())
    con.row_factory = sqlite3.Row
    row = con.execute(
        """
        SELECT l4_run_id, assessment_json FROM l4_records
        WHERE record_id=?
        ORDER BY rowid DESC LIMIT 1
        """,
        (record_id,),
    ).fetchone()
    con.close()
    if not row:
        raise RuntimeError(f"record not found in L4 store: {record_id}")
    assessment = json.loads(row["assessment_json"] or "{}")
    return row["l4_run_id"], assessment


def core_hash(assessment: dict) -> str:
    blob = json.dumps(
        {
            "candidates": assessment.get("root_cause_candidates"),
            "paths": assessment.get("causal_paths"),
            "impact": assessment.get("impact_assessment"),
            "confirmation": assessment.get("root_cause_confirmation"),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def main() -> None:
    targets = TARGET_ORIGINAL + TARGET_EXTRA
    # group by l4_run_id
    by_run: dict[str, list[str]] = {}
    before_hashes: dict[str, str] = {}
    for rid in targets:
        try:
            l4_run_id, assessment = find_record(rid)
        except Exception as exc:
            print("SKIP", rid, exc)
            continue
        by_run.setdefault(l4_run_id, []).append(rid)
        before_hashes[rid] = core_hash(assessment)
        print("FOUND", rid, "l4", l4_run_id, "core_hash", before_hashes[rid])

    results_summary = []
    for l4_run_id, rids in by_run.items():
        print("RUN", l4_run_id, rids)
        out = run_l4_reasoning(
            l4_run_id,
            record_ids=rids,
            force_new=True,
            dry_run=False,
            max_records=len(rids),
        )
        rr = out["reasoning_run"]
        print("reasoning_run", rr.get("reasoning_run_id"), rr.get("status"))
        for rid in rids:
            item = L4_REASONING_STORE.get_record(rr["reasoning_run_id"], rid)
            a = item.get("assessment") or {}
            audit = a.get("audit") or {}
            # verify core unchanged
            _, core_now = find_record(rid)
            after = core_hash(core_now)
            ok_hash = after == before_hashes[rid]
            row = {
                "record_id": rid,
                "outcome": item.get("outcome") or a.get("outcome"),
                "finish_reason": audit.get("finish_reason"),
                "error_type": audit.get("error_type"),
                "json_failure_class": audit.get("json_failure_class"),
                "total_tokens": audit.get("total_tokens"),
                "completion_tokens": audit.get("completion_tokens"),
                "prompt_tokens": audit.get("prompt_tokens"),
                "llm_call_count": audit.get("llm_call_count"),
                "tool_call_count": audit.get("tool_call_count"),
                "unique_tool_exec_count": audit.get("unique_tool_exec_count"),
                "cache_hits": audit.get("cache_hits"),
                "duration": audit.get("execution_duration"),
                "final_response_format": audit.get("final_response_format"),
                "json_repaired": audit.get("json_repaired"),
                "confirmation": a.get("root_cause_confirmation"),
                "impact_degree": (a.get("impact_interpretation") or {}).get("impact_degree"),
                "propagation": (a.get("impact_interpretation") or {}).get("propagation_status"),
                "core_hash_unchanged": ok_hash,
                "validity": [
                    f"{r.get('candidate_id')}:{r.get('validity_status')}"
                    for r in (a.get("candidate_reviews") or [])
                ],
                "tools": [h.get("tool") for h in (item.get("tool_history") or [])],
            }
            results_summary.append(row)
            print(json.dumps(row, ensure_ascii=False))

    out_path = Path(__file__).resolve().parents[1] / "L4_REASONING_RELIABILITY_LIVE.json"
    out_path.write_text(json.dumps(results_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE", out_path)


if __name__ == "__main__":
    main()
