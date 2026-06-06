"""Solr edismax query builder for ARSA and TXSearch.

handler が ``compile_to_solr`` 経由で生成した edismax ``q`` 文字列を受け取り、
qf / fl / uf / sort / start / rows / shards を Solr ``/select`` リクエスト用の
dict に組み立てる。q 文字列の組み立て自体は ``ddbj_search_api.search.dsl.compiler_solr``
が担当する (auto-phrase / quote escape は ``compile_free_text_solr`` 経由)。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from ddbj_search_api.search.dsl import (
    arsa_uf_fields,
    compile_to_solr,
    split_top_level_field,
    txsearch_uf_fields,
)
from ddbj_search_api.search.dsl.ast import BoolOp, FieldClause, Node

_ARSA_QF = "AllText^0.1 PrimaryAccessionNumber^20 AccessionNumber^10 Definition^5 Organism^3 ReferenceTitle^2"
# ``fl`` must include every source field that ``arsa_docs_to_hits`` reads;
# omitting one silently demotes it to ``None`` in the DbPortalHitDdbj envelope
# even though Solr has the value.  ``Feature`` recovers the TaxID from
# ``/db_xref="taxon:..."`` (organism.identifier) and the ``/gene=`` qualifiers
# (geneName); ARSA's queryable ``FeatureQualifier`` is indexed-only
# (stored=false) so it cannot be selected and gene names come from ``Feature``.
_ARSA_FL = (
    "PrimaryAccessionNumber,Definition,Organism,Division,Date,MolecularType,SequenceLength,"
    "Feature,ReferenceTitle,ReferenceJournal,Lineage,score"
)
_TXSEARCH_QF = "scientific_name^10 scientific_name_ex^20 common_name^5 synonym^3 text^0.1"
_TXSEARCH_FL = (
    "tax_id,scientific_name,common_name,rank,lineage,synonym,blast_name,"
    "kingdom,phylum,class,order,family,genus,equivalent_name,score"
)

# ``uf`` (user fields) restricts edismax field references in the q string to
# the DSL allowlist.  Derived from compile_to_solr's field map so the two
# cannot drift — omitting a field here silently demotes ``Field:value`` in
# ``q`` to a bare keyword and matches it against ``qf`` (= wrong counts).
_ARSA_ADV_UF = " ".join(arsa_uf_fields())
_TXSEARCH_ADV_UF = " ".join(txsearch_uf_fields())

_ARSA_SORT_ALLOWLIST: dict[str, str] = {
    "datePublished:desc": "Date desc",
    "datePublished:asc": "Date asc",
}

# === db-portal facet field maps (facet wire-name → Solr facet.field) ===
#
# Solr-backed db-portal facets (docs/db-portal-api-spec.md § facet 集計).
# The wire-names mirror the camelCase facet vocabulary that /facets exposes
# (``molecularType`` etc.) so the db-portal facet allowlist is consistent
# across ES and Solr backends.  The DSL field that re-injects a bucket value
# is the snake_case allowlist name (``molecular_type`` for ARSA, same name
# for ``division`` / ``rank`` / ``kingdom``); see compiler_solr field maps.
_ARSA_FACET_FIELD_MAP: dict[str, str] = {
    "division": "Division",
    "molecularType": "MolecularType",
}
_TXSEARCH_FACET_FIELD_MAP: dict[str, str] = {
    "rank": "rank",
    "kingdom": "kingdom",
}

# Facet wire-name → DSL allowlist field (snake_case)。self-exclusion
# (docs/db-portal-api-spec.md § 集計母集団と self-exclusion) で、``split_top_level_field``
# に渡して ``q`` から分離する clause の DSL field を引くのに使う。各 ``*_FACET_FIELD_MAP``
# と 1:1 対応 (``molecularType`` → ``molecular_type``、他は同名)。値が DSL allowlist に
# 無いとコンパイルできないため整合は unit test で担保する。
_ARSA_FACET_TO_DSL_FIELD: dict[str, str] = {
    "division": "division",
    "molecularType": "molecular_type",
}
_TXSEARCH_FACET_TO_DSL_FIELD: dict[str, str] = {
    "rank": "rank",
    "kingdom": "kingdom",
}

# Public allowlist of Solr-backed db-portal facet names.  Consumed by the
# wire-level ``facets`` validation (schemas.db_portal) and the scope
# resolver (routers.db_portal); kept here so the field maps stay the SSOT.
DB_PORTAL_SOLR_FACET_NAMES: frozenset[str] = frozenset(_ARSA_FACET_FIELD_MAP) | frozenset(_TXSEARCH_FACET_FIELD_MAP)

# Fallback bucket cap mirrored from ``es.query.DEFAULT_FACET_SIZE`` (kept as
# a literal so the Solr layer does not depend on the ES layer).  Production
# callers always pass the value resolved by ``resolve_facets_size``.
_DEFAULT_FACET_LIMIT = 100


def arsa_facet_field_map() -> dict[str, str]:
    """Return a copy of the ARSA (ddbj) facet name → Solr field map."""
    return dict(_ARSA_FACET_FIELD_MAP)


def txsearch_facet_field_map() -> dict[str, str]:
    """Return a copy of the TXSearch (taxonomy) facet name → Solr field map."""
    return dict(_TXSEARCH_FACET_FIELD_MAP)


def arsa_facet_dsl_field_map() -> dict[str, str]:
    """Return a copy of the ARSA facet name → DSL allowlist field map (self-exclusion)."""
    return dict(_ARSA_FACET_TO_DSL_FIELD)


def txsearch_facet_dsl_field_map() -> dict[str, str]:
    """Return a copy of the TXSearch facet name → DSL allowlist field map (self-exclusion)."""
    return dict(_TXSEARCH_FACET_TO_DSL_FIELD)


# dialect → (facet wire-name → Solr field, facet wire-name → DSL field).  The
# two maps share keys (the facet wire-names) so ``build_solr_facet_plan`` can
# resolve both the response field and the self-exclusion clause for one facet.
_SOLR_FACET_MAPS: dict[str, tuple[dict[str, str], dict[str, str]]] = {
    "arsa": (_ARSA_FACET_FIELD_MAP, _ARSA_FACET_TO_DSL_FIELD),
    "txsearch": (_TXSEARCH_FACET_FIELD_MAP, _TXSEARCH_FACET_TO_DSL_FIELD),
}


@dataclass(frozen=True)
class SolrFacetPlan:
    """Resolved Solr request pieces for one db-specific search's facets.

    ``q`` is the edismax query string (``*:*`` when the AST is empty).  Without
    self-exclusion it compiles the whole AST and ``fq`` is empty.  Under
    self-exclusion the top-level field clauses matching a requested facet move
    out of ``q`` into tagged ``fq``, so each facet can drop its own filter via
    ``{!ex}`` while the hit population (``q`` ∧ all ``fq``) stays equal to the
    full query (docs/db-portal-api-spec.md § 集計母集団と self-exclusion).

    - ``facet_fields``: ``facet.field`` specs — a bare Solr field, or
      ``{!ex=<tag> key=<field>}<field>`` for a self-excluded facet.  ``key``
      keeps the response key equal to the Solr field so :func:`parse_solr_facets`
      still finds it.
    - ``fq``: tagged filter queries (``{!tag=<tag>}<compiled clause>``).
    - ``name_to_field``: facet wire-name → Solr field, for the response parse.
    """

    q: str
    facet_fields: list[str]
    fq: list[str]
    name_to_field: dict[str, str]


def _compile_solr_q(
    ast: Node | None,
    dialect: Literal["arsa", "txsearch"],
    free_text_operator: Literal["AND", "OR"],
) -> str:
    """Compile an AST to an edismax ``q`` string; empty AST → ``*:*`` (all docs)."""
    if ast is None:
        return "*:*"
    return compile_to_solr(ast, dialect=dialect, free_text_operator=free_text_operator)


def _self_exclude_tag(dsl_field: str) -> str:
    """Solr ``{!tag}`` / ``{!ex}`` tag for a self-excluded facet's clause."""
    return f"selfex_{dsl_field}"


