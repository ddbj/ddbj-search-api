"""Tests for ddbj_search_api.solr.query.

Covers Solr-specific edismax param builders for ARSA and TXSearch. The
caller (router) builds the ``q`` string via :mod:`ddbj_search_api.search.dsl`
(``compile_to_solr`` / ``compile_free_text_solr``); these tests verify the
remaining qf / fl / uf / sort / start / rows wiring around it.
"""

from __future__ import annotations

from ddbj_search_api.search.dsl import arsa_uf_fields, txsearch_uf_fields
from ddbj_search_api.search.dsl.compiler_solr import _ARSA_FIELD_MAP, _TXSEARCH_FIELD_MAP
from ddbj_search_api.solr.query import (
    DB_PORTAL_SOLR_FACET_NAMES,
    arsa_facet_field_map,
    build_arsa_request_params,
    build_txsearch_request_params,
    txsearch_facet_field_map,
)

# === Unified request params ===


class TestBuildArsaRequestParams:
    """compile_to_solr で生成した ``q`` を ARSA Solr params に組み立てる."""

    def test_q_passed_through(self) -> None:
        p = build_arsa_request_params(
            q='"cancer"',
            page=1,
            per_page=20,
            sort=None,
            shards=None,
            with_uf=False,
        )
        assert p["q"] == '"cancer"'

    def test_def_type_edismax(self) -> None:
        p = build_arsa_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort=None,
            shards=None,
            with_uf=False,
        )
        assert p["defType"] == "edismax"

    def test_qf_and_fl_constants(self) -> None:
        p = build_arsa_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort=None,
            shards=None,
            with_uf=False,
        )
        assert "AllText^0.1" in p["qf"]
        assert "PrimaryAccessionNumber" in p["fl"]

    def test_without_uf_omits_param(self) -> None:
        """FreeText 単独 (with_uf=False) では uf を付けない."""
        p = build_arsa_request_params(
            q='"cancer"',
            page=1,
            per_page=20,
            sort=None,
            shards=None,
            with_uf=False,
        )
        assert "uf" not in p

    def test_with_uf_adds_allowlist(self) -> None:
        """adv 含む AST (with_uf=True) では uf に allowlist を付与."""
        p = build_arsa_request_params(
            q='Division:"BCT"',
            page=1,
            per_page=20,
            sort=None,
            shards=None,
            with_uf=True,
        )
        assert "uf" in p
        uf_set = set(p["uf"].split())
        for field in arsa_uf_fields():
            assert field in uf_set

    def test_start_rows_pagination(self) -> None:
        p = build_arsa_request_params(
            q="*:*",
            page=3,
            per_page=20,
            sort=None,
            shards=None,
            with_uf=False,
        )
        assert p["start"] == "40"
        assert p["rows"] == "20"

    def test_sort_passes_through_allowlist(self) -> None:
        p = build_arsa_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort="datePublished:desc",
            shards=None,
            with_uf=False,
        )
        assert p["sort"] == "Date desc"

    def test_sort_outside_allowlist_omits(self) -> None:
        p = build_arsa_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort="bogus:desc",
            shards=None,
            with_uf=False,
        )
        assert "sort" not in p

    def test_shards_included_when_provided(self) -> None:
        p = build_arsa_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort=None,
            shards="a:1/solr/c,a:2/solr/c",
            with_uf=False,
        )
        assert p["shards"] == "a:1/solr/c,a:2/solr/c"

    def test_no_shards_when_blank(self) -> None:
        p = build_arsa_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort=None,
            shards="  ",
            with_uf=False,
        )
        assert "shards" not in p

    def test_wt_json(self) -> None:
        p = build_arsa_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort=None,
            shards=None,
            with_uf=False,
        )
        assert p["wt"] == "json"


class TestBuildTxsearchRequestParams:
    def test_q_passed_through(self) -> None:
        p = build_txsearch_request_params(
            q='"Homo"',
            page=1,
            per_page=20,
            sort=None,
            with_uf=False,
        )
        assert p["q"] == '"Homo"'

    def test_def_type_edismax(self) -> None:
        p = build_txsearch_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort=None,
            with_uf=False,
        )
        assert p["defType"] == "edismax"

    def test_without_uf_omits_param(self) -> None:
        p = build_txsearch_request_params(
            q='"x"',
            page=1,
            per_page=20,
            sort=None,
            with_uf=False,
        )
        assert "uf" not in p

    def test_with_uf_adds_allowlist(self) -> None:
        p = build_txsearch_request_params(
            q='rank:"species"',
            page=1,
            per_page=20,
            sort=None,
            with_uf=True,
        )
        assert "uf" in p
        uf_set = set(p["uf"].split())
        for field in txsearch_uf_fields():
            assert field in uf_set

    def test_sort_silently_ignored(self) -> None:
        """Taxonomy に日付なし → sort は無視."""
        p = build_txsearch_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort="datePublished:desc",
            with_uf=False,
        )
        assert "sort" not in p

    def test_no_shards_param(self) -> None:
        p = build_txsearch_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort=None,
            with_uf=False,
        )
        assert "shards" not in p

    def test_start_rows_pagination(self) -> None:
        p = build_txsearch_request_params(
            q="*:*",
            page=2,
            per_page=50,
            sort=None,
            with_uf=False,
        )
        assert p["start"] == "50"
        assert p["rows"] == "50"


