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


def _contains_should(es_field: str, value: str) -> dict[str, Any]:
    """text 型 field-scoped contains の記号なし single word が展開する should ラッパ.

    完全一致 (match_phrase) + 末尾前方一致 (match_phrase_prefix) を ``minimum_should_match=1``
    で結合する。
    """
    return {
        "bool": {
            "should": [
                {"match_phrase": {es_field: value}},
                {"match_phrase_prefix": {es_field: value}},
            ],
            "minimum_should_match": 1,
        },
    }


def _keyword_should(text: str, fields: list[str]) -> dict[str, Any]:
    """keyword box の記号なし bare word トークンが展開する should ラッパ.

    完全語一致 (multi_match operator=and) + 末尾前方一致 (multi_match type=phrase_prefix) を
    ``minimum_should_match=1`` で結合する。
    """
    return {
        "bool": {
            "should": [
                {"multi_match": {"query": text, "fields": fields, "operator": "and"}},
                {"multi_match": {"query": text, "fields": fields, "type": "phrase_prefix"}},
            ],
            "minimum_should_match": 1,
        },
    }


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
    @pytest.mark.parametrize(
        ("field", "es_field"),
        [("title", "title"), ("name", "name"), ("description", "description")],
    )
    def test_word_becomes_match_phrase(self, field: str, es_field: str) -> None:
        assert _compile(f"{field}:cancer") == _contains_should(es_field, "cancer")

    @pytest.mark.parametrize(
        ("field", "es_field"),
        [("title", "title"), ("name", "name"), ("description", "description")],
    )
    def test_phrase_becomes_match_phrase(self, field: str, es_field: str) -> None:
        assert _compile(f'{field}:"cancer treatment"') == {
            "match_phrase": {es_field: "cancer treatment"},
        }

    @pytest.mark.parametrize(
        ("field", "es_field"),
        [("title", "title"), ("name", "name"), ("description", "description")],
    )
    def test_wildcard(self, field: str, es_field: str) -> None:
        assert _compile(f"{field}:canc*") == {
            "wildcard": {es_field: {"value": "canc*", "case_insensitive": True}},
        }


class TestOrganismFields:
    # organism は taxID exact 用の identifier 型 (organism_id → organism.identifier に term)
    # と 学名 match 用の text 型 (organism_name → organism.name に match_phrase) の 2 field に
    # 分割。converter mapping (common.py:39-48) は organism.identifier が keyword、
    # organism.name が text + standard analyzer。
    def test_organism_id_word(self) -> None:
        assert _compile("organism_id:9606") == {"term": {"organism.identifier": "9606"}}

    def test_organism_id_phrase(self) -> None:
        # accession ID 系で phrase を使うことは稀だが文法上許可されている (eq 経路).
        assert _compile('organism_id:"9606"') == {"term": {"organism.identifier": "9606"}}

    def test_organism_id_wildcard(self) -> None:
        # identifier 型は wildcard も使える (organism_name 側は別)。
        assert _compile("organism_id:96*") == {
            "wildcard": {"organism.identifier": {"value": "96*", "case_insensitive": True}},
        }

    def test_organism_name_word(self) -> None:
        # text 型 contains の記号なし single word は完全一致 + 末尾前方一致の should に展開。
        # match_phrase は analyzer を通すため、term だと lowercase tokenize 後の inverted
        # index と単一値が不一致で 0 件になる経路を避けつつ、打ちかけ入力にも応える。
        assert _compile("organism_name:human") == _contains_should("organism.name", "human")

    def test_organism_name_phrase(self) -> None:
        assert _compile('organism_name:"Homo sapiens"') == {
            "match_phrase": {"organism.name": "Homo sapiens"},
        }

    def test_organism_name_wildcard(self) -> None:
        assert _compile("organism_name:Homo*") == {
            "wildcard": {"organism.name": {"value": "Homo*", "case_insensitive": True}},
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
                    _contains_should("title", "cancer"),
                    _contains_should("description", "tumor"),
                ],
            },
        }

    def test_or(self) -> None:
        assert _compile("title:cancer OR title:tumor") == {
            "bool": {
                "should": [
                    _contains_should("title", "cancer"),
                    _contains_should("title", "tumor"),
                ],
                "minimum_should_match": 1,
            },
        }

    def test_not(self) -> None:
        assert _compile("NOT title:cancer") == {
            "bool": {"must_not": [_contains_should("title", "cancer")]},
        }


