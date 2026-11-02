"""P4 规则标识和状态。转换规则由 Schema 与配置决定，不绑定某一份工作簿。"""

from __future__ import annotations

RULE_ID = "standardization_v1"
DECIMAL_STYLES = ("point", "comma")
THOUSANDS = ("", ",", ".", " ")
FAILURE_POLICIES = ("keep_record",)
DATETIME_TYPES = ("timestamp", "datetime", "date")
NUMERIC_TYPES = ("float64", "integer")
TEXT_TYPES = ("string", "categorical_string")

STATUS_SUCCESS = "success"
STATUS_MISSING_EMPTY = "missing_empty"
STATUS_MISSING_BLANK = "missing_blank"
STATUS_MISSING_MARKER = "missing_marker"
STATUS_PARSE_FAILED = "parse_failed"
STATUS_NUMERIC_AMBIGUOUS = "numeric_ambiguous"
STATUS_DATETIME_FAILED = "datetime_failed"
STATUS_DATETIME_AMBIGUOUS = "datetime_ambiguous"
STATUS_EXCEL_UNRELIABLE = "datetime_excel_unreliable"
STATUS_UNIT_INCOMPATIBLE = "unit_incompatible"
STATUS_UNIT_UNCONFIRMED = "unit_unconfirmed"
STATUS_UNCONFIRMED = "mapping_unconfirmed"
STATUS_NOT_EXECUTED = "not_executed"
STATUS_NOT_APPLICABLE = "not_applicable"

TYPE_FAILURES = {STATUS_PARSE_FAILED, STATUS_NUMERIC_AMBIGUOUS}
TIME_FAILURES = {STATUS_DATETIME_FAILED, STATUS_DATETIME_AMBIGUOUS, STATUS_EXCEL_UNRELIABLE}
UNIT_FAILURES = {STATUS_UNIT_INCOMPATIBLE}
CELL_FAILURES = TYPE_FAILURES | TIME_FAILURES | UNIT_FAILURES
MISSING = {STATUS_MISSING_EMPTY, STATUS_MISSING_BLANK, STATUS_MISSING_MARKER}
