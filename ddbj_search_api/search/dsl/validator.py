"""クエリ AST validator (Stage 2: AST → 検証).

チェック項目:
1. フィールド名が allowlist (Tier 1/2/3) に含まれるか → unknown-field
2. mode=cross で Tier 3 フィールドを使用していないか → field-not-available-in-cross-db
   mode=single で指定 db に存在しない field を使用していないか → field-not-available-for-db
3. (field_type, value_kind) が OPERATOR_BY_KIND に含まれるか → invalid-operator-for-field
4. date 型の値が YYYY-MM-DD 厳密一致 + 実在日付 (閏年含む) → invalid-date-format
5. phrase 値が empty でないか → missing-value
6. AND/OR/NOT のネスト深さが max_depth 以下、AST ノード総数が max_nodes 以下か → nest-depth-exceeded
   (max_depth は深さのみを抑えるため、`a OR b OR ... OR z` のような横幅も同 slug でガードする)
7. FreeText の位置制約 (root 単独 or トップレベル AND 直下に最大 1 つ) → invalid-freetext-position / duplicate-freetext
"""

from __future__ import annotations

import datetime
import re
from typing import Literal, NoReturn, TypeAlias

from ddbj_search_api.search.dsl.allowlist import (
    ALL_ALLOWED_FIELDS,
    FIELD_TYPES,
    OPERATOR_BY_KIND,
    TIER2_FIELD_DBS,
    TIER3_FIELD_DBS,
    TIER3_FIELDS,
)
from ddbj_search_api.search.dsl.ast import BoolOp, FieldClause, FreeText, Node, Range
from ddbj_search_api.search.dsl.errors import DslError, ErrorType

ValidationMode: TypeAlias = Literal["cross", "single"]

DEFAULT_MAX_DEPTH = 5
DEFAULT_MAX_NODES = 512

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DIGIT_RE = re.compile(r"^\d+$")

# Minimum literal characters that must precede the first wildcard marker.
# Guards against ``field:*`` and ``field:?`` (lone wildcard) and against
# ``field:a*`` style 1-character prefix queries that still force ES to scan
# nearly every term.  Two characters is a balance: long enough to keep ES
# wildcard cost bounded, short enough that legitimate accession prefixes
# (e.g. ``PRJDB*``) still work.
_MIN_WILDCARD_LITERAL_LEN = 2
_WILDCARD_SAFE_RE = re.compile(r"^[A-Za-z0-9_\-.*?]+$")


