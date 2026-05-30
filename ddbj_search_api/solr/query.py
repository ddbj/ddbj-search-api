"""Solr edismax query builder for ARSA and TXSearch.

handler が ``compile_to_solr`` 経由で生成した edismax ``q`` 文字列を受け取り、
qf / fl / uf / sort / start / rows / shards を Solr ``/select`` リクエスト用の
dict に組み立てる。q 文字列の組み立て自体は ``ddbj_search_api.search.dsl.compiler_solr``
が担当する (auto-phrase / quote escape は ``compile_free_text_solr`` 経由)。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ddbj_search_api.search.dsl import arsa_uf_fields, txsearch_uf_fields

_ARSA_QF = "AllText^0.1 PrimaryAccessionNumber^20 AccessionNumber^10 Definition^5 Organism^3 ReferenceTitle^2"
# ``fl`` must include every source field that ``arsa_docs_to_hits`` reads;
# omitting one silently demotes it to ``None`` in the DbPortalHitTrad envelope
# even though Solr has the value.  ``Feature`` is needed only to recover the
# TaxID from ``/db_xref="taxon:..."`` so ``organism.identifier`` can be set.
_ARSA_FL = "PrimaryAccessionNumber,Definition,Organism,Division,Date,MolecularType,SequenceLength,Feature,score"
_TXSEARCH_QF = "scientific_name^10 scientific_name_ex^20 common_name^5 synonym^3 japanese_name^5 text^0.1"
_TXSEARCH_FL = "tax_id,scientific_name,common_name,japanese_name,rank,lineage,score"

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

# Public allowlist of Solr-backed db-portal facet names.  Consumed by the
# wire-level ``facets`` validation (schemas.db_portal) and the scope
# resolver (routers.db_portal); kept here so the field maps stay the SSOT.
DB_PORTAL_SOLR_FACET_NAMES: frozenset[str] = frozenset(_ARSA_FACET_FIELD_MAP) | frozenset(_TXSEARCH_FACET_FIELD_MAP)

# Fallback bucket cap mirrored from ``es.query.DEFAULT_FACET_SIZE`` (kept as
# a literal so the Solr layer does not depend on the ES layer).  Production
# callers always pass the value resolved by ``resolve_facets_size``.
_DEFAULT_FACET_LIMIT = 100


def arsa_facet_field_map() -> dict[str, str]:
    """Return a copy of the ARSA (trad) facet name → Solr field map."""
    return dict(_ARSA_FACET_FIELD_MAP)


def txsearch_facet_field_map() -> dict[str, str]:
    """Return a copy of the TXSearch (taxonomy) facet name → Solr field map."""
    return dict(_TXSEARCH_FACET_FIELD_MAP)


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
) -> dict[str, Any]:
    """Build Solr query params for TXSearch ``/ncbi_taxonomy/select`` from a pre-compiled ``q`` string.

    ``sort`` は Taxonomy に日付フィールドが無いため silently ignored (caller symmetry
    のため引数だけ受ける)。``with_uf`` の判定基準は
    :func:`build_arsa_request_params` と同じ。``facet_fields`` が非空のとき
    terms faceting を相乗りさせる (docs/db-portal-api-spec.md § facet 集計)。
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
    params["start"] = str(start)
    params["rows"] = str(rows)
    params["wt"] = "json"
    _apply_facet_params(params, facet_fields, facet_limit)
    return params
