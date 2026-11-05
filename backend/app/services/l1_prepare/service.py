"""P5–P8 数据准备编排与查询 API。"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

import sqlite3

from app.services.l1_cleaning.service import run_p6
from app.services.l1_features.service import run_p7
from app.services.l1_prepare.context import require_p4
from app.services.l1_prepare.repository import ensure_tables
from app.services.l1_publishing.service import run_p8
from app.services.l1_quality.service import run_p5
from app.services.l1_store import L1Error, _connect, _require, _require_indexed

_FINAL = {"completed", "completed_with_warnings", "partial", "failed", "needs_review"}


def run_prepare(dataset_id: str, config: dict | None = None) -> dict:
    if not isinstance(config, dict):
        config = {}
    dataset = _require(dataset_id)
    _require_indexed(dataset)
    connection = _open()
    started = False
    try:
        p4_run = require_p4(connection, dataset_id, config.get("p4_run_id"))
        signature = _signature(p4_run["run_id"], dataset["sha256"], config)
        existing = connection.execute(
            """
            SELECT * FROM prepare_runs
            WHERE dataset_id = ? AND signature = ? AND status IN (
                'completed', 'completed_with_warnings', 'partial', 'failed', 'needs_review'
            )
            ORDER BY revision DESC LIMIT 1
            """,
            (dataset_id, signature),
        ).fetchone()
        if existing and not config.get("force"):
            body = _prepare_public(existing)
            _enqueue_l2_handoff(body)
            return body

        current = connection.execute(
            "SELECT MAX(revision) AS revision FROM prepare_runs WHERE dataset_id = ?",
            (dataset_id,),
        ).fetchone()
        revision = int(current["revision"] or 0) + 1
        prepare_run_id = "PREP-" + uuid.uuid4().hex[:12]
        version_id = "PD-" + uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        connection.execute("BEGIN IMMEDIATE")
        started = True
        connection.execute(
            """
            INSERT INTO prepare_runs (
                run_id, dataset_id, revision, status, created_at, finished_at,
                p0_sha256, p4_run_id, signature, config_json, summary_json,
                p5_run_id, p6_run_id, p7_run_id, p8_run_id, version_id
            ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?)
            """,
            (
                prepare_run_id,
                dataset_id,
                revision,
                "running",
                now,
                dataset["sha256"],
                p4_run["run_id"],
                signature,
                json.dumps(config, ensure_ascii=False, separators=(",", ":")),
                "{}",
                version_id,
            ),
        )
        connection.execute(
            "UPDATE prepare_runs SET status = ? WHERE run_id = ?",
            ("running", prepare_run_id),
        )

        p5_run_id, p5_summary = run_p5(connection, dataset_id, prepare_run_id, p4_run, config)
        p6_run_id, p6_summary = run_p6(connection, dataset_id, prepare_run_id, p4_run, p5_run_id, config)
        p7_run_id, p7_summary = run_p7(connection, dataset_id, prepare_run_id, p4_run, p6_run_id, version_id, config)
        p8_run_id, p8_summary, readiness = run_p8(
            connection,
            dataset_id,
            prepare_run_id,
            dataset,
            p4_run,
            p7_run_id,
            p7_summary,
            p5_summary,
            p6_summary,
            version_id,
            config,
        )

        stages = {
            "p5": connection.execute("SELECT status FROM p5_runs WHERE run_id = ?", (p5_run_id,)).fetchone()["status"],
            "p6": "completed",
            "p7": "completed",
            "p8": connection.execute("SELECT status FROM p8_runs WHERE run_id = ?", (p8_run_id,)).fetchone()["status"],
        }
        final_status = _aggregate_status(stages)
        finished = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        summary = {
            "p5_run_id": p5_run_id,
            "p6_run_id": p6_run_id,
            "p7_run_id": p7_run_id,
            "p8_run_id": p8_run_id,
            "version_id": version_id,
            "p5_summary": p5_summary,
            "p6_summary": p6_summary,
            "p7_summary": {k: v for k, v in p7_summary.items() if k != "feature_catalog"},
            "p8_summary": p8_summary,
            "readinessByAnalysisType": readiness,
            "stage_status": stages,
        }
        report = {
            "dataset_id": dataset_id,
            "prepare_run_id": prepare_run_id,
            "version_id": version_id,
            "generated_at": finished,
            "stages": stages,
            "readinessByAnalysisType": readiness,
            "p5": p5_summary,
            "p6": p6_summary,
            "p7": {"prepared_row_count": p7_summary.get("prepared_row_count"), "catalog_size": len(p7_summary.get("feature_catalog") or [])},
            "p8": p8_summary,
        }
        connection.execute(
            """
            UPDATE prepare_runs SET
                status = ?, finished_at = ?, summary_json = ?,
                p5_run_id = ?, p6_run_id = ?, p7_run_id = ?, p8_run_id = ?, version_id = ?
            WHERE run_id = ?
            """,
            (
                final_status,
                finished,
                json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
                p5_run_id,
                p6_run_id,
                p7_run_id,
                p8_run_id,
                version_id,
                prepare_run_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO data_preparation_reports (prepare_run_id, report_json, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(prepare_run_id) DO UPDATE SET report_json = excluded.report_json, created_at = excluded.created_at
            """,
            (prepare_run_id, json.dumps(report, ensure_ascii=False, separators=(",", ":")), finished),
        )
        connection.commit()
        started = False
        row = connection.execute("SELECT * FROM prepare_runs WHERE run_id = ?", (prepare_run_id,)).fetchone()
        body = _prepare_public(row)
        body["report"] = report
        _enqueue_l2_handoff(body)
        return body
    except Exception:
        if started:
            connection.rollback()
        raise
    finally:
        connection.close()