def validate(
    ast: Node,
    *,
    mode: ValidationMode,
    db: str | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> None:
    """Validate an AST in place. Raises DslError on violation.

    ``db`` is the db-portal DB name (a ``DbPortalDb`` value) in single mode;
    it scopes Tier 2/3 fields to the DBs that actually own them. ``None``
    (cross mode, or single mode without a target) skips the db-scope check.
    """
    _check_total_nodes(ast, max_nodes=max_nodes)
    _check_depth(ast, current=1, max_depth=max_depth)
    _check_nodes(ast, mode=mode, db=db)
    _check_freetext_position(ast)


def _count_nodes(node: Node) -> int:
    if isinstance(node, FieldClause | FreeText):
        return 1
    return 1 + sum(_count_nodes(child) for child in node.children)


def _check_total_nodes(node: Node, *, max_nodes: int) -> None:
    total = _count_nodes(node)
    if total > max_nodes:
        # FreeText 単独ツリーは Position を持たないが、validator は Lark 由来の AST にのみ
        # 適用される設計のため、実運用ではこの分岐に到達しない。安全側に column=1,length=0 で raise。
        position = node.position if not isinstance(node, FreeText) else None
        raise DslError(
            type=ErrorType.nest_depth_exceeded,
            detail=(f"total node count {total} exceeds limit {max_nodes}"),
            column=position.column if position else 1,
            length=position.length if position else 0,
        )


def _check_depth(node: Node, *, current: int, max_depth: int) -> None:
    if isinstance(node, FieldClause | FreeText):
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


def _check_nodes(node: Node, *, mode: ValidationMode, db: str | None) -> None:
    if isinstance(node, FreeText):
        return
    if isinstance(node, BoolOp):
        for child in node.children:
            _check_nodes(child, mode=mode, db=db)
        return
    _check_field(node, mode=mode, db=db)
    _check_value_kind_and_operator(node)
    _check_value(node)


def _check_field(clause: FieldClause, *, mode: ValidationMode, db: str | None) -> None:
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
    if mode == "single" and db is not None:
        scope = _single_db_field_scope(clause.field)
        if scope is not None and db not in scope:
            hint = " or ".join(f"db={d}" for d in scope)
            raise DslError(
                type=ErrorType.field_not_available_for_db,
                detail=(
                    f"field {clause.field!r} is not available for db={db!r} "
                    f"at column {clause.position.column} (length {clause.position.length}). use {hint}."
                ),
                column=clause.position.column,
                length=clause.position.length,
            )


def _single_db_field_scope(field: str) -> tuple[str, ...] | None:
    """single-mode で ``field`` が実在する db の許可リスト。``None`` は全 db で有効。

    Tier 3 は :data:`TIER3_FIELD_DBS`、scope を持つ Tier 2 (``publication``) は
    :data:`TIER2_FIELD_DBS` を参照する。Tier 1 と ``submitter`` は全 db common なので
    ``None`` を返し、scope 検証をスキップする。
    """
    if field in TIER3_FIELD_DBS:
        return TIER3_FIELD_DBS[field]
    return TIER2_FIELD_DBS.get(field)


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
    # Literal な new slug は増やさず、invalid_operator_for_field に流用。
    if clause.value_kind == "word" and isinstance(clause.value, str) and FIELD_TYPES.get(clause.field) == "number":
        _ensure_digit(clause.value, clause)
    if clause.value_kind == "wildcard" and isinstance(clause.value, str):
        _ensure_safe_wildcard(clause.value, clause)


def _ensure_safe_wildcard(value: str, clause: FieldClause) -> None:
    """Reject leading wildcards, lone ``*``/``?``, and short prefixes.

    Defence-in-depth alongside the grammar's narrowed WILDCARD character
    class: leading ``*foo`` / lone ``*`` / 1-char prefix ``f*`` would force
    Elasticsearch and Solr to scan effectively every term in the index, so
    we surface them as ``invalid-operator-for-field`` (the same slug already
    used for "shape of value does not match field constraints").  Also
    re-checks the metachar allow-list in case an AST is hand-built and
    bypasses the grammar.
    """
    if not _WILDCARD_SAFE_RE.match(value):
        raise DslError(
            type=ErrorType.invalid_operator_for_field,
            detail=(
                f"wildcard value {value!r} for field {clause.field!r} contains characters "
                f"outside the allowed set [A-Za-z0-9_\\-.*?] at column {clause.position.column} "
                f"(length {clause.position.length})"
            ),
            column=clause.position.column,
            length=clause.position.length,
        )
    if value[0] in "*?":
        raise DslError(
            type=ErrorType.invalid_operator_for_field,
            detail=(
                f"leading wildcard not allowed for field {clause.field!r} at column "
                f"{clause.position.column} (length {clause.position.length}); the value must "
                f"start with at least {_MIN_WILDCARD_LITERAL_LEN} literal characters before "
                f"a '*' or '?'"
            ),
            column=clause.position.column,
            length=clause.position.length,
        )
    first_wild = next((i for i, c in enumerate(value) if c in "*?"), len(value))
    if first_wild < _MIN_WILDCARD_LITERAL_LEN:
        raise DslError(
            type=ErrorType.invalid_operator_for_field,
            detail=(
                f"wildcard prefix too short for field {clause.field!r} at column "
                f"{clause.position.column} (length {clause.position.length}); at least "
                f"{_MIN_WILDCARD_LITERAL_LEN} literal characters are required before a '*' or '?'"
            ),
            column=clause.position.column,
            length=clause.position.length,
        )


def _ensure_digit(value: str, clause: FieldClause) -> None:
    """number 型フィールドの値は非負整数でなければならない。

    現状 `sequence_length` のみが number 型。negative / 小数 / 非 digit は
    `invalid_operator_for_field` として弾く (新 slug を増やさない方針で流用)。
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


def _check_freetext_position(ast: Node) -> None:
    """FreeText の位置制約を enforce.

    OK: root が FreeText 単独 / トップレベル AND 直下に FreeText が最大 1 つ
    NG: OR / NOT 配下 / ネスト深部 AND 配下 / トップレベル AND 直下に 2 つ以上
    """
    if isinstance(ast, FreeText):
        return
    if isinstance(ast, FieldClause):
        return
    # ast is BoolOp
    if ast.op == "AND":
        freetext_children = [c for c in ast.children if isinstance(c, FreeText)]
        if len(freetext_children) >= 2:
            duplicate = freetext_children[1]
            _raise_duplicate_freetext(duplicate, ast)
        for child in ast.children:
            if isinstance(child, FreeText):
                continue
            _ensure_no_freetext(child)
        return
    _ensure_no_freetext(ast)


def _ensure_no_freetext(node: Node) -> None:
    if isinstance(node, FreeText):
        _raise_invalid_freetext_position(node)
    if isinstance(node, FieldClause):
        return
    for child in node.children:
        _ensure_no_freetext(child)


def _raise_invalid_freetext_position(node: FreeText) -> NoReturn:
    col = node.position.column if node.position else 1
    length = node.position.length if node.position else max(len(node.value), 1)
    raise DslError(
        type=ErrorType.invalid_freetext_position,
        detail=(
            f"free-text term {node.value!r} must appear directly under a top-level "
            f"AND operator (or as the sole top-level term) at column {col} "
            f"(length {length}). free text is not allowed under OR / NOT or nested AND."
        ),
        column=col,
        length=length,
    )


def _raise_duplicate_freetext(node: FreeText, parent: BoolOp) -> NoReturn:
    position = node.position or parent.position
    raise DslError(
        type=ErrorType.duplicate_freetext,
        detail=(
            f"multiple free-text terms in AND clause at column {position.column} "
            f"(length {position.length}). combine them into a single phrase or "
            "wrap one as a field clause."
        ),
        column=position.column,
        length=position.length,
    )
