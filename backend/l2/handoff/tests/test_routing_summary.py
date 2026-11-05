from __future__ import annotations

from pathlib import Path

from l2.handoff.database import connect, init_db
from l2.handoff.store import HandoffStore


def test_routing_summary_normal_stays_abnormal_enters_l3(tmp_path: Path):
    db = tmp_path / "handoff.sqlite3"
    init_db(db)
    store = HandoffStore(db_path=db)
    connection = connect(db)
    try:
        connection.execute(
            """
            INSERT INTO diagnosis_runs (
                diagnosis_run_id, prepared_dataset_id, dataset_revision_id, equipment_type,
                model_bundle_id, schema_version, status, total_records, processed_records,
                succeeded_records, failed_records, counts_json, content_key, result_dir, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "DR-test-route",
                "PED-fake",
                "rev1",
                "main_mill_motor",
                "TR-fake",
                "v1",
                "completed",
                5,
                5,
                5,
                0,
                '{"NORMAL":2,"ABNORMAL":1,"CONFLICT":1,"UNCERTAIN":1}',
                "ck",
                str(tmp_path),
                "2026-01-01T00:00:00Z",
            ),
        )
        rows = [
            ("R1", "NORMAL"),
            ("R2", "NORMAL"),
            ("R3", "ABNORMAL"),
            ("R4", "CONFLICT"),
            ("R5", "UNCERTAIN"),
        ]
        for rid, screening in rows:
            connection.execute(
                """
                INSERT INTO diagnosis_records(
                    diagnosis_run_id, record_id, status, screening_result, result_json
                ) VALUES (?, ?, 'ok', ?, ?)
                """,
                (
                    "DR-test-route",
                    rid,
                    screening,
                    '{"decision":{"screening_result":"%s"}}' % screening,
                ),
            )
        connection.commit()
    finally:
        connection.close()

    summary = store.get_routing_summary("DR-test-route")
    assert summary["total_records"] == 5
    # l2_complete = NORMAL；l3_review = ABNORMAL + CONFLICT + UNCERTAIN
    assert summary["l2_complete_count"] == 2
    assert summary["l3_review_count"] == 3
    assert summary["route_counts"]["l2_complete"] == 2
    assert summary["route_counts"]["l3_review_recommended"] == 3
    assert summary["routing_policy_version"] == "l2_l3_routing_v2"
    assert len(summary["groups"]) == 1
    group = summary["groups"][0]
    assert group["screening_status"] == "needs_l3_review"
    assert group["l3_review_count"] == 3
    assert group["l2_complete_count"] == 2
    assert group["abnormal_count"] == 1
    assert set(group["l3_record_ids"]) == {"R3", "R4", "R5"}
    assert "R1" not in group["l3_record_ids"]
