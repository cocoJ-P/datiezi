"""用 openpyxl 读取 xlsx 的工作表结构，并保留单元格存储文本。

不计算公式，不把数字格式换算成显示值。合并区域、隐藏行和公式缓存来自工作表 XML，
因为 openpyxl 的只读模式不提供这些结构。P0 不判断表头，也不做设备分组。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from openpyxl.utils.cell import coordinate_from_string, column_index_from_string


class XlsxStructureError(Exception):
    def __init__(self, message: str, error_type: str = "illegal_format") -> None:
        super().__init__(message)
        self.message = message
        self.error_type = error_type


@dataclass
class XlsxCell:
    column_number: int
    coordinate: str
    raw_value: str
    cell_type: str
    number_format: str | None
    formula: str | None
    cached_value: str | None
    merge_range: str | None


@dataclass
class XlsxRow:
    row_number: int
    hidden: bool
    cells: list[XlsxCell]
    parse_status: str = "ok"


@dataclass
class XlsxSheet:
    name: str
    visibility: str
    rows: list[XlsxRow]
    merges: list[str]
    max_column: int


@dataclass
class XlsxOutcome:
    status: str
    sheets: list[XlsxSheet] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    error: dict | None = None


_MAX_ROWS = 200_000
_MAX_COLUMNS = 512
_VISIBILITY = {"visible": "visible", "hidden": "hidden", "veryHidden": "veryHidden"}


def read_xlsx(payload: bytes) -> XlsxOutcome:
    try:
        _guard_zip(payload)
        workbook = load_workbook(BytesIO(payload), read_only=True, data_only=False, keep_links=False)
    except XlsxStructureError as exc:
        return _failed(exc.message, exc.error_type)
    except Exception as exc:
        return _failed(f"xlsx 无法打开：{exc}")
    try:
        archive = ZipFile(BytesIO(payload))
    except BadZipFile:
        workbook.close()
        return _failed("xlsx 文件已损坏，无法打开工作簿。")
    sheets: list[XlsxSheet] = []
    try:
        for worksheet in workbook.worksheets:
            sheets.append(_read_sheet(worksheet, archive))
    except XlsxStructureError as exc:
        return _failed(exc.message, exc.error_type)
    finally:
        archive.close()
        workbook.close()
    if not sheets:
        return _failed("工作簿里没有工作表。")
    warnings = []
    if len(sheets) > 1:
        warnings.append(
            {
                "error_type": "workbook_sheets",
                "message": f"工作簿有 {len(sheets)} 个工作表，已按原始顺序保留。",
                "sheets": [item.name for item in sheets],
            }
        )
    return XlsxOutcome(status="parsed", sheets=sheets, warnings=warnings)


def _read_sheet(worksheet, archive: ZipFile) -> XlsxSheet:
    hidden, merges, numeric_text, formula_cache = _sheet_xml(archive, worksheet._worksheet_path)
    merge_of = _merge_index(merges)
    grouped: dict[int, list[XlsxCell]] = {}
    max_column = 0
    for row in worksheet.iter_rows():
        for cell in row:
            if cell.value is None and cell.data_type != "f":
                continue
            column = cell.column
            if column > _MAX_COLUMNS:
                raise XlsxStructureError(
                    f"工作表「{worksheet.title}」超过 {_MAX_COLUMNS} 列，P0 停止索引并保留原件。",
                    "index_limit",
                )
            coordinate = cell.coordinate
            formula, cached, raw_value, cell_type = _value(cell, numeric_text, formula_cache)
            grouped.setdefault(cell.row, []).append(
                XlsxCell(
                    column_number=column,
                    coordinate=coordinate,
                    raw_value=raw_value,
                    cell_type=cell_type,
                    number_format=cell.number_format or None,
                    formula=formula,
                    cached_value=cached,
                    merge_range=merge_of.get(coordinate),
                )
            )
            max_column = max(max_column, column)
    max_row = max(grouped) if grouped else 0
    for number in hidden:
        if number > max_row:
            max_row = number
    if max_row > _MAX_ROWS:
        raise XlsxStructureError(
            f"工作表「{worksheet.title}」超过 {_MAX_ROWS} 行，P0 停止索引并保留原件。",
            "index_limit",
        )
    rows = [
        XlsxRow(
            row_number=number,
            hidden=number in hidden,
            cells=sorted(grouped.get(number, []), key=lambda item: item.column_number),
            parse_status="ok" if grouped.get(number) else "blank_line",
        )
        for number in range(1, max_row + 1)
    ]
    return XlsxSheet(
        name=worksheet.title,
        visibility=_VISIBILITY.get(worksheet.sheet_state, worksheet.sheet_state),
        rows=rows,
        merges=merges,
        max_column=max_column,
    )


def _value(cell, numeric_text: dict[str, str], formula_cache: dict[str, str | None]) -> tuple[str | None, str | None, str, str]:
    if cell.data_type == "f":
        formula = cell.value if isinstance(cell.value, str) else ""
        if formula and not formula.startswith("="):
            formula = "=" + formula
        cached = formula_cache.get(cell.coordinate)
        return formula, cached, formula, "formula"
    if cell.data_type == "b":
        return None, None, "1" if cell.value else "0", "boolean"
    if cell.data_type == "e":
        return None, None, "" if cell.value is None else str(cell.value), "error"
    if cell.data_type in {"n", "d"}:
        stored = numeric_text.get(cell.coordinate)
        if stored is None:
            stored = _plain(cell.value)
        return None, None, stored, "date" if cell.data_type == "d" else "number"
    return None, None, "" if cell.value is None else str(cell.value), "text"


def _plain(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float) and value.is_integer() and abs(value) < 10**15:
        return str(int(value))
    return str(value)


def _sheet_xml(archive: ZipFile, part: str) -> tuple[set[int], list[str], dict[str, str], dict[str, str | None]]:
    hidden: set[int] = set()
    merges: list[str] = []
    numeric_text: dict[str, str] = {}
    formula_cache: dict[str, str | None] = {}
    try:
        handle = archive.open(part)
    except KeyError as exc:
        raise XlsxStructureError("xlsx 缺少工作表文件。") from exc
    with handle:
        for _event, element in ElementTree.iterparse(handle, events=("end",)):
            name = element.tag.rsplit("}", 1)[-1]
            if name == "row":
                if element.get("hidden") in {"1", "true"}:
                    row_number = element.get("r")
                    if row_number and row_number.isdigit():
                        hidden.add(int(row_number))
                element.clear()
            elif name == "c":
                ref = element.get("r") or ""
                kind = element.get("t")
                stored = None
                formula = None
                for child in list(element):
                    child_name = child.tag.rsplit("}", 1)[-1]
                    if child_name == "v" and child.text not in {None, ""}:
                        stored = child.text
                    elif child_name == "f" and child.text:
                        formula = child.text
                if ref and formula is not None:
                    formula_cache[ref] = stored
                elif ref and stored is not None and kind not in {"s", "inlineStr", "str"}:
                    numeric_text[ref] = stored
                element.clear()
            elif name == "mergeCell":
                ref = element.get("ref")
                if ref:
                    merges.append(ref)
    return hidden, merges, numeric_text, formula_cache


def _merge_index(merges: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for ref in merges:
        start, _separator, end = ref.partition(":")
        if not end:
            found[start] = ref
            continue
        start_column, start_row = _parts(start)
        end_column, end_row = _parts(end)
        if start_column is None or end_column is None:
            continue
        if (end_row - start_row + 1) * (end_column - start_column + 1) > 5000:
            found[start] = ref
            continue
        for row in range(start_row, end_row + 1):
            for column in range(start_column, end_column + 1):
                found[_coordinate(column, row)] = ref
    return found


def _parts(coordinate: str) -> tuple[int | None, int]:
    try:
        letters, row = coordinate_from_string(coordinate)
        return column_index_from_string(letters), row
    except ValueError:
        return None, 0


def _coordinate(column: int, row: int) -> str:
    letters = ""
    number = column
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"{letters}{row}"


def _guard_zip(payload: bytes) -> None:
    if not payload.startswith(b"PK\x03\x04"):
        raise XlsxStructureError("文件扩展名是 xlsx，但内容不是 Excel 工作簿。")
    try:
        archive = ZipFile(BytesIO(payload))
    except BadZipFile as exc:
        raise XlsxStructureError("xlsx 文件已损坏，无法打开工作簿。") from exc
    total = 0
    with archive:
        infos = archive.infolist()
        if len(infos) > 400:
            raise XlsxStructureError("xlsx 内部文件过多，已拒绝读取。")
        for info in infos:
            if info.flag_bits & 0x1:
                raise XlsxStructureError("不读取加密的 xlsx。")
            if info.file_size < 0 or info.file_size > 80_000_000:
                raise XlsxStructureError("xlsx 内有异常大小的文件，已拒绝读取。")
            total += info.file_size
            if total > 200_000_000:
                raise XlsxStructureError("xlsx 解压后过大，已拒绝读取。")


def _failed(message: str, error_type: str = "illegal_format") -> XlsxOutcome:
    return XlsxOutcome(status="failed", error={"error_type": error_type, "message": message, "location": None})
