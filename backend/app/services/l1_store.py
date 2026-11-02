"""L1 P0：原始文件只读存档和单元格索引。

解析失败也会留下原件和失败状态。重复哈希返回已有数据集，不删除原记录。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.services.l1_csv import parse_csv_text
from app.services.l1_xlsx import read_xlsx

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_DIR = ROOT / "data" / "runtime" / "l1"
DEFAULT_MAX_BYTES = 100 * 1024 * 1024
TEMPLATE_ID = "steel_equipment_diagnosis_v1"
STAGES = (
    ("p0", "数据接入与原件存档", True),
    ("p1", "工作表与数据区域识别", True),
    ("p2", "设备分组与索引", True),
    ("p3", "字段语义与 Schema 映射", True),
    ("p4", "数据类型及时间标准化", True),
    ("p5", "数据质量与物理规则检测", True),
    ("p6", "数据清洗与修复", True),
    ("p7", "特征整理与标签隔离", True),
    ("p8", "验证、报告与数据发布", True),
)
_BLOCKED = (
    (ROOT / "data" / "official").resolve(),
    (ROOT / "data" / "official_dataset").resolve(),
    (ROOT / "data" / "processed").resolve(),
)
_NAME_CHARS = __import__("re").compile(r'[<>:"|?*\x00-\x1f]')
_DELIMITERS = {",": "comma", "\t": "tab", ";": "semicolon", "|": "pipe", "": ""}
_DELIMITER_INPUT = {
    "comma": ",",
    ",": ",",
    "tab": "\t",
    "\\t": "\t",
    "semicolon": ";",
    ";": ";",
    "pipe": "|",
    "|": "|",
}


class L1Error(Exception):
    def __init__(self, error_type: str, message: str, status_code: int = 400, location: dict | None = None) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.status_code = status_code
        self.location = location


@dataclass
class _Cell:
    column_number: int
    coordinate: str
    raw_value: str
    cell_type: str
    number_format: str | None
    formula: str | None
    cached_value: str | None
    merge_range: str | None


@dataclass
class _Row:
    row_number: int
    hidden: bool
    line_start: int | None
    line_end: int | None
    parse_status: str
    cells: list[_Cell]


@dataclass
class _Sheet:
    name: str
    visibility: str
    merges: list[str]
    max_column: int
    rows: list[_Row]


@dataclass
class _Inspected:
    file_type: str
    encoding: str
    delimiter: str
    status: str
    sheets: list[_Sheet]
    warnings: list[dict]
    error: dict | None


def max_upload_bytes() -> int:
    raw = os.environ.get("L1_MAX_UPLOAD_BYTES", "").strip()
    if not raw:
        return DEFAULT_MAX_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_BYTES
    return value if value > 0 else DEFAULT_MAX_BYTES


def data_dir() -> Path:
    override = os.environ.get("L1_DATA_DIR", "").strip()
    return _guard(Path(override) if override else DEFAULT_DATA_DIR)


def database_path() -> Path:
    override = os.environ.get("L1_DATABASE", "").strip()
    return _guard(Path(override) if override else data_dir() / "l1.sqlite3")


def stage_catalog(
    p0_status: str = "not_started",
    p1_status: str = "not_started",
    p2_status: str = "not_started",
    p3_status: str = "not_started",
    p3_revision: int | None = None,
    p4_status: str = "not_started",
    p4_revision: int | None = None,
    p4_stale: bool = False,
    p5_status: str = "not_started",
    p5_revision: int | None = None,
    p5_stale: bool = False,
    p6_status: str = "not_started",
    p6_revision: int | None = None,
    p6_stale: bool = False,
    p7_status: str = "not_started",
    p7_revision: int | None = None,
    p7_stale: bool = False,
    p8_status: str = "not_started",
    p8_revision: int | None = None,
    p8_stale: bool = False,
) -> list[dict]:
    labels = {
        "completed": "已完成",
        "completed_with_warnings": "已完成，有警告",
        "needs_review": "待审核",
        "partial": "部分完成",
        "failed": "失败",
        "not_started": "未执行",
        "stale": "已过期",
        "running": "执行中",
        "queued": "排队中",
        "ready": "可用",
        "ready_with_warnings": "可用（有警告）",
        "blocked": "已阻断",
    }
    items = []
    for stage_id, name, implemented in STAGES:
        if stage_id == "p0":
            status = p0_status if p0_status in labels else "not_started"
            label = labels[status]
        elif stage_id == "p1":
            status = p1_status if p1_status in labels else "not_started"
            label = labels[status]
        elif stage_id == "p2":
            status = p2_status if p2_status in labels else "not_started"
            label = labels[status]
        elif stage_id == "p3":
            status = p3_status if p3_status in labels else "not_started"
            label = labels[status]
        elif stage_id == "p4":
            status = "stale" if p4_stale and p4_status not in {"not_started", ""} else p4_status if p4_status in labels else "not_started"
            label = labels[status]
        elif stage_id == "p5":
            status = "stale" if p5_stale and p5_status not in {"not_started", ""} else p5_status if p5_status in labels else "not_started"
            label = labels[status]
        elif stage_id == "p6":
            status = "stale" if p6_stale and p6_status not in {"not_started", ""} else p6_status if p6_status in labels else "not_started"
            label = labels[status]
        elif stage_id == "p7":
            status = "stale" if p7_stale and p7_status not in {"not_started", ""} else p7_status if p7_status in labels else "not_started"
            label = labels[status]
        elif stage_id == "p8":
            status = "stale" if p8_stale and p8_status not in {"not_started", ""} else p8_status if p8_status in labels else "not_started"
            label = labels[status]
        else:
            status = "pending"
            label = "待开发"
        item = {
            "stage_id": stage_id,
            "name": name,
            "implemented": implemented,
            "status": status,
            "status_label": label,
        }
        if stage_id == "p4":
            item["depends_on"] = "p3"
            item["upstream_revision"] = p3_revision
            item["stale"] = p4_stale
        elif stage_id == "p5":
            item["depends_on"] = "p4"
            item["upstream_revision"] = p4_revision
            item["stale"] = p5_stale
        elif stage_id == "p6":
            item["depends_on"] = "p5"
            item["upstream_revision"] = p5_revision
            item["stale"] = p6_stale
        elif stage_id == "p7":
            item["depends_on"] = "p6"
            item["upstream_revision"] = p6_revision
            item["stale"] = p7_stale
        elif stage_id == "p8":
            item["depends_on"] = "p7"
            item["upstream_revision"] = p7_revision
            item["stale"] = p8_stale
        items.append(item)
    return items


def safe_original_name(filename: str) -> str:
    name = filename.replace("\\", "/").split("/")[-1].strip()
    name = _NAME_CHARS.sub("", name)
    if name in {"", ".", ".."} or name.startswith("."):
        raise L1Error("invalid_filename", "文件名无效。")
    lower = name.lower()
    if lower.endswith(".xls") and not lower.endswith(".xlsx"):
        raise L1Error("unsupported_xls", "暂不支持旧版 .xls，请另存为 .xlsx 后再上传。", 415)
    if not lower.endswith((".csv", ".xlsx")):
        raise L1Error("invalid_file_type", "只接受 .csv 或 .xlsx 文件。", 415)
    if len(name) > 180:
        raise L1Error("invalid_filename", "文件名过长。")
    return name


def resolve_encoding(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip().lower().replace("_", "-")
    if text in {"", "auto", "自动"}:
        return None
    allowed = {"utf-8", "utf-8-sig", "gb18030", "gbk"}
    if text not in allowed:
        raise L1Error("invalid_encoding", "编码只支持自动、UTF-8、UTF-8 BOM、GB18030 或 GBK。")
    return text


def resolve_delimiter(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip().lower()
    if text in {"", "auto", "自动"}:
        return None
    found = _DELIMITER_INPUT.get(text)
    if found is None:
        raise L1Error("invalid_delimiter", "分隔符只支持逗号、制表符、分号或竖线。")
    return found


def create_dataset(filename: str, payload: bytes, encoding: str | None = None, delimiter: str | None = None) -> tuple[dict, int]:
    if not isinstance(payload, bytes):
        raise L1Error("invalid_body", "上传内容必须是原始字节。")
    original = safe_original_name(filename)
    chosen_encoding = resolve_encoding(encoding)
    chosen_delimiter = resolve_delimiter(delimiter)
    if len(payload) > max_upload_bytes():
        raise L1Error("file_too_large", f"文件超过 {max_upload_bytes() // (1024 * 1024)}MB 上限。", 413)
    digest = hashlib.sha256(payload).hexdigest()
    existing = _dataset_by_hash(digest)
    if existing is not None:
        _restore_archived_bytes(existing, payload)
        _auto_regions(str(existing["dataset_id"]))
        body = _public_dataset(_load(str(existing["dataset_id"])))
        body["duplicate"] = True
        return body, 200

    dataset_id = "DS-" + uuid.uuid4().hex[:12]
    extension = ".xlsx" if original.lower().endswith(".xlsx") else ".csv"
    directory = data_dir() / "files"
    directory.mkdir(parents=True, exist_ok=True)
    temp_path = data_dir() / "tmp" / f"{dataset_id}.partial"
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    final_path = directory / f"{dataset_id}{extension}"
    _write_bytes(temp_path, payload)
    inspected = _inspect(payload, chosen_encoding, chosen_delimiter, original)
    connection = _connect()
    committed = False
    try:
        connection.execute("BEGIN IMMEDIATE")
        again = connection.execute("SELECT dataset_id FROM datasets WHERE sha256 = ?", (digest,)).fetchone()
        if again is not None:
            connection.rollback()
            _auto_regions(str(again["dataset_id"]))
            body = _public_dataset(_load(str(again["dataset_id"])))
            body["duplicate"] = True
            return body, 200
        _insert(connection, dataset_id, original, final_path.name, payload, digest, inspected)
        connection.execute("COMMIT")
        committed = True
        os.replace(temp_path, final_path)
    except L1Error:
        if connection.in_transaction:
            connection.rollback()
        raise
    except sqlite3.Error as exc:
        if connection.in_transaction:
            connection.rollback()
        raise L1Error("storage_failed", f"原始索引没有写入：{exc}", 500) from exc
    finally:
        connection.close()
        if not committed and temp_path.exists():
            temp_path.unlink()
    if inspected.status != "failed":
        _auto_regions(dataset_id)
    return _public_dataset(_load(dataset_id)), 201


def list_datasets() -> dict:
    connection = _connect()
    try:
        rows = connection.execute("SELECT * FROM datasets ORDER BY uploaded_at DESC, dataset_id DESC").fetchall()
    finally:
        connection.close()
    return {"datasets": [_summary(row) for row in rows]}


def get_dataset(dataset_id: str) -> dict:
    return _public_dataset(_require(dataset_id))


def list_sheets(dataset_id: str) -> dict:
    _require(dataset_id)
    connection = _connect()
    try:
        sheets = connection.execute(
            "SELECT * FROM sheets WHERE dataset_id = ? ORDER BY sheet_index",
            (dataset_id,),
        ).fetchall()
    finally:
        connection.close()
    return {"dataset_id": dataset_id, "sheets": [_public_sheet(item) for item in sheets]}


def list_raw_rows(
    dataset_id: str,
    sheet_id: str | None,
    offset: int,
    limit: int,
    row_number: int | None,
) -> dict:
    if offset < 0:
        raise L1Error("invalid_offset", "offset 必须是大于等于 0 的整数。", 422)
    if limit < 1 or limit > 200:
        raise L1Error("invalid_limit", "limit 必须是 1 到 200 的整数。", 422)
    if row_number is not None and row_number < 1:
        raise L1Error("invalid_row", "原始行号必须从 1 开始。", 422)
    row = _require(dataset_id)
    _require_indexed(row)
    connection = _connect()
    try:
        sheet = _resolve_sheet(connection, dataset_id, sheet_id)
        total = connection.execute(
            "SELECT COUNT(*) FROM source_rows WHERE dataset_id = ? AND sheet_id = ?",
            (dataset_id, sheet["sheet_id"]),
        ).fetchone()[0]
        if row_number is not None:
            found = connection.execute(
                """
                SELECT COUNT(*) FROM source_rows
                WHERE dataset_id = ? AND sheet_id = ? AND row_number <= ?
                """,
                (dataset_id, sheet["sheet_id"], row_number),
            ).fetchone()[0]
            exists = connection.execute(
                """
                SELECT 1 FROM source_rows
                WHERE dataset_id = ? AND sheet_id = ? AND row_number = ?
                """,
                (dataset_id, sheet["sheet_id"], row_number),
            ).fetchone()
            if exists is None:
                raise L1Error("row_not_found", "当前工作表没有这一行。", 404)
            offset = ((int(found) - 1) // limit) * limit
        rows = connection.execute(
            """
            SELECT * FROM source_rows
            WHERE dataset_id = ? AND sheet_id = ?
            ORDER BY row_number
            LIMIT ? OFFSET ?
            """,
            (dataset_id, sheet["sheet_id"], limit, offset),
        ).fetchall()
        cells = []
        if rows:
            cells = connection.execute(
                """
                SELECT * FROM raw_cells
                WHERE dataset_id = ? AND sheet_id = ? AND row_number >= ? AND row_number <= ?
                ORDER BY row_number, column_number
                """,
                (dataset_id, sheet["sheet_id"], rows[0]["row_number"], rows[-1]["row_number"]),
            ).fetchall()
    finally:
        connection.close()
    grouped: dict[int, list] = {}
    for cell in cells:
        grouped.setdefault(int(cell["row_number"]), []).append(_public_cell(cell))
    return {
        "dataset_id": dataset_id,
        "sheet_id": sheet["sheet_id"],
        "sheet_name": sheet["name"],
        "total_rows": total,
        "max_column": int(sheet["max_column"]),
        "offset": offset,
        "limit": limit,
        "row_number": row_number,
        "rows": [
            {
                "source_row_id": item["source_row_id"],
                "row_number": int(item["row_number"]),
                "hidden": bool(item["hidden"]),
                "parse_status": item["parse_status"],
                "source_line_start": item["source_line_start"],
                "source_line_end": item["source_line_end"],
                "cells": grouped.get(int(item["row_number"]), []),
            }
            for item in rows
        ],
    }


def get_raw_cell(dataset_id: str, cell_id: str) -> dict:
    row = _require(dataset_id)
    _require_indexed(row)
    connection = _connect()
    try:
        found = connection.execute(
            """
            SELECT raw_cells.*, sheets.name AS sheet_name, sheets.visibility AS sheet_visibility
            FROM raw_cells
            JOIN sheets ON sheets.dataset_id = raw_cells.dataset_id AND sheets.sheet_id = raw_cells.sheet_id
            WHERE raw_cells.dataset_id = ? AND raw_cells.cell_id = ?
            """,
            (dataset_id, cell_id),
        ).fetchone()
        source = None
        if found is not None:
            source = connection.execute(
                "SELECT * FROM source_rows WHERE dataset_id = ? AND source_row_id = ?",
                (dataset_id, found["source_row_id"]),
            ).fetchone()
    finally:
        connection.close()
    if found is None or source is None:
        raise L1Error("cell_not_found", "没有这个原始单元格。", 404)
    body = _public_cell(found)
    body.update(
        {
            "dataset_id": dataset_id,
            "original_filename": row["original_filename"],
            "sheet_name": found["sheet_name"],
            "sheet_visibility": found["sheet_visibility"],
            "source_row_id": source["source_row_id"],
            "row_number": int(source["row_number"]),
            "hidden": bool(source["hidden"]),
            "source_line_start": source["source_line_start"],
            "source_line_end": source["source_line_end"],
            "row_parse_status": source["parse_status"],
        }
    )
    return body


def raw_file(dataset_id: str) -> tuple[Path, str]:
    row = _require(dataset_id)
    path = _file_path(row)
    if not path.is_file():
        raise L1Error("storage_inconsistent", "索引中的原始文件不存在。", 409)
    return path, str(row["original_filename"])


def _inspect(payload: bytes, encoding: str | None, delimiter: str | None, filename: str) -> _Inspected:
    if filename.lower().endswith(".xlsx"):
        outcome = read_xlsx(payload)
        sheets = [
            _Sheet(
                name=item.name,
                visibility=item.visibility,
                merges=item.merges,
                max_column=item.max_column,
                rows=[
                    _Row(
                        row_number=row.row_number,
                        hidden=row.hidden,
                        line_start=row.row_number,
                        line_end=row.row_number,
                        parse_status=row.parse_status,
                        cells=[
                            _Cell(
                                column_number=cell.column_number,
                                coordinate=cell.coordinate,
                                raw_value=cell.raw_value,
                                cell_type=cell.cell_type,
                                number_format=cell.number_format,
                                formula=cell.formula,
                                cached_value=cell.cached_value,
                                merge_range=cell.merge_range,
                            )
                            for cell in row.cells
                        ],
                    )
                    for row in item.rows
                ],
            )
            for item in outcome.sheets
        ]
        return _Inspected("xlsx", "xlsx", "", outcome.status, sheets, outcome.warnings, outcome.error)
    if len(payload) == 0:
        return _Inspected("csv", "", delimiter or ",", "failed", [], [], _error_body("empty_file", "文件为空，没有可读取的内容。", None))
    if payload.startswith((b"PK\x03\x04", b"\xd0\xcf\x11\xe0")) or b"\x00" in payload[:4096]:
        return _Inspected(
            "csv",
            "",
            delimiter or ",",
            "failed",
            [],
            [],
            _error_body("illegal_format", "文件内容不是 CSV 文本。请上传文本 CSV，而不是 Excel 或二进制文件。", None),
        )
    try:
        text, used = _decode(payload, encoding)
    except L1Error as exc:
        return _Inspected("csv", "", delimiter or ",", "failed", [], [], _error_body(exc.error_type, exc.message, exc.location))
    parsed = parse_csv_text(text, delimiter)
    max_column = parsed.max_column
    rows: list[_Row] = []
    for item in parsed.rows:
        cells = [
            _Cell(
                column_number=cell.column_number,
                coordinate=f"{_column_letter(cell.column_number)}{item.row_number}",
                raw_value=cell.text,
                cell_type="text" if cell.text != "" else "empty",
                number_format=None,
                formula=None,
                cached_value=None,
                merge_range=None,
            )
            for cell in item.cells
        ]
        rows.append(
            _Row(
                row_number=item.row_number,
                hidden=False,
                line_start=item.source_line_start,
                line_end=item.source_line_end,
                parse_status=item.parse_status,
                cells=cells,
            )
        )
    sheets = []
    if parsed.status != "failed":
        sheets.append(_Sheet("CSV", "visible", [], max_column, rows))
    return _Inspected("csv", used, parsed.delimiter, parsed.status, sheets, parsed.warnings, parsed.error)


def _insert(
    connection: sqlite3.Connection,
    dataset_id: str,
    original: str,
    stored_name: str,
    payload: bytes,
    digest: str,
    inspected: _Inspected,
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    row_count = sum(len(sheet.rows) for sheet in inspected.sheets)
    column_count = max((sheet.max_column for sheet in inspected.sheets), default=0)
    cell_count = sum(len(row.cells) for sheet in inspected.sheets for row in sheet.rows)
    p0_status = "failed" if inspected.status == "failed" else "completed"
    connection.execute(
        """
        INSERT INTO datasets (
            dataset_id, original_filename, stored_name, file_type, size_bytes, sha256, uploaded_at,
            encoding, delimiter, parse_status, template_id, p0_status, sheet_count, row_count,
            column_count, cell_count, warning_json, error_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            dataset_id,
            original,
            stored_name,
            inspected.file_type,
            len(payload),
            digest,
            now,
            inspected.encoding,
            _DELIMITERS.get(inspected.delimiter, inspected.delimiter),
            inspected.status,
            TEMPLATE_ID,
            p0_status,
            len(inspected.sheets),
            row_count,
            column_count,
            cell_count,
            json.dumps(inspected.warnings, ensure_ascii=False, separators=(",", ":")),
            json.dumps(inspected.error, ensure_ascii=False, separators=(",", ":")) if inspected.error else None,
        ),
    )
    for index, sheet in enumerate(inspected.sheets, start=1):
        sheet_id = f"{dataset_id}-S{index:02d}"
        hidden_count = sum(1 for row in sheet.rows if row.hidden)
        connection.execute(
            """
            INSERT INTO sheets (
                dataset_id, sheet_id, sheet_index, name, visibility, max_row, max_column,
                cell_count, hidden_row_count, merge_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dataset_id,
                sheet_id,
                index - 1,
                sheet.name,
                sheet.visibility,
                sheet.rows[-1].row_number if sheet.rows else 0,
                sheet.max_column,
                sum(len(row.cells) for row in sheet.rows),
                hidden_count,
                json.dumps(sheet.merges, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        row_records = []
        cell_records = []
        for item in sheet.rows:
            source_row_id = f"{sheet_id}-R{item.row_number:08d}"
            row_records.append(
                (
                    dataset_id,
                    sheet_id,
                    source_row_id,
                    item.row_number,
                    item.line_start,
                    item.line_end,
                    1 if item.hidden else 0,
                    item.parse_status,
                    len(item.cells),
                )
            )
            for cell in item.cells:
                cell_records.append(
                    (
                        dataset_id,
                        f"{source_row_id}C{cell.column_number:04d}",
                        sheet_id,
                        source_row_id,
                        item.row_number,
                        cell.column_number,
                        f"C{cell.column_number:04d}",
                        cell.coordinate,
                        cell.raw_value,
                        cell.cell_type,
                        cell.number_format,
                        cell.formula,
                        cell.cached_value,
                        cell.merge_range,
                        "ok",
                    )
                )
        connection.executemany(
            """
            INSERT INTO source_rows (
                dataset_id, sheet_id, source_row_id, row_number, source_line_start, source_line_end,
                hidden, parse_status, cell_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            row_records,
        )
        if cell_records:
            connection.executemany(
                """
                INSERT INTO raw_cells (
                    dataset_id, cell_id, sheet_id, source_row_id, row_number, column_number, column_id,
                    coordinate, raw_value, cell_type, number_format, formula, cached_value, merge_range, parse_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                cell_records,
            )


def _resolve_sheet(connection: sqlite3.Connection, dataset_id: str, sheet_id: str | None) -> sqlite3.Row:
    if sheet_id:
        found = connection.execute(
            "SELECT * FROM sheets WHERE dataset_id = ? AND sheet_id = ?",
            (dataset_id, sheet_id),
        ).fetchone()
        if found is None:
            raise L1Error("sheet_not_found", "没有这个工作表。", 404)
        return found
    found = connection.execute(
        "SELECT * FROM sheets WHERE dataset_id = ? ORDER BY sheet_index LIMIT 1",
        (dataset_id,),
    ).fetchone()
    if found is None:
        raise L1Error("sheet_not_found", "这个数据集没有可读取的工作表。", 404)
    return found


def _require_indexed(row: sqlite3.Row) -> None:
    if row["parse_status"] == "failed":
        message = "文件没有完成解析，不能当作完整数据浏览。原始文件仍可下载。"
        error = _json_or_none(row["error_json"])
        if error and error.get("message"):
            message = str(error["message"]) + " 原始文件仍可下载。"
        raise L1Error("parse_failed", message, 409)


def _public_dataset(row: sqlite3.Row) -> dict:
    path = _file_path(row)
    consistent = path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]
    return {
        "dataset_id": row["dataset_id"],
        "original_filename": row["original_filename"],
        "file_type": row["file_type"],
        "size_bytes": int(row["size_bytes"]),
        "sha256": row["sha256"],
        "uploaded_at": row["uploaded_at"],
        "encoding": row["encoding"],
        "delimiter": row["delimiter"],
        "parse_status": row["parse_status"],
        "template_id": row["template_id"],
        "p0_status": row["p0_status"],
        "sheet_count": int(row["sheet_count"]),
        "row_count": int(row["row_count"]),
        "column_count": int(row["column_count"]),
        "cell_count": int(row["cell_count"]),
        "warnings": _json_list(row["warning_json"]),
        "error": _json_or_none(row["error_json"]),
        "stages": _dataset_stages(str(row["dataset_id"]), str(row["p0_status"])),
        "storage_consistent": consistent,
        "file_url": f"/api/l1/datasets/{row['dataset_id']}/file",
    }


def _dataset_stages(dataset_id: str, p0_status: str) -> list[dict]:
    from app.services.l1_prepare.service import p5_status, p6_status, p7_status, p8_status, prepare_stale

    p3, p3_rev = _p3_status(dataset_id)
    p4, p4_rev, p4_stale = _p4_status(dataset_id)
    p5, p5_rev, p5_st = p5_status(dataset_id)
    p6, p6_rev, p6_st = p6_status(dataset_id)
    p7, p7_rev, p7_st = p7_status(dataset_id)
    p8, p8_rev, p8_st = p8_status(dataset_id)
    prep_stale = prepare_stale(dataset_id)
    return stage_catalog(
        p0_status,
        _p1_status(dataset_id),
        _p2_status(dataset_id),
        p3,
        p3_rev,
        p4,
        p4_rev,
        p4_stale,
        p5,
        p5_rev,
        prep_stale or p5_st,
        p6,
        p6_rev,
        p6_st,
        p7,
        p7_rev,
        p7_st,
        p8,
        p8_rev,
        p8_st,
    )


def _summary(row: sqlite3.Row) -> dict:
    return {
        "dataset_id": row["dataset_id"],
        "original_filename": row["original_filename"],
        "file_type": row["file_type"],
        "size_bytes": int(row["size_bytes"]),
        "sha256": row["sha256"],
        "uploaded_at": row["uploaded_at"],
        "parse_status": row["parse_status"],
        "p0_status": row["p0_status"],
        "sheet_count": int(row["sheet_count"]),
        "row_count": int(row["row_count"]),
        "column_count": int(row["column_count"]),
        "template_id": row["template_id"],
    }


def _public_sheet(row: sqlite3.Row) -> dict:
    return {
        "sheet_id": row["sheet_id"],
        "sheet_index": int(row["sheet_index"]),
        "name": row["name"],
        "visibility": row["visibility"],
        "max_row": int(row["max_row"]),
        "max_column": int(row["max_column"]),
        "cell_count": int(row["cell_count"]),
        "hidden_row_count": int(row["hidden_row_count"]),
        "merges": _json_list(row["merge_json"]),
    }


def _public_cell(row: sqlite3.Row) -> dict:
    return {
        "cell_id": row["cell_id"],
        "column_number": int(row["column_number"]),
        "column_id": row["column_id"],
        "coordinate": row["coordinate"],
        "raw_value": row["raw_value"],
        "cell_type": row["cell_type"],
        "number_format": row["number_format"],
        "formula": row["formula"],
        "cached_value": row["cached_value"],
        "merge_range": row["merge_range"],
        "parse_status": row["parse_status"],
    }


def _dataset_by_hash(digest: str) -> sqlite3.Row | None:
    connection = _connect()
    try:
        return connection.execute("SELECT * FROM datasets WHERE sha256 = ?", (digest,)).fetchone()
    finally:
        connection.close()


def _require(dataset_id: str) -> sqlite3.Row:
    if not dataset_id.startswith("DS-") or len(dataset_id) != 15:
        raise L1Error("dataset_not_found", "没有这个数据集。", 404)
    row = _load(dataset_id)
    if row is None:
        raise L1Error("dataset_not_found", "没有这个数据集。", 404)
    return row


def _load(dataset_id: str) -> sqlite3.Row | None:
    connection = _connect()
    try:
        return connection.execute("SELECT * FROM datasets WHERE dataset_id = ?", (dataset_id,)).fetchone()
    finally:
        connection.close()


def _restore_archived_bytes(row: sqlite3.Row, payload: bytes) -> None:
    path = _file_path(row)
    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_bytes(path, payload)


def _file_path(row: sqlite3.Row) -> Path:
    return data_dir() / "files" / str(row["stored_name"])


def _decode(payload: bytes, encoding: str | None) -> tuple[str, str]:
    if encoding:
        try:
            return payload.decode(encoding), encoding
        except UnicodeDecodeError as exc:
            raise L1Error("encoding_error", f"无法用 {encoding} 解码这个文件。", 422) from exc
    for name in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return payload.decode(name), name
        except UnicodeDecodeError:
            continue
    raise L1Error("encoding_error", "无法识别文件编码。可以指定 UTF-8 或 GB18030。", 422)


def _error_body(error_type: str, message: str, location: dict | None) -> dict:
    return {"error_type": error_type, "message": message, "location": location}


def _json_list(value: str | None) -> list:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _json_or_none(value: str | None) -> dict | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _auto_regions(dataset_id: str) -> None:
    from app.services.l1_regions import auto_apply_regions
    from app.services.l1_grouping import auto_apply_grouping

    auto_apply_regions(dataset_id)
    auto_apply_grouping(dataset_id)


def _p1_status(dataset_id: str) -> str:
    connection = _connect()
    try:
        found = connection.execute(
            "SELECT status FROM p1_runs WHERE dataset_id = ? ORDER BY revision DESC LIMIT 1",
            (dataset_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return "not_started"
    finally:
        connection.close()
    if found is None or found["status"] not in {"completed", "completed_with_warnings", "needs_review", "failed"}:
        return "not_started"
    return str(found["status"])


def _p3_status(dataset_id: str) -> tuple[str, int | None]:
    connection = _connect()
    try:
        found = connection.execute(
            "SELECT status, revision FROM p3_runs WHERE dataset_id = ? ORDER BY revision DESC LIMIT 1",
            (dataset_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return "not_started", None
    finally:
        connection.close()
    if found is None or found["status"] not in {"completed", "failed"}:
        return "not_started", None
    return str(found["status"]), int(found["revision"])


def _p4_status(dataset_id: str) -> tuple[str, int | None, bool]:
    connection = _connect()
    try:
        found = connection.execute(
            "SELECT status, revision, p3_run_id FROM p4_runs WHERE dataset_id = ? ORDER BY revision DESC LIMIT 1",
            (dataset_id,),
        ).fetchone()
        latest_p3 = connection.execute(
            "SELECT run_id FROM p3_runs WHERE dataset_id = ? ORDER BY revision DESC LIMIT 1",
            (dataset_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return "not_started", None, False
    finally:
        connection.close()
    if found is None or found["status"] not in {"completed", "partial", "failed"}:
        return "not_started", None, False
    stale = latest_p3 is not None and str(latest_p3["run_id"]) != str(found["p3_run_id"])
    return str(found["status"]), int(found["revision"]), stale


def _p2_status(dataset_id: str) -> str:
    connection = _connect()
    try:
        found = connection.execute(
            "SELECT status FROM p2_runs WHERE dataset_id = ? ORDER BY revision DESC LIMIT 1",
            (dataset_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return "not_started"
    finally:
        connection.close()
    if found is None:
        return "not_started"
    status = str(found["status"])
    if status in {"completed", "completed_with_warnings", "needs_review", "failed"}:
        return status
    return "not_started"


def _column_letter(column: int) -> str:
    letters = ""
    number = column
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _guard(path: Path) -> Path:
    resolved = path.resolve()
    for blocked in _BLOCKED:
        if resolved == blocked or blocked in resolved.parents:
            raise L1Error("storage_failed", "原始数据不能写入官方资料或既有处理结果目录。", 500)
    return resolved


def _connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS datasets (
            dataset_id TEXT PRIMARY KEY,
            original_filename TEXT NOT NULL,
            stored_name TEXT NOT NULL,
            file_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL UNIQUE,
            uploaded_at TEXT NOT NULL,
            encoding TEXT NOT NULL,
            delimiter TEXT NOT NULL,
            parse_status TEXT NOT NULL,
            template_id TEXT NOT NULL,
            p0_status TEXT NOT NULL,
            sheet_count INTEGER NOT NULL,
            row_count INTEGER NOT NULL,
            column_count INTEGER NOT NULL,
            cell_count INTEGER NOT NULL,
            warning_json TEXT NOT NULL,
            error_json TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sheets (
            dataset_id TEXT NOT NULL,
            sheet_id TEXT NOT NULL,
            sheet_index INTEGER NOT NULL,
            name TEXT NOT NULL,
            visibility TEXT NOT NULL,
            max_row INTEGER NOT NULL,
            max_column INTEGER NOT NULL,
            cell_count INTEGER NOT NULL,
            hidden_row_count INTEGER NOT NULL,
            merge_json TEXT NOT NULL,
            PRIMARY KEY (dataset_id, sheet_id),
            FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS source_rows (
            dataset_id TEXT NOT NULL,
            sheet_id TEXT NOT NULL,
            source_row_id TEXT NOT NULL,
            row_number INTEGER NOT NULL,
            source_line_start INTEGER,
            source_line_end INTEGER,
            hidden INTEGER NOT NULL,
            parse_status TEXT NOT NULL,
            cell_count INTEGER NOT NULL,
            PRIMARY KEY (dataset_id, source_row_id),
            UNIQUE (dataset_id, sheet_id, row_number),
            FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS raw_cells (
            dataset_id TEXT NOT NULL,
            cell_id TEXT NOT NULL,
            sheet_id TEXT NOT NULL,
            source_row_id TEXT NOT NULL,
            row_number INTEGER NOT NULL,
            column_number INTEGER NOT NULL,
            column_id TEXT NOT NULL,
            coordinate TEXT NOT NULL,
            raw_value TEXT NOT NULL,
            cell_type TEXT NOT NULL,
            number_format TEXT,
            formula TEXT,
            cached_value TEXT,
            merge_range TEXT,
            parse_status TEXT NOT NULL,
            PRIMARY KEY (dataset_id, cell_id),
            FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id)
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_l1_rows_order ON source_rows(dataset_id, sheet_id, row_number)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_l1_cells_order ON raw_cells(dataset_id, sheet_id, row_number, column_number)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_l1_cells_source ON raw_cells(dataset_id, source_row_id, column_number)")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS p0_sheet_occupancy (
            dataset_id TEXT NOT NULL,
            sheet_id TEXT NOT NULL,
            nonempty_cells INTEGER NOT NULL,
            occupancy_json TEXT NOT NULL,
            PRIMARY KEY (dataset_id, sheet_id),
            FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS p1_runs (
            run_id TEXT PRIMARY KEY,
            dataset_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            rule_id TEXT NOT NULL,
            p0_sha256 TEXT NOT NULL,
            config_json TEXT NOT NULL,
            region_count INTEGER NOT NULL,
            unclassified_cell_count INTEGER NOT NULL,
            record_count INTEGER NOT NULL,
            UNIQUE (dataset_id, revision),
            FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS p1_regions (
            region_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            dataset_id TEXT NOT NULL,
            client_region_id TEXT NOT NULL,
            sheet_id TEXT NOT NULL,
            sheet_enabled INTEGER NOT NULL,
            role TEXT NOT NULL,
            start_row INTEGER NOT NULL,
            end_row INTEGER NOT NULL,
            start_column INTEGER NOT NULL,
            end_column INTEGER NOT NULL,
            header_start_row INTEGER,
            header_row_count INTEGER NOT NULL,
            data_start_row INTEGER,
            data_end_row INTEGER,
            detection_method TEXT NOT NULL,
            confidence TEXT NOT NULL,
            confirmation_status TEXT NOT NULL,
            record_count INTEGER NOT NULL,
            column_json TEXT NOT NULL,
            warning_json TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            FOREIGN KEY (run_id) REFERENCES p1_runs(run_id),
            FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id)
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_p1_regions_run ON p1_regions(run_id, ordinal)")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS p2_runs (
            run_id TEXT PRIMARY KEY,
            dataset_id TEXT NOT NULL,
            p1_run_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            rule_id TEXT NOT NULL,
            p0_sha256 TEXT NOT NULL,
            config_json TEXT NOT NULL,
            equipment_count INTEGER NOT NULL,
            grouped_count INTEGER NOT NULL,
            ungrouped_count INTEGER NOT NULL,
            business_count INTEGER NOT NULL,
            sheet_count INTEGER NOT NULL,
            balance_ok INTEGER NOT NULL,
            UNIQUE (dataset_id, revision),
            FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS p2_groups (
            group_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            dataset_id TEXT NOT NULL,
            equipment_id TEXT NOT NULL,
            match_key TEXT NOT NULL,
            scope TEXT NOT NULL,
            sheet_id TEXT,
            asset_id TEXT,
            record_count INTEGER NOT NULL,
            original_values_json TEXT NOT NULL,
            label TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES p2_runs(run_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS p2_membership (
            run_id TEXT NOT NULL,
            dataset_id TEXT NOT NULL,
            source_row_id TEXT NOT NULL,
            region_id TEXT NOT NULL,
            sheet_id TEXT NOT NULL,
            row_number INTEGER NOT NULL,
            group_id TEXT,
            membership_status TEXT NOT NULL,
            ungroup_reason TEXT NOT NULL,
            original_equipment_value TEXT NOT NULL,
            grouping_value TEXT NOT NULL,
            match_key TEXT NOT NULL,
            column_id TEXT NOT NULL,
            field_header TEXT NOT NULL,
            equipment_cell_id TEXT,
            coordinate TEXT NOT NULL,
            override_applied INTEGER NOT NULL,
            asset_id TEXT,
            PRIMARY KEY (run_id, source_row_id),
            FOREIGN KEY (run_id) REFERENCES p2_runs(run_id)
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_p2_groups_run ON p2_groups(run_id, label)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_p2_membership_group ON p2_membership(run_id, group_id, sheet_id, row_number)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_p2_membership_status ON p2_membership(run_id, membership_status, row_number)")
    from app.services.l1_schema.repository import ensure_tables
    from app.services.l1_standardization.repository import ensure_tables as ensure_p4_tables
    from app.services.l1_prepare.repository import ensure_tables as ensure_prepare_tables

    ensure_tables(connection)
    ensure_p4_tables(connection)
    ensure_prepare_tables(connection)
    return connection
