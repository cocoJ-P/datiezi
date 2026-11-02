"""P5 数据质量与物理规则检测（确定性，不改 P0/P4）。"""

from __future__ import annotations

import json
import statistics
import uuid
from datetime import datetime, timezone

import sqlite3

from app.services.l1_prepare.context import (
    field_by_standard_id,
    iter_row_records,
    load_compiled,
    membership_map,
    numeric_scan_fields,
    numeric_value,
    parse_timestamp_value,
    timestamp_field_for_region,
)
from app.services.l1_standardization.definitions import CELL_FAILURES, MISSING

_ISSUE_CAP = 5000
_RULE = "quality_v1"


def run_p5(
    connection: sqlite3.Connection,
    dataset_id: str,
    prepare_run_id: str,
    p4_run: sqlite3.Row,
    config: dict,
) -> tuple[str, dict]:
    compiled = load_compiled(connection, p4_run)
    membership = membership_map(connection, p4_run["p2_run_id"])
    numeric_fields = numeric_scan_fields(compiled)
    by_sid = field_by_standard_id(compiled)
    speed_field = by_sid.get("rotational_speed")
    freq_field = by_sid.get("rotational_frequency")

    issues: list[dict] = []
    overflow = 0

    def emit(
        code: str,
        severity: str,
        message: str,
        *,
        equipment_id: str = "",
        source_row_id: str = "",
        region_id: str = "",
        field_id: str = "",
        detail: dict | None = None,
    ) -> None:
        nonlocal overflow
        if len(issues) >= _ISSUE_CAP:
            overflow += 1
            return
        issues.append(
            {
                "severity": severity,
                "code": code,
                "message": message,
                "equipment_id": equipment_id,
                "source_row_id": source_row_id,
                "region_id": region_id,
                "field_id": field_id,
                "detail_json": json.dumps(detail or {}, ensure_ascii=False, separators=(",", ":")),
            }
        )

    p4_summary = compiled.get("p4_summary") or {}
    for fld in compiled["fields"]:
        if fld.get("confirmation_status") != "confirmed":
            emit(
                "unconfirmed_field",
                "warning",
                f"字段 {fld.get('header') or fld.get('standard_field_id')} 映射未确认。",
                field_id=str(fld.get("standard_field_id") or fld.get("source_column_id")),
                region_id=str(fld.get("region_id") or ""),
                detail={"source_column_id": fld.get("source_column_id")},
            )

    p4_issue_rows = connection.execute(
        """
        SELECT code, severity, message, source_row_id, region_id, source_column_id
        FROM p4_issues WHERE run_id = ? LIMIT 500
        """,
        (p4_run["run_id"],),
    ).fetchall()
    for row in p4_issue_rows:
        fld = next((f for f in compiled["fields"] if f["source_column_id"] == row["source_column_id"]), None)
        code = str(row["code"])
        if code in MISSING or "missing" in code:
            p5_code = "missing_cell"
        elif "time" in code or code in {"time_failure"}:
            p5_code = "time_conversion_failure"
        elif "type" in code or code in CELL_FAILURES:
            p5_code = "type_conversion_failure"
        else:
            p5_code = "completeness_issue"
        emit(
            p5_code,
            "warning" if row["severity"] != "error" else "error",
            str(row["message"]),
            source_row_id=str(row["source_row_id"] or ""),
            region_id=str(row["region_id"] or ""),
            field_id=str(fld.get("standard_field_id") if fld else row["source_column_id"]),
            detail={"p4_code": code},
        )

    fingerprints: dict[str, list[str]] = {}
    equip_time: dict[tuple[str, str], list[str]] = {}
    equip_series: dict[str, list[tuple[str, str, int]]] = {}
    numeric_buckets: dict[str, list[tuple[str, float]]] = {f["standard_field_id"]: [] for f in numeric_fields}

    row_count = 0
    for record in iter_row_records(connection, dataset_id, compiled, membership):
        row_count += 1
        ts_text, ts_status = parse_timestamp_value(
            record,
            timestamp_field_for_region(compiled, record.region_id),
        )
        if ts_status == "failure":
            emit(
                "timestamp_parse_failure",
                "warning",
                "时间字段解析失败。",
                equipment_id=record.equipment_id,
                source_row_id=record.source_row_id,
                region_id=record.region_id,
                field_id="timestamp",
            )
        fp_parts = [record.equipment_id, ts_text]
        for nf in numeric_fields[:8]:
            val = numeric_value(record, nf)
            fp_parts.append("" if val is None else f"{val:.6g}")
        fp = "|".join(fp_parts)
        fingerprints.setdefault(fp, []).append(record.source_row_id)
        if record.equipment_id and ts_text:
            key = (record.equipment_id, ts_text)
            equip_time.setdefault(key, []).append(record.source_row_id)
        if record.equipment_id and ts_status == "success" and ts_text:
            equip_series.setdefault(record.equipment_id, []).append((ts_text, record.source_row_id, record.row_number))
        for nf in numeric_fields:
            val = numeric_value(record, nf)
            if val is not None:
                numeric_buckets[nf["standard_field_id"]].append((record.source_row_id, val))

    for fp, row_ids in fingerprints.items():
        if len(row_ids) > 1:
            emit(
                "exact_duplicate_row",
                "warning",
                f"发现 {len(row_ids)} 条完全重复的业务记录。",
                source_row_id=row_ids[0],
                detail={"row_ids": row_ids[:20], "count": len(row_ids)},
            )

    for (_equip, _ts), row_ids in equip_time.items():
        if len(row_ids) > 1:
            emit(
                "timestamp_conflict",
                "warning",
                "同一设备在同一时间戳出现多条记录。",
                equipment_id=_equip,
                source_row_id=row_ids[0],
                field_id="timestamp",
                detail={"row_ids": row_ids[:20], "count": len(row_ids)},
            )

    for equip, series in equip_series.items():
        if len(series) < 2:
            continue
        ordered = sorted(series, key=lambda x: x[0])
        seen_ts: set[str] = set()
        for ts_text, sid, _rn in ordered:
            if ts_text in seen_ts:
                emit(
                    "duplicate_timestamp",
                    "warning",
                    "设备时间序列存在重复时间戳。",
                    equipment_id=equip,
                    source_row_id=sid,
                    field_id="timestamp",
                )
            seen_ts.add(ts_text)
        prev = ordered[0][0]
        for ts_text, sid, _rn in ordered[1:]:
            if ts_text < prev:
                emit(
                    "out_of_order_timestamp",
                    "warning",
                    "设备时间序列存在乱序。",
                    equipment_id=equip,
                    source_row_id=sid,
                    field_id="timestamp",
                )
            prev = ts_text
        if len(ordered) < 3:
            emit(
                "time_gap_not_evaluable",
                "info",
                "时间点不足 3 个，无法评估大间隔。",
                equipment_id=equip,
                field_id="timestamp",
            )
            continue
        gaps = []
        for i in range(1, len(ordered)):
            gaps.append(_gap_seconds(ordered[i - 1][0], ordered[i][0]))
        usable = [g for g in gaps if g is not None]
        if len(usable) < 2:
            emit(
                "time_gap_not_evaluable",
                "info",
                "无法解析足够时间间隔。",
                equipment_id=equip,
                field_id="timestamp",
            )
            continue
        median_gap = statistics.median(usable)
        threshold = median_gap * 5 if median_gap > 0 else None
        if threshold is None:
            continue
        for i, gap in enumerate(gaps):
            if gap is not None and gap > threshold:
                emit(
                    "large_time_gap",
                    "warning",
                    "相邻采样间隔超过中位间隔的 5 倍。",
                    equipment_id=equip,
                    source_row_id=ordered[i + 1][1],
                    field_id="timestamp",
                    detail={"gap_seconds": gap, "threshold_seconds": threshold},
                )

    field_stats: dict[str, dict] = {}
    for nf in numeric_fields:
        sid = nf["standard_field_id"]
        points = numeric_buckets.get(sid) or []
        stats = _numeric_stats(points)
        field_stats[sid] = stats
        if stats.get("constant_column"):
            emit(
                "constant_numeric_column",
                "info",
                f"数值列 {nf.get('header') or sid} 为常数列。",
                field_id=sid,
                region_id=str(nf.get("region_id") or ""),
                detail=stats,
            )
        for source_row_id, value in stats.get("iqr_outliers") or []:
            emit(
                "iqr_outlier",
                "info",
                "统计离群点（仅标记，不删除）。",
                source_row_id=source_row_id,
                field_id=sid,
                detail={"value": value, **{k: stats[k] for k in ("q1", "q3", "iqr") if k in stats}},
            )

    physical_status = "not_evaluable"
    physical_checked = 0
    physical_mismatch = 0
    if speed_field and freq_field:
        for record in iter_row_records(connection, dataset_id, compiled, membership):
            speed = numeric_value(record, speed_field)
            freq = numeric_value(record, freq_field)
            if speed is None or freq is None:
                continue
            if speed == 0:
                continue
            physical_checked += 1
            expected = speed / 60.0
            if expected == 0:
                continue
            rel = abs(freq - expected) / abs(expected)
            if rel > 0.05:
                physical_mismatch += 1
                emit(
                    "rotational_frequency_mismatch",
                    "warning",
                    "转频与转速/60 相对偏差超过 5%。",
                    equipment_id=record.equipment_id,
                    source_row_id=record.source_row_id,
                    field_id="rotational_frequency",
                    detail={"speed_rpm": speed, "frequency_hz": freq, "expected_hz": expected, "relative_error": rel},
                )
        physical_status = "evaluated" if physical_checked else "not_evaluable"
    else:
        emit(
            "physical_rule_not_evaluable",
            "info",
            "缺少转速或转频字段，无法评估物理一致性。",
            field_id="rotational_frequency",
        )

    error_count = sum(1 for i in issues if i["severity"] == "error")
    warning_count = sum(1 for i in issues if i["severity"] == "warning")
    status = "failed" if error_count else "completed_with_warnings" if warning_count or overflow else "completed"

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_id = "P5-" + uuid.uuid4().hex[:12]
    summary = {
        "rule_id": _RULE,
        "record_count": row_count,
        "issue_count": len(issues),
        "issue_overflow": overflow,
        "error_count": error_count,
        "warning_count": warning_count,
        "p4_run_id": p4_run["run_id"],
        "completeness": {
            "p4_scanned_cells": p4_summary.get("scanned_cells"),
            "p4_missing_cells": p4_summary.get("missing_cells"),
            "unconfirmed_field_count": p4_summary.get("unconfirmed_field_count"),
        },
        "duplicates": {
            "exact_duplicate_groups": sum(1 for v in fingerprints.values() if len(v) > 1),
            "timestamp_conflict_groups": sum(1 for v in equip_time.values() if len(v) > 1),
        },
        "time": {"equipment_series": len(equip_series)},
        "numeric_field_stats": field_stats,
        "physical": {
            "status": physical_status,
            "checked_rows": physical_checked,
            "mismatch_rows": physical_mismatch,
        },
    }
    connection.execute(
        """
        INSERT INTO p5_runs (run_id, prepare_run_id, dataset_id, status, created_at, p4_run_id, config_json, summary_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            prepare_run_id,
            dataset_id,
            status,
            now,
            p4_run["run_id"],
            json.dumps(config, ensure_ascii=False, separators=(",", ":")),
            json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
        ),
    )
    if issues:
        connection.executemany(
            """
            INSERT INTO p5_issues (
                run_id, severity, code, message, equipment_id, source_row_id, region_id, field_id, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(run_id, i["severity"], i["code"], i["message"], i["equipment_id"], i["source_row_id"], i["region_id"], i["field_id"], i["detail_json"]) for i in issues],
        )
    return run_id, summary