def _enqueue_l2_handoff(prepare_body: dict) -> None:
    """P8/prepare 持久化成功后可靠入队 L2 Handoff（不阻塞、不 HTTP 自调用）。"""
    try:
        from l2.handoff.orchestrator import ORCHESTRATOR

        version_id = prepare_body.get("version_id")
        dataset_id = prepare_body.get("dataset_id")
        if not version_id or not dataset_id:
            return
        ORCHESTRATOR.enqueue_after_prepare(
            dataset_id=str(dataset_id),
            pd_version_id=str(version_id),
            prepare_run_id=prepare_body.get("run_id"),
            prepare_status=prepare_body.get("status"),
        )
    except Exception:  # noqa: BLE001 - 入队失败不回滚 L1；依赖重启补偿扫描
        import logging

        logging.getLogger("l1.prepare").exception(
            "enqueue L2 handoff failed dataset=%s pd=%s",
            prepare_body.get("dataset_id"),
            prepare_body.get("version_id"),
        )


def get_prepare_run(run_id: str) -> dict:
    _check_prep(run_id)
    connection = _open()
    try:
        row = _require_prepare(connection, run_id)
        return _prepare_public(row)
    finally:
        connection.close()


def list_prepare_stages(run_id: str) -> dict:
    _check_prep(run_id)
    connection = _open()
    try:
        prep = _require_prepare(connection, run_id)
        stages = []
        for stage, prefix, col in (
            ("p5", "P5", "p5_run_id"),
            ("p6", "P6", "p6_run_id"),
            ("p7", "P7", "p7_run_id"),
            ("p8", "P8", "p8_run_id"),
        ):
            sub_id = prep[col]
            if not sub_id:
                stages.append({"stage_id": stage, "run_id": None, "status": "not_started"})
                continue
            table = f"{stage}_runs"
            sub = connection.execute(f"SELECT status, created_at, summary_json FROM {table} WHERE run_id = ?", (sub_id,)).fetchone()
            stages.append(
                {
                    "stage_id": stage,
                    "run_id": sub_id,
                    "status": sub["status"] if sub else "unknown",
                    "created_at": sub["created_at"] if sub else None,
                    "summary": json.loads(sub["summary_json"]) if sub and sub["summary_json"] else None,
                }
            )
        return {"prepare_run_id": run_id, "stages": stages}
    finally:
        connection.close()


