"""缺失 PED 时应将 AnalysisRun 标记 failed 并出队，而不是保持 queued。"""

from __future__ import annotations

from pathlib import Path

from l2.handoff.database import connect, init_db
from l2.handoff.store import HandoffStore
from l3.runtime.jobs import execute_analysis_run
from l3.runtime.store import AnalysisStore


def test_missing_ped_marks_analysis_run_failed(tmp_path: Path):
    hdb = tmp_path / "handoff.sqlite3"
    adb = tmp_path / "l3.sqlite3"
    init_db(hdb)
    hstore = HandoffStore(db_path=hdb)
    astore = AnalysisStore(db_path=adb)
    connection = connect(hdb)
    try:
        connection.execute(
            """
            INSERT INTO diagnosis_runs (
                diagnosis_run_id, prepared_dataset_id, dataset_revision_id, equipment_type,
                model_bundle_id, schema_version, status, total_records, processed_records,
                succeeded_records, failed_records, counts_json, content_key, result_dir, started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "DR-missing-ped",
                "PED-does-not-exist",
                "rev1",
                "main_mill_motor",
                "MB-1",
                "v1",
                "completed",
                1,
                1,
                1,
                0,
                "{}",
                "ck-missing",
                str(tmp_path),
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:01:00Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO diagnosis_records(diagnosis_run_id, record_id, status, screening_result, result_json)
            VALUES (?, ?, 'ok', ?, ?)
            """,
            (
                "DR-missing-ped",
                "R1",
                "ABNORMAL",
                '{"decision":{"screening_result":"ABNORMAL","preliminary_fault":"轴承磨损","route":"l3_review_recommended"}}',
            ),
        )
        connection.commit()
    finally:
        connection.close()

    run, _ = astore.create_or_get_run(
        content_key="missing-ped-key",
        source_diagnosis_run_id="DR-missing-ped",
        prepared_dataset_id="PED-does-not-exist",
        dataset_revision_id="rev1",
        equipment_type="main_mill_motor",
        model_bundle_id="MB-1",
        selected_record_count=1,
        analyzer_config_hash="x",
        routing_policy_version="l2_l3_routing_v2",
    )
    out = execute_analysis_run(run["analysis_run_id"], handoff_store=hstore, analysis_store=astore)
    assert out["status"] == "failed"
    assert "PED" in (out.get("error_summary") or "")
    assert astore.list_queued() == []
