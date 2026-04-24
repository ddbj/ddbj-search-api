"""Tests for ddbj_search_api.search.phrase (ES/Solr 共通 auto-phrase util).

ES と Solr 両 backend で再利用する auto-phrase 関連 helper:
- ``ES_AUTO_PHRASE_CHARS`` / ``SOLR_AUTO_PHRASE_CHARS`` : trigger char set
- ``has_auto_phrase_trigger`` : trigger 検出 (trigger-set パラメータ化)
- ``tokenize_keywords`` : カンマ分割 + quote 保持 (両 backend 共通)
- ``parse_keywords_with_autophrase`` : tokenize + phrase flag (ES 用)
- ``escape_solr_phrase`` : Solr phrase 内エスケープ (Solr 用)
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ddbj_search_api.search.phrase import (
    ES_AUTO_PHRASE_CHARS,
    SOLR_AUTO_PHRASE_CHARS,
    escape_solr_phrase,
    has_auto_phrase_trigger,
    parse_keywords_with_autophrase,
    tokenize_keywords,
)
from tests.unit.strategies import (
    ES_AUTO_PHRASE_TRIGGERS,
    SOLR_AUTO_PHRASE_TRIGGERS,
    alphanumeric_no_trigger,
    text_with_trigger,
)

# ===================================================================
# Trigger char set 構成
# ===================================================================


class TestAutoPhraseChars:
    """ES と Solr の trigger char set 構成。"""

    @pytest.mark.parametrize("c", list("-/.+:"))
    def test_es_includes_core(self, c: str) -> None:
        assert c in ES_AUTO_PHRASE_CHARS

    @pytest.mark.parametrize("c", list("-/.+:"))
    def test_solr_includes_es_chars(self, c: str) -> None:
        assert c in SOLR_AUTO_PHRASE_CHARS

    @pytest.mark.parametrize("c", list("*?()[]{}^~!|&\\"))
    def test_solr_includes_meta(self, c: str) -> None:
        assert c in SOLR_AUTO_PHRASE_CHARS

    @pytest.mark.parametrize("c", list("*?()[]{}^~!|&\\"))
    def test_es_excludes_solr_meta(self, c: str) -> None:
        assert c not in ES_AUTO_PHRASE_CHARS

    @pytest.mark.parametrize("c", list("abcXYZ0123 _"))
    def test_alphanumeric_not_trigger_es(self, c: str) -> None:
        assert c not in ES_AUTO_PHRASE_CHARS

    @pytest.mark.parametrize("c", list("abcXYZ0123 _"))
    def test_alphanumeric_not_trigger_solr(self, c: str) -> None:
        assert c not in SOLR_AUTO_PHRASE_CHARS

    def test_es_size(self) -> None:
        assert len(ES_AUTO_PHRASE_CHARS) == 5

    def test_solr_size(self) -> None:
        # ES 5 chars + Solr extra 14 chars (`*?()[]{}^~!|&\\`)
        assert len(SOLR_AUTO_PHRASE_CHARS) == 19

    def test_solr_is_strict_superset(self) -> None:
        assert ES_AUTO_PHRASE_CHARS < SOLR_AUTO_PHRASE_CHARS


# ===================================================================
# has_auto_phrase_trigger
# ===================================================================


class TestHasAutoPhraseTrigger:
    """trigger char detection (ES / Solr 両方)。"""

    def test_empty_string_es(self) -> None:
        assert has_auto_phrase_trigger("", ES_AUTO_PHRASE_CHARS) is False

    def test_empty_string_solr(self) -> None:
        assert has_auto_phrase_trigger("", SOLR_AUTO_PHRASE_CHARS) is False

    def test_alphanumeric_es(self) -> None:
        assert has_auto_phrase_trigger("cancer", ES_AUTO_PHRASE_CHARS) is False

    def test_alphanumeric_solr(self) -> None:
        assert has_auto_phrase_trigger("cancer", SOLR_AUTO_PHRASE_CHARS) is False

    def test_hyphen_es(self) -> None:
        assert has_auto_phrase_trigger("HIF-1", ES_AUTO_PHRASE_CHARS) is True

    def test_hyphen_solr(self) -> None:
        assert has_auto_phrase_trigger("HIF-1", SOLR_AUTO_PHRASE_CHARS) is True

    def test_slash_solr(self) -> None:
        assert has_auto_phrase_trigger("BRCA1/2", SOLR_AUTO_PHRASE_CHARS) is True

    def test_solr_meta_only_solr_triggers(self) -> None:
        # Wildcard は Solr trigger だが ES では trigger ではない
        assert has_auto_phrase_trigger("can*", SOLR_AUTO_PHRASE_CHARS) is True
        assert has_auto_phrase_trigger("can*", ES_AUTO_PHRASE_CHARS) is False

    def test_backslash_solr(self) -> None:
        assert has_auto_phrase_trigger("a\\b", SOLR_AUTO_PHRASE_CHARS) is True

    def test_backslash_not_es(self) -> None:
        assert has_auto_phrase_trigger("a\\b", ES_AUTO_PHRASE_CHARS) is False

    def test_japanese_no_trigger_es(self) -> None:
        assert has_auto_phrase_trigger("がん", ES_AUTO_PHRASE_CHARS) is False

    def test_japanese_no_trigger_solr(self) -> None:
        assert has_auto_phrase_trigger("がん", SOLR_AUTO_PHRASE_CHARS) is False


# ===================================================================
# tokenize_keywords
# ===================================================================


class TestTokenizeKeywords:
    """カンマ分割 + quote 保持の境界値網羅。"""

    def test_none_returns_empty(self) -> None:
        assert tokenize_keywords(None) == []

    def test_empty_returns_empty(self) -> None:
        assert tokenize_keywords("") == []

    def test_whitespace_only(self) -> None:
        assert tokenize_keywords("   ") == []

    def test_only_commas_returns_empty(self) -> None:
        assert tokenize_keywords(",,,") == []

    def test_single_keyword(self) -> None:
        assert tokenize_keywords("cancer") == ["cancer"]

    def test_quoted_keyword_strips_quotes(self) -> None:
        assert tokenize_keywords('"cancer"') == ["cancer"]

    def test_quoted_keyword_preserves_hyphen(self) -> None:
        assert tokenize_keywords('"RNA-Seq"') == ["RNA-Seq"]

    def test_quoted_preserves_inner_comma(self) -> None:
        assert tokenize_keywords('"cancer, human"') == ["cancer, human"]

    def test_comma_separated(self) -> None:
        assert tokenize_keywords("cancer,human") == ["cancer", "human"]

    def test_comma_separated_with_whitespace(self) -> None:
        assert tokenize_keywords("cancer , human") == ["cancer", "human"]

    def test_mix_quoted_and_plain(self) -> None:
        assert tokenize_keywords('"HIF-1",cancer') == ["HIF-1", "cancer"]

    def test_empty_inside_quotes_dropped(self) -> None:
        assert tokenize_keywords('""') == []

    def test_unclosed_quote_cleaned(self) -> None:
        # stray quote removed; token kept
        assert tokenize_keywords('HIF-1"') == ["HIF-1"]

    def test_unclosed_quote_at_start(self) -> None:
        assert tokenize_keywords('"HIF-1') == ["HIF-1"]

    def test_long_input(self) -> None:
        long_text = "a" * 5000
        assert tokenize_keywords(long_text) == [long_text]


# ===================================================================
# parse_keywords_with_autophrase
# ===================================================================


class TestParseKeywordsWithAutophrase:
    """tokenize + phrase 判定 (trigger-set パラメータ化)。"""

    def test_none_returns_empty(self) -> None:
        assert parse_keywords_with_autophrase(None, ES_AUTO_PHRASE_CHARS) == []

    def test_empty_returns_empty(self) -> None:
        assert parse_keywords_with_autophrase("", ES_AUTO_PHRASE_CHARS) == []

    def test_alphanumeric_no_phrase(self) -> None:
        assert parse_keywords_with_autophrase("cancer", ES_AUTO_PHRASE_CHARS) == [
            ("cancer", False),
        ]

    def test_hyphen_triggers_phrase_es(self) -> None:
        assert parse_keywords_with_autophrase("HIF-1", ES_AUTO_PHRASE_CHARS) == [
            ("HIF-1", True),
        ]

    def test_explicit_quote_phrase(self) -> None:
        assert parse_keywords_with_autophrase(
            '"whole genome"',
            ES_AUTO_PHRASE_CHARS,
        ) == [("whole genome", True)]

    def test_mixed_auto_explicit_normal(self) -> None:
        result = parse_keywords_with_autophrase(
            'HIF-1,"whole genome",cancer',
            ES_AUTO_PHRASE_CHARS,
        )
        assert result == [
            ("HIF-1", True),
            ("whole genome", True),
            ("cancer", False),
        ]

    def test_solr_set_promotes_more_tokens(self) -> None:
        # wildcard は Solr trigger では phrase になるが ES では非 phrase
        assert parse_keywords_with_autophrase("can*", SOLR_AUTO_PHRASE_CHARS) == [
            ("can*", True),
        ]
        assert parse_keywords_with_autophrase("can*", ES_AUTO_PHRASE_CHARS) == [
            ("can*", False),
        ]

    def test_unclosed_quote_with_trigger(self) -> None:
        assert parse_keywords_with_autophrase(
            '"RNA-Seq',
            ES_AUTO_PHRASE_CHARS,
        ) == [("RNA-Seq", True)]

    def test_explicit_phrase_without_symbol(self) -> None:
        # explicit quote: always phrase regardless of trigger
        assert parse_keywords_with_autophrase(
            '"whole genome"',
            ES_AUTO_PHRASE_CHARS,
        ) == [("whole genome", True)]

    def test_empty_trigger_set_never_promotes(self) -> None:
        # 空の trigger set: explicit quote のみ phrase
        empty: frozenset[str] = frozenset()
        assert parse_keywords_with_autophrase("HIF-1", empty) == [("HIF-1", False)]
        assert parse_keywords_with_autophrase('"HIF-1"', empty) == [("HIF-1", True)]


# ===================================================================
# escape_solr_phrase
# ===================================================================


class TestEscapeSolrPhrase:
    """Solr phrase 内エスケープの仕様 (backslash + quote)。"""

    def test_plain(self) -> None:
        assert escape_solr_phrase("cancer") == "cancer"

    def test_empty_string(self) -> None:
        assert escape_solr_phrase("") == ""

    def test_backslash_doubled(self) -> None:
        assert escape_solr_phrase("a\\b") == "a\\\\b"

    def test_quote_escaped(self) -> None:
        assert escape_solr_phrase('a"b') == 'a\\"b'

    def test_backslash_then_quote_order(self) -> None:
        # backslash 二重化 → quote escape の順序が重要
        # input: a\"b → output: a\\\"b
        assert escape_solr_phrase('a\\"b') == 'a\\\\\\"b'

    def test_hyphen_unchanged(self) -> None:
        assert escape_solr_phrase("HIF-1") == "HIF-1"

    def test_slash_unchanged(self) -> None:
        assert escape_solr_phrase("BRCA1/2") == "BRCA1/2"


# ===================================================================
# Property-based tests
# ===================================================================


class TestPhrasePBT:
    """Property-based tests for the shared phrase utilities."""

    @given(st.text(max_size=200))
    def test_tokenize_never_crashes(self, keywords: str) -> None:
        result = tokenize_keywords(keywords)
        assert isinstance(result, list)
        for t in result:
            assert isinstance(t, str)
            assert len(t) > 0
            assert '"' not in t

    @given(st.text(max_size=200))
    def test_parse_with_autophrase_never_crashes(self, keywords: str) -> None:
        result = parse_keywords_with_autophrase(keywords, ES_AUTO_PHRASE_CHARS)
        assert isinstance(result, list)
        for text, is_phrase in result:
            assert isinstance(text, str)
            assert len(text) > 0
            assert isinstance(is_phrase, bool)

    @given(text=alphanumeric_no_trigger(ES_AUTO_PHRASE_TRIGGERS))
    def test_no_es_trigger_means_not_phrase(self, text: str) -> None:
        assert has_auto_phrase_trigger(text, ES_AUTO_PHRASE_CHARS) is False
        assert parse_keywords_with_autophrase(text, ES_AUTO_PHRASE_CHARS) == [
            (text, False),
        ]

    @given(text=text_with_trigger(ES_AUTO_PHRASE_TRIGGERS))
    def test_es_trigger_present_means_phrase(self, text: str) -> None:
        assert has_auto_phrase_trigger(text, ES_AUTO_PHRASE_CHARS) is True
        assert parse_keywords_with_autophrase(text, ES_AUTO_PHRASE_CHARS) == [
            (text, True),
        ]

    @given(text=alphanumeric_no_trigger(ES_AUTO_PHRASE_TRIGGERS))
    def test_quoted_text_always_phrase(self, text: str) -> None:
        assert parse_keywords_with_autophrase(
            f'"{text}"',
            ES_AUTO_PHRASE_CHARS,
        ) == [(text, True)]

    @given(text=text_with_trigger(SOLR_AUTO_PHRASE_TRIGGERS))
    def test_solr_trigger_present(self, text: str) -> None:
        assert has_auto_phrase_trigger(text, SOLR_AUTO_PHRASE_CHARS) is True

    @given(text=st.text(max_size=30).filter(lambda s: s.strip() and "," not in s))
    def test_escape_backslash_count_monotone(self, text: str) -> None:
        # backslash 数は減らない (二重化のみ)
        escaped = escape_solr_phrase(text)
        assert escaped.count("\\") >= text.count("\\")

    @given(text=st.text(max_size=30))
    def test_escape_idempotent_on_no_special(self, text: str) -> None:
        # `\` も `"` も含まない text は変化なし
        if "\\" not in text and '"' not in text:
            assert escape_solr_phrase(text) == text

    @given(text=st.text(max_size=30))
    def test_escape_double_safe(self, text: str) -> None:
        # 二重 escape も crash しない、backslash 数も減らない
        escaped = escape_solr_phrase(text)
        double_escaped = escape_solr_phrase(escaped)
        assert double_escaped.count("\\") >= escaped.count("\\")