def build_solr_facet_plan(
    ast: Node | None,
    requested_facets: Sequence[str] | None,
    *,
    dialect: Literal["arsa", "txsearch"],
    free_text_operator: Literal["AND", "OR"] = "AND",
    self_exclude: bool = False,
) -> SolrFacetPlan:
    """Resolve the ``q`` / ``fq`` / ``facet.field`` pieces for a Solr search.

    Without ``self_exclude`` (or with no facets / no AST) this compiles the
    whole AST into ``q`` and requests each facet on its bare Solr field — the
    population is the hits themselves.

    With ``self_exclude`` the top-level-AND clauses whose DSL field backs a
    requested facet are split out of ``q`` (via
    :func:`split_top_level_field`) into tagged ``fq``, and those facets request
    ``{!ex=<tag> key=<field>}<field>`` so their aggregation ignores their own
    filter.  Clauses under OR / NOT or nested AND are not split (they stay in
    ``q``), so those facets degrade to the full population
    (docs/db-portal-api-spec.md § 集計母集団と self-exclusion).
    """
    field_map, dsl_map = _SOLR_FACET_MAPS[dialect]
    facets = list(requested_facets or [])
    name_to_field = {name: field_map[name] for name in facets}
    if not (self_exclude and ast is not None and facets):
        return SolrFacetPlan(
            q=_compile_solr_q(ast, dialect, free_text_operator),
            facet_fields=[name_to_field[name] for name in facets],
            fq=[],
            name_to_field=name_to_field,
        )
    target_dsl = {dsl_map[name] for name in facets}
    remaining, extracted = split_top_level_field(ast, target_dsl)
    fq: list[str] = []
    dsl_to_tag: dict[str, str] = {}
    for dsl_field, clauses in extracted.items():
        clause_ast: Node = clauses[0] if len(clauses) == 1 else _and_clauses(clauses)
        tag = _self_exclude_tag(dsl_field)
        compiled = compile_to_solr(clause_ast, dialect=dialect, free_text_operator=free_text_operator)
        fq.append(f"{{!tag={tag}}}{compiled}")
        dsl_to_tag[dsl_field] = tag
    facet_fields: list[str] = []
    for name in facets:
        solr_field = name_to_field[name]
        ex_tag = dsl_to_tag.get(dsl_map[name])
        facet_fields.append(f"{{!ex={ex_tag} key={solr_field}}}{solr_field}" if ex_tag is not None else solr_field)
    return SolrFacetPlan(
        q=_compile_solr_q(remaining, dialect, free_text_operator),
        facet_fields=facet_fields,
        fq=fq,
        name_to_field=name_to_field,
    )


