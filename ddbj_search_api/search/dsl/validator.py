"""AP3 DSL validator (Stage 2: AST → 検証).

チェック項目:
1. フィールド名が allowlist (Tier 1/2/3) に含まれるか → unknown-field
2. mode=cross で Tier 3 フィールドを使用していないか → field-not-available-in-cross-db
3. (field_type, value_kind) が OPERATOR_BY_KIND に含まれるか → invalid-operator-for-field
4. date 型の値が YYYY-MM-DD 厳密一致 + 実在日付 (閏年含む) → invalid-date-format
5. phrase 値が empty でないか → missing-value
6. AND/OR/NOT のネスト深さが max_depth 以下か → nest-depth-exceeded
"""

from __future__ import annotations

import datetime
import re
from typing import Literal, TypeAlias

from ddbj_search_api.schemas.db_portal import DbPortalDb
from ddbj_search_api.search.dsl.allowlist import (
    ALL_ALLOWED_FIELDS,
    FIELD_TYPES,
    OPERATOR_BY_KIND,
    TIER3_FIELD_DBS,
    TIER3_FIELDS,
)
from ddbj_search_api.search.dsl.ast import BoolOp, FieldClause, Node, Range
from ddbj_search_api.search.dsl.errors import DslError, ErrorType

ValidationMode: TypeAlias = Literal["cross", "single"]

DEFAULT_MAX_DEPTH = 5

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DIGIT_RE = re.compile(r"^\d+$")


def validate(
    ast: Node,
    *,
    mode: ValidationMode,
    db: DbPortalDb | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> None:
    """Validate an AST in place. Raises DslError on violation."""
    _check_depth(ast, current=1, max_depth=max_depth)
    _check_nodes(ast, mode=mode)


def _check_depth(node: Node, *, current: int, max_depth: int) -> None:
    if isinstance(node, FieldClause):
        return
    if current > max_depth:
        raise DslError(
            type=ErrorType.nest_depth_exceeded,
            detail=(f"nest depth {current} exceeds limit {max_depth} at column {node.position.column}"),
            column=node.position.column,
            length=node.position.length,
        )
    for child in node.children:
        _check_depth(child, current=current + 1, max_depth=max_depth)


def _check_nodes(node: Node, *, mode: ValidationMode) -> None:
    if isinstance(node, BoolOp):
        for child in node.children:
            _check_nodes(child, mode=mode)
        return
    _check_field(node, mode=mode)
    _check_value_kind_and_operator(node)
    _check_value(node)


def _check_field(clause: FieldClause, *, mode: ValidationMode) -> None:
    if clause.field not in ALL_ALLOWED_FIELDS:
        allowed = ", ".join(sorted(ALL_ALLOWED_FIELDS))
        raise DslError(
            type=ErrorType.unknown_field,
            detail=(
                f"unknown field {clause.field!r} at column {clause.position.column} "
                f"(length {clause.position.length}). allowed: {allowed}"
            ),
            column=clause.position.column,
            length=clause.position.length,
        )
    if mode == "cross" and clause.field in TIER3_FIELDS:
        dbs = TIER3_FIELD_DBS.get(clause.field, ())
        hint = " or ".join(f"db={d}" for d in dbs) if dbs else "a 'db' parameter"
        raise DslError(
            type=ErrorType.field_not_available_in_cross_db,
            detail=(
                f"field {clause.field!r} is only available in single-DB mode "
                f"at column {clause.position.column}. use {hint}."
            ),
            column=clause.position.column,
            length=clause.position.length,
        )


def _check_value_kind_and_operator(clause: FieldClause) -> None:
    field_type = FIELD_TYPES.get(clause.field)
    if field_type is None:
        return
    if (field_type, clause.value_kind) not in OPERATOR_BY_KIND:
        human_op = _value_kind_to_human(clause.value_kind)
        raise DslError(
            type=ErrorType.invalid_operator_for_field,
            detail=(
                f"operator {human_op!r} is not allowed for field {clause.field!r} "
                f"at column {clause.position.column} (length {clause.position.length})"
            ),
            column=clause.position.column,
            length=clause.position.length,
        )


_VALUE_KIND_TO_HUMAN: dict[str, str] = {
    "word": "equals/contains",
    "phrase": "phrase",
    "wildcard": "wildcard",
    "date": "date",
    "range": "range",
}


def _value_kind_to_human(kind: str) -> str:
    return _VALUE_KIND_TO_HUMAN.get(kind, kind)


def _check_value(clause: FieldClause) -> None:
    if clause.value_kind == "phrase" and clause.value == "":
        raise DslError(
            type=ErrorType.missing_value,
            detail=(
                f"empty value for field {clause.field!r} at column {clause.position.column} "
                f"(length {clause.position.length})"
            ),
            column=clause.position.column,
            length=clause.position.length,
        )
    if clause.value_kind == "date" and isinstance(clause.value, str):
        _ensure_iso_date(clause.value, clause)
    if clause.value_kind == "range" and isinstance(clause.value, Range):
        field_type = FIELD_TYPES.get(clause.field)
        if field_type == "date":
            _ensure_iso_date(clause.value.from_, clause)
            _ensure_iso_date(clause.value.to, clause)
        elif field_type == "number":
            _ensure_digit(clause.value.from_, clause)
            _ensure_digit(clause.value.to, clause)
    # number 型の単値 (e.g. sequence_length:5000) は digit 必須。
    # Literal な new slug は増やさず、invalid_operator_for_field に流用 (plan §14)。
    if clause.value_kind == "word" and isinstance(clause.value, str) and FIELD_TYPES.get(clause.field) == "number":
        _ensure_digit(clause.value, clause)


def _ensure_digit(value: str, clause: FieldClause) -> None:
    """number 型フィールドの値は非負整数でなければならない。

    AP6 では `sequence_length` のみが number 型。negative / 小数 / 非 digit は
    `invalid_operator_for_field` として弾く (plan §14: 新 slug を増やさない方針で流用)。
    """
    if not _DIGIT_RE.match(value):
        raise DslError(
            type=ErrorType.invalid_operator_for_field,
            detail=(
                f"field {clause.field!r} requires a non-negative integer value, "
                f"got {value!r} at column {clause.position.column} (length {clause.position.length})"
            ),
            column=clause.position.column,
            length=clause.position.length,
        )


def _ensure_iso_date(value: str, clause: FieldClause) -> None:
    if not _ISO_DATE_RE.match(value):
        raise DslError(
            type=ErrorType.invalid_date_format,
            detail=(
                f"invalid date {value!r} at column {clause.position.column} "
                f"(length {clause.position.length}). expected YYYY-MM-DD"
            ),
            column=clause.position.column,
            length=clause.position.length,
        )
    try:
        datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise DslError(
            type=ErrorType.invalid_date_format,
            detail=(
                f"invalid date {value!r} at column {clause.position.column} "
                f"(length {clause.position.length}). expected YYYY-MM-DD"
            ),
            column=clause.position.column,
            length=clause.position.length,
        ) from exc