# === db-portal facet 集計 (terms faceting params + field maps) ===


class TestArsaFacetParams:
    """``facet_fields`` 指定で terms faceting params を相乗りさせる."""

    def test_no_facet_params_when_empty(self) -> None:
        """facet_fields 未指定 (既定) では facet 系 key を一切付けない."""
        p = build_arsa_request_params(q="*:*", page=1, per_page=20, sort=None, shards=None, with_uf=False)
        assert "facet" not in p
        assert "facet.field" not in p
        assert "facet.mincount" not in p
        assert "facet.limit" not in p

    def test_facet_params_present(self) -> None:
        p = build_arsa_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort=None,
            shards=None,
            with_uf=False,
            facet_fields=["Division", "MolecularType"],
            facet_limit=50,
        )
        assert p["facet"] == "true"
        assert p["facet.mincount"] == "1"
        assert p["facet.limit"] == "50"

    def test_facet_field_is_list_for_multivalue(self) -> None:
        """facet.field は list で保持する (httpx が repeated param に展開する).

        str に collapse すると Solr が単一フィールド名として誤解釈し、
        2 つ目以降の facet が黙って欠落する。
        """
        p = build_arsa_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort=None,
            shards=None,
            with_uf=False,
            facet_fields=["Division", "MolecularType"],
            facet_limit=100,
        )
        assert p["facet.field"] == ["Division", "MolecularType"]
        assert isinstance(p["facet.field"], list)

    def test_facet_limit_default_is_100(self) -> None:
        p = build_arsa_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort=None,
            shards=None,
            with_uf=False,
            facet_fields=["Division"],
        )
        assert p["facet.limit"] == "100"

    def test_facets_coexist_with_shards(self) -> None:
        """8 shard fan-out (shards) と facet params が両立する (分散集計)."""
        p = build_arsa_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort=None,
            shards="a:1/solr/c,a:2/solr/c",
            with_uf=False,
            facet_fields=["Division"],
            facet_limit=100,
        )
        assert p["shards"] == "a:1/solr/c,a:2/solr/c"
        assert p["facet"] == "true"
        assert p["facet.field"] == ["Division"]


class TestTxsearchFacetParams:
    def test_no_facet_params_when_empty(self) -> None:
        p = build_txsearch_request_params(q="*:*", page=1, per_page=20, sort=None, with_uf=False)
        assert "facet" not in p
        assert "facet.field" not in p

    def test_facet_params_present(self) -> None:
        p = build_txsearch_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort=None,
            with_uf=False,
            facet_fields=["rank", "kingdom"],
            facet_limit=25,
        )
        assert p["facet"] == "true"
        assert p["facet.field"] == ["rank", "kingdom"]
        assert p["facet.mincount"] == "1"
        assert p["facet.limit"] == "25"


class TestDbPortalSolrFacetMaps:
    """facet wire-name → Solr field map と DSL compiler との整合."""

    def test_arsa_map_content(self) -> None:
        assert arsa_facet_field_map() == {"division": "Division", "molecularType": "MolecularType"}

    def test_txsearch_map_content(self) -> None:
        assert txsearch_facet_field_map() == {"rank": "rank", "kingdom": "kingdom"}

    def test_db_portal_solr_facet_names(self) -> None:
        assert frozenset({"division", "molecularType", "rank", "kingdom"}) == DB_PORTAL_SOLR_FACET_NAMES

    def test_maps_return_independent_copies(self) -> None:
        m = arsa_facet_field_map()
        m["injected"] = "Bogus"
        assert "injected" not in arsa_facet_field_map()

    def test_arsa_facet_field_matches_compiler_field(self) -> None:
        """facet.field は同 DSL field の compiler マップ先と一致しなければならない.

        ズレると facet bucket の value を ``molecular_type:<value>`` 等で
        再注入したとき別 Solr field を叩き、母集団が facet と食い違う。
        """
        facet_to_dsl = {"division": "division", "molecularType": "molecular_type"}
        for facet_name, solr_field in arsa_facet_field_map().items():
            dsl_field = facet_to_dsl[facet_name]
            assert solr_field in _ARSA_FIELD_MAP[dsl_field]

    def test_txsearch_facet_field_matches_compiler_field(self) -> None:
        for facet_name, solr_field in txsearch_facet_field_map().items():
            assert solr_field in _TXSEARCH_FIELD_MAP[facet_name]