class TestPrecedence:
    # 値は 2 文字以上にして前方一致 (should-wrapper) を exercise する
    # (1 文字値は最小 prefix 長未満で match_phrase 単独になり precedence の主題から逸れる)。
    def test_and_before_or(self) -> None:
        assert _compile("title:aa OR title:bb AND title:cc") == {
            "bool": {
                "should": [
                    _contains_should("title", "aa"),
                    {
                        "bool": {
                            "must": [
                                _contains_should("title", "bb"),
                                _contains_should("title", "cc"),
                            ],
                        },
                    },
                ],
                "minimum_should_match": 1,
            },
        }

    def test_parens_override(self) -> None:
        assert _compile("(title:aa OR title:bb) AND title:cc") == {
            "bool": {
                "must": [
                    {
                        "bool": {
                            "should": [
                                _contains_should("title", "aa"),
                                _contains_should("title", "bb"),
                            ],
                            "minimum_should_match": 1,
                        },
                    },
                    _contains_should("title", "cc"),
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
        # 2 文字以上 (1 文字は最小 prefix 長未満で match_phrase 単独。別途 1 文字ガードを検証)
        word=st.text(
            alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")),
            min_size=2,
            max_size=20,
        ),
    )
    @settings(max_examples=30, deadline=None)
    def test_text_word_expands_to_phrase_and_prefix_should(self, field: str, word: str) -> None:
        # 記号なし 2 文字以上の single word の text 型 contains は、完全一致 (match_phrase) と
        # 末尾前方一致 (match_phrase_prefix) の 2 句を持つ minimum_should_match=1 の
        # bool.should に必ず展開される。
        q = _compile(f"{field}:{word}")
        assert q == {
            "bool": {
                "should": [
                    {"match_phrase": {field: word}},
                    {"match_phrase_prefix": {field: word}},
                ],
                "minimum_should_match": 1,
            },
        }

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
                "ignore_unmapped": True,
            },
        }

    def test_submitter_phrase(self) -> None:
        assert _compile('submitter:"National Institute"') == {
            "nested": {
                "path": "organization",
                "query": {"match_phrase": {"organization.name": "National Institute"}},
                "ignore_unmapped": True,
            },
        }

    def test_submitter_wildcard(self) -> None:
        assert _compile("submitter:Tok*") == {
            "nested": {
                "path": "organization",
                "query": {
                    "wildcard": {"organization.name": {"value": "Tok*", "case_insensitive": True}},
                },
                "ignore_unmapped": True,
            },
        }


class TestTier2Publication:
    """``publication`` は publication.title (text) を match_phrase で検索する。"""

    def test_publication_word(self) -> None:
        assert _compile("publication:cancer") == {
            "nested": {
                "path": "publication",
                "query": _contains_should("publication.title", "cancer"),
                "ignore_unmapped": True,
            },
        }

    def test_publication_phrase(self) -> None:
        assert _compile('publication:"whole genome sequencing"') == {
            "nested": {
                "path": "publication",
                "query": {"match_phrase": {"publication.title": "whole genome sequencing"}},
                "ignore_unmapped": True,
            },
        }

    def test_publication_wildcard(self) -> None:
        assert _compile("publication:canc*") == {
            "nested": {
                "path": "publication",
                "query": {
                    "wildcard": {"publication.title": {"value": "canc*", "case_insensitive": True}},
                },
                "ignore_unmapped": True,
            },
        }


