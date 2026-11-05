"""L5b 真实 PED 回归（不改 PED，不调用 LLM）。"""
from __future__ import annotations

import json
from pathlib import Path

from l5.alerts.jobs import run_alerts
from l5.alerts.store import ALERT_STORE
from l5.store import L5_STORE


def main() -> None:
    # 取最近一个有记录的 L5 run
    page = L5_STORE.list_runs(offset=0, limit=10)
    if not page.get("items"):
        raise SystemExit("no l5 runs")
    l5 = page["items"][0]
    l5_id = l5["l5_run_id"]
    print("l5", l5_id, l5.get("analysis_run_id"))
    out = run_alerts(source_l5_run_id=l5_id, force_new=True, max_records=5)
    ar = out["alert_run"]
    print("alert_run", ar["alert_run_id"], ar["status"], "formal_total", ar.get("formal_alert_total"))
    recs = ALERT_STORE.list_records(ar["alert_run_id"], limit=10)
    summary = []
    for item in recs.get("items") or []:
        a = item.get("assessment") or {}
        summary.append(
            {
                "record_id": item["record_id"],
                "formal": a.get("formal_alert_count"),
                "reference": a.get("reference_count"),
                "triggered_ref": [
                    {
                        "rule_id": r.get("rule_id"),
                        "feature": r.get("canonical_feature_id"),
                        "obs": r.get("observed_value"),
                        "thr": r.get("threshold_value"),
                        "level": r.get("alert_level"),
                        "status": r.get("evaluation_status"),
                        "review": r.get("review_status"),
                        "formal": r.get("formal"),
                    }
                    for r in (a.get("reference_comparisons") or [])
                    if r.get("evaluation_status") in {"triggered", "rule_conflict", "within_limit"}
                ][:12],
                "conflicts": [c.get("conflict_id") for c in (a.get("conflicts") or [])[:6]],
            }
        )
        print(json.dumps(summary[-1], ensure_ascii=False))
    path = Path(__file__).resolve().parent / "L5B_LIVE_E2E.json"
    path.write_text(json.dumps({"alert_run": ar, "summary": summary}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE", path)


if __name__ == "__main__":
    main()
