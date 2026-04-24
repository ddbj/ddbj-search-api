"""AP3 DSL compiler for Solr edismax q string (Stage 3b).

SSOT: search-backends.md §バックエンド変換 (L520, L542-543).

Dialect:
- ``arsa``: ARSA (Solr 4.4.0)、``PrimaryAccessionNumber`` / ``Definition`` /
  ``AllText`` / ``Organism`` / ``Lineage`` / ``Date``。``date_modified`` /
  ``date_created`` / ``date``(エイリアス) は ARSA に対応フィールドがなく degenerate。
- ``txsearch``: TXSearch (Solr 4.4.0)、``tax_id`` / ``scientific_name`` / ``text``。
  organism 自体が Taxonomy のため ``organism`` と日付系は degenerate。

degenerate は leaf を ``(-*:*)`` (no-match リテラル) に置換する。ツリー構造は維持する。
"""

from __future__ import annotations

from typing import Literal, TypeAlias

from ddbj_search_api.search.dsl.allowlist import FIELD_TYPES, OPERATOR_BY_KIND
from ddbj_search_api.search.dsl.ast import FieldClause, Node, Range
from ddbj_search_api.search.phrase import escape_solr_phrase

SolrDialect: TypeAlias = Literal["arsa", "txsearch"]

_ARSA_FIELD_MAP: dict[str, tuple[str, ...]] = {
    "identifier": ("PrimaryAccessionNumber",),
    "title": ("Definition",),
    "description": ("AllText",),
    "organism": ("Organism", "Lineage"),
    "date_published": ("Date",),
}
_ARSA_UNAVAILABLE: frozenset[str] = frozenset({"date_modified", "date_created", "date"})

_TXSEARCH_FIELD_MAP: dict[str, tuple[str, ...]] = {
    "identifier": ("tax_id",),
    "title": ("scientific_name",),
    "description": ("text",),
}
_TXSEARCH_UNAVAILABLE: frozenset[str] = frozenset(
    {"organism", "date_published", "date_modified", "date_created", "date"},
)

_NO_MATCH_LITERAL = "(-*:*)"


def compile_to_solr(ast: Node, *, dialect: SolrDialect) -> str:
    """Convert a validated AST to an edismax ``q`` string for the given Solr dialect."""
    return _compile_node(ast, dialect=dialect)


def _compile_node(node: Node, *, dialect: SolrDialect) -> str:
    if isinstance(node, FieldClause):
        return _compile_leaf(node, dialect=dialect)
    children_q = [_compile_node(c, dialect=dialect) for c in node.children]
    if node.op == "AND":
        return "(" + " AND ".join(children_q) + ")"
    if node.op == "OR":
        return "(" + " OR ".join(children_q) + ")"
    return f"(NOT {children_q[0]})"


def _compile_leaf(clause: FieldClause, *, dialect: SolrDialect) -> str:
    if dialect == "arsa":
        if clause.field in _ARSA_UNAVAILABLE:
            return _NO_MATCH_LITERAL
        solr_fields = _ARSA_FIELD_MAP.get(clause.field)
    else:
        if clause.field in _TXSEARCH_UNAVAILABLE:
            return _NO_MATCH_LITERAL
        solr_fields = _TXSEARCH_FIELD_MAP.get(clause.field)
    if not solr_fields:
        return _NO_MATCH_LITERAL
    if len(solr_fields) == 1:
        return _basic_leaf(solr_fields[0], clause)
    return "(" + " OR ".join(_basic_leaf(f, clause) for f in solr_fields) + ")"


def _basic_leaf(solr_field: str, clause: FieldClause) -> str:
    field_type = FIELD_TYPES[clause.field]
    op = OPERATOR_BY_KIND[(field_type, clause.value_kind)]
    value = clause.value
    if op == "between" and isinstance(value, Range):
        from_v = _format_date_for_solr(value.from_) if field_type == "date" else value.from_
        to_v = _format_date_for_solr(value.to) if field_type == "date" else value.to
        return f"{solr_field}:[{from_v} TO {to_v}]"
    if not isinstance(value, str):
        raise TypeError(f"expected str value for field {clause.field!r}")
    if clause.value_kind == "date":
        formatted = _format_date_for_solr(value) if field_type == "date" else value
        return f"{solr_field}:{formatted}"
    if clause.value_kind == "wildcard":
        return f"{solr_field}:{value}"
    # word / phrase は両方 quote (AP2 観測: Solr edismax metachar 解釈回避)
    escaped = escape_solr_phrase(value)
    return f'{solr_field}:"{escaped}"'


def _format_date_for_solr(iso: str) -> str:
    """YYYY-MM-DD → YYYYMMDD (ARSA ``Date`` field format)."""
    return iso.replace("-", "")
