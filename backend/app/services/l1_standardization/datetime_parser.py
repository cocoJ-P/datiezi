"""时间解析。无时区的采集时间保持无偏移，不用服务器时区填补。"""

from __future__ import annotations

import re
import zipfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from openpyxl.utils.datetime import MAC_EPOCH, WINDOWS_EPOCH, from_excel

_UNAMBIGUOUS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S.%f",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d",
)
_AMBIGUOUS_DATE = re.compile(r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?$")
_OFFSET = re.compile(r"^(?P<body>.*?)(?P<offset>Z|[+-]\d{2}:\d{2})$")
_DATE_FORMAT = re.compile(r"(yy|yyyy|dd|hh|ss|h:mm)", re.IGNORECASE)


def read_excel_date_system(path: Path) -> str:
    if path.suffix.lower() != ".xlsx":
        return "1900"
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("xl/workbook.xml")
    except (OSError, zipfile.BadZipFile, KeyError):
        return "1900"
    text = xml.decode("utf-8", errors="ignore")
    if re.search(r"date1904\s*=\s*['\"](?:1|true)['\"]", text, re.IGNORECASE):
        return "1904"
    return "1900"


def is_excel_date_format(number_format: str | None) -> bool:
    if not number_format or number_format == "General":
        return False
    return _DATE_FORMAT.search(number_format.split(";", 1)[0]) is not None


def parse_offset(value: str) -> timezone | None:
    text = value.strip()
    if text == "":
        return None
    if text in {"Z", "z"}:
        return timezone.utc
    match = re.fullmatch(r"([+-])(\d{2}):(\d{2})", text)
    if not match:
        return None
    sign = 1 if match.group(1) == "+" else -1
    hours = int(match.group(2))
    minutes = int(match.group(3))
    if hours > 14 or minutes > 59:
        return None
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def parse_datetime(
    raw: str,
    *,
    cell_type: str,
    number_format: str | None,
    formats: tuple[str, ...],
    missing_tokens: tuple[str, ...],
    source_timezone: str,
    target_timezone: str,
    excel_system: str,
) -> dict:
    if raw is None or raw == "":
        return _result("missing_empty", "", "", "")
    if raw.strip() == "":
        return _result("missing_blank", "", "", "")
    if raw.strip().casefold() in {token.casefold() for token in missing_tokens if token}:
        return _result("missing_marker", "", "", "")
    if cell_type == "date" or (cell_type == "number" and is_excel_date_format(number_format)):
        return _from_serial(raw.strip(), excel_system, source_timezone, target_timezone)
    return _from_text(raw.strip(), formats, source_timezone, target_timezone)


def _from_serial(raw: str, excel_system: str, source_timezone: str, target_timezone: str) -> dict:
    try:
        serial = Decimal(raw)
    except Exception:
        return _result("datetime_failed", "", "", "Excel 日期序列不是数字。")
    if excel_system == "1900" and serial == 60:
        return _result("datetime_excel_unreliable", "", "", "1900 日期系统把不存在的 1900-02-29 记为序列 60，不能可靠解析。")
    if serial <= 0:
        return _result("datetime_failed", "", "", "Excel 日期序列不在可解析范围内。")
    epoch = WINDOWS_EPOCH if excel_system != "1904" else MAC_EPOCH
    try:
        parsed = from_excel(float(serial), epoch=epoch)
    except (OverflowError, ValueError):
        return _result("datetime_failed", "", "", "Excel 日期序列不能转换成日期。")
    if not isinstance(parsed, datetime):
        return _result("datetime_failed", "", "", "这个值只有时间、没有日期，不能当作采集时间戳。")
    precision = "second" if parsed.second or parsed.microsecond else "minute" if parsed.hour or parsed.minute else "day"
    return _finalize(parsed.replace(tzinfo=None), precision, False, source_timezone, target_timezone)


def _from_text(raw: str, formats: tuple[str, ...], source_timezone: str, target_timezone: str) -> dict:
    offset = None
    body = raw
    if "T" in raw or raw.endswith("Z") or re.search(r"[+-]\d{2}:\d{2}$", raw):
        iso = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        try:
            parsed = datetime.fromisoformat(iso)
        except ValueError:
            parsed = None
        if parsed is not None:
            aware = parsed.tzinfo is not None
            precision = "second" if parsed.second or parsed.microsecond or "T" in raw else "day"
            return _finalize(parsed.replace(tzinfo=None), precision, aware, source_timezone if not aware else _format_offset(parsed), target_timezone, parsed.tzinfo)
    for fmt in (*formats, *_UNAMBIGUOUS):
        try:
            parsed = datetime.strptime(body, fmt)
        except ValueError:
            continue
        precision = "second" if "%S" in fmt else "minute" if "%M" in fmt else "day"
        return _finalize(parsed, precision, False, source_timezone, target_timezone)
    if _AMBIGUOUS_DATE.match(raw):
        return _result("datetime_ambiguous", "", "", "月和日的顺序不明确。请指定日期格式后再解析。")
    return _result("datetime_failed", "", "", "时间文本不符合已配置或无歧义的日期格式。")


def _finalize(parsed: datetime, precision: str, aware: bool, source_timezone: str, target_timezone: str, existing: timezone | None = None) -> dict:
    source = existing if aware and existing is not None else parse_offset(source_timezone)
    text = parsed.strftime("%Y-%m-%dT%H:%M:%S")
    if parsed.microsecond:
        text = parsed.strftime("%Y-%m-%dT%H:%M:%S.%f").rstrip("0")
    if source is None:
        message = "来源时区未确认，保留为无偏移的本地时间。"
        if target_timezone.strip():
            message = "来源时区未确认，没有换算到目标时区。"
        return _result("success", text, "unconfirmed", message, precision)
    aware_value = parsed.replace(tzinfo=source)
    target = parse_offset(target_timezone)
    if target is not None and target != source:
        aware_value = aware_value.astimezone(target)
    rendered = aware_value.isoformat()
    status_zone = "converted" if target is not None and target != source else "offset"
    return _result("success", rendered, status_zone, "", precision)


def _format_offset(value: datetime) -> str:
    if value.tzinfo is None:
        return ""
    offset = value.utcoffset() or timedelta(0)
    total = int(offset.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return f"{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"


def _result(status: str, text: str, zone: str, message: str, precision: str = "") -> dict:
    return {
        "status": status,
        "normalized_text": text,
        "timezone_status": zone,
        "precision": precision,
        "message": message,
        "rule_id": "DATETIME_PARSE",
    }
