"""AP3 DSL エラー型.

AP1 決定 (source.md §AP1 L140-142) に従い、``ProblemDetails`` schema は拡張しない。
エラー位置情報 (column / length) は ``detail`` 文字列に自然言語で埋め込み、
type URI slug で機械判別する。
"""

from __future__ import annotations

from enum import Enum

TYPE_URI_PREFIX = "https://ddbj.nig.ac.jp/problems/"


class ErrorType(str, Enum):
    """AP3 で追加する 7 slug (``advanced-search-not-implemented`` は AP3 完了時に事実上廃止)."""

    unexpected_token = "unexpected-token"
    unknown_field = "unknown-field"
    field_not_available_in_cross_db = "field-not-available-in-cross-db"
    invalid_date_format = "invalid-date-format"
    invalid_operator_for_field = "invalid-operator-for-field"
    nest_depth_exceeded = "nest-depth-exceeded"
    missing_value = "missing-value"


def type_uri(error_type: ErrorType) -> str:
    return f"{TYPE_URI_PREFIX}{error_type.value}"


class DslError(Exception):
    """3 段階処理 (parse / validate / compile) で発生する全エラーの表現型."""

    def __init__(self, *, type: ErrorType, detail: str, column: int, length: int) -> None:
        super().__init__(detail)
        self.type = type
        self.detail = detail
        self.column = column
        self.length = length

    def __repr__(self) -> str:
        return f"DslError(type={self.type.value!r}, column={self.column}, length={self.length}, detail={self.detail!r})"

    @property
    def type_uri(self) -> str:
        return type_uri(self.type)