class TestTier3ExternalLinkLabel:
    """``external_link_label`` は externalLink nested の label を text 型扱いで検索する。

    converter mapping は keyword だが、allowlist が text 型として公開しているため、
    ``contains`` 経路で ``match_phrase`` を生成する (順序保持)。
    """

    def test_external_link_label_word(self) -> None:
        assert _compile("external_link_label:GEO") == {
            "nested": {
                "path": "externalLink",
                "query": _contains_should("externalLink.label", "GEO"),
                "ignore_unmapped": True,
            },
        }

    def test_external_link_label_phrase(self) -> None:
        assert _compile('external_link_label:"GEO Sample"') == {
            "nested": {
                "path": "externalLink",
                "query": {"match_phrase": {"externalLink.label": "GEO Sample"}},
                "ignore_unmapped": True,
            },
        }

    def test_external_link_label_wildcard(self) -> None:
        assert _compile("external_link_label:GE*") == {
            "nested": {
                "path": "externalLink",
                "query": {
                    "wildcard": {"externalLink.label": {"value": "GE*", "case_insensitive": True}},
                },
                "ignore_unmapped": True,
            },
        }


class TestTier3DerivedFromId:
    """``derived_from_id`` は derivedFrom nested の identifier を identifier 型 (term/wildcard) で検索する。"""

    def test_derived_from_id_word(self) -> None:
        assert _compile("derived_from_id:SAMD00012345") == {
            "nested": {
                "path": "derivedFrom",
                "query": {"term": {"derivedFrom.identifier": "SAMD00012345"}},
                "ignore_unmapped": True,
            },
        }

    def test_derived_from_id_wildcard(self) -> None:
        assert _compile("derived_from_id:SAMD*") == {
            "nested": {
                "path": "derivedFrom",
                "query": {
                    "wildcard": {
                        "derivedFrom.identifier": {"value": "SAMD*", "case_insensitive": True},
                    },
                },
                "ignore_unmapped": True,
            },
        }


# === Tier 3 flat queries ===


class TestTier3FlatEnum:
    @pytest.mark.parametrize(
        ("dsl", "es_field", "value"),
        [
            ("object_type:BioProject", "objectType", "BioProject"),
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
            # text+keyword multi-field の enum (facet bucket .keyword exact を op=eq で
            # 再注入)。term は <field>.keyword に当てる (libraryStrategy 等と同じ理由)。
            ("instrument_model:NovaSeq", "instrumentModel.keyword", "NovaSeq"),
            ("analysis_type:variation", "analysisType.keyword", "variation"),
            ("dataset_type:fastq", "datasetType.keyword", "fastq"),
            ("experiment_type:ChIP-Seq", "experimentType.keyword", "ChIP-Seq"),
            ("submission_type:metabolite", "submissionType.keyword", "metabolite"),
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
            # JGA exclusive
            ("vendor:Illumina", "vendor"),
            # BioProject INSDC controlled vocab (text+keyword)、object_type とは別 field
            ("project_type:genome", "projectType"),
        ],
    )
    def test_text_match_phrase(self, dsl: str, es_field: str) -> None:
        # いずれも記号なし single word なので、完全一致 + 末尾前方一致の should に展開。
        value = dsl.split(":", 1)[1].strip('"')
        assert _compile(dsl) == _contains_should(es_field, value)


