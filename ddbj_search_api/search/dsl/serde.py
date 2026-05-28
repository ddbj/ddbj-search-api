"""AST ↔ JSON tree (双方向).

SSOT: search-backends.md §スキーマ仕様.

Tree form::

    BoolOp:  {"op": "AND"|"OR"|"NOT", "rules": [...]}
    Leaf (value): {"field": "...", "op": "eq"|"contains"|"wildcard", "value": "..."}
    Leaf (range): {"field": "...", "op": "between", "from": "...", "to": "..."}
    FreeText:    {"op": "free_text", "value": "..."}

- ``ast_to_json``: ``GET /db-portal/parse?q=...`` のレスポンス形式として使用.
- ``json_to_ast``: ``POST /db-portal/serialize`` の request body をパース.
  Pydantic で shape が validated 済 (``DbPortalParseNode``) を前提とする.
  ``(field_type, op) → value_kind`` の逆引きは ``_KIND_BY_FIELD_OP`` で行い、
  ``word`` / ``phrase`` の曖昧ケースは WORD regex full-match で決定する.
"""

from __future__ import annotations

from typing import Any, cast

from ddbj_search_api.search.dsl.allowlist import (
    FIELD_TYPES,
    OPERATOR_BY_KIND,
    FieldType,
    Operator,
)
from ddbj_search_api.search.dsl.ast import (
    BoolOp,
    BoolOpKind,
    FieldClause,
    FreeText,
    Node,
    Position,
    Range,
    ValueKind,
)
from ddbj_search_api.search.dsl.lex_patterns import WORD_RE

# json_to_ast 経由の AST は元の DSL 文字列を持たないので、Position は dummy を割り当てる.
# validator のエラー detail に出る ``column 1 (length 0)`` は意味を持たない (serialize
# endpoint のエラー文脈では JSON path ベースの情報を別途付ける).
_DUMMY_POSITION = Position(column=1, length=0)

# (field_type, operator) → value_kind 逆引き.  None は「value 文字列で word/phrase 判定」.
# OPERATOR_BY_KIND の (field_type, value_kind) → operator の inverse.
_KIND_BY_FIELD_OP: dict[tuple[FieldType, Operator], ValueKind | None] = {
    ("identifier", "eq"): None,
    ("identifier", "wildcard"): "wildcard",
    ("text", "contains"): None,
    ("text", "wildcard"): "wildcard",
    ("date", "eq"): "date",
    ("date", "between"): "range",
    ("enum", "eq"): None,
    ("number", "eq"): "word",
    ("number", "between"): "range",
}

_BOOL_OPS: frozenset[str] = frozenset({"AND", "OR", "NOT"})


def ast_to_json(ast: Node) -> dict[str, Any]:
    """Convert an AST to the SSOT query-tree JSON form."""
    return _node_to_json(ast)


def _node_to_json(node: Node) -> dict[str, Any]:
    if isinstance(node, FreeText):
        # is_phrase は常に出力する (True / False 明示).  router 経由で
        # ``DbPortalParseResponse`` を通すと Pydantic が default fill して常に出力する
        # ため、dict 表現も同じ shape にして両者をずらさない.
        return {"op": "free_text", "value": node.value, "is_phrase": node.is_phrase}
    if isinstance(node, FieldClause):
        return _leaf_to_json(node)
    return {
        "op": node.op,
        "rules": [_node_to_json(c) for c in node.children],
    }


def _leaf_to_json(clause: FieldClause) -> dict[str, Any]:
    field_type = FIELD_TYPES[clause.field]
    op = OPERATOR_BY_KIND[(field_type, clause.value_kind)]
    if clause.value_kind == "range" and isinstance(clause.value, Range):
        return {
            "field": clause.field,
            "op": op,
            "from": clause.value.from_,
            "to": clause.value.to,
        }
    return {
        "field": clause.field,
        "op": op,
        "value": clause.value,
    }


def json_to_ast(payload: dict[str, Any]) -> Node:
    """Convert a JSON tree (the shape ``ast_to_json`` emits) back to an AST.

    Caller is expected to have already validated the shape via the Pydantic
    ``DbPortalParseNode`` discriminated union; this function does not re-check
    structural validity.  Unknown fields pass through (value_kind is a
    placeholder) and are rejected later by ``validate()`` with
    ``unknown-field``.
    """
    op = payload.get("op")
    if op == "free_text":
        # 旧形式 JSON tree (is_phrase 不在) は False で復元 (後方互換).
        return FreeText(value=payload["value"], is_phrase=bool(payload.get("is_phrase", False)))
    if op in _BOOL_OPS:
        children = tuple(json_to_ast(rule) for rule in payload["rules"])
        return BoolOp(op=cast(BoolOpKind, op), children=children, position=_DUMMY_POSITION)
    return _json_to_field_clause(payload)


def _json_to_field_clause(payload: dict[str, Any]) -> FieldClause:
    field = payload["field"]
    op: Operator = payload["op"]
    if op == "between":
        return FieldClause(
            field=field,
            value_kind="range",
            value=Range(from_=payload["from"], to=payload["to"]),
            position=_DUMMY_POSITION,
        )
    value = payload["value"]
    value_kind = _infer_value_kind(field=field, op=op, value=value)
    return FieldClause(
        field=field,
        value_kind=value_kind,
        value=value,
        position=_DUMMY_POSITION,
    )


def _infer_value_kind(*, field: str, op: Operator, value: str) -> ValueKind:
    field_type = FIELD_TYPES.get(field)
    if field_type is not None:
        fixed = _KIND_BY_FIELD_OP.get((field_type, op))
        if fixed is not None:
            return fixed
    # Either unknown field (validator will reject) or ambiguous (word vs phrase).
    # WORD regex full-match → word、それ以外 → phrase.
    return _word_or_phrase(value)


def _word_or_phrase(value: str) -> ValueKind:
    if value and WORD_RE.match(value):
        return "word"
    return "phrase"
