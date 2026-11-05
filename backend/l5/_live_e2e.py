"""L5a 真实数据端到端：生成至少 5 条报告，可选小批量。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from l4.database import default_db_path as l4_db
from l4.store import L4_STORE
from l5.jobs import run_l5
from l5.store import L5_STORE


def core_hash(assessment: dict) -> str:
    blob = json.dumps(
        {
            "cands": assessment.get("root_cause_candidates"),
            "impact": assessment.get("impact_assessment"),
            "conf": assessment.get("root_cause_confirmation"),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def pick_records(limit: int = 8) -> list[tuple[str, str, str]]:
    """Return list of (l4_run_id, analysis_run_id, record_id)."""
    con = sqlite3.connect(l4_db())
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT r.l4_run_id, u.source_analysis_run_id, r.record_id, r.assessment_json
        FROM l4_records r
        JOIN l4_runs u ON u.l4_run_id = r.l4_run_id
        WHERE r.status = 'succeeded'
        ORDER BY r.rowid DESC
        LIMIT ?
        """,
        (limit * 3,),
    ).fetchall()
    con.close()
    out = []
    seen = set()
    for row in rows:
        rid = row["record_id"]
        if rid in seen:
            continue
        seen.add(rid)
        out.append((row["l4_run_id"], row["source_analysis_run_id"], rid))
        if len(out) >= limit:
            break
    return out


def main() -> None:
    picks = pick_records(8)
    if len(picks) < 5:
        raise SystemExit(f"need >=5 l4 records, got {len(picks)}")

    # group by (l4, analysis)
    groups: dict[tuple[str, str], list[str]] = {}
    before: dict[str, str] = {}
    for l4_id, ar_id, rid in picks:
        groups.setdefault((l4_id, ar_id), []).append(rid)
        before[rid] = core_hash(L4_STORE.get_record(l4_id, rid).get("assessment") or {})

    summaries = []
    for (l4_id, ar_id), rids in groups.items():
        # first small batch of 5 across groups
        pass

    # Take first 5 records possibly across runs — run per group
    remaining = 5
    for (l4_id, ar_id), rids in groups.items():
        if remaining <= 0:
            break
        batch = rids[:remaining]
        remaining -= len(batch)
        print("RUN", l4_id, ar_id, batch)
        out = run_l5(analysis_run_id=ar_id, l4_run_id=l4_id, record_ids=batch, force_new=True, max_records=len(batch))
        l5_id = out["l5_run"]["l5_run_id"]
        print("l5", l5_id, out["l5_run"]["status"])
        for rid in batch:
            item = L5_STORE.get_record(l5_id, rid)
            a = item.get("assessment") or {}
            fields = a.get("fields") or {}
            # core hash unchanged
            after = core_hash(L4_STORE.get_record(l4_id, rid).get("assessment") or {})
            summaries.append(
                {
                    "l5_run_id": l5_id,
                    "record_id": rid,
                    "outcome": item.get("outcome"),
                    "review_status": item.get("review_status"),
                    "equipment_status": (fields.get("equipment_status") or {}).get("value"),
                    "fault_severity": (fields.get("fault_severity") or {}).get("value"),
                    "priority": (fields.get("priority") or {}).get("value"),
                    "impact_degree": (fields.get("impact_degree") or {}).get("value"),
                    "diagnosis_confidence": (fields.get("diagnosis_confidence") or {}).get("value"),
                    "root_cause_confirmation": a.get("root_cause_confirmation"),
                    "verification_count": len(a.get("verification_recommendations") or []),
                    "l4_hash_unchanged": after == before[rid],
                    "source_run_ids": a.get("source_run_ids"),
                    "null_reasons": {
                        k: (fields.get(k) or {}).get("null_reason")
                        for k in ("fault_severity", "priority", "impact_degree", "diagnosis_confidence")
                    },
                }
            )
            print(json.dumps(summaries[-1], ensure_ascii=False))

    # optional batch up to 200 from one l4 run
    big_l4, big_ar, _ = picks[0]
    page = L4_STORE.list_records(big_l4, offset=0, limit=50)
    batch_ids = [i["record_id"] for i in page.get("items") or []][:50]
    print("BATCH", big_l4, len(batch_ids))
    bout = run_l5(
        analysis_run_id=big_ar,
        l4_run_id=big_l4,
        record_ids=batch_ids,
        force_new=True,
        max_records=50,
    )
    print("batch_status", bout["l5_run"]["status"], bout["l5_run"]["processed_record_count"])

    # mismatch test
    try:
        run_l5(analysis_run_id=big_ar, l4_run_id="L4R-does-not-exist", record_ids=[batch_ids[0]], force_new=True)
        mismatch = "unexpected_success"
    except Exception as exc:
        mismatch = f"rejected:{type(exc).__name__}:{exc}"
    print("mismatch", mismatch)

    try:
        # 故意交叉绑定：用另一条 L4 的 analysis（若只有同一 analysis 则跳过）
        other = next(((a, b, c) for a, b, c in picks if b != big_ar), None)
        if other:
            run_l5(analysis_run_id=big_ar, l4_run_id=other[0], record_ids=[batch_ids[0]], force_new=True)
            cross = "unexpected_success"
        else:
            cross = "skipped_same_analysis"
    except Exception as exc:
        cross = f"rejected:{type(exc).__name__}:{exc}"
    print("cross_mismatch", cross)

    out_path = Path(__file__).resolve().parents[1] / "l4" / "L5A_LIVE_E2E.json"
    # store under backend/l5 delivery folder instead
    out_path = Path(__file__).resolve().parent / "L5A_LIVE_E2E.json"
    out_path.write_text(
        json.dumps({"summaries": summaries, "batch": bout["l5_run"], "mismatch": mismatch}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("WROTE", out_path)


if __name__ == "__main__":
    main()