def list_prepare_issues(
    run_id: str,
    offset: int,
    limit: int,
    stage: str | None = None,
    code: str | None = None,
) -> dict:
    _check_prep(run_id)
    _check_page(offset, limit)
    connection = _open()
    try:
        prep = _require_prepare(connection, run_id)
        target_stage = (stage or "p5").lower()
        if target_stage != "p5":
            return {"prepare_run_id": run_id, "stage": target_stage, "offset": offset, "limit": limit, "total": 0, "issues": []}
        p5_id = prep["p5_run_id"]
        if not p5_id:
            return {"prepare_run_id": run_id, "stage": "p5", "offset": offset, "limit": limit, "total": 0, "issues": []}
        where = "run_id = ?"
        params: list[object] = [p5_id]
        if code:
            where += " AND code = ?"
            params.append(code)
        total = int(connection.execute(f"SELECT COUNT(*) AS total FROM p5_issues WHERE {where}", params).fetchone()["total"])
        found = connection.execute(
            f"SELECT * FROM p5_issues WHERE {where} ORDER BY issue_id LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
    finally:
        connection.close()
    return {
        "prepare_run_id": run_id,
        "stage": "p5",
        "offset": offset,
        "limit": limit,
        "total": total,
        "issues": [_p5_issue(row) for row in found],
    }


def get_prepare_report(run_id: str) -> dict:
    _check_prep(run_id)
    connection = _open()
    try:
        row = connection.execute(
            "SELECT report_json FROM data_preparation_reports WHERE prepare_run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            prep = _require_prepare(connection, run_id)
            summary = json.loads(prep["summary_json"] or "{}")
            return {"prepare_run_id": run_id, "report": summary}
        return {"prepare_run_id": run_id, "report": json.loads(row["report_json"])}
    finally:
        connection.close()


def list_prepare_artifacts(run_id: str) -> dict:
    _check_prep(run_id)
    connection = _open()
    try:
        prep = _require_prepare(connection, run_id)
        version_id = prep["version_id"]
        if not version_id:
            return {"prepare_run_id": run_id, "artifacts": []}
        pd = connection.execute("SELECT * FROM prepared_datasets WHERE version_id = ?", (version_id,)).fetchone()
        if pd is None:
            return {"prepare_run_id": run_id, "version_id": version_id, "artifacts": []}
        from pathlib import Path

        manifest_path = Path(pd["manifest_path"])
        items = [{"name": "manifest.json", "path": str(manifest_path), "kind": "manifest"}]
        for name in ("features.parquet", "features.jsonl"):
            path = manifest_path.parent / name
            if path.is_file():
                items.append({"name": name, "path": str(path), "kind": "features"})
    finally:
        connection.close()
    return {"prepare_run_id": run_id, "version_id": version_id, "artifacts": items}


def get_prepared_dataset(version_id: str) -> dict:
    if not version_id.startswith("PD-") or len(version_id) != 15:
        raise L1Error("version_not_found", "没有这个已发布数据集版本。", 404)
    connection = _open()
    try:
        row = connection.execute("SELECT * FROM prepared_datasets WHERE version_id = ?", (version_id,)).fetchone()
        if row is None:
            raise L1Error("version_not_found", "没有这个已发布数据集版本。", 404)
        return _prepared_public(row)
    finally:
        connection.close()


def list_prepared_rows(
    version_id: str,
    offset: int,
    limit: int,
    equipment_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    if not version_id.startswith("PD-") or len(version_id) != 15:
        raise L1Error("version_not_found", "没有这个已发布数据集版本。", 404)
    _check_page(offset, limit)
    connection = _open()
    try:
        if connection.execute("SELECT 1 FROM prepared_datasets WHERE version_id = ?", (version_id,)).fetchone() is None:
            raise L1Error("version_not_found", "没有这个已发布数据集版本。", 404)
        where = "version_id = ?"
        params: list[object] = [version_id]
        if equipment_id:
            where += " AND equipment_id = ?"
            params.append(equipment_id)
        if start:
            where += " AND timestamp_text >= ?"
            params.append(start)
        if end:
            where += " AND timestamp_text <= ?"
            params.append(end)
        total = int(connection.execute(f"SELECT COUNT(*) AS total FROM prepared_feature_rows WHERE {where}", params).fetchone()["total"])
        found = connection.execute(
            f"""
            SELECT source_row_id, equipment_id, timestamp_text, features_json, validity_json, quality_json
            FROM prepared_feature_rows WHERE {where} ORDER BY row_id LIMIT ? OFFSET ?
            """,
            (*params, limit, offset),
        ).fetchall()
    finally:
        connection.close()
    return {
        "version_id": version_id,
        "offset": offset,
        "limit": limit,
        "total": total,
        "total_records": total,
        "rows": [
            {
                "source_row_id": row["source_row_id"],
                "equipment_id": row["equipment_id"],
                "timestamp_text": row["timestamp_text"],
                "features": json.loads(row["features_json"]),
                "validity": json.loads(row["validity_json"]),
                "quality": json.loads(row["quality_json"]),
            }
            for row in found
        ],
    }


def latest_prepare(dataset_id: str) -> dict:
    _require(dataset_id)
    connection = _open()
    try:
        row = connection.execute(
            """
            SELECT * FROM prepare_runs WHERE dataset_id = ?
            AND status IN ('completed', 'completed_with_warnings', 'partial', 'failed', 'needs_review')
            ORDER BY revision DESC LIMIT 1
            """,
            (dataset_id,),
        ).fetchone()
        if row is None:
            return {"dataset_id": dataset_id, "applied": False, "run_id": "", "status": "not_started", "summary": None}
        body = _prepare_public(row)
        body["applied"] = True
        return body
    finally:
        connection.close()


def p5_status(dataset_id: str) -> tuple[str, int | None, bool]:
    return _stage_status(dataset_id, "p5")


def p6_status(dataset_id: str) -> tuple[str, int | None, bool]:
    return _stage_status(dataset_id, "p6")


def p7_status(dataset_id: str) -> tuple[str, int | None, bool]:
    return _stage_status(dataset_id, "p7")


def p8_status(dataset_id: str) -> tuple[str, int | None, bool]:
    return _stage_status(dataset_id, "p8")


def prepare_stale(dataset_id: str) -> bool:
    connection = _open()
    try:
        prep = connection.execute(
            "SELECT p4_run_id FROM prepare_runs WHERE dataset_id = ? ORDER BY revision DESC LIMIT 1",
            (dataset_id,),
        ).fetchone()
        latest_p4 = connection.execute(
            """
            SELECT run_id FROM p4_runs
            WHERE dataset_id = ? AND status IN ('completed', 'partial')
            ORDER BY revision DESC LIMIT 1
            """,
            (dataset_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    finally:
        connection.close()
    if prep is None or latest_p4 is None:
        return False
    return str(prep["p4_run_id"]) != str(latest_p4["run_id"])


def _stage_status(dataset_id: str, stage: str) -> tuple[str, int | None, bool]:
    connection = _open()
    try:
        prep = connection.execute(
            "SELECT revision, p4_run_id, p5_run_id, p6_run_id, p7_run_id, p8_run_id, status FROM prepare_runs WHERE dataset_id = ? ORDER BY revision DESC LIMIT 1",
            (dataset_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return "not_started", None, False
    finally:
        connection.close()
    if prep is None:
        return "not_started", None, False
    run_id = prep[f"{stage}_run_id"]
    if not run_id:
        return "not_started", int(prep["revision"]), prepare_stale(dataset_id)
    connection = _open()
    try:
        sub = connection.execute(f"SELECT status FROM {stage}_runs WHERE run_id = ?", (run_id,)).fetchone()
    except sqlite3.OperationalError:
        return "not_started", int(prep["revision"]), prepare_stale(dataset_id)
    finally:
        connection.close()
    status = sub["status"] if sub else prep["status"]
    return str(status), int(prep["revision"]), prepare_stale(dataset_id)


def _open() -> sqlite3.Connection:
    connection = _connect()
    ensure_tables(connection)
    return connection


def _signature(p4_run_id: str, sha256: str, config: dict) -> str:
    payload = {"p4_run_id": p4_run_id, "p0_sha256": sha256, "config": config}
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _aggregate_status(stages: dict[str, str]) -> str:
    values = list(stages.values())
    if any(v == "failed" for v in values):
        return "failed"
    if any(v == "needs_review" for v in values):
        return "needs_review"
    if any(v == "partial" for v in values):
        return "partial"
    if any(v == "completed_with_warnings" for v in values):
        return "completed_with_warnings"
    return "completed"


def _prepare_public(row: sqlite3.Row) -> dict:
    summary = json.loads(row["summary_json"] or "{}")
    return {
        "run_id": row["run_id"],
        "dataset_id": row["dataset_id"],
        "revision": int(row["revision"]),
        "status": row["status"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
        "p0_sha256": row["p0_sha256"],
        "p4_run_id": row["p4_run_id"],
        "signature": row["signature"],
        "version_id": row["version_id"],
        "p5_run_id": row["p5_run_id"],
        "p6_run_id": row["p6_run_id"],
        "p7_run_id": row["p7_run_id"],
        "p8_run_id": row["p8_run_id"],
        "summary": summary,
        "config": json.loads(row["config_json"] or "{}"),
        "readinessByAnalysisType": summary.get("readinessByAnalysisType"),
    }


def _prepared_public(row: sqlite3.Row) -> dict:
    return {
        "version_id": row["version_id"],
        "dataset_id": row["dataset_id"],
        "prepare_run_id": row["prepare_run_id"],
        "created_at": row["created_at"],
        "p0_sha256": row["p0_sha256"],
        "p4_run_id": row["p4_run_id"],
        "manifest_path": row["manifest_path"],
        "readinessByAnalysisType": json.loads(row["readiness_json"] or "{}"),
        "row_count": int(row["row_count"]),
        "feature_count": int(row["feature_count"]),
    }


def _p5_issue(row: sqlite3.Row) -> dict:
    return {
        "issue_id": int(row["issue_id"]),
        "severity": row["severity"],
        "code": row["code"],
        "message": row["message"],
        "equipment_id": row["equipment_id"],
        "source_row_id": row["source_row_id"],
        "region_id": row["region_id"],
        "field_id": row["field_id"],
        "detail": json.loads(row["detail_json"] or "{}"),
    }


def _require_prepare(connection: sqlite3.Connection, run_id: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM prepare_runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise L1Error("prepare_not_found", "没有这次数据准备记录。", 404)
    return row


def _check_prep(run_id: str) -> None:
    if not run_id.startswith("PREP-") or len(run_id) != 17:
        raise L1Error("prepare_not_found", "没有这次数据准备记录。", 404)


def _check_page(offset: int, limit: int) -> None:
    if offset < 0 or limit < 1 or limit > 200:
        raise L1Error("invalid_page", "offset 必须大于等于 0，limit 必须是 1 到 200 的整数。", 422)
