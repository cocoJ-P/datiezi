"""数值解析。地区格式不明确时返回歧义，不猜测，也不把失败值改成 0。"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_SCIENCE = re.compile(r"[eE]")


def parse_number(raw: str, *, decimal: str, thousands: str, missing_tokens: tuple[str, ...], excel_number: bool) -> tuple[Decimal | None, str]:
    if raw is None or raw == "":
        return None, "missing_empty"
    if raw.strip() == "":
        return None, "missing_blank"
    folded = {token.casefold() for token in missing_tokens if token}
    if raw.strip().casefold() in folded:
        return None, "missing_marker"
    if excel_number:
        return _plain_decimal(raw.strip(), decimal="point", thousands="")
    return _plain_decimal(raw.strip(), decimal=decimal, thousands=thousands)


def _plain_decimal(text: str, *, decimal: str, thousands: str) -> tuple[Decimal | None, str]:
    sign = ""
    body = text
    if body[0] in "+-":
        sign = body[0]
        body = body[1:]
    if not body:
        return None, "parse_failed"
    point = "." if decimal == "point" else ","
    other = "," if point == "." else "."
    if thousands == point or (thousands and thousands == point):
        return None, "parse_failed"
    extra = "," if decimal == "point" else "."
    if " " in body and thousands != " ":
        return None, "numeric_ambiguous"
    if not thousands and extra in body:
        return None, "numeric_ambiguous"
    if thousands and thousands in body:
        body = _strip_thousands(body, point, thousands)
        if body is None:
            return None, "numeric_ambiguous"
    if point not in {".", ","}:
        return None, "parse_failed"
    if other in body and other != thousands:
        return None, "numeric_ambiguous"
    if point == ",":
        if body.count(",") > 1:
            return None, "parse_failed"
        body = body.replace(",", ".")
    if body.count(".") > 1:
        return None, "parse_failed"
    if _SCIENCE.search(body) and not re.fullmatch(r"\d+(\.\d+)?[eE][+-]?\d+", body):
        return None, "parse_failed"
    if not re.fullmatch(r"\d+(\.\d+)?([eE][+-]?\d+)?", body):
        return None, "parse_failed"
    try:
        value = Decimal(sign + body)
    except InvalidOperation:
        return None, "parse_failed"
    return value, "success"


def _strip_thousands(body: str, point: str, thousands: str) -> str | None:
    fractional = ""
    whole = body
    if point in body:
        whole, fractional = body.split(point, 1)
        if not fractional.isdigit():
            return None
    groups = whole.split(thousands)
    if len(groups) == 1:
        return body
    if not groups[0].isdigit() or not 1 <= len(groups[0]) <= 3:
        return None
    if any(not group.isdigit() or len(group) != 3 for group in groups[1:]):
        return None
    joined = "".join(groups)
    return joined if not fractional else f"{joined}{point}{fractional}"


def decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def as_integer(value: Decimal) -> int | None:
    if value != value.to_integral_value():
        return None
    return int(value)
