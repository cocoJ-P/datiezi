"""P6 数据清洗：仅标记/隔离/排除，不改 P0/P4 值。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import sqlite3

from app.services.l1_prepare.context import (
    iter_row_records,
    load_compiled,
    membership_map,
    parse_timestamp_value,
    require_p4,
    timestamp_field_for_region,
)
from app.services.l1_store import L1Error

_RULE = "cleaning_v1"
# 非隔离决策可截断；隔离决策必须完整落库，否则 P7 保留集与摘要会不一致。
_FLAG_DECISION_CAP = 10000


def run_p6(
    connection: sqlite3.Connection,
    dataset_id: str,
    prepare_run_id: str,
    p4_run: sqlite3.Row,
    p5_run_id: str,
    config: dict,
) -> tuple[str, dict]:
    compiled = load_compiled(connection, p4_run)
    membership = membership_map(connection, p4_run["p2_run_id"])

    p5_issues = connection.execute(
        "SELECT code, source_row_id, field_id, message FROM p5_issues WHERE run_id = ?",
        (p5_run_id,),
    ).fetchall()
    p5_by_row: dict[str, list[sqlite3.Row]] = {}
    for row in p5_issues:
        sid = str(row["source_row_id"] or "")
        if sid:
            p5_by_row.setdefault(sid, []).append(row)

    decisions: list[tuple] = []
    flag_decisions = 0
    overflow = 0
    quarantined = 0
    excluded_fields = 0
    flagged = 0
    retained = 0
    scanned = 0

    def add_decision(source_row_id: str, action: str, reason: str, rule_id: str) -> None:
        nonlocal overflow, flag_decisions
        if action in {"quarantine", "exclude_field"}:
            decisions.append((source_row_id, action, reason, rule_id))
            return
        if flag_decisions >= _FLAG_DECISION_CAP:
            overflow += 1
            return
        flag_decisions += 1
        decisions.append((source_row_id, action, reason, rule_id))

    for fld in compiled["fields"]:
        if fld.get("confirmation_status") != "confirmed":
            add_decision(
                "",
                "exclude_field",
                f"未确认映射：{fld.get('header') or fld.get('standard_field_id')}",
                "p6_unconfirmed_mapping",
            )
            excluded_fields += 1

    for record in iter_row_records(connection, dataset_id, compiled, membership):
        scanned += 1
        ts_text, ts_status = parse_timestamp_value(
            record,
            timestamp_field_for_region(compiled, record.region_id),
        )
        row_quarantine = False
        if not record.equipment_id.strip():
            add_decision(record.source_row_id, "quarantine", "缺少可用设备标识。", "p6_missing_equipment")
            quarantined += 1
            row_quarantine = True
        timeseries = config.get("timeseries_mode", True)
        if timeseries and ts_status != "success":
            add_decision(record.source_row_id, "quarantine", "时间序列缺少可解析时间戳。", "p6_missing_timestamp")
            if not row_quarantine:
                quarantined += 1
            row_quarantine = True
        for issue in p5_by_row.get(record.source_row_id, []):
            add_decision(
                record.source_row_id,
                "flag",
                str(issue["message"]),
                f"p5_{issue['code']}",
            )
            flagged += 1
        if not row_quarantine:
            retained += 1

    status = "completed"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_id = "P6-" + uuid.uuid4().hex[:12]
    summary = {
        "rule_id": _RULE,
        "scanned_row_count": scanned,
        "retained_row_count": retained,
        "quarantined_row_count": quarantined,
        "flag_count": flagged,
        "exclude_field_decisions": excluded_fields,
        "decision_count": len(decisions),
        "decision_overflow": overflow,
        "zero_modification": True,
        "p5_run_id": p5_run_id,
    }
    connection.execute(
        """
        INSERT INTO p6_runs (run_id, prepare_run_id, dataset_id, status, created_at, p5_run_id, config_json, summary_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            prepare_run_id,
            dataset_id,
            status,
            now,
            p5_run_id,
            json.dumps(config, ensure_ascii=False, separators=(",", ":")),
            json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
        ),
    )
    if decisions:
        connection.executemany(
            "INSERT INTO p6_decisions (run_id, source_row_id, action, reason, rule_id) VALUES (?, ?, ?, ?, ?)",
            [(run_id, *item) for item in decisions],
        )
    return run_id, summary


def retained_row_ids(
    connection: sqlite3.Connection,
    dataset_id: str,
    compiled: dict,
    membership: dict[str, dict],
    p6_run_id: str,
) -> set[str]:
    quarantined = {
        str(r["source_row_id"])
        for r in connection.execute(
            "SELECT source_row_id FROM p6_decisions WHERE run_id = ? AND action = 'quarantine'",
            (p6_run_id,),
        ).fetchall()
    }
    retained: set[str] = set()
    for record in iter_row_records(connection, dataset_id, compiled, membership):
        if record.source_row_id not in quarantined:
            retained.add(record.source_row_id)
    return retained
