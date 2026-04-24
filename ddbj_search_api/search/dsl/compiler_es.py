"""DSL compiler for Elasticsearch (Stage 3a: AST → ES bool query dict).

SSOT: search-backends.md §バックエンド変換 (L517-520, L546-575).

- Tier 1 は 6 flat + 2 or_flat (organism / date alias)。
- Tier 2 は 2 nested (submitter: organization, publication: publication)。
- Tier 3 (ES 対象) は 8 flat (BioProject project_type / SRA 5 / JGA/MetaboBank shared 3) +
  1 double-nested (grant_agency: grant → grant.agency)。
- Trad / Taxonomy 系 Tier 3 は compiler_solr 側で扱うため、本 module の allowlist には含めない。

前提: validator で ``(field_type, value_kind)`` 互換性および cross-mode Tier 3 拒否は担保済。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ddbj_search_api.search.dsl.allowlist import FIELD_TYPES, OPERATOR_BY_KIND
from ddbj_search_api.search.dsl.ast import FieldClause, Node, Range


@dataclass(frozen=True, slots=True)
class _ESStrategy:
    """DSL field 名 → ES query 構築方針。

    `kind` に応じて使うフィールドが異なる:
    - ``flat``    : ``path`` (単一 top-level) に basic leaf を直接投げる。
    - ``or_flat`` : ``paths`` (複数 top-level) に OR (bool should) で投げる。
    - ``nested``  : ``path`` の nested wrapper + ``sub`` に basic leaf。
    - ``nested2`` : ``path`` → ``inner_path`` の 2 段 nested + ``sub`` に basic leaf。
    """

    kind: Literal["flat", "or_flat", "nested", "nested2"]
    path: str | None = None
    paths: tuple[str, ...] | None = None
    sub: str | None = None
    inner_path: str | None = None


_ES_FIELD_STRATEGY: dict[str, _ESStrategy] = {
    # === Tier 1 ===
    "identifier": _ESStrategy(kind="flat", path="identifier"),
    "title": _ESStrategy(kind="flat", path="title"),
    "description": _ESStrategy(kind="flat", path="description"),
    "organism": _ESStrategy(kind="or_flat", paths=("organism.name", "organism.identifier")),
    "date_published": _ESStrategy(kind="flat", path="datePublished"),
    "date_modified": _ESStrategy(kind="flat", path="dateModified"),
    "date_created": _ESStrategy(kind="flat", path="dateCreated"),
    "date": _ESStrategy(kind="or_flat", paths=("datePublished", "dateModified", "dateCreated")),
    # === Tier 2 ===
    "submitter": _ESStrategy(kind="nested", path="organization", sub="organization.name"),
    "publication": _ESStrategy(kind="nested", path="publication", sub="publication.id"),
    # === Tier 3 flat ===
    "project_type": _ESStrategy(kind="flat", path="objectType"),
    "library_strategy": _ESStrategy(kind="flat", path="libraryStrategy"),
    "library_source": _ESStrategy(kind="flat", path="librarySource"),
    "library_layout": _ESStrategy(kind="flat", path="libraryLayout"),
    "platform": _ESStrategy(kind="flat", path="platform"),
    "instrument_model": _ESStrategy(kind="flat", path="instrumentModel"),
    "study_type": _ESStrategy(kind="flat", path="studyType"),
    "experiment_type": _ESStrategy(kind="flat", path="experimentType"),
    "submission_type": _ESStrategy(kind="flat", path="submissionType"),
    # === Tier 3 double-nested ===
    # BioProject / JGA 共通: grant[].agency[].name。GUI 側で bioproject_grant_agency /
    # jga_grant_agency の ID 区別あり、DSL 名は `grant_agency` 統一 (search-backends.md L551)。
    "grant_agency": _ESStrategy(
        kind="nested2",
        path="grant",
        inner_path="grant.agency",
        sub="grant.agency.name",
    ),
    # Trad / Taxonomy 系 Tier 3 は ES 対象外 (compiler_solr で処理)。本 map には入れない。
}


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
    strategy = _ES_FIELD_STRATEGY[clause.field]
    if strategy.kind == "flat":
        assert strategy.path is not None
        return _basic_leaf(strategy.path, clause)
    if strategy.kind == "or_flat":
        assert strategy.paths is not None
        return _or_over_fields(clause, strategy.paths)
    if strategy.kind == "nested":
        assert strategy.path is not None
        assert strategy.sub is not None
        return {
            "nested": {
                "path": strategy.path,
                "query": _basic_leaf(strategy.sub, clause),
            },
        }
    # nested2
    assert strategy.path is not None
    assert strategy.inner_path is not None
    assert strategy.sub is not None
    return {
        "nested": {
            "path": strategy.path,
            "query": {
                "nested": {
                    "path": strategy.inner_path,
                    "query": _basic_leaf(strategy.sub, clause),
                },
            },
        },
    }


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
        # ES wildcard does not apply the analyzer to the value, so text-type
        # fields (tokenized lowercase) miss any uppercase letter in the
        # pattern and keyword fields miss values that do not match case
        # exactly.  ``case_insensitive`` restores the symmetric behaviour
        # users expect (staging probe 2026-04-24: ``title:Cancer*`` 0 → 10k).
        return {"wildcard": {es_field: {"value": value, "case_insensitive": True}}}
    if op == "between" and isinstance(value, Range):
        return {"range": {es_field: {"gte": value.from_, "lte": value.to}}}
    # 構造上ここに到達しない (validator が弾いている) が、mypy 安全のため
    raise ValueError(f"unsupported (field={clause.field!r}, op={op!r}) in compile_to_es")
