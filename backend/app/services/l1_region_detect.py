"""L1 P1 识别规则 sheet_region_heuristic_v2。

只使用 P0 稀疏索引上的行列占用、类型计数、合并范围和少量边界文本。
不读取原始工作簿，也不把单元格值写入区域结果。
"""

from __future__ import annotations

from dataclasses import dataclass, field

RULE_ID = "sheet_region_heuristic_v2"
RULE_SUMMARY = "按行列占用、空带、类型差异和合并结构识别数据表。标题、说明和空行只做覆盖统计。"
_BOUNDARY_WORDS = ("备注", "说明", "注释", "合计", "总计", "小计", "平均")
_VALUE_KINDS = {"number", "boolean", "date", "formula"}


@dataclass
class OccRow:
    row_number: int
    hidden: bool
    columns: frozenset[int]
    kinds: dict[int, str]
    max_merge: int

    @property
    def nonempty(self) -> int:
        return len(self.columns)

    @property
    def min_column(self) -> int:
        return min(self.columns)

    @property
    def max_column(self) -> int:
        return max(self.columns)

    def _count(self, kinds: set[str]) -> int:
        return sum(1 for column in self.columns if self.kinds.get(column) in kinds)

    @property
    def numeric(self) -> int:
        return self._count({"number", "boolean"})

    @property
    def date(self) -> int:
        return self._count({"date"})

    @property
    def formula(self) -> int:
        return self._count({"formula"})

    @property
    def text(self) -> int:
        return self._count({"text", "error"})

    @property
    def value_cells(self) -> int:
        return self._count(_VALUE_KINDS)

    @property
    def ratio(self) -> float:
        return self.value_cells / self.nonempty if self.nonempty else 0.0

    def select(self, columns: frozenset[int]) -> OccRow:
        return OccRow(self.row_number, self.hidden, columns, {column: self.kinds[column] for column in columns if column in self.kinds}, 1)


@dataclass
class Draft:
    rows: list[OccRow]
    start_column: int
    end_column: int
    header_count: int
    warnings: list[dict]
    confidence: str
    detection_method: str = "heuristic"
    review: bool = False

    @property
    def start_row(self) -> int:
        return self.rows[0].row_number

    @property
    def end_row(self) -> int:
        return self.rows[-1].row_number

    @property
    def data_rows(self) -> list[OccRow]:
        return self.rows[self.header_count :]


@dataclass
class SheetDetection:
    drafts: list[Draft] = field(default_factory=list)
    exceptions: list[dict] = field(default_factory=list)
    nonempty_cells: int = 0
    covered_cells: int = 0


def boundary_row_numbers(occupied: list[OccRow]) -> list[int]:
    numbers: list[int] = []
    for block in _vertical_blocks(occupied):
        numbers.extend(row.row_number for row in block[-3:])
    return numbers


def detect_sheet(sheet_id: str, sheet_name: str, occupied: list[OccRow], labels: dict[int, str], nonempty_cells: int) -> SheetDetection:
    result = SheetDetection(nonempty_cells=nonempty_cells)
    if not occupied:
        result.exceptions.append(
            {
                "code": "empty_sheet",
                "sheet_id": sheet_id,
                "sheet_name": sheet_name,
                "cell_count": 0,
                "message": f"工作表「{sheet_name}」没有非空单元格。",
            }
        )
        return result
    for block in _vertical_blocks(occupied):
        for rows in _split_horizontal(block):
            trimmed, _peeled = _trim(rows, labels)
            draft = _draft_from_rows(trimmed)
            if draft is not None:
                result.drafts.append(draft)
    _cover_gaps(result, sheet_id, sheet_name, occupied, labels)
    result.covered_cells = _count_covered(occupied, result.drafts)
    return result


def _vertical_blocks(rows: list[OccRow]) -> list[list[OccRow]]:
    blocks: list[list[OccRow]] = []
    for row in rows:
        if not blocks:
            blocks.append([row])
            continue
        previous = blocks[-1][-1]
        gap = row.row_number - previous.row_number - 1
        if gap <= 0 or (gap == 1 and _continues(blocks[-1], row)):
            blocks[-1].append(row)
        else:
            blocks.append([row])
    return blocks


