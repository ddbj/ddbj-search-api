"""Tests for ddbj_search_api.search.dsl.compiler_solr (AP3 Stage 3b).

ARSA / TXSearch 両 dialect をカバー。
SSOT: search-backends.md §バックエンド変換 (L520).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ddbj_search_api.search.dsl import parse
from ddbj_search_api.search.dsl.compiler_solr import SolrDialect, compile_to_solr
from ddbj_search_api.search.dsl.validator import validate


def _c(dsl: str, dialect: SolrDialect = "arsa") -> str:
    ast = parse(dsl)
    validate(ast, mode="cross")
    return compile_to_solr(ast, dialect=dialect)


class TestArsaBasics:
    def test_identifier_word_quoted(self) -> None:
        assert _c("identifier:PRJDB1") == 'PrimaryAccessionNumber:"PRJDB1"'

    def test_identifier_phrase_quoted(self) -> None:
        assert _c('identifier:"PRJDB1"') == 'PrimaryAccessionNumber:"PRJDB1"'

    def test_identifier_wildcard_unquoted(self) -> None:
        assert _c("identifier:PRJ*") == "PrimaryAccessionNumber:PRJ*"

    def test_title_word_quoted(self) -> None:
        assert _c("title:cancer") == 'Definition:"cancer"'

    def test_title_phrase(self) -> None:
        assert _c('title:"cancer treatment"') == 'Definition:"cancer treatment"'

    def test_description_word(self) -> None:
        assert _c("description:tumor") == 'AllText:"tumor"'


class TestArsaOrganism:
    def test_organism_word_expands_to_2_fields(self) -> None:
        assert _c("organism:human") == '(Organism:"human" OR Lineage:"human")'

    def test_organism_phrase_expands_to_2_fields(self) -> None:
        assert _c('organism:"Homo sapiens"') == '(Organism:"Homo sapiens" OR Lineage:"Homo sapiens")'


class TestArsaDate:
    def test_date_published_eq_formats_yyyymmdd(self) -> None:
        assert _c("date_published:2024-01-01") == "Date:20240101"

    def test_date_published_range_formats_yyyymmdd(self) -> None:
        assert _c("date_published:[2020-01-01 TO 2024-12-31]") == "Date:[20200101 TO 20241231]"

    @pytest.mark.parametrize(
        "dsl",
        [
            "date_modified:2024-01-01",
            "date_created:2024-01-01",
            "date:2024-01-01",
            "date:[2020-01-01 TO 2024-12-31]",
        ],
    )
    def test_unavailable_date_fields_degenerate(self, dsl: str) -> None:
        assert _c(dsl) == "(-*:*)"


class TestArsaBool:
    def test_and(self) -> None:
        assert _c("title:cancer AND organism:human") == (
            '(Definition:"cancer" AND (Organism:"human" OR Lineage:"human"))'
        )

    def test_or(self) -> None:
        assert _c("title:cancer OR title:tumor") == '(Definition:"cancer" OR Definition:"tumor")'

    def test_not(self) -> None:
        assert _c("NOT title:cancer") == '(NOT Definition:"cancer")'

    def test_precedence(self) -> None:
        assert _c("title:a OR title:b AND title:c") == ('(Definition:"a" OR (Definition:"b" AND Definition:"c"))')

    def test_parens_override(self) -> None:
        assert _c("(title:a OR title:b) AND title:c") == ('((Definition:"a" OR Definition:"b") AND Definition:"c")')

    def test_bool_with_degenerate_leaf(self) -> None:
        # title と date_modified の AND → 片方は degenerate、ツリー構造は維持
        assert _c("title:cancer AND date_modified:2024-01-01") == ('(Definition:"cancer" AND (-*:*))')


class TestTxSearchBasics:
    def test_identifier(self) -> None:
        assert _c("identifier:9606", "txsearch") == 'tax_id:"9606"'

    def test_identifier_wildcard(self) -> None:
        assert _c("identifier:96*", "txsearch") == "tax_id:96*"

    def test_title_word(self) -> None:
        assert _c("title:human", "txsearch") == 'scientific_name:"human"'

    def test_description_word(self) -> None:
        assert _c("description:tumor", "txsearch") == 'text:"tumor"'


class TestTxSearchDegenerate:
    @pytest.mark.parametrize(
        "dsl",
        [
            "organism:human",
            'organism:"Homo sapiens"',
            "date_published:2024-01-01",
            "date_modified:2024-01-01",
            "date_created:2024-01-01",
            "date:2024-01-01",
            "date_published:[2020-01-01 TO 2024-12-31]",
        ],
    )
    def test_unavailable_fields_degenerate(self, dsl: str) -> None:
        assert _c(dsl, "txsearch") == "(-*:*)"

    def test_bool_preserves_structure_with_degenerate_children(self) -> None:
        assert _c("title:human AND organism:primate", "txsearch") == ('(scientific_name:"human" AND (-*:*))')


class TestPhraseEscaping:
    def test_escape_double_quote_in_phrase(self) -> None:
        # DSL value `foo"bar` → Solr phrase `"foo\"bar"`
        assert _c(r'title:"foo\"bar"') == 'Definition:"foo\\"bar"'

    def test_escape_backslash_in_phrase(self) -> None:
        # DSL value `foo\bar` → Solr phrase `"foo\\bar"`
        assert _c(r'title:"foo\\bar"') == 'Definition:"foo\\\\bar"'


class TestCompilerSolrPBT:
    @given(
        field=st.sampled_from(["title", "description"]),
        word=st.text(
            alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")),
            min_size=1,
            max_size=10,
        ),
    )
    @settings(max_examples=30, deadline=None)
    def test_arsa_text_word_quoted(self, field: str, word: str) -> None:
        result = _c(f"{field}:{word}", "arsa")
        expected_field = {"title": "Definition", "description": "AllText"}[field]
        assert result == f'{expected_field}:"{word}"'

    @given(
        field=st.sampled_from(["title", "description"]),
        word=st.text(
            alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")),
            min_size=1,
            max_size=10,
        ),
    )
    @settings(max_examples=30, deadline=None)
    def test_txsearch_text_word_quoted(self, field: str, word: str) -> None:
        result = _c(f"{field}:{word}", "txsearch")
        expected_field = {"title": "scientific_name", "description": "text"}[field]
        assert result == f'{expected_field}:"{word}"'
