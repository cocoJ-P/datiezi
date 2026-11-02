"""把 CSV 读成原始文本单元格。

不识别表头，不把字段转成数字，不把 0 当成缺失，也不丢弃列数不一致或空行。
引号无法闭合时整文件失败，由上层保留原件。
"""

from __future__ import annotations

from dataclasses import dataclass, field


class CsvStructureError(Exception):
    def __init__(self, message: str, source_line: int) -> None:
        super().__init__(message)
        self.message = message
        self.source_line = source_line


@dataclass
class CsvCell:
    column_number: int
    text: str


@dataclass
class CsvRow:
    row_number: int
    source_line_start: int
    source_line_end: int
    cells: list[CsvCell]
    parse_status: str


@dataclass
class CsvOutcome:
    status: str
    delimiter: str
    rows: list[CsvRow] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    error: dict | None = None
    max_column: int = 0


_CANDIDATES = (",", "\t", ";", "|")


def parse_csv_text(text: str, delimiter: str | None = None) -> CsvOutcome:
    if text == "":
        return _failed("empty_file", "文件为空，没有可读取的内容。", None, delimiter or ",")
    chosen = delimiter or _detect_delimiter(text)
    try:
        logical = _fast_rows(text, chosen) if '"' not in text else _slow_rows(text, chosen)
    except CsvStructureError as exc:
        return _failed("unmatched_quote", exc.message, exc.source_line, chosen)
    return _interpret(logical, chosen)


def _interpret(logical: list[tuple[int, int, list[str] | None]], delimiter: str) -> CsvOutcome:
    if not logical:
        return _failed("empty_file", "文件为空，没有可读取的内容。", None, delimiter)
    reference = 0
    for _start, _end, cells in logical:
        if cells:
            reference = len(cells)
            break
    rows: list[CsvRow] = []
    mismatch = 0
    blank = 0
    max_column = 0
    for index, (start, end, cells) in enumerate(logical, start=1):
        if not cells:
            blank += 1
            rows.append(CsvRow(index, start, end, [], "blank_line"))
            continue
        status = "ok"
        if reference and len(cells) != reference:
            status = "column_count_mismatch"
            mismatch += 1
        parsed = [CsvCell(position, value) for position, value in enumerate(cells, start=1)]
        max_column = max(max_column, len(parsed))
        rows.append(CsvRow(index, start, end, parsed, status))
    warnings: list[dict] = []
    if mismatch or blank:
        status = "parsed_with_errors"
        warnings.append(
            {
                "error_type": "abnormal_records",
                "message": f"异常记录 {mismatch + blank} 条（列数不一致 {mismatch}，空行 {blank}），已按原始位置保留。",
                "mismatch_count": mismatch,
                "blank_count": blank,
            }
        )
    else:
        status = "parsed"
    return CsvOutcome(status=status, delimiter=delimiter, rows=rows, warnings=warnings, max_column=max_column)


def _fast_rows(text: str, delimiter: str) -> list[tuple[int, int, list[str] | None]]:
    lines = text.splitlines()
    rows: list[tuple[int, int, list[str] | None]] = []
    for number, line in enumerate(lines, start=1):
        if line == "":
            rows.append((number, number, None))
        else:
            rows.append((number, number, line.split(delimiter)))
    return rows


def _slow_rows(text: str, delimiter: str) -> list[tuple[int, int, list[str] | None]]:
    rows: list[tuple[int, int, list[str] | None]] = []
    field: list[str] = []
    fields: list[str] = []
    line = 1
    start = 1
    index = 0
    in_quotes = False
    saw = False
    length = len(text)
    while index < length:
        char = text[index]
        if in_quotes:
            if char == '"':
                if index + 1 < length and text[index + 1] == '"':
                    field.append('"')
                    index += 2
                    saw = True
                    continue
                in_quotes = False
                index += 1
                continue
            field.append(char)
            if char == "\n":
                line += 1
            saw = True
            index += 1
            continue
        if char == '"':
            in_quotes = True
            saw = True
            index += 1
            continue
        if char == delimiter:
            fields.append("".join(field))
            field = []
            saw = True
            index += 1
            continue
        if char == "\r":
            if index + 1 < length and text[index + 1] == "\n":
                index += 1
            _emit(rows, fields, field, saw, start, line)
            field = []
            fields = []
            saw = False
            line += 1
            start = line
            index += 1
            continue
        if char == "\n":
            _emit(rows, fields, field, saw, start, line)
            field = []
            fields = []
            saw = False
            line += 1
            start = line
            index += 1
            continue
        field.append(char)
        saw = True
        index += 1
    if in_quotes:
        raise CsvStructureError(f"第 {start} 行的引号没有闭合，后续记录无法可靠分隔。", start)
    if saw or fields:
        _emit(rows, fields, field, saw, start, line)
    return rows


def _emit(
    rows: list[tuple[int, int, list[str] | None]],
    fields: list[str],
    field: list[str],
    saw: bool,
    start: int,
    end: int,
) -> None:
    if not saw and not fields:
        rows.append((start, end, None))
        return
    fields.append("".join(field))
    rows.append((start, end, fields))


def _detect_delimiter(text: str) -> str:
    sample = text[:8192]
    counts = {item: 0 for item in _CANDIDATES}
    in_quotes = False
    index = 0
    while index < len(sample):
        char = sample[index]
        if char == '"':
            if in_quotes and index + 1 < len(sample) and sample[index + 1] == '"':
                index += 2
                continue
            in_quotes = not in_quotes
        elif not in_quotes and char in counts and char != "\n":
            counts[char] += 1
        elif not in_quotes and char == "\n":
            break
        index += 1
    best = max(counts, key=lambda item: (counts[item], item == ","))
    return best if counts[best] else ","


def _failed(error_type: str, message: str, line: int | None, delimiter: str) -> CsvOutcome:
    location = None if line is None else {"source_line_start": line, "source_line_end": line}
    return CsvOutcome(
        status="failed",
        delimiter=delimiter,
        error={"error_type": error_type, "message": message, "location": location},
    )