class TestTier3WildcardExpansion:
    """Tier 3 text field の wildcard は match_phrase ではなく wildcard query (case_insensitive=True)."""

    def test_host_wildcard(self) -> None:
        assert _compile("host:Homo*") == {
            "wildcard": {"host": {"value": "Homo*", "case_insensitive": True}},
        }

    def test_project_type_wildcard(self) -> None:
        # project_type は text のまま (enum 化対象外)、wildcard query 経路を維持する。
        assert _compile("project_type:gen*") == {
            "wildcard": {"projectType": {"value": "gen*", "case_insensitive": True}},
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


class TestTier3GrantTitleNested:
    # grant_title は単一 nested (grant.title) で、grant_agency の 2 段 nested と path が違う.
    def test_grant_title_word_single_nested(self) -> None:
        assert _compile("grant_title:CREST") == {
            "nested": {
                "path": "grant",
                "query": _contains_should("grant.title", "CREST"),
                "ignore_unmapped": True,
            },
        }

    def test_grant_title_phrase(self) -> None:
        assert _compile('grant_title:"JST CREST"') == {
            "nested": {
                "path": "grant",
                "query": {"match_phrase": {"grant.title": "JST CREST"}},
                "ignore_unmapped": True,
            },
        }

    def test_grant_title_wildcard(self) -> None:
        assert _compile("grant_title:CRES*") == {
            "nested": {
                "path": "grant",
                "query": {
                    "wildcard": {"grant.title": {"value": "CRES*", "case_insensitive": True}},
                },
                "ignore_unmapped": True,
            },
        }


class TestTier3GrantAgencyNested2:
    def test_grant_agency_word_two_level_nested(self) -> None:
        assert _compile("grant_agency:JSPS") == {
            "nested": {
                "path": "grant",
                "ignore_unmapped": True,
                "query": {
                    "nested": {
                        "path": "grant.agency",
                        "query": _contains_should("grant.agency.name", "JSPS"),
                        "ignore_unmapped": True,
                    },
                },
            },
        }

    def test_grant_agency_phrase(self) -> None:
        assert _compile('grant_agency:"National Institutes"') == {
            "nested": {
                "path": "grant",
                "ignore_unmapped": True,
                "query": {
                    "nested": {
                        "path": "grant.agency",
                        "query": {"match_phrase": {"grant.agency.name": "National Institutes"}},
                        "ignore_unmapped": True,
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
                            "ignore_unmapped": True,
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
                            "query": _contains_should("organization.name", "DDBJ"),
                            "ignore_unmapped": True,
                        },
                    },
                    _contains_should("title", "cancer"),
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
                "ignore_unmapped": True,
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
                        "ignore_unmapped": True,
                    },
                },
            },
        }


class TestCompileFreeText:
    """compile_free_text helper と FreeText AST 経由の compile が等価か."""

    _DEFAULT_FIELDS = ["identifier", "title", "name", "description", "organism.name"]

    def test_single_token_word_no_phrase(self) -> None:

        assert compile_free_text("cancer") == {
            "bool": {
                "must": [_keyword_should("cancer", self._DEFAULT_FIELDS)],
            },
        }

    def test_single_token_with_spaces_has_operator_and(self) -> None:
        """1 keyword 値内の空白は完全語 should の operator=and 側で AND 結合し、
        末尾トークンは phrase_prefix で前方一致する."""

        assert compile_free_text("whole genome") == {
            "bool": {
                "must": [_keyword_should("whole genome", self._DEFAULT_FIELDS)],
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
                    _keyword_should("cancer", self._DEFAULT_FIELDS),
                    _keyword_should("human", self._DEFAULT_FIELDS),
                ],
            },
        }

    def test_operator_or_uses_should_minimum_should_match(self) -> None:
        """``OR`` 操作子は ``bool.should`` + ``minimum_should_match: 1``."""

        assert compile_free_text("cancer, human", operator="OR") == {
            "bool": {
                "should": [
                    _keyword_should("cancer", self._DEFAULT_FIELDS),
                    _keyword_should("human", self._DEFAULT_FIELDS),
                ],
                "minimum_should_match": 1,
            },
        }

    def test_custom_fields(self) -> None:

        assert compile_free_text("cancer", fields=["title"]) == {
            "bool": {
                "must": [_keyword_should("cancer", ["title"])],
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

    _DEFAULT_FIELDS = ["identifier", "title", "name", "description", "organism.name"]

    def test_free_text_node_equals_compile_free_text(self) -> None:

        node = FreeText("cancer")
        assert compile_to_es(node) == compile_free_text("cancer")

    def test_multiword_free_text_node_emits_operator_and(self) -> None:
        """空白区切り bare word が畳まれた ``FreeText(value="cancer tumor")`` は値内空白を
        完全語 should の ``operator=and`` 側で AND 結合し、末尾トークンを phrase_prefix で
        前方一致する (parser → 1 FreeText → compile)."""

        node = FreeText("cancer tumor")
        assert compile_to_es(node) == {
            "bool": {
                "must": [_keyword_should("cancer tumor", self._DEFAULT_FIELDS)],
            },
        }

    def test_and_of_adv_and_free_text_flattens_multi_match(self) -> None:
        """``BoolOp(AND, [adv_ast, FreeText(q)])`` で FreeText の bool.must を flatten する."""

        adv_ast = FieldClause(
            field="organism_name",
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
                    # adv_ast の compile 結果 (organism_name は text 型 + value_kind=phrase →
                    # contains の phrase 経路 → match_phrase 単独。クオート値は前方一致しない)
                    {"match_phrase": {"organism.name": "Homo sapiens"}},
                    # FreeText の bare word should ラッパが flatten されて並ぶ.
                    _keyword_should("cancer", self._DEFAULT_FIELDS),
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
                    _contains_should("title", "cancer"),
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
                    _contains_should("title", "cancer"),
                    # FreeText の bool wrapper はそのまま (flatten しない)
                    {
                        "bool": {
                            "must": [_keyword_should("tumor", self._DEFAULT_FIELDS)],
                        },
                    },
                ],
                "minimum_should_match": 1,
            },
        }


