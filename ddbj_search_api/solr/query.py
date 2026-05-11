"""Solr edismax query builder for ARSA and TXSearch.

handler が ``compile_to_solr`` 経由で生成した edismax ``q`` 文字列を受け取り、
qf / fl / uf / sort / start / rows / shards を Solr ``/select`` リクエスト用の
dict に組み立てる。q 文字列の組み立て自体は ``ddbj_search_api.search.dsl.compiler_solr``
が担当する (auto-phrase / quote escape は ``compile_free_text_solr`` 経由)。
"""

from __future__ import annotations

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
) -> dict[str, str]:
    """Build Solr query params for ARSA ``/collection1/select`` from a pre-compiled ``q`` string.

    ``q`` は handler が :func:`ddbj_search_api.search.dsl.compile_to_solr` で生成した
    edismax ``q`` 文字列 (FreeText 単独なら ``"..."`` 群、``field:value`` 含むなら
    ``(<field_compiled> AND "<token>"...)`` 等)。``with_uf`` が True のとき
    ``uf`` (edismax allowlist) を付与する。AST 内に ``FieldClause`` が含まれるかを
    handler 側で判定して渡す
    (:func:`ddbj_search_api.search.dsl.inspect.ast_has_field_clause`)。
    """
    start, rows = _pagination_to_start_rows(page, per_page)
    params: dict[str, str] = {
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
    return params


def build_txsearch_request_params(
    *,
    q: str,
    page: int,
    per_page: int,
    sort: str | None,
    with_uf: bool,
) -> dict[str, str]:
    """Build Solr query params for TXSearch ``/ncbi_taxonomy/select`` from a pre-compiled ``q`` string.

    ``sort`` は Taxonomy に日付フィールドが無いため silently ignored (caller symmetry
    のため引数だけ受ける)。``with_uf`` の判定基準は
    :func:`build_arsa_request_params` と同じ。
    """
    _ = sort
    start, rows = _pagination_to_start_rows(page, per_page)
    params: dict[str, str] = {
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
    return params
