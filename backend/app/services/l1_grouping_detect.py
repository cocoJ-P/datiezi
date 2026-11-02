"""P2 设备编号字段多特征识别（equipment_grouping_v2）。

不依赖 LLM / 外部 API。样本仅用于候选打分，最终归属仍由全量编号匹配决定。
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

_ALIASES_EXACT = ("设备编号", "设备id", "资产编号", "equipmentid", "deviceid")
_PLACEHOLDERS = {"无", "空", "null", "n/a", "na", "none", "-", "—", "#n/a", "#null"}
_TIME_HINTS = ("时间", "日期", "timestamp", "datetime", "date", "time", "采集时刻", "采样时间")
_MEASURE_HINTS = (
    "温度",
    "振动",
    "压力",
    "电流",
    "电压",
    "转速",
    "流量",
    "振幅",
    "功率",
    "载荷",
    "频次",
    "value",
    "测量",
    "测点",
)
_INDEX_HINTS = ("序号", "行号", "记录号", "index", "row", "id号", "流水号")
_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]{0,63}$")
_PURE_INT_RE = re.compile(r"^-?\d+$")
_DECIMAL_RE = re.compile(r"^-?\d+\.\d+$")
_DATE_RE = re.compile(r"^\d{4}[-/年]\d{1,2}([-/月]\d{1,2})?")


@dataclass
class FieldScore:
    column_number: int
    column_id: str
    header: str
    match: str
    alias: str
    score: float
    confidence_level: str
    recognition_source: str
    warning_codes: list[str]
    features: dict


def header_match(header: str) -> tuple[str | None, str]:
    key = "".join(header.split()).casefold()
    if not key:
        return None, ""
    if key in _ALIASES_EXACT:
        return "exact", header
    fuzzy = (
        ("设备" in key and any(part in key for part in ("编号", "id", "编码", "代号")))
        or "equipment" in key
        or "device" in key
        or ("资产" in key and any(part in key for part in ("编号", "id", "编码")))
    )
    if fuzzy:
        return "fuzzy", header
    return None, ""


def score_columns(
    connection: sqlite3.Connection,
    dataset_id: str,
    sheet_id: str,
    start_row: int,
    end_row: int,
    header_start_row: int | None,
    header_row_count: int,
    columns: list[dict],
    sample_limit: int = 200,
) -> list[FieldScore]:
    header_end = 0
    if header_row_count and header_start_row:
        header_end = int(header_start_row) + int(header_row_count) - 1
    scores: list[FieldScore] = []
    for column in columns:
        header = " / ".join(part for part in (column.get("header") or []) if part)
        match, alias = header_match(header)
        values = _sample_values(
            connection,
            dataset_id,
            sheet_id,
            int(column["column_number"]),
            start_row,
            end_row,
            header_row_count,
            int(header_start_row or 0),
            header_end,
            sample_limit,
        )
        features = _features(header, match, values)
        score, confidence, source, warnings = _rank(features)
        scores.append(
            FieldScore(
                column_number=int(column["column_number"]),
                column_id=str(column["column_id"]),
                header=header,
                match=match or "none",
                alias=alias,
                score=score,
                confidence_level=confidence,
                recognition_source=source,
                warning_codes=warnings,
                features=features,
            )
        )
    scores.sort(key=lambda item: (-item.score, item.column_number))
    return scores


def select_equipment_field(scores: list[FieldScore]) -> dict:
    """返回 selected_field / candidate_fields / confidence / warnings / requires_review。"""
    candidates = [item for item in scores if item.score >= 0.35 or item.match in {"exact", "fuzzy"}]
    if not candidates:
        return {
            "selected_field": None,
            "candidate_fields": [],
            "confidence_level": "none",
            "recognition_source": "none",
            "warning_codes": ["no_equipment_field"],
            "requires_review": False,
            "confirmation_status": "no_field",
        }

    top = candidates[0]
    rivals = [item for item in candidates[1:] if item.score >= top.score - 0.12 and item.score >= 0.45]

    # 唯一 exact，且没有接近的 rival
    if top.match == "exact" and not rivals:
        return _decision(top, candidates, requires_review=False, extra_warnings=[])

    # 唯一高分候选（含可靠模糊）
    if not rivals and top.score >= 0.55 and top.confidence_level in {"high", "medium"}:
        warnings = list(top.warning_codes)
        if top.match == "fuzzy":
            warnings.append("fuzzy_auto_selected")
        return _decision(top, candidates, requires_review=False, extra_warnings=warnings)

    # 多个互相冲突的编号候选
    if rivals or (top.match == "exact" and any(item.match == "exact" for item in rivals)):
        return {
            "selected_field": None,
            "candidate_fields": [_public_score(item) for item in candidates[:8]],
            "confidence_level": "low",
            "recognition_source": "ambiguous",
            "warning_codes": ["conflicting_equipment_candidates"],
            "requires_review": True,
            "confirmation_status": "needs_confirmation",
        }

    # 分数不够高：有候选但不自动选定
    if top.score < 0.55:
        return {
            "selected_field": None,
            "candidate_fields": [_public_score(item) for item in candidates[:8]],
            "confidence_level": "low",
            "recognition_source": "weak",
            "warning_codes": ["weak_equipment_signal"] + list(top.warning_codes),
            "requires_review": True,
            "confirmation_status": "needs_confirmation",
        }

    return _decision(top, candidates, requires_review=False, extra_warnings=list(top.warning_codes))


def _decision(top: FieldScore, candidates: list[FieldScore], requires_review: bool, extra_warnings: list[str]) -> dict:
    warnings = []
    for code in extra_warnings:
        if code not in warnings:
            warnings.append(code)
    return {
        "selected_field": top.column_id,
        "candidate_fields": [_public_score(item) for item in candidates[:8]],
        "confidence_level": top.confidence_level,
        "recognition_source": top.recognition_source,
        "warning_codes": warnings,
        "requires_review": requires_review,
        "confirmation_status": "suggested" if not requires_review else "needs_confirmation",
    }


def _public_score(item: FieldScore) -> dict:
    return {
        "column_number": item.column_number,
        "column_id": item.column_id,
        "header": item.header,
        "match": item.match,
        "alias": item.alias,
        "score": round(item.score, 4),
        "confidence_level": item.confidence_level,
        "recognition_source": item.recognition_source,
        "warning_codes": item.warning_codes,
        "features": item.features,
    }


def _sample_values(
    connection: sqlite3.Connection,
    dataset_id: str,
    sheet_id: str,
    column_number: int,
    start_row: int,
    end_row: int,
    header_row_count: int,
    header_start: int,
    header_end: int,
    sample_limit: int,
) -> list[str]:
    rows = connection.execute(
        """
        SELECT c.raw_value
        FROM source_rows s
        LEFT JOIN raw_cells c
          ON c.dataset_id = s.dataset_id AND c.sheet_id = s.sheet_id
         AND c.row_number = s.row_number AND c.column_number = ?
        WHERE s.dataset_id = ? AND s.sheet_id = ? AND s.row_number BETWEEN ? AND ?
          AND (? = 0 OR s.row_number < ? OR s.row_number > ?)
        ORDER BY s.row_number
        LIMIT ?
        """,
        (
            column_number,
            dataset_id,
            sheet_id,
            start_row,
            end_row,
            header_row_count,
            header_start,
            header_end,
            sample_limit,
        ),
    ).fetchall()
    return ["" if row["raw_value"] is None else str(row["raw_value"]) for row in rows]


def _features(header: str, match: str | None, values: list[str]) -> dict:
    total = max(len(values), 1)
    empty = 0
    placeholder = 0
    code_like = 0
    pure_int = 0
    decimal = 0
    date_like = 0
    non_empty: list[str] = []
    for value in values:
        text = value.strip()
        if text == "":
            empty += 1
            continue
        if text.casefold() in _PLACEHOLDERS:
            placeholder += 1
            continue
        non_empty.append(text)
        if _DATE_RE.match(text):
            date_like += 1
        if _DECIMAL_RE.match(text):
            decimal += 1
        elif _PURE_INT_RE.match(text):
            pure_int += 1
        if _CODE_RE.match(text):
            code_like += 1
    filled = len(non_empty)
    unique = len({item.casefold() for item in non_empty})
    unique_ratio = (unique / filled) if filled else 0.0
    # 大量重复对设备编号是正常的；高唯一且全是递增整数更像序号
    sequential = _looks_sequential(non_empty)
    key = "".join(header.split()).casefold()
    return {
        "header_exact": match == "exact",
        "header_fuzzy": match == "fuzzy",
        "non_empty_ratio": round(filled / total, 4),
        "empty_ratio": round(empty / total, 4),
        "placeholder_ratio": round(placeholder / total, 4),
        "code_like_ratio": round((code_like / filled) if filled else 0.0, 4),
        "pure_int_ratio": round((pure_int / filled) if filled else 0.0, 4),
        "decimal_ratio": round((decimal / filled) if filled else 0.0, 4),
        "date_like_ratio": round((date_like / filled) if filled else 0.0, 4),
        "unique_ratio": round(unique_ratio, 4),
        "unique_count": unique,
        "filled_count": filled,
        "sample_count": total,
        "sequential_index_like": sequential,
        "header_time_like": any(hint in key for hint in _TIME_HINTS),
        "header_measure_like": any(hint in key for hint in _MEASURE_HINTS),
        "header_index_like": any(hint in key for hint in _INDEX_HINTS),
    }


def _looks_sequential(values: list[str]) -> bool:
    if len(values) < 5:
        return False
    ints: list[int] = []
    for value in values:
        if not _PURE_INT_RE.match(value):
            return False
        ints.append(int(value))
    if len(set(ints)) < len(ints) * 0.9:
        return False
    ordered = sorted(ints)
    diffs = [ordered[i + 1] - ordered[i] for i in range(len(ordered) - 1)]
    return bool(diffs) and all(diff == 1 for diff in diffs[: min(20, len(diffs))])


def _rank(features: dict) -> tuple[float, str, str, list[str]]:
    score = 0.0
    warnings: list[str] = []
    source = "value_stats"
    if features["header_exact"]:
        score += 0.62
        source = "header_exact"
    elif features["header_fuzzy"]:
        score += 0.38
        source = "header_fuzzy"
    score += min(0.18, features["non_empty_ratio"] * 0.18)
    score -= min(0.2, features["placeholder_ratio"] * 0.4)
    score += min(0.18, features["code_like_ratio"] * 0.18)
    if features["decimal_ratio"] > 0.4:
        score -= 0.35
        warnings.append("looks_like_measurement")
    if features["date_like_ratio"] > 0.35 or features["header_time_like"]:
        score -= 0.4
        warnings.append("looks_like_timestamp")
    if features["header_measure_like"] and not features["header_exact"] and not features["header_fuzzy"]:
        score -= 0.35
        warnings.append("looks_like_measurement_header")
    if features["header_index_like"] or features["sequential_index_like"]:
        score -= 0.45
        warnings.append("looks_like_row_index")
    # 高唯一 + 纯整数 + 无设备表头 → 更像序号，不能当设备编号
    if features["unique_ratio"] > 0.95 and features["pure_int_ratio"] > 0.9 and not features["header_exact"] and not features["header_fuzzy"]:
        score -= 0.35
        warnings.append("high_uniqueness_integer")
    # 设备编号允许大量重复：重复本身不扣分；若几乎全空则扣分
    if features["non_empty_ratio"] < 0.05:
        score -= 0.3
        warnings.append("mostly_empty")
    score = max(0.0, min(1.0, score))
    if score >= 0.75:
        confidence = "high"
    elif score >= 0.55:
        confidence = "medium"
    elif score >= 0.35:
        confidence = "low"
    else:
        confidence = "none"
    if features["header_exact"] and features["non_empty_ratio"] >= 0.2:
        source = "header_exact"
    elif features["header_fuzzy"] and score >= 0.55:
        source = "header_fuzzy_validated"
    return score, confidence, source, warnings
