"""Tests for ddbj_search_api.search.dsl.compiler_es (Stage 3a).

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

from ddbj_search_api.es.query import build_search_query
from ddbj_search_api.search.dsl import parse
from ddbj_search_api.search.dsl.ast import BoolOp, FieldClause, FreeText, Position
from ddbj_search_api.search.dsl.compiler_es import compile_free_text, compile_to_es


def _compile(dsl: str) -> dict[str, Any]:
    return compile_to_es(parse(dsl))


class TestIdentifierField:
    def test_word(self) -> None:
        assert _compile("identifier:PRJDB1") == {"term": {"identifier": "PRJDB1"}}

    def test_phrase(self) -> None:
        assert _compile('identifier:"PRJDB1"') == {"term": {"identifier": "PRJDB1"}}

    def test_wildcard(self) -> None:
        assert _compile("identifier:PRJ*") == {
            "wildcard": {"identifier": {"value": "PRJ*", "case_insensitive": True}},
        }


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
        assert _compile(f"{field}:canc*") == {
            "wildcard": {es_field: {"value": "canc*", "case_insensitive": True}},
        }


class TestOrganismField:
    # organism.name は text + standard analyzer (converter common.py:39-48)、
    # organism.identifier は keyword (taxID)。name 側で term だと analyzer mismatch
    # (lowercase tokenize 後の inverted index と単一値が不一致) で 0 件になるため、
    # name は match_phrase で analyzer を通し、identifier は term で taxID exact。
    def test_word_expands_to_should(self) -> None:
        assert _compile("organism:human") == {
            "bool": {
                "should": [
                    {"match_phrase": {"organism.name": "human"}},
                    {"term": {"organism.identifier": "human"}},
                ],
                "minimum_should_match": 1,
            },
        }

    def test_phrase_expands_to_should(self) -> None:
        assert _compile('organism:"Homo sapiens"') == {
            "bool": {
                "should": [
                    {"match_phrase": {"organism.name": "Homo sapiens"}},
                    {"term": {"organism.identifier": "Homo sapiens"}},
                ],
                "minimum_should_match": 1,
            },
        }

    def test_taxid_word_hits_identifier_path(self) -> None:
        # taxID 直接入力時は organism.identifier (keyword) 側で term hit する。
        # name 側は analyzer 通すが数値だと当たらず、bool.should の identifier 句で拾う。
        assert _compile("organism:9606") == {
            "bool": {
                "should": [
                    {"match_phrase": {"organism.name": "9606"}},
                    {"term": {"organism.identifier": "9606"}},
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


# === Tier 2 nested queries ===


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
                "query": {
                    "wildcard": {"organization.name": {"value": "Tok*", "case_insensitive": True}},
                },
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
                "query": {
                    "wildcard": {"publication.id": {"value": "123*", "case_insensitive": True}},
                },
            },
        }


# === Tier 3 flat queries ===


class TestTier3FlatEnum:
    @pytest.mark.parametrize(
        ("dsl", "es_field", "value"),
        [
            ("project_type:BioProject", "objectType", "BioProject"),
            # text+keyword multi-field の enum 系は `.keyword` サブフィールドを使う
            # (term query で analyzer 適用後 lowercase token と uppercase 値が一致しないため)
            ("library_strategy:WGS", "libraryStrategy.keyword", "WGS"),
            ("library_source:GENOMIC", "librarySource.keyword", "GENOMIC"),
            ("library_layout:SINGLE", "libraryLayout.keyword", "SINGLE"),
            ("platform:ILLUMINA", "platform.keyword", "ILLUMINA"),
            ("study_type:Cohort", "studyType.keyword", "Cohort"),
            # keyword 単独 (multi-field でない) なので suffix 不要
            ("relevance:reference", "relevance", "reference"),
            ("package:MIGS.ba", "package.name", "MIGS.ba"),
            ("model:HiSeq", "model", "HiSeq"),
            # SRA + JGA 共通 type (subtype 識別子)
            ("type:sra-experiment", "type", "sra-experiment"),
            ("type:jga-dataset", "type", "jga-dataset"),
            # db-portal sidebar 第 2 弾: library_selection (sra-experiment INSDC controlled、multi-field)
            ("library_selection:RANDOM", "librarySelection.keyword", "RANDOM"),
            # db-portal sidebar 第 2 弾: accessibility (Tier 1 cross 可、keyword 単独で suffix 不要)
            ("accessibility:public-access", "accessibility", "public-access"),
        ],
    )
    def test_enum_eq(self, dsl: str, es_field: str, value: str) -> None:
        assert _compile(dsl) == {"term": {es_field: value}}

    def test_enum_phrase_with_spaces(self) -> None:
        """enum value に空白を含む場合 (VIRAL RNA) は phrase 経由 (multi-field の .keyword)."""
        assert _compile('library_source:"VIRAL RNA"') == {
            "term": {"librarySource.keyword": "VIRAL RNA"},
        }


class TestTier3FlatText:
    @pytest.mark.parametrize(
        ("dsl", "es_field"),
        [
            # 既存
            ("instrument_model:NovaSeq", "instrumentModel"),
            ("experiment_type:ChIP-Seq", "experimentType"),
            ("submission_type:metabolite", "submissionType"),
            # BioSample exclusive (converter 0.3.0 top-level)
            ("host:Homo", "host"),
            ("strain:C57BL", "strain"),
            ("isolate:test_isolate", "isolate"),
            # BioSample + SRA shared (TIER3_FIELD_DBS の 2 候補両方を確認)
            ("geo_loc_name:Japan", "geoLocName"),
            ("collection_date:2020", "collectionDate"),
            # SRA exclusive
            ("library_name:test_lib", "libraryName"),
            ("library_construction_protocol:Illumina", "libraryConstructionProtocol"),
            ("analysis_type:variation", "analysisType"),
            # JGA exclusive
            ("dataset_type:fastq", "datasetType"),
            ("vendor:Illumina", "vendor"),
        ],
    )
    def test_text_match_phrase(self, dsl: str, es_field: str) -> None:
        value = dsl.split(":", 1)[1].strip('"')
        assert _compile(dsl) == {"match_phrase": {es_field: value}}


class TestTier3WildcardExpansion:
    """Tier 3 text field の wildcard は match_phrase ではなく wildcard query (case_insensitive=True)."""

    def test_host_wildcard(self) -> None:
        assert _compile("host:Homo*") == {
            "wildcard": {"host": {"value": "Homo*", "case_insensitive": True}},
        }

    def test_analysis_type_wildcard(self) -> None:
        assert _compile("analysis_type:var*") == {
            "wildcard": {"analysisType": {"value": "var*", "case_insensitive": True}},
        }


class TestTier3PhraseSpaceValue:
    """空白含み値は phrase quote 経由で match_phrase に渡る (token 順保持)."""

    def test_host_phrase_with_space(self) -> None:
        assert _compile('host:"Homo sapiens"') == {
            "match_phrase": {"host": "Homo sapiens"},
        }

    def test_library_construction_protocol_phrase_with_space(self) -> None:
        assert _compile('library_construction_protocol:"Illumina TruSeq"') == {
            "match_phrase": {"libraryConstructionProtocol": "Illumina TruSeq"},
        }


class TestTier3GrantAgencyNested2:
    def test_grant_agency_word_two_level_nested(self) -> None:
        assert _compile("grant_agency:JSPS") == {
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
        assert _compile('grant_agency:"National Institutes"') == {
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
        assert _compile("NOT platform:ILLUMINA") == {
            "bool": {"must_not": [{"term": {"platform.keyword": "ILLUMINA"}}]},
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
        assert _compile("library_strategy:WGS AND platform:ILLUMINA") == {
            "bool": {
                "must": [
                    {"term": {"libraryStrategy.keyword": "WGS"}},
                    {"term": {"platform.keyword": "ILLUMINA"}},
                ],
            },
        }

    def test_or_two_platforms(self) -> None:
        assert _compile("platform:ILLUMINA OR platform:PACBIO_SMRT") == {
            "bool": {
                "should": [
                    {"term": {"platform.keyword": "ILLUMINA"}},
                    {"term": {"platform.keyword": "PACBIO_SMRT"}},
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


# === Wildcard case-insensitivity ===


class TestWildcardCaseInsensitive:
    """ES wildcard does not run the value through the analyzer, so without
    ``case_insensitive: true`` text-type tokens (lowercased) and keyword
    values (case-preserving) would skip mixed-case patterns.  Staging probe
    2026-04-24: ``title:Cancer*`` returned 0 / ``title:cancer*`` returned
    10k+ — both must return the same set.  The compile_to_es contract must
    attach the flag to every wildcard leaf it emits.
    """

    @pytest.mark.parametrize(
        ("dsl", "es_field", "value"),
        [
            # text-type with uppercase prefix (lowercased tokens in index)
            ("title:Cancer*", "title", "Cancer*"),
            ("title:BRCA*", "title", "BRCA*"),
            ("description:COVID*", "description", "COVID*"),
            # keyword-type with lowercase (accessions stored uppercase)
            ("identifier:prjdb*", "identifier", "prjdb*"),
            # keyword-type with mixed case
            ("identifier:PrJdB*", "identifier", "PrJdB*"),
        ],
    )
    def test_wildcard_leaf_carries_case_insensitive_flag(
        self,
        dsl: str,
        es_field: str,
        value: str,
    ) -> None:
        assert _compile(dsl) == {
            "wildcard": {es_field: {"value": value, "case_insensitive": True}},
        }

    def test_grant_agency_wildcard_preserves_double_nested_shape(self) -> None:
        """nested2 strategy keeps the flag inside the inner leaf."""
        assert _compile("grant_agency:JSPS*") == {
            "nested": {
                "path": "grant",
                "query": {
                    "nested": {
                        "path": "grant.agency",
                        "query": {
                            "wildcard": {
                                "grant.agency.name": {
                                    "value": "JSPS*",
                                    "case_insensitive": True,
                                },
                            },
                        },
                    },
                },
            },
        }


class TestCompileFreeText:
    """compile_free_text helper と FreeText AST 経由の compile が等価か."""

    _DEFAULT_FIELDS = ["identifier", "title", "name", "description"]

    def test_single_token_word_no_phrase(self) -> None:

        assert compile_free_text("cancer") == {
            "bool": {
                "must": [
                    {"multi_match": {"query": "cancer", "fields": self._DEFAULT_FIELDS}},
                ],
            },
        }

    def test_symbol_token_promoted_to_phrase(self) -> None:
        """``HIF-1`` のようなハイフン含みトークンは ``type: phrase`` 付与."""

        assert compile_free_text("HIF-1") == {
            "bool": {
                "must": [
                    {
                        "multi_match": {
                            "query": "HIF-1",
                            "fields": self._DEFAULT_FIELDS,
                            "type": "phrase",
                        },
                    },
                ],
            },
        }

    def test_quoted_token_treated_as_phrase(self) -> None:
        """double-quote で囲まれたトークンは ``type: phrase``."""

        assert compile_free_text('"RNA Seq"') == {
            "bool": {
                "must": [
                    {
                        "multi_match": {
                            "query": "RNA Seq",
                            "fields": self._DEFAULT_FIELDS,
                            "type": "phrase",
                        },
                    },
                ],
            },
        }

    def test_comma_separated_multiple_tokens_and(self) -> None:
        """カンマ区切りは複数 multi_match に分解、``AND`` 操作子は ``bool.must``."""

        result = compile_free_text("cancer, human")
        assert result == {
            "bool": {
                "must": [
                    {"multi_match": {"query": "cancer", "fields": self._DEFAULT_FIELDS}},
                    {"multi_match": {"query": "human", "fields": self._DEFAULT_FIELDS}},
                ],
            },
        }

    def test_operator_or_uses_should_minimum_should_match(self) -> None:
        """``OR`` 操作子は ``bool.should`` + ``minimum_should_match: 1``."""

        assert compile_free_text("cancer, human", operator="OR") == {
            "bool": {
                "should": [
                    {"multi_match": {"query": "cancer", "fields": self._DEFAULT_FIELDS}},
                    {"multi_match": {"query": "human", "fields": self._DEFAULT_FIELDS}},
                ],
                "minimum_should_match": 1,
            },
        }

    def test_custom_fields(self) -> None:

        assert compile_free_text("cancer", fields=["title"]) == {
            "bool": {
                "must": [{"multi_match": {"query": "cancer", "fields": ["title"]}}],
            },
        }

    def test_empty_value_raises(self) -> None:

        with pytest.raises(ValueError, match="empty free-text value"):
            compile_free_text("")

    def test_whitespace_only_raises(self) -> None:

        with pytest.raises(ValueError, match="empty free-text value"):
            compile_free_text("   ")


class TestCompileToEsFreeTextNode:
    """``compile_to_es(FreeText(...))`` が ``compile_free_text`` のデフォルトと等価."""

    _DEFAULT_FIELDS = ["identifier", "title", "name", "description"]

    def test_free_text_node_equals_compile_free_text(self) -> None:

        node = FreeText("cancer")
        assert compile_to_es(node) == compile_free_text("cancer")

    def test_and_of_adv_and_free_text_flattens_multi_match(self) -> None:
        """``BoolOp(AND, [adv_ast, FreeText(q)])`` で FreeText の bool.must を flatten する."""

        adv_ast = FieldClause(
            field="organism",
            value_kind="phrase",
            value="Homo sapiens",
            position=Position(column=1, length=24),
        )
        composite = BoolOp(
            op="AND",
            children=(adv_ast, FreeText("cancer")),
            position=Position(column=1, length=24),
        )
        result = compile_to_es(composite)
        assert result == {
            "bool": {
                "must": [
                    # adv_ast の compile 結果 (organism kind: name は match_phrase、identifier は term)
                    {
                        "bool": {
                            "should": [
                                {"match_phrase": {"organism.name": "Homo sapiens"}},
                                {"term": {"organism.identifier": "Homo sapiens"}},
                            ],
                            "minimum_should_match": 1,
                        },
                    },
                    # FreeText の multi_match が flatten されて並ぶ
                    {"multi_match": {"query": "cancer", "fields": self._DEFAULT_FIELDS}},
                ],
            },
        }

    def test_and_of_free_text_with_symbol_token_keeps_phrase_type(self) -> None:
        """合成 BoolOp 経由でも auto-phrase が効くこと (HIF-1 で type=phrase)."""

        adv_ast = FieldClause(
            field="title",
            value_kind="word",
            value="cancer",
            position=Position(column=1, length=12),
        )
        composite = BoolOp(
            op="AND",
            children=(adv_ast, FreeText("HIF-1")),
            position=Position(column=1, length=12),
        )
        result = compile_to_es(composite)
        assert result == {
            "bool": {
                "must": [
                    {"match_phrase": {"title": "cancer"}},
                    {
                        "multi_match": {
                            "query": "HIF-1",
                            "fields": self._DEFAULT_FIELDS,
                            "type": "phrase",
                        },
                    },
                ],
            },
        }

    def test_or_of_free_text_does_not_flatten(self) -> None:
        """OR 配下では FreeText の bool.must は flatten せず、bool wrapper が残る."""

        adv_ast = FieldClause(
            field="title",
            value_kind="word",
            value="cancer",
            position=Position(column=1, length=12),
        )
        composite = BoolOp(
            op="OR",
            children=(adv_ast, FreeText("tumor")),
            position=Position(column=1, length=12),
        )
        result = compile_to_es(composite)
        assert result == {
            "bool": {
                "should": [
                    {"match_phrase": {"title": "cancer"}},
                    # FreeText の bool wrapper はそのまま (flatten しない)
                    {
                        "bool": {
                            "must": [
                                {"multi_match": {"query": "tumor", "fields": self._DEFAULT_FIELDS}},
                            ],
                        },
                    },
                ],
                "minimum_should_match": 1,
            },
        }


class TestCompileFreeTextMatchesBuildSearchQuery:
    """compile_free_text の出力と build_search_query(keywords=...) の出力 (filter/status なし) が一致."""

    @pytest.mark.parametrize(
        "keywords",
        ["cancer", "HIF-1", '"RNA Seq"', "cancer, human", "BRCA1/2"],
    )
    def test_and_operator(self, keywords: str) -> None:

        expected = build_search_query(
            keywords=keywords,
            keyword_operator="AND",
            status_mode=None,
        )
        actual = compile_free_text(keywords)
        assert actual == expected

    @pytest.mark.parametrize(
        "keywords",
        ["cancer", "HIF-1", "cancer, human"],
    )
    def test_or_operator(self, keywords: str) -> None:

        expected = build_search_query(
            keywords=keywords,
            keyword_operator="OR",
            status_mode=None,
        )
        actual = compile_free_text(keywords, operator="OR")
        assert actual == expected
