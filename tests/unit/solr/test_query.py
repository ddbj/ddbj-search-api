"""Tests for ddbj_search_api.solr.query.

Covers Solr-specific edismax param builders for ARSA and TXSearch and
the q string assembler.  Trigger set / tokenize / escape behaviour for
the shared helpers lives in ``tests/unit/search/test_phrase.py``.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from ddbj_search_api.search.dsl import arsa_uf_fields, txsearch_uf_fields
from ddbj_search_api.solr.query import (
    _build_q_string,
    build_arsa_adv_params,
    build_arsa_params,
    build_txsearch_adv_params,
    build_txsearch_params,
)

# === q string builder ===


class TestBuildQString:
    def test_none_is_match_all(self) -> None:
        assert _build_q_string(None) == "*:*"

    def test_empty_is_match_all(self) -> None:
        assert _build_q_string("") == "*:*"

    def test_single_keyword_quoted(self) -> None:
        assert _build_q_string("cancer") == '"cancer"'

    def test_hyphen_keyword_quoted(self) -> None:
        assert _build_q_string("HIF-1") == '"HIF-1"'

    def test_multiple_keywords_joined(self) -> None:
        assert _build_q_string("cancer,human") == '"cancer" "human"'

    def test_quote_inside_keyword_stripped(self) -> None:
        # tokenize_keywords treats stray ``"`` as parser toggles and
        # strips them before escape, so that a single ``"`` in user input
        # never injects phrase syntax.
        assert _build_q_string('a"b') == '"ab"'

    def test_backslash_inside_keyword_escaped(self) -> None:
        assert _build_q_string("a\\b") == '"a\\\\b"'


# === ARSA params ===


class TestBuildArsaParams:
    def test_no_keywords_match_all(self) -> None:
        p = build_arsa_params(keywords=None, page=1, per_page=20, sort=None, shards=None)
        assert p["q"] == "*:*"

    def test_single_keyword(self) -> None:
        p = build_arsa_params(keywords="cancer", page=1, per_page=20, sort=None, shards=None)
        assert p["q"] == '"cancer"'

    def test_hyphen_keyword(self) -> None:
        p = build_arsa_params(keywords="HIF-1", page=1, per_page=20, sort=None, shards=None)
        assert p["q"] == '"HIF-1"'

    def test_def_type_edismax(self) -> None:
        p = build_arsa_params(keywords="x", page=1, per_page=20, sort=None, shards=None)
        assert p["defType"] == "edismax"

    def test_qf_has_arsa_boosts(self) -> None:
        p = build_arsa_params(keywords="x", page=1, per_page=20, sort=None, shards=None)
        qf = p["qf"]
        assert "AllText^0.1" in qf
        assert "PrimaryAccessionNumber^20" in qf
        assert "AccessionNumber^10" in qf
        assert "Definition^5" in qf
        assert "Organism^3" in qf
        assert "ReferenceTitle^2" in qf

    def test_fl_includes_arsa_fields(self) -> None:
        p = build_arsa_params(keywords="x", page=1, per_page=20, sort=None, shards=None)
        fl = p["fl"]
        # Must match every field arsa_docs_to_hits reads; otherwise Solr
        # drops the value from its response and the hit envelope ends up
        # with ``None`` (regression: 2026-04-24 MolecularType/SequenceLength,
        # Feature added so we can recover TaxID for organism.identifier).
        for field in (
            "PrimaryAccessionNumber",
            "Definition",
            "Organism",
            "Division",
            "Date",
            "MolecularType",
            "SequenceLength",
            "Feature",
            "score",
        ):
            assert field in fl

    def test_start_and_rows(self) -> None:
        p = build_arsa_params(keywords="x", page=2, per_page=20, sort=None, shards=None)
        assert p["start"] == "20"
        assert p["rows"] == "20"

    def test_start_first_page_is_zero(self) -> None:
        p = build_arsa_params(keywords="x", page=1, per_page=50, sort=None, shards=None)
        assert p["start"] == "0"
        assert p["rows"] == "50"

    def test_start_rows_zero(self) -> None:
        p = build_arsa_params(keywords="x", page=1, per_page=0, sort=None, shards=None)
        assert p["rows"] == "0"

    def test_wt_json(self) -> None:
        p = build_arsa_params(keywords="x", page=1, per_page=20, sort=None, shards=None)
        assert p["wt"] == "json"

    def test_shards_included_when_provided(self) -> None:
        p = build_arsa_params(
            keywords="x",
            page=1,
            per_page=20,
            sort=None,
            shards="a012:51981/solr/collection1,a012:51982/solr/collection1",
        )
        assert p["shards"] == "a012:51981/solr/collection1,a012:51982/solr/collection1"

    def test_no_shards_when_none(self) -> None:
        p = build_arsa_params(keywords="x", page=1, per_page=20, sort=None, shards=None)
        assert "shards" not in p

    def test_no_shards_when_blank(self) -> None:
        p = build_arsa_params(keywords="x", page=1, per_page=20, sort=None, shards="  ")
        assert "shards" not in p

    def test_sort_date_desc(self) -> None:
        p = build_arsa_params(keywords="x", page=1, per_page=20, sort="datePublished:desc", shards=None)
        assert p["sort"] == "Date desc"

    def test_sort_date_asc(self) -> None:
        p = build_arsa_params(keywords="x", page=1, per_page=20, sort="datePublished:asc", shards=None)
        assert p["sort"] == "Date asc"

    def test_sort_none_omits_param(self) -> None:
        p = build_arsa_params(keywords="x", page=1, per_page=20, sort=None, shards=None)
        assert "sort" not in p


# === TXSearch params ===


class TestBuildTxsearchParams:
    def test_def_type_edismax(self) -> None:
        p = build_txsearch_params(keywords="x", page=1, per_page=20, sort=None)
        assert p["defType"] == "edismax"

    def test_qf_has_txsearch_boosts(self) -> None:
        p = build_txsearch_params(keywords="x", page=1, per_page=20, sort=None)
        qf = p["qf"]
        assert "scientific_name^10" in qf
        assert "scientific_name_ex^20" in qf
        assert "common_name^5" in qf
        assert "synonym^3" in qf
        assert "japanese_name^5" in qf
        assert "text^0.1" in qf

    def test_fl_includes_txsearch_fields(self) -> None:
        p = build_txsearch_params(keywords="x", page=1, per_page=20, sort=None)
        fl = p["fl"]
        for field in ("tax_id", "scientific_name", "common_name", "japanese_name", "rank", "lineage", "score"):
            assert field in fl

    def test_single_keyword_quoted(self) -> None:
        p = build_txsearch_params(keywords="Homo sapiens", page=1, per_page=20, sort=None)
        assert p["q"] == '"Homo sapiens"'

    def test_start_rows(self) -> None:
        p = build_txsearch_params(keywords="x", page=3, per_page=50, sort=None)
        assert p["start"] == "100"
        assert p["rows"] == "50"

    def test_sort_silently_ignored(self) -> None:
        # TXSearch has no date field; the API-layer sort param must not reach Solr.
        p = build_txsearch_params(keywords="x", page=1, per_page=20, sort="datePublished:desc")
        assert "sort" not in p

    def test_no_shards_param(self) -> None:
        p = build_txsearch_params(keywords="x", page=1, per_page=20, sort=None)
        assert "shards" not in p

    def test_wt_json(self) -> None:
        p = build_txsearch_params(keywords="x", page=1, per_page=20, sort=None)
        assert p["wt"] == "json"


# === adv params: uf allowlist coverage ===


class TestAdvUfAllowlistCoverage:
    """``uf`` must cover every field compile_to_solr can emit.

    Otherwise edismax treats ``Field:value`` as a bare keyword, matches it
    against ``qf`` (= wrong field), and returns wildly wrong counts —
    staging probe 2026-04-24: ``Division:"BCT"`` returned 88.8M / all-docs
    without ``uf`` vs 753k with it.
    """

    def test_arsa_adv_uf_covers_every_compiler_field(self) -> None:
        p = build_arsa_adv_params(q="*:*", page=1, per_page=20, sort=None, shards=None)
        uf_set = set(p["uf"].split())
        for field in arsa_uf_fields():
            assert field in uf_set, f"ARSA compiler field {field!r} missing from uf allowlist"

    def test_txsearch_adv_uf_covers_every_compiler_field(self) -> None:
        p = build_txsearch_adv_params(q="*:*", page=1, per_page=20, sort=None)
        uf_set = set(p["uf"].split())
        for field in txsearch_uf_fields():
            assert field in uf_set, f"TXSearch compiler field {field!r} missing from uf allowlist"


# === PBT ===


class TestSolrQueryPBT:
    @given(keyword=st.text(min_size=1, max_size=30).filter(lambda s: '"' not in s and "," not in s and s.strip()))
    def test_single_keyword_appears_quoted_in_q(self, keyword: str) -> None:
        p = build_arsa_params(keywords=keyword, page=1, per_page=20, sort=None, shards=None)
        assert p["q"].startswith('"')
        assert p["q"].endswith('"')

    @given(
        page=st.integers(min_value=1, max_value=500),
        per_page=st.integers(min_value=0, max_value=100),
    )
    def test_start_and_rows_non_negative(self, page: int, per_page: int) -> None:
        p = build_arsa_params(keywords="x", page=page, per_page=per_page, sort=None, shards=None)
        assert int(p["start"]) >= 0
        assert int(p["rows"]) >= 0
        assert int(p["rows"]) == per_page
        assert int(p["start"]) == (page - 1) * per_page

    @given(text=st.text(max_size=30).filter(lambda s: '"' not in s and "," not in s and s.strip()))
    def test_q_string_quoted_keyword_via_build(self, text: str) -> None:
        # _build_q_string が tokenize_keywords + escape_solr_phrase を統合し
        # 必ず ``"..."`` で wrap することの統合 regression check
        q = _build_q_string(text)
        assert q.startswith('"')
        assert q.endswith('"')