def _and_clauses(clauses: list[FieldClause]) -> BoolOp:
    """AND-combine multiple top-level clauses on the same field for one ``fq``.

    Two top-level-AND terms on the same field (e.g. ``division:BCT AND
    division:GSS``) are an intersection; re-joining them with AND keeps
    ``q`` ∧ ``fq`` equal to the original query.  ``position`` is borrowed from
    the first clause (compilers only read it for error context, not output).
    """
    return BoolOp(op="AND", children=tuple(clauses), position=clauses[0].position)


def _apply_facet_params(
    params: dict[str, Any],
    facet_fields: Sequence[str],
    facet_limit: int,
) -> None:
    """Add Solr terms-faceting params in place when ``facet_fields`` is non-empty.

    ``facet.mincount=1`` drops empty buckets; ``facet.limit`` caps each
    field's bucket count (mirrors the ES ``terms.size`` semantics).  For
    ARSA the request is fanned out across shards and Solr merges the facet
    counts (distributed faceting).
    """
    if not facet_fields:
        return
    params["facet"] = "true"
    params["facet.field"] = list(facet_fields)
    params["facet.mincount"] = "1"
    params["facet.limit"] = str(facet_limit)


def _pagination_to_start_rows(page: int, per_page: int) -> tuple[int, int]:
    return (max(0, (page - 1) * per_page), max(0, per_page))