def _numeric_stats(points: list[tuple[str, float]]) -> dict:
    if not points:
        return {"count": 0, "missing_note": "no_values"}
    values = [p[1] for p in points]
    count = len(values)
    zero_ratio = sum(1 for v in values if v == 0) / count
    unique = len(set(values))
    stats: dict = {
        "count": count,
        "min": min(values),
        "max": max(values),
        "mean": statistics.fmean(values),
        "zero_ratio": zero_ratio,
        "constant_column": unique <= 1,
    }
    if count > 1:
        stats["std"] = statistics.pstdev(values)
    sorted_vals = sorted(values)
    q1 = _quantile(sorted_vals, 0.25)
    q3 = _quantile(sorted_vals, 0.75)
    iqr = q3 - q1
    stats["q1"] = q1
    stats["q3"] = q3
    stats["iqr"] = iqr
    outliers: list[tuple[str, float]] = []
    if iqr > 0:
        low = q1 - 1.5 * iqr
        high = q3 + 1.5 * iqr
        for sid, val in points:
            if val < low or val > high:
                outliers.append((sid, val))
    stats["iqr_outlier_count"] = len(outliers)
    stats["iqr_outliers"] = outliers[:50]
    return stats


def _quantile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _gap_seconds(a: str, b: str) -> float | None:
    pa = _parse_iso(a)
    pb = _parse_iso(b)
    if pa is None or pb is None:
        return None
    return abs((pb - pa).total_seconds())


def _parse_iso(text: str) -> datetime | None:
    text = (text or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None
