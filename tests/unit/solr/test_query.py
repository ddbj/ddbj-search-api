"""Tests for ddbj_search_api.solr.query (AP4).

Covers auto-phrase trigger set (Solr extended), keyword parsing with
comma-split and quoted preservation, Solr phrase escaping, and the
edismax param builders for ARSA and TXSearch.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ddbj_search_api.solr.query import (
    _SOLR_AUTO_PHRASE_CHARS,
    _build_q_string,
    _escape_solr_phrase,
    _has_solr_auto_phrase_trigger,
    _parse_solr_keywords,
    build_arsa_params,
    build_txsearch_params,
)

# === Symbol set ===


class TestSolrAutoPhraseChars:
    """Solr auto-phrase set: ES chars + Solr syntax meta chars."""

    @pytest.mark.parametrize("c", list("-/.+:"))
    def test_es_triggers_included(self, c: str) -> None:
        assert c in _SOLR_AUTO_PHRASE_CHARS

    @pytest.mark.parametrize("c", list("*?()[]{}^~!|&\\"))
    def test_solr_extra_triggers(self, c: str) -> None:
        assert c in _SOLR_AUTO_PHRASE_CHARS

    @pytest.mark.parametrize("c", list("abcXYZ0123 _"))
    def test_alphanumeric_not_trigger(self, c: str) -> None:
        assert c not in _SOLR_AUTO_PHRASE_CHARS


class TestHasSolrAutoPhraseTrigger:
    def test_plain_word(self) -> None:
        assert _has_solr_auto_phrase_trigger("cancer") is False

    def test_hyphen(self) -> None:
        assert _has_solr_auto_phrase_trigger("HIF-1") is True

    def test_slash(self) -> None:
        assert _has_solr_auto_phrase_trigger("BRCA1/2") is True

    def test_wildcard(self) -> None:
        assert _has_solr_auto_phrase_trigger("can*") is True

    def test_backslash(self) -> None:
        assert _has_solr_auto_phrase_trigger("a\\b") is True


# === Keyword parser ===


class TestParseSolrKeywords:
    def test_none_returns_empty_list(self) -> None:
        assert _parse_solr_keywords(None) == []

    def test_empty_returns_empty_list(self) -> None:
        assert _parse_solr_keywords("") == []

    def test_whitespace_only_returns_empty(self) -> None:
        assert _parse_solr_keywords("   ") == []

    def test_single_keyword(self) -> None:
        assert _parse_solr_keywords("cancer") == ["cancer"]

    def test_quoted_keyword_preserves_hyphen(self) -> None:
        assert _parse_solr_keywords('"RNA-Seq"') == ["RNA-Seq"]

    def test_quoted_keyword_strips_quotes(self) -> None:
        assert _parse_solr_keywords('"cancer"') == ["cancer"]

    def test_comma_separated(self) -> None:
        assert _parse_solr_keywords("cancer,human") == ["cancer", "human"]

    def test_comma_separated_with_whitespace(self) -> None:
        assert _parse_solr_keywords("cancer , human") == ["cancer", "human"]

    def test_quote_with_inner_comma_preserved(self) -> None:
        assert _parse_solr_keywords('"cancer, human"') == ["cancer, human"]

    def test_mix_quoted_and_plain(self) -> None:
        assert _parse_solr_keywords('"HIF-1",cancer') == ["HIF-1", "cancer"]

    def test_empty_inside_quotes_dropped(self) -> None:
        assert _parse_solr_keywords('""') == []

    def test_unclosed_quote_cleaned(self) -> None:
        # stray quote removed; token kept
        assert _parse_solr_keywords('HIF-1"') == ["HIF-1"]


# === Phrase escape ===


class TestEscapeSolrPhrase:
    def test_plain(self) -> None:
        assert _escape_solr_phrase("cancer") == "cancer"

    def test_backslash_doubled(self) -> None:
        assert _escape_solr_phrase("a\\b") == "a\\\\b"

    def test_quote_escaped(self) -> None:
        assert _escape_solr_phrase('a"b') == 'a\\"b'

    def test_backslash_escaped_before_quote(self) -> None:
        # "\"" --> "\\\""  (backslash doubled THEN quote escaped)
        assert _escape_solr_phrase('a\\"b') == 'a\\\\\\"b'

    def test_hyphen_unchanged(self) -> None:
        assert _escape_solr_phrase("HIF-1") == "HIF-1"

    def test_slash_unchanged(self) -> None:
        assert _escape_solr_phrase("BRCA1/2") == "BRCA1/2"


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
        # _parse_solr_keywords treats stray ``"`` as parser toggles and
        # strips them before escape, mirroring ES ``_parse_keywords`` so
        # that a single ``"`` in user input never injects phrase syntax.
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
        for field in ("PrimaryAccessionNumber", "Definition", "Organism", "Division", "Date", "score"):
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

    @given(text=st.text(max_size=50))
    def test_parse_never_crashes(self, text: str) -> None:
        tokens = _parse_solr_keywords(text)
        for t in tokens:
            assert '"' not in t  # stripped / cleaned

    @given(text=st.text(max_size=30).filter(lambda s: s.strip() and "," not in s))
    def test_escape_preserves_non_meta(self, text: str) -> None:
        escaped = _escape_solr_phrase(text)
        # backslash count must not decrease (only increase via doubling)
        assert escaped.count("\\") >= text.count("\\")