def _continues(block: list[OccRow], row: OccRow) -> bool:
    if row.max_column < min(item.min_column for item in block) or row.min_column > max(item.max_column for item in block):
        return False
    if any(item.value_cells > 0 for item in block) and row.value_cells == 0 and row.text > 0 and len(block) >= 2:
        return False
    widths = sorted(item.nonempty for item in block)
    median = widths[len(widths) // 2]
    return abs(row.nonempty - median) <= max(1, median)


def _split_horizontal(block: list[OccRow]) -> list[list[OccRow]]:
    columns = sorted({column for row in block for column in row.columns})
    clusters: list[list[int]] = []
    for column in columns:
        if clusters and column == clusters[-1][-1] + 1:
            clusters[-1].append(column)
        else:
            clusters.append([column])
    if len(clusters) <= 1:
        return [block]
    ranges = [(item[0], item[-1]) for item in clusters]
    grouped: list[list[OccRow]] = [[] for _ in ranges]
    bridging: list[OccRow] = []
    for row in block:
        pieces = _column_runs(row)
        assignment: list[tuple[int, OccRow]] = []
        crossed = False
        for piece in pieces:
            ids = {index for index, (left, right) in enumerate(ranges) if not (piece.max_column < left or piece.min_column > right)}
            if len(ids) != 1:
                crossed = True
                break
            assignment.append((next(iter(ids)), piece))
        if crossed:
            bridging.append(row)
            continue
        for index, piece in assignment:
            grouped[index].append(piece)
    if bridging and not _edge_bridge(block, bridging):
        return [block]
    tables = [rows for rows in grouped if len(rows) >= 2 or any(row.value_cells > 0 for row in rows)]
    return tables or [block]


def _edge_bridge(block: list[OccRow], bridging: list[OccRow]) -> bool:
    marked = {row.row_number for row in bridging}
    prefix = 0
    while prefix < len(block) and block[prefix].row_number in marked:
        prefix += 1
    suffix = 0
    while suffix < len(block) - prefix and block[-1 - suffix].row_number in marked:
        suffix += 1
    return prefix + suffix == len(bridging) and 0 < prefix + suffix <= 3


def _trim(rows: list[OccRow], labels: dict[int, str]) -> tuple[list[OccRow], list[OccRow]]:
    kept = list(rows)
    peeled: list[OccRow] = []
    while len(kept) > 1:
        rest = sorted(item.nonempty for item in kept[1:])
        median = rest[len(rest) // 2]
        row = kept[0]
        banner = row.max_merge >= 3 and row.nonempty <= 1 and kept[1].nonempty >= 2
        caption = row.nonempty <= 1 and row.value_cells == 0 and median >= 2 and row.max_merge < 3
        if banner or caption:
            peeled.append(kept.pop(0))
            continue
        break
    peeled_tail = 0
    while len(kept) > 1 and peeled_tail < 3:
        body = sorted(item.nonempty for item in kept[:-1])
        median = body[len(body) // 2]
        row = kept[-1]
        collapsed = row.nonempty <= 2 and row.nonempty < median and median >= 2 and row.value_cells <= 1
        if collapsed or _boundary_word(labels.get(row.row_number, "")):
            peeled.append(kept.pop())
            peeled_tail += 1
            continue
        break
    return kept, peeled


def _boundary_word(text: str) -> bool:
    return any(word in text for word in _BOUNDARY_WORDS)


def _draft_from_rows(rows: list[OccRow]) -> Draft | None:
    if not rows:
        return None
    columns = {column for row in rows for column in row.columns}
    if not columns:
        return None
    if len(rows) == 1 and (rows[0].value_cells == 0 or rows[0].nonempty <= 1):
        return None
    header_count = _header_count(rows)
    warnings: list[dict] = []
    confidence = "high"
    if header_count == 0:
        confidence = "low"
        warnings.append({"code": "header_ambiguous", "message": "表头和数据行的结构接近，表头按 0 行保留，后续阶段继续使用这些记录。"})
    elif header_count >= 2:
        confidence = "medium"
        warnings.append({"code": "multi_row_header", "message": "识别为多行表头，范围已写入区域。"})
    data = rows[header_count:]
    if not data:
        confidence = "medium"
        warnings.append({"code": "header_only", "message": "这个区域只有表头，没有数据行。"})
    widths = [row.nonempty for row in data]
    if widths and max(widths) - min(widths) > 1:
        confidence = "medium"
        warnings.append({"code": "uneven_width", "message": "数据行的非空列数不完全一致。"})
    hidden = [str(row.row_number) for row in rows if row.hidden]
    if hidden:
        warnings.append({"code": "hidden_rows", "message": "包含隐藏行：" + "、".join(hidden[:12]) + "。内容已保留在数据范围内。"})
    return Draft(rows, min(columns), max(columns), header_count, warnings, confidence)


def _header_count(body: list[OccRow]) -> int:
    if len(body) < 2:
        return 0
    count = 0
    limit = min(4, len(body) - 1)
    while count < limit:
        row = body[count]
        nxt = body[count + 1]
        rest = body[count + 1 :]
        proto = _median(item.ratio for item in rest)
        if row.max_merge >= 2 and nxt.nonempty > row.nonempty and row.ratio <= max(proto, row.ratio):
            count += 1
            continue
        if row.formula > 0 and _median(float(item.formula) for item in rest) == 0 and row.numeric == 0 and row.date == 0:
            count += 1
            continue
        if row.date > 0 and row.numeric == 0 and row.formula == 0 and proto >= 0.4 and _median(float(item.date) for item in rest) == 0:
            count += 1
            continue
        if row.text > 0 and row.ratio + 0.2 < proto:
            count += 1
            continue
        if count > 0:
            previous = body[count - 1]
            if previous.max_merge >= 2 and row.nonempty > previous.nonempty and row.ratio <= max(proto, 0.34):
                count += 1
                continue
        if count == 0 and proto < 0.3 and row.ratio < 0.3 and row.nonempty >= 1 and nxt.nonempty >= 1:
            count += 1
            continue
        break
    return count


def _cover_gaps(result: SheetDetection, sheet_id: str, sheet_name: str, occupied: list[OccRow], labels: dict[int, str]) -> None:
    pending = _uncovered(occupied, result.drafts)
    for group in _components(pending):
        numeric = sum(row.value_cells for row in group)
        columns = {column for row in group for column in row.columns}
        if len(group) >= 2 and numeric > 0:
            draft = _draft_from_rows(group)
            if draft is not None and not any(_overlaps(draft, item) for item in result.drafts):
                draft.detection_method = "coverage"
                draft.review = True
                draft.confidence = "low"
                draft.warnings.append({"code": "recovered_table", "message": "这张表没有落在主识别结果里，已按占用结构补回，并进入待审核。"})
                result.drafts.append(draft)
                result.exceptions.append(_box("omitted_table", sheet_id, sheet_name, draft.start_row, draft.end_row, draft.start_column, draft.end_column, sum(row.nonempty for row in group), f"工作表「{sheet_name}」有一块潜在数据表被补回，执行状态为待审核。"))
                continue
            result.exceptions.append(_box("omitted_table", sheet_id, sheet_name, group[0].row_number, group[-1].row_number, min(columns), max(columns), sum(row.nonempty for row in group), f"工作表「{sheet_name}」有潜在数据表没有进入区域。"))
            continue
        result.exceptions.append(_box("auxiliary_content", sheet_id, sheet_name, group[0].row_number, group[-1].row_number, min(columns), max(columns), sum(row.nonempty for row in group), f"工作表「{sheet_name}」有 {sum(row.nonempty for row in group)} 个非空单元格位于数据表外，已记入覆盖统计，原始单元格仍在 P0。"))


def _uncovered(occupied: list[OccRow], drafts: list[Draft]) -> list[OccRow]:
    found = []
    for row in occupied:
        columns = frozenset(
            column
            for column in row.columns
            if not any(draft.start_row <= row.row_number <= draft.end_row and draft.start_column <= column <= draft.end_column for draft in drafts)
        )
        if columns:
            found.append(row.select(columns))
    return found


def _components(rows: list[OccRow]) -> list[list[OccRow]]:
    pieces: list[OccRow] = []
    for row in rows:
        pieces.extend(_column_runs(row))
    grouped: list[list[OccRow]] = []
    for piece in pieces:
        if grouped and grouped[-1][-1].row_number + 1 == piece.row_number and grouped[-1][-1].columns == piece.columns:
            grouped[-1].append(piece)
        else:
            grouped.append([piece])
    return grouped


def _column_runs(row: OccRow) -> list[OccRow]:
    columns = sorted(row.columns)
    runs: list[list[int]] = []
    for column in columns:
        if runs and column == runs[-1][-1] + 1:
            runs[-1].append(column)
        else:
            runs.append([column])
    if len(runs) <= 1:
        return [row]
    return [row.select(frozenset(run)) for run in runs]


def _overlaps(left: Draft, right: Draft) -> bool:
    return not (left.end_row < right.start_row or right.end_row < left.start_row or left.end_column < right.start_column or right.end_column < left.start_column)


def _count_covered(occupied: list[OccRow], drafts: list[Draft]) -> int:
    total = 0
    for row in occupied:
        for column in row.columns:
            if any(draft.start_row <= row.row_number <= draft.end_row and draft.start_column <= column <= draft.end_column for draft in drafts):
                total += 1
    return total


def _box(code: str, sheet_id: str, sheet_name: str, start_row: int, end_row: int, start_column: int, end_column: int, cell_count: int, message: str) -> dict:
    return {
        "code": code,
        "sheet_id": sheet_id,
        "sheet_name": sheet_name,
        "start_row": start_row,
        "end_row": end_row,
        "start_column": start_column,
        "end_column": end_column,
        "cell_count": cell_count,
        "message": message,
    }


def _median(values) -> float:
    items = list(values)
    if not items:
        return 0.0
    items.sort()
    return items[len(items) // 2]
