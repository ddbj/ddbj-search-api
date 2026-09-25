"""Keyword search also matches ``sameAs.identifier`` exactly (Secondary ID search)."""

from __future__ import annotations

from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from ddbj_search_api.es.query import build_search_query
from ddbj_search_api.search.dsl.compiler_es import compile_free_text


def _same_as_terms(node: Any) -> list[str]:
    """Every ``sameAs.identifier`` term value under a ``nested`` on ``sameAs``, in tree order."""
    found: list[str] = []
    if isinstance(node, dict):
        nested = node.get("nested")
        if isinstance(nested, dict) and nested.get("path") == "sameAs":
            assert nested.get("ignore_unmapped") is True
            found.append(nested["query"]["term"]["sameAs.identifier"])
        for value in node.values():
            found.extend(_same_as_terms(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_same_as_terms(item))
    return found


class TestSameAsInKeywords:
    def test_zero_padded_jga_id_is_matched_verbatim(self) -> None:
        query = build_search_query(keywords="JGAS00000000038")

        assert _same_as_terms(query) == ["JGAS00000000038"]

    def test_each_token_gets_its_own_same_as_alternative(self) -> None:
        """With AND, a token must match somewhere; sameAs is an alternative per token, not a global OR."""
        query = build_search_query(keywords="JGAS00000000038,cancer", keyword_operator="AND")

        must = query["bool"]["must"]
        assert len(must) == 2
        assert [_same_as_terms(clause) for clause in must] == [["JGAS00000000038"], ["cancer"]]
        for clause in must:
            assert clause["bool"]["minimum_should_match"] == 1
            assert len(clause["bool"]["should"]) == 2

    def test_symbol_token_uses_the_whole_token(self) -> None:
        """Auto-phrased tokens (with ``_`` / ``-``) are matched against sameAs as the whole token."""
        query = build_search_query(keywords="AGDD_000001")

        assert _same_as_terms(query) == ["AGDD_000001"]

    def test_quoted_free_text_uses_the_whole_value(self) -> None:
        query = compile_free_text("GSE12345", is_phrase=True)

        assert _same_as_terms(query) == ["GSE12345"]

    def test_not_applied_when_identifier_is_not_searched(self) -> None:
        query = build_search_query(keywords="JGAS00000000038", keyword_fields="title,description")

        assert _same_as_terms(query) == []

    def test_applied_when_identifier_is_the_only_field(self) -> None:
        query = build_search_query(keywords="JGAS00000000038", keyword_fields="identifier")

        assert _same_as_terms(query) == ["JGAS00000000038"]

    def test_no_keywords_means_no_same_as(self) -> None:
        assert _same_as_terms(build_search_query(organism="9606")) == []

    @given(
        tokens=st.lists(
            st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=1, max_size=15),
            min_size=1,
            max_size=4,
        ),
        operator=st.sampled_from(["AND", "OR"]),
    )
    def test_every_token_is_matched_against_same_as_unchanged(self, tokens: list[str], operator: str) -> None:
        query = build_search_query(keywords=",".join(tokens), keyword_operator=operator)

        assert _same_as_terms(query) == tokens
