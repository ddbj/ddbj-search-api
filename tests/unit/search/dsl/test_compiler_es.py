"""Tests for ddbj_search_api.search.dsl.compiler_es (AP3 Stage 3a).

SSOT: search-backends.md §バックエンド変換 (L517-520).

``compile_to_es`` returns the body of the ``query`` key (matches the shape
of :func:`ddbj_search_api.es.query.build_search_query`), so the router can
wrap it with ``{"query": ..., "size": ..., "sort": ...}``.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ddbj_search_api.search.dsl import parse
from ddbj_search_api.search.dsl.compiler_es import compile_to_es
from ddbj_search_api.search.dsl.validator import validate


def _compile(dsl: str) -> dict[str, Any]:
    ast = parse(dsl)
    validate(ast, mode="cross")
    return compile_to_es(ast)


class TestIdentifierField:
    def test_word(self) -> None:
        assert _compile("identifier:PRJDB1") == {"term": {"identifier": "PRJDB1"}}

    def test_phrase(self) -> None:
        assert _compile('identifier:"PRJDB1"') == {"term": {"identifier": "PRJDB1"}}

    def test_wildcard(self) -> None:
        assert _compile("identifier:PRJ*") == {"wildcard": {"identifier": "PRJ*"}}


class TestTextFields:
    @pytest.mark.parametrize(("field", "es_field"), [("title", "title"), ("description", "description")])
    def test_word_becomes_match_phrase(self, field: str, es_field: str) -> None:
        assert _compile(f"{field}:cancer") == {"match_phrase": {es_field: "cancer"}}

    @pytest.mark.parametrize(("field", "es_field"), [("title", "title"), ("description", "description")])
    def test_phrase_becomes_match_phrase(self, field: str, es_field: str) -> None:
        assert _compile(f'{field}:"cancer treatment"') == {
            "match_phrase": {es_field: "cancer treatment"},
        }

    @pytest.mark.parametrize(("field", "es_field"), [("title", "title"), ("description", "description")])
    def test_wildcard(self, field: str, es_field: str) -> None:
        assert _compile(f"{field}:canc*") == {"wildcard": {es_field: "canc*"}}


class TestOrganismField:
    def test_word_expands_to_should(self) -> None:
        assert _compile("organism:human") == {
            "bool": {
                "should": [
                    {"term": {"organism.name": "human"}},
                    {"term": {"organism.identifier": "human"}},
                ],
                "minimum_should_match": 1,
            },
        }

    def test_phrase_expands_to_should(self) -> None:
        assert _compile('organism:"Homo sapiens"') == {
            "bool": {
                "should": [
                    {"term": {"organism.name": "Homo sapiens"}},
                    {"term": {"organism.identifier": "Homo sapiens"}},
                ],
                "minimum_should_match": 1,
            },
        }


class TestDateFields:
    def test_date_published_eq(self) -> None:
        assert _compile("date_published:2024-01-01") == {"term": {"datePublished": "2024-01-01"}}

    def test_date_published_range(self) -> None:
        assert _compile("date_published:[2020-01-01 TO 2024-12-31]") == {
            "range": {"datePublished": {"gte": "2020-01-01", "lte": "2024-12-31"}},
        }

    def test_date_modified_eq(self) -> None:
        assert _compile("date_modified:2024-06-15") == {"term": {"dateModified": "2024-06-15"}}

    def test_date_created_eq(self) -> None:
        assert _compile("date_created:2024-01-01") == {"term": {"dateCreated": "2024-01-01"}}


class TestDateAlias:
    def test_date_alias_eq_expands_to_3_should(self) -> None:
        assert _compile("date:2024-01-01") == {
            "bool": {
                "should": [
                    {"term": {"datePublished": "2024-01-01"}},
                    {"term": {"dateModified": "2024-01-01"}},
                    {"term": {"dateCreated": "2024-01-01"}},
                ],
                "minimum_should_match": 1,
            },
        }

    def test_date_alias_range_expands_to_3_should(self) -> None:
        assert _compile("date:[2020-01-01 TO 2024-12-31]") == {
            "bool": {
                "should": [
                    {"range": {"datePublished": {"gte": "2020-01-01", "lte": "2024-12-31"}}},
                    {"range": {"dateModified": {"gte": "2020-01-01", "lte": "2024-12-31"}}},
                    {"range": {"dateCreated": {"gte": "2020-01-01", "lte": "2024-12-31"}}},
                ],
                "minimum_should_match": 1,
            },
        }


class TestBoolOperators:
    def test_and(self) -> None:
        assert _compile("title:cancer AND description:tumor") == {
            "bool": {
                "must": [
                    {"match_phrase": {"title": "cancer"}},
                    {"match_phrase": {"description": "tumor"}},
                ],
            },
        }

    def test_or(self) -> None:
        assert _compile("title:cancer OR title:tumor") == {
            "bool": {
                "should": [
                    {"match_phrase": {"title": "cancer"}},
                    {"match_phrase": {"title": "tumor"}},
                ],
                "minimum_should_match": 1,
            },
        }

    def test_not(self) -> None:
        assert _compile("NOT title:cancer") == {
            "bool": {"must_not": [{"match_phrase": {"title": "cancer"}}]},
        }


class TestPrecedence:
    def test_and_before_or(self) -> None:
        assert _compile("title:a OR title:b AND title:c") == {
            "bool": {
                "should": [
                    {"match_phrase": {"title": "a"}},
                    {
                        "bool": {
                            "must": [
                                {"match_phrase": {"title": "b"}},
                                {"match_phrase": {"title": "c"}},
                            ],
                        },
                    },
                ],
                "minimum_should_match": 1,
            },
        }

    def test_parens_override(self) -> None:
        assert _compile("(title:a OR title:b) AND title:c") == {
            "bool": {
                "must": [
                    {
                        "bool": {
                            "should": [
                                {"match_phrase": {"title": "a"}},
                                {"match_phrase": {"title": "b"}},
                            ],
                            "minimum_should_match": 1,
                        },
                    },
                    {"match_phrase": {"title": "c"}},
                ],
            },
        }


def _is_valid_iso_date(s: str) -> bool:
    try:
        datetime.date.fromisoformat(s)
    except ValueError:
        return False
    return True


class TestCompilerPBT:
    @given(
        field=st.sampled_from(["title", "description"]),
        word=st.text(
            alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")),
            min_size=1,
            max_size=20,
        ),
    )
    @settings(max_examples=30, deadline=None)
    def test_text_word_always_match_phrase(self, field: str, word: str) -> None:
        q = _compile(f"{field}:{word}")
        assert "match_phrase" in q
        assert q["match_phrase"] == {field: word}

    @given(
        d=st.dates(min_value=datetime.date(1000, 1, 1), max_value=datetime.date(9999, 12, 31)),
    )
    @settings(max_examples=30, deadline=None)
    def test_date_published_always_term(self, d: datetime.date) -> None:
        date_str = d.isoformat()
        q = _compile(f"date_published:{date_str}")
        assert q == {"term": {"datePublished": date_str}}

    @given(
        op=st.sampled_from(["AND", "OR"]),
    )
    @settings(max_examples=10, deadline=None)
    def test_bool_shape_matches_operator(self, op: str) -> None:
        q = _compile(f"title:a {op} title:b")
        expected_key = "must" if op == "AND" else "should"
        assert expected_key in q["bool"]