def build_arsa_request_params(
    *,
    q: str,
    page: int,
    per_page: int,
    sort: str | None,
    shards: str | None,
    with_uf: bool,
    facet_fields: Sequence[str] = (),
    facet_limit: int = _DEFAULT_FACET_LIMIT,
    fq: Sequence[str] = (),
) -> dict[str, Any]:
    """Build Solr query params for ARSA ``/collection1/select`` from a pre-compiled ``q`` string.

    ``q`` は handler が :func:`ddbj_search_api.search.dsl.compile_to_solr` で生成した
    edismax ``q`` 文字列 (FreeText 単独なら ``"..."`` 群、``field:value`` 含むなら
    ``(<field_compiled> AND "<token>"...)`` 等)。``with_uf`` が True のとき
    ``uf`` (edismax allowlist) を付与する。AST 内に ``FieldClause`` が含まれるかを
    handler 側で判定して渡す
    (:func:`ddbj_search_api.search.dsl.inspect.ast_has_field_clause`)。

    ``facet_fields`` が非空のとき terms faceting (``facet.field`` 群 +
    ``facet.mincount=1`` + ``facet.limit=facet_limit``) を相乗りさせる。Solr 側で
    8 shard 分散集計される (docs/db-portal-api-spec.md § facet 集計)。

    ``fq`` は self-exclusion で ``q`` から分離した tagged フィルタ
    (``{!tag=...}...``) を渡す。hits には全 ``fq`` が効くので母集団は ``q`` ∧ ``fq``
    のまま不変で、facet 側だけ ``{!ex=...}`` で当該フィルタを外す
    (:func:`build_solr_facet_plan`)。
    """
    start, rows = _pagination_to_start_rows(page, per_page)
    params: dict[str, Any] = {
        "q": q,
        "defType": "edismax",
        "qf": _ARSA_QF,
        "fl": _ARSA_FL,
    }
    if with_uf:
        params["uf"] = _ARSA_ADV_UF
    if fq:
        params["fq"] = list(fq)
    params["start"] = str(start)
    params["rows"] = str(rows)
    params["wt"] = "json"
    if sort in _ARSA_SORT_ALLOWLIST:
        params["sort"] = _ARSA_SORT_ALLOWLIST[sort]
    if shards is not None and shards.strip():
        params["shards"] = shards
    _apply_facet_params(params, facet_fields, facet_limit)
    return params


def build_txsearch_request_params(
    *,
    q: str,
    page: int,
    per_page: int,
    sort: str | None,
    with_uf: bool,
    facet_fields: Sequence[str] = (),
    facet_limit: int = _DEFAULT_FACET_LIMIT,
    fq: Sequence[str] = (),
) -> dict[str, Any]:
    """Build Solr query params for TXSearch ``/ncbi_taxonomy/select`` from a pre-compiled ``q`` string.

    ``sort`` は Taxonomy に日付フィールドが無いため silently ignored (caller symmetry
    のため引数だけ受ける)。``with_uf`` の判定基準は
    :func:`build_arsa_request_params` と同じ。``facet_fields`` が非空のとき
    terms faceting を相乗りさせる (docs/db-portal-api-spec.md § facet 集計)。``fq`` は
    self-exclusion の tagged フィルタ (:func:`build_arsa_request_params` と同じ意味)。
    """
    _ = sort
    start, rows = _pagination_to_start_rows(page, per_page)
    params: dict[str, Any] = {
        "q": q,
        "defType": "edismax",
        "qf": _TXSEARCH_QF,
        "fl": _TXSEARCH_FL,
    }
    if with_uf:
        params["uf"] = _TXSEARCH_ADV_UF
    if fq:
        params["fq"] = list(fq)
    params["start"] = str(start)
    params["rows"] = str(rows)
    params["wt"] = "json"
    _apply_facet_params(params, facet_fields, facet_limit)
    return params