class TestCompileToEsFreeTextOperator:
    """``compile_to_es(ast, free_text_operator="OR")`` の挙動."""

    _DEFAULT_FIELDS = ["identifier", "title", "name", "description", "organism.name"]

    def test_free_text_alone_or(self) -> None:
        """単一 FreeText で operator=OR は bool.should + minimum_should_match=1 を出す."""
        node = FreeText("cancer, tumor")
        result = compile_to_es(node, free_text_operator="OR")
        assert result == {
            "bool": {
                "should": [
                    _keyword_should("cancer", self._DEFAULT_FIELDS),
                    _keyword_should("tumor", self._DEFAULT_FIELDS),
                ],
                "minimum_should_match": 1,
            },
        }

    def test_and_of_adv_and_free_text_or_does_not_flatten(self) -> None:
        """AND 直下の FreeText でも operator=OR なら flatten せず bool wrapper を残す.

        OR semantics を AND clauses に inline すると意味が崩れるため。
        """
        adv_ast = FieldClause(
            field="title",
            value_kind="word",
            value="cancer",
            position=Position(column=1, length=12),
        )
        composite = BoolOp(
            op="AND",
            children=(adv_ast, FreeText("apple, banana")),
            position=Position(column=1, length=12),
        )
        result = compile_to_es(composite, free_text_operator="OR")
        # FreeText 側は bool.should にコンパイルされ、AND の must clauses にそのまま並ぶ
        assert result == {
            "bool": {
                "must": [
                    _contains_should("title", "cancer"),
                    {
                        "bool": {
                            "should": [
                                _keyword_should("apple", self._DEFAULT_FIELDS),
                                _keyword_should("banana", self._DEFAULT_FIELDS),
                            ],
                            "minimum_should_match": 1,
                        },
                    },
                ],
            },
        }

    def test_and_of_adv_and_free_text_and_still_flattens(self) -> None:
        """operator=AND (デフォルト) では従来通り flatten される (回帰テスト)."""
        adv_ast = FieldClause(
            field="title",
            value_kind="word",
            value="cancer",
            position=Position(column=1, length=12),
        )
        composite = BoolOp(
            op="AND",
            children=(adv_ast, FreeText("apple, banana")),
            position=Position(column=1, length=12),
        )
        result_default = compile_to_es(composite)
        result_explicit = compile_to_es(composite, free_text_operator="AND")
        # デフォルトと明示 AND は同じ結果 + flatten 済 (multi_match が must に直に並ぶ)
        assert result_default == result_explicit
        assert result_default == {
            "bool": {
                "must": [
                    _contains_should("title", "cancer"),
                    _keyword_should("apple", self._DEFAULT_FIELDS),
                    _keyword_should("banana", self._DEFAULT_FIELDS),
                ],
            },
        }


