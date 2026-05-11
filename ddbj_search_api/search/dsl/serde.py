"""AST → JSON tree.

SSOT: search-backends.md §スキーマ仕様 (L363-381).

Tree form::

    BoolOp:  {"op": "AND"|"OR"|"NOT", "rules": [...]}
    Leaf (value): {"field": "...", "op": "eq"|"contains"|"wildcard", "value": "..."}
    Leaf (range): {"field": "...", "op": "between", "from": "...", "to": "..."}

``GET /db-portal/parse?adv=...`` endpoint のレスポンス形式として使用する。
"""

from __future__ import annotations

from typing import Any

from ddbj_search_api.search.dsl.allowlist import FIELD_TYPES, OPERATOR_BY_KIND
from ddbj_search_api.search.dsl.ast import FieldClause, FreeText, Node, Range


def ast_to_json(ast: Node) -> dict[str, Any]:
    """Convert an AST to the SSOT query-tree JSON form.

    ``FreeText`` ノードは ``/db-portal/parse`` の返却対象外 (Lark パーサからは
    生成されず、handler 経由でしか出現しない)。serializer に到達した場合は
    contract 違反として ``TypeError`` を raise する。
    """
    return _node_to_json(ast)


def _node_to_json(node: Node) -> dict[str, Any]:
    if isinstance(node, FreeText):
        raise TypeError(
            "ast_to_json received FreeText; this node type is internal to handler routing "
            "and must not be serialized via /db-portal/parse."
        )
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
