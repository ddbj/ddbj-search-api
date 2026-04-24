"""AP3 DSL compiler for Elasticsearch (Stage 3a: AST → ES bool query dict).

SSOT: search-backends.md §バックエンド変換 (L517-520).

前提: validator で ``(field_type, value_kind)`` の互換性は担保済。
- organism は ``organism.name`` + ``organism.identifier`` の bool should で OR 展開
- ``date`` (エイリアス) は ``datePublished`` + ``dateModified`` + ``dateCreated`` の 3-way OR
- その他の Tier 1 フィールドは 1:1 で ES フィールドへマッピング
"""

from __future__ import annotations

from typing import Any

from ddbj_search_api.search.dsl.allowlist import FIELD_TYPES, OPERATOR_BY_KIND
from ddbj_search_api.search.dsl.ast import FieldClause, Node, Range

_ES_FIELD_MAP: dict[str, str] = {
    "identifier": "identifier",
    "title": "title",
    "description": "description",
    "date_published": "datePublished",
    "date_modified": "dateModified",
    "date_created": "dateCreated",
}

_ORGANISM_ES_FIELDS: tuple[str, ...] = ("organism.name", "organism.identifier")
_DATE_ALIAS_ES_FIELDS: tuple[str, ...] = ("datePublished", "dateModified", "dateCreated")


def compile_to_es(ast: Node) -> dict[str, Any]:
    """Convert a validated AST to an ES query body (value of the ``query`` key).

    Returns a bool / leaf dict suitable for embedding as ``{"query": <result>, "size": ...}``
    — matches the shape produced by :func:`ddbj_search_api.es.query.build_search_query` so
    the router can swap simple-search and adv-search results through the same helpers.
    """
    return _compile_node(ast)


def _compile_node(node: Node) -> dict[str, Any]:
    if isinstance(node, FieldClause):
        return _compile_leaf(node)
    if node.op == "AND":
        return {"bool": {"must": [_compile_node(c) for c in node.children]}}
    if node.op == "OR":
        return {
            "bool": {
                "should": [_compile_node(c) for c in node.children],
                "minimum_should_match": 1,
            },
        }
    # NOT
    return {"bool": {"must_not": [_compile_node(c) for c in node.children]}}


def _compile_leaf(clause: FieldClause) -> dict[str, Any]:
    if clause.field == "organism":
        return _or_over_fields(clause, _ORGANISM_ES_FIELDS)
    if clause.field == "date":
        return _or_over_fields(clause, _DATE_ALIAS_ES_FIELDS)
    es_field = _ES_FIELD_MAP[clause.field]
    return _basic_leaf(es_field, clause)


def _or_over_fields(clause: FieldClause, es_fields: tuple[str, ...]) -> dict[str, Any]:
    return {
        "bool": {
            "should": [_basic_leaf(f, clause) for f in es_fields],
            "minimum_should_match": 1,
        },
    }


def _basic_leaf(es_field: str, clause: FieldClause) -> dict[str, Any]:
    field_type = FIELD_TYPES[clause.field]
    op = OPERATOR_BY_KIND[(field_type, clause.value_kind)]
    value = clause.value
    if op == "eq":
        return {"term": {es_field: value}}
    if op == "contains":
        return {"match_phrase": {es_field: value}}
    if op == "wildcard":
        return {"wildcard": {es_field: value}}
    if op == "between" and isinstance(value, Range):
        return {"range": {es_field: {"gte": value.from_, "lte": value.to}}}
    # 構造上ここに到達しない (validator が弾いている) が、mypy 安全のため
    raise ValueError(f"unsupported (field={clause.field!r}, op={op!r}) in compile_to_es")