class TestCompileFreeTextMatchesBuildSearchQuery:
    """compile_free_text と build_search_query が同じ multi_match を返す.

    db-portal の ``_FREE_TEXT_DEFAULT_FIELDS`` と entries の ``_DEFAULT_KEYWORD_FIELDS``
    は同じ 5 field (``identifier`` / ``title`` / ``name`` / ``description`` / ``organism.name``) で揃えてあり、
    fields を省略しても一致する。auto-phrase 適用・operator 配置・token 分割など、
    fields 以外の構造の同期を保証するために fields を明示揃えて比較する。
    """

    _SHARED_FIELDS = ("identifier", "title", "name", "description", "organism.name")

    @pytest.mark.parametrize(
        "keywords",
        ["cancer", "HIF-1", '"RNA Seq"', "cancer, human", "BRCA1/2"],
    )
    def test_and_operator(self, keywords: str) -> None:
        expected = build_search_query(
            keywords=keywords,
            keyword_operator="AND",
            keyword_fields=list(self._SHARED_FIELDS),
            status_mode=None,
        )
        actual = compile_free_text(keywords, fields=self._SHARED_FIELDS)
        assert actual == expected

    @pytest.mark.parametrize(
        "keywords",
        ["cancer", "HIF-1", "cancer, human"],
    )
    def test_or_operator(self, keywords: str) -> None:
        expected = build_search_query(
            keywords=keywords,
            keyword_operator="OR",
            keyword_fields=list(self._SHARED_FIELDS),
            status_mode=None,
        )
        actual = compile_free_text(keywords, operator="OR", fields=self._SHARED_FIELDS)
        assert actual == expected

    @pytest.mark.parametrize(
        "keywords",
        ["cancer", "HIF-1", "cancer, human", "Homo sapiens"],
    )
    def test_default_fields_match(self, keywords: str) -> None:
        # fields を両側とも省略しても一致する (default 同期の契約).
        # entries 側 `_DEFAULT_KEYWORD_FIELDS` と db-portal 側 `_FREE_TEXT_DEFAULT_FIELDS`
        # がずれると "Homo sapiens" のような organism.name 依存クエリで結果が分岐する.
        expected = build_search_query(
            keywords=keywords,
            keyword_operator="AND",
            status_mode=None,
        )
        actual = compile_free_text(keywords)
        assert actual == expected


