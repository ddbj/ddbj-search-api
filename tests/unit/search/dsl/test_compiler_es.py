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

from ddbj_search_api.schemas.db_portal import DbPortalDb
from ddbj_search_api.search.dsl import parse
from ddbj_search_api.search.dsl.compiler_es import compile_to_es
from ddbj_search_api.search.dsl.validator import validate


def _compile(dsl: str) -> dict[str, Any]:
    ast = parse(dsl)
    validate(ast, mode="cross")
    return compile_to_es(ast)


def _compile_single(dsl: str, db: DbPortalDb = DbPortalDb.bioproject) -> dict[str, Any]:
    """Tier 3 field (single mode required) を compile するヘルパ."""
    ast = parse(dsl)
    validate(ast, mode="single", db=db)
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


# === AP6: Tier 2 nested queries ===


class TestTier2Submitter:
    def test_submitter_word(self) -> None:
        assert _compile('submitter:"Tokyo University"') == {
            "nested": {
                "path": "organization",
                "query": {"match_phrase": {"organization.name": "Tokyo University"}},
            },
        }

    def test_submitter_phrase(self) -> None:
        assert _compile('submitter:"National Institute"') == {
            "nested": {
                "path": "organization",
                "query": {"match_phrase": {"organization.name": "National Institute"}},
            },
        }

    def test_submitter_wildcard(self) -> None:
        assert _compile("submitter:Tok*") == {
            "nested": {
                "path": "organization",
                "query": {"wildcard": {"organization.name": "Tok*"}},
            },
        }


class TestTier2Publication:
    def test_publication_word(self) -> None:
        assert _compile("publication:12345678") == {
            "nested": {
                "path": "publication",
                "query": {"term": {"publication.id": "12345678"}},
            },
        }

    def test_publication_wildcard(self) -> None:
        assert _compile("publication:123*") == {
            "nested": {
                "path": "publication",
                "query": {"wildcard": {"publication.id": "123*"}},
            },
        }


# === AP6: Tier 3 flat queries ===


class TestTier3FlatEnum:
    @pytest.mark.parametrize(
        ("dsl", "db", "es_field", "value"),
        [
            ("project_type:BioProject", DbPortalDb.bioproject, "objectType", "BioProject"),
            ("library_strategy:WGS", DbPortalDb.sra, "libraryStrategy", "WGS"),
            ("library_source:GENOMIC", DbPortalDb.sra, "librarySource", "GENOMIC"),
            ("library_layout:SINGLE", DbPortalDb.sra, "libraryLayout", "SINGLE"),
            ("platform:ILLUMINA", DbPortalDb.sra, "platform", "ILLUMINA"),
            ("study_type:Cohort", DbPortalDb.jga, "studyType", "Cohort"),
        ],
    )
    def test_enum_eq(self, dsl: str, db: DbPortalDb, es_field: str, value: str) -> None:
        assert _compile_single(dsl, db) == {"term": {es_field: value}}

    def test_enum_phrase_with_spaces(self) -> None:
        """enum value に空白を含む場合 (VIRAL RNA) は phrase 経由."""
        assert _compile_single('library_source:"VIRAL RNA"', DbPortalDb.sra) == {
            "term": {"librarySource": "VIRAL RNA"},
        }


class TestTier3FlatText:
    @pytest.mark.parametrize(
        ("dsl", "db", "es_field"),
        [
            ("instrument_model:NovaSeq", DbPortalDb.sra, "instrumentModel"),
            ("experiment_type:ChIP-Seq", DbPortalDb.gea, "experimentType"),
            ("submission_type:metabolite", DbPortalDb.metabobank, "submissionType"),
        ],
    )
    def test_text_match_phrase(self, dsl: str, db: DbPortalDb, es_field: str) -> None:
        value = dsl.split(":", 1)[1].strip('"')
        assert _compile_single(dsl, db) == {"match_phrase": {es_field: value}}


class TestTier3GrantAgencyNested2:
    def test_grant_agency_word_two_level_nested(self) -> None:
        assert _compile_single("grant_agency:JSPS", DbPortalDb.bioproject) == {
            "nested": {
                "path": "grant",
                "query": {
                    "nested": {
                        "path": "grant.agency",
                        "query": {"match_phrase": {"grant.agency.name": "JSPS"}},
                    },
                },
            },
        }

    def test_grant_agency_phrase(self) -> None:
        assert _compile_single('grant_agency:"National Institutes"', DbPortalDb.jga) == {
            "nested": {
                "path": "grant",
                "query": {
                    "nested": {
                        "path": "grant.agency",
                        "query": {"match_phrase": {"grant.agency.name": "National Institutes"}},
                    },
                },
            },
        }


class TestTier3NotEnum:
    """GUI の not_equals 演算子は NOT FieldClause で表現される (Operator Literal 拡張なし)."""

    def test_not_platform(self) -> None:
        assert _compile_single("NOT platform:ILLUMINA", DbPortalDb.sra) == {
            "bool": {"must_not": [{"term": {"platform": "ILLUMINA"}}]},
        }

    def test_not_nested_submitter(self) -> None:
        assert _compile('NOT submitter:"Xyz Labs"') == {
            "bool": {
                "must_not": [
                    {
                        "nested": {
                            "path": "organization",
                            "query": {"match_phrase": {"organization.name": "Xyz Labs"}},
                        },
                    },
                ],
            },
        }


class TestTier3BoolCombinations:
    def test_and_two_sra_enums(self) -> None:
        assert _compile_single("library_strategy:WGS AND platform:ILLUMINA", DbPortalDb.sra) == {
            "bool": {
                "must": [
                    {"term": {"libraryStrategy": "WGS"}},
                    {"term": {"platform": "ILLUMINA"}},
                ],
            },
        }

    def test_or_two_platforms(self) -> None:
        assert _compile_single("platform:ILLUMINA OR platform:PACBIO_SMRT", DbPortalDb.sra) == {
            "bool": {
                "should": [
                    {"term": {"platform": "ILLUMINA"}},
                    {"term": {"platform": "PACBIO_SMRT"}},
                ],
                "minimum_should_match": 1,
            },
        }

    def test_tier2_with_tier1(self) -> None:
        """Tier 2 nested と Tier 1 leaf を AND 結合."""
        assert _compile("submitter:DDBJ AND title:cancer") == {
            "bool": {
                "must": [
                    {
                        "nested": {
                            "path": "organization",
                            "query": {"match_phrase": {"organization.name": "DDBJ"}},
                        },
                    },
                    {"match_phrase": {"title": "cancer"}},
                ],
            },
        }
