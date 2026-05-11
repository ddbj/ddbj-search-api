"""Tests for ddbj_search_api.solr.query.

Covers Solr-specific edismax param builders for ARSA and TXSearch. The
caller (router) builds the ``q`` string via :mod:`ddbj_search_api.search.dsl`
(``compile_to_solr`` / ``compile_free_text_solr``); these tests verify the
remaining qf / fl / uf / sort / start / rows wiring around it.
"""

from __future__ import annotations

from ddbj_search_api.search.dsl import arsa_uf_fields, txsearch_uf_fields
from ddbj_search_api.solr.query import (
    build_arsa_request_params,
    build_txsearch_request_params,
)

# === Unified request params (AST 経由 handler が呼ぶ新 API) ===


class TestBuildArsaRequestParams:
    """compile_to_solr 経由で生成した ``q`` を Solr params に組み立てる新 API."""

    def test_q_passed_through(self) -> None:
        p = build_arsa_request_params(
            q='"cancer"', page=1, per_page=20, sort=None, shards=None, with_uf=False,
        )
        assert p["q"] == '"cancer"'

    def test_def_type_edismax(self) -> None:
        p = build_arsa_request_params(
            q="*:*", page=1, per_page=20, sort=None, shards=None, with_uf=False,
        )
        assert p["defType"] == "edismax"

    def test_qf_and_fl_constants(self) -> None:
        p = build_arsa_request_params(
            q="*:*", page=1, per_page=20, sort=None, shards=None, with_uf=False,
        )
        assert "AllText^0.1" in p["qf"]
        assert "PrimaryAccessionNumber" in p["fl"]

    def test_without_uf_omits_param(self) -> None:
        """FreeText 単独 (with_uf=False) では uf を付けない (現状 build_arsa_params 互換)."""
        p = build_arsa_request_params(
            q='"cancer"', page=1, per_page=20, sort=None, shards=None, with_uf=False,
        )
        assert "uf" not in p

    def test_with_uf_adds_allowlist(self) -> None:
        """adv 含む AST (with_uf=True) では uf に allowlist を付与."""
        p = build_arsa_request_params(
            q='Division:"BCT"', page=1, per_page=20, sort=None, shards=None, with_uf=True,
        )
        assert "uf" in p
        uf_set = set(p["uf"].split())
        for field in arsa_uf_fields():
            assert field in uf_set

    def test_start_rows_pagination(self) -> None:
        p = build_arsa_request_params(
            q="*:*", page=3, per_page=20, sort=None, shards=None, with_uf=False,
        )
        assert p["start"] == "40"
        assert p["rows"] == "20"

    def test_sort_passes_through_allowlist(self) -> None:
        p = build_arsa_request_params(
            q="*:*", page=1, per_page=20, sort="datePublished:desc", shards=None, with_uf=False,
        )
        assert p["sort"] == "Date desc"

    def test_sort_outside_allowlist_omits(self) -> None:
        p = build_arsa_request_params(
            q="*:*", page=1, per_page=20, sort="bogus:desc", shards=None, with_uf=False,
        )
        assert "sort" not in p

    def test_shards_included_when_provided(self) -> None:
        p = build_arsa_request_params(
            q="*:*", page=1, per_page=20, sort=None,
            shards="a:1/solr/c,a:2/solr/c", with_uf=False,
        )
        assert p["shards"] == "a:1/solr/c,a:2/solr/c"

    def test_no_shards_when_blank(self) -> None:
        p = build_arsa_request_params(
            q="*:*", page=1, per_page=20, sort=None, shards="  ", with_uf=False,
        )
        assert "shards" not in p

    def test_wt_json(self) -> None:
        p = build_arsa_request_params(
            q="*:*", page=1, per_page=20, sort=None, shards=None, with_uf=False,
        )
        assert p["wt"] == "json"


class TestBuildTxsearchRequestParams:
    def test_q_passed_through(self) -> None:
        p = build_txsearch_request_params(
            q='"Homo"', page=1, per_page=20, sort=None, with_uf=False,
        )
        assert p["q"] == '"Homo"'

    def test_def_type_edismax(self) -> None:
        p = build_txsearch_request_params(
            q="*:*", page=1, per_page=20, sort=None, with_uf=False,
        )
        assert p["defType"] == "edismax"

    def test_without_uf_omits_param(self) -> None:
        p = build_txsearch_request_params(
            q='"x"', page=1, per_page=20, sort=None, with_uf=False,
        )
        assert "uf" not in p

    def test_with_uf_adds_allowlist(self) -> None:
        p = build_txsearch_request_params(
            q='rank:"species"', page=1, per_page=20, sort=None, with_uf=True,
        )
        assert "uf" in p
        uf_set = set(p["uf"].split())
        for field in txsearch_uf_fields():
            assert field in uf_set

    def test_sort_silently_ignored(self) -> None:
        """Taxonomy に日付なし → sort は無視."""
        p = build_txsearch_request_params(
            q="*:*", page=1, per_page=20, sort="datePublished:desc", with_uf=False,
        )
        assert "sort" not in p

    def test_no_shards_param(self) -> None:
        p = build_txsearch_request_params(
            q="*:*", page=1, per_page=20, sort=None, with_uf=False,
        )
        assert "shards" not in p

    def test_start_rows_pagination(self) -> None:
        p = build_txsearch_request_params(
            q="*:*", page=2, per_page=50, sort=None, with_uf=False,
        )
        assert p["start"] == "50"
        assert p["rows"] == "50"