class TestCompileFreeTextNodePhrase:
    """``is_phrase=True`` の FreeText を AST 経由で compile すると順序保持の phrase match を出す.

    bug 期: parser が引用符を strip 後、``parse_keywords_with_autophrase`` の物理
    quote 判定が False になり、``multi_match.operator=and`` (順序非保持の AND match)
    が出力されていた。``is_phrase`` flag を AST に伝播することで
    ``multi_match.type=phrase`` (順序保持) を出すよう修正する。
    """

    _DEFAULT_FIELDS = ["identifier", "title", "name", "description", "organism.name"]

    def test_quoted_phrase_emits_multi_match_phrase(self) -> None:
        # parse('"Homo sapiens"') は FreeText(value="Homo sapiens", is_phrase=True) を生成.
        # 期待: multi_match.type=phrase (operator は付かない).
        result = compile_to_es(parse('"Homo sapiens"'))
        assert result == {
            "bool": {
                "must": [
                    {
                        "multi_match": {
                            "query": "Homo sapiens",
                            "fields": self._DEFAULT_FIELDS,
                            "type": "phrase",
                        },
                    },
                ],
            },
        }

    def test_single_quoted_phrase_emits_multi_match_phrase(self) -> None:
        # single quote でも対称に phrase match.
        result = compile_to_es(parse("'Homo sapiens'"))
        assert result == {
            "bool": {
                "must": [
                    {
                        "multi_match": {
                            "query": "Homo sapiens",
                            "fields": self._DEFAULT_FIELDS,
                            "type": "phrase",
                        },
                    },
                ],
            },
        }

    def test_quoted_phrase_with_comma_kept_as_single_phrase(self) -> None:
        # is_phrase=True ではコンマ分割を bypass し、value 全体を 1 phrase token として渡す.
        # bare の ``cancer, tumor`` (is_phrase=False) はコンマで 2 token に分割するが、
        # quoted ``"cancer, tumor"`` は 1 phrase のまま (引用符内コンマ保持仕様).
        result = compile_to_es(parse('"cancer, tumor"'))
        assert result == {
            "bool": {
                "must": [
                    {
                        "multi_match": {
                            "query": "cancer, tumor",
                            "fields": self._DEFAULT_FIELDS,
                            "type": "phrase",
                        },
                    },
                ],
            },
        }

    def test_quoted_phrase_inside_and_flatten_preserves_phrase(self) -> None:
        # AND 直下の FreeText (is_phrase=True) でも flatten ロジックで phrase が保持される.
        # AST children 順: [FreeText("Homo sapiens", phrase=True), FieldClause(title:cancer)].
        result = compile_to_es(parse('"Homo sapiens" AND title:cancer'))
        assert result == {
            "bool": {
                "must": [
                    {
                        "multi_match": {
                            "query": "Homo sapiens",
                            "fields": self._DEFAULT_FIELDS,
                            "type": "phrase",
                        },
                    },
                    # FIELD_TYPES["title"]="text" + value_kind="word" → "contains" →
                    # 完全一致 + 末尾前方一致の should ラッパ.
                    _contains_should("title", "cancer"),
                ],
            },
        }

    def test_bare_word_expands_to_phrase_prefix_should(self) -> None:
        # is_phrase=False の bare word は完全語一致 (operator=and) と末尾前方一致
        # (phrase_prefix) の 2 multi_match を持つ should ラッパに展開される (打ちかけ対応).
        result = compile_to_es(parse("cancer"))
        assert result == {
            "bool": {
                "must": [_keyword_should("cancer", self._DEFAULT_FIELDS)],
            },
        }

    def test_bare_word_with_symbol_still_auto_phrases(self) -> None:
        # 回帰: bare の auto-phrase trigger (-/.+:) は従来通り compile 段で type=phrase 化される.
        # AST の is_phrase=False とは独立して、value 文字列の物理判定で trigger 検出する.
        result = compile_to_es(parse("HIF-1"))
        assert result == {
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

    def test_compile_free_text_with_is_phrase_kwarg(self) -> None:
        # compile_free_text(value, is_phrase=True) は AST 非経由でも phrase 化する.
        # AST 経路 (_compile_node) はこの引数を内部で渡す.
        result = compile_free_text("Homo sapiens", is_phrase=True)
        assert result == {
            "bool": {
                "must": [
                    {
                        "multi_match": {
                            "query": "Homo sapiens",
                            "fields": self._DEFAULT_FIELDS,
                            "type": "phrase",
                        },
                    },
                ],
            },
        }

    def test_compile_free_text_is_phrase_default_false_is_backward_compatible(self) -> None:
        # entries / facets 系 (string-based 経路) は is_phrase 引数を渡さないため、
        # default False で従来の comma split + auto-phrase 経路 (operator=and) を維持する.
        result_implicit = compile_free_text("cancer tumor")
        result_explicit_false = compile_free_text("cancer tumor", is_phrase=False)
        assert result_implicit == result_explicit_false
        assert result_implicit == {
            "bool": {
                "must": [_keyword_should("cancer tumor", self._DEFAULT_FIELDS)],
            },
        }

    def test_free_text_node_with_is_phrase_true_directly(self) -> None:
        # AST を直接組み立てるユースケース (e.g. /db-portal/serialize) でも is_phrase が効く.
        node = FreeText(value="whole genome", is_phrase=True)
        result = compile_to_es(node)
        assert result == {
            "bool": {
                "must": [
                    {
                        "multi_match": {
                            "query": "whole genome",
                            "fields": self._DEFAULT_FIELDS,
                            "type": "phrase",
                        },
                    },
                ],
            },
        }
