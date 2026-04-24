"""Solr edismax query builder for ARSA and TXSearch.

Pure functions that convert API parameters to Solr ``/select`` query
params.  Keywords are always wrapped in double quotes because ARSA
Solr 4.4.0 interprets bare tokens like ``HIF-1`` as NOT expressions
(staging-measured: numFound jumped from 15050 quoted to 295M unquoted).
"""

from __future__ import annotations

from ddbj_search_api.search.dsl import arsa_uf_fields, txsearch_uf_fields
from ddbj_search_api.search.phrase import escape_solr_phrase, tokenize_keywords

_ARSA_QF = "AllText^0.1 PrimaryAccessionNumber^20 AccessionNumber^10 Definition^5 Organism^3 ReferenceTitle^2"
# ``fl`` must include every source field that ``arsa_docs_to_hits`` reads;
# omitting one silently demotes it to ``None`` in the DbPortalHitTrad envelope
# even though Solr has the value.  ``Feature`` is needed only to recover the
# TaxID from ``/db_xref="taxon:..."`` so ``organism.identifier`` can be set.
_ARSA_FL = "PrimaryAccessionNumber,Definition,Organism,Division,Date,MolecularType,SequenceLength,Feature,score"
_TXSEARCH_QF = "scientific_name^10 scientific_name_ex^20 common_name^5 synonym^3 japanese_name^5 text^0.1"
_TXSEARCH_FL = "tax_id,scientific_name,common_name,japanese_name,rank,lineage,score"
_DEFAULT_Q = "*:*"

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


def _build_q_string(keywords: str | None) -> str:
    """Build the ``q`` parameter: all tokens quoted and space-joined."""
    parsed = tokenize_keywords(keywords)
    if not parsed:
        return _DEFAULT_Q
    return " ".join(f'"{escape_solr_phrase(t)}"' for t in parsed)


def _pagination_to_start_rows(page: int, per_page: int) -> tuple[int, int]:
    return (max(0, (page - 1) * per_page), max(0, per_page))


def build_arsa_params(
    *,
    keywords: str | None,
    page: int,
    per_page: int,
    sort: str | None,
    shards: str | None,
) -> dict[str, str]:
    """Build Solr query params for ARSA ``/collection1/select``.

    ``shards`` (comma-separated ``host:port/solr/core`` list) is appended
    only when non-empty, enabling distributed fan-out on staging/prod and
    single-shard fallback in tests.
    """
    start, rows = _pagination_to_start_rows(page, per_page)
    params: dict[str, str] = {
        "q": _build_q_string(keywords),
        "defType": "edismax",
        "qf": _ARSA_QF,
        "fl": _ARSA_FL,
        "start": str(start),
        "rows": str(rows),
        "wt": "json",
    }
    if sort in _ARSA_SORT_ALLOWLIST:
        params["sort"] = _ARSA_SORT_ALLOWLIST[sort]
    if shards is not None and shards.strip():
        params["shards"] = shards
    return params


def build_txsearch_params(
    *,
    keywords: str | None,
    page: int,
    per_page: int,
    sort: str | None,
) -> dict[str, str]:
    """Build Solr query params for TXSearch ``/ncbi_taxonomy/select``.

    ``sort`` is silently ignored (taxonomy has no date fields and the API
    allowlist only exposes ``datePublished:*``).  Argument retained for
    caller symmetry with :func:`build_arsa_params`.
    """
    _ = sort
    start, rows = _pagination_to_start_rows(page, per_page)
    return {
        "q": _build_q_string(keywords),
        "defType": "edismax",
        "qf": _TXSEARCH_QF,
        "fl": _TXSEARCH_FL,
        "start": str(start),
        "rows": str(rows),
        "wt": "json",
    }


def build_arsa_adv_params(
    *,
    q: str,
    page: int,
    per_page: int,
    sort: str | None,
    shards: str | None,
) -> dict[str, str]:
    """Build Solr params for ARSA when the caller already has a DSL-compiled ``q`` string.

    Same shape as :func:`build_arsa_params` except that ``q`` is passed through verbatim
    and ``uf`` restricts referenceable fields to the DSL allowlist.
    """
    start, rows = _pagination_to_start_rows(page, per_page)
    params: dict[str, str] = {
        "q": q,
        "defType": "edismax",
        "qf": _ARSA_QF,
        "fl": _ARSA_FL,
        "uf": _ARSA_ADV_UF,
        "start": str(start),
        "rows": str(rows),
        "wt": "json",
    }
    if sort in _ARSA_SORT_ALLOWLIST:
        params["sort"] = _ARSA_SORT_ALLOWLIST[sort]
    if shards is not None and shards.strip():
        params["shards"] = shards
    return params


def build_txsearch_adv_params(
    *,
    q: str,
    page: int,
    per_page: int,
    sort: str | None,
) -> dict[str, str]:
    """Build Solr params for TXSearch with a DSL-compiled ``q`` string."""
    _ = sort
    start, rows = _pagination_to_start_rows(page, per_page)
    return {
        "q": q,
        "defType": "edismax",
        "qf": _TXSEARCH_QF,
        "fl": _TXSEARCH_FL,
        "uf": _TXSEARCH_ADV_UF,
        "start": str(start),
        "rows": str(rows),
        "wt": "json",
    }
