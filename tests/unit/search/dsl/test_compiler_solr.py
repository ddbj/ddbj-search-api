"""Tests for ddbj_search_api.search.dsl.compiler_solr (Stage 3b).

ARSA / TXSearch 両 dialect をカバー。
SSOT: search-backends.md §バックエンド変換 (L520).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ddbj_search_api.search.dsl import parse
from ddbj_search_api.search.dsl.ast import BoolOp, FieldClause, FreeText, Position
from ddbj_search_api.search.dsl.compiler_solr import (
    SolrDialect,
    compile_free_text_solr,
    compile_to_solr,
)


def _c(dsl: str, dialect: SolrDialect = "arsa") -> str:
    return compile_to_solr(parse(dsl), dialect=dialect)


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
            # accessibility は ES backed 6 DB 共通だが Trad (ARSA) は INSDC 登録系で全 public、
            # accessibility field 不在のため degenerate
            "accessibility:public-access",
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
            # accessibility は Taxonomy 概念外、TXSearch field 不在で degenerate
            "accessibility:public-access",
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


"""=== Tier 2 / Tier 3 ==="""


class TestArsaTier2PublicationDegenerate:
    """Tier 2 publication は publication.title (text) に正規化され、ARSA に相当 field なし → degenerate."""

    def test_publication_word_degenerate(self) -> None:
        assert _c("publication:cancer") == "(-*:*)"

    def test_publication_wildcard_degenerate(self) -> None:
        assert _c("publication:canc*") == "(-*:*)"


class TestArsaTier2SubmitterDegenerate:
    """Tier 2 submitter は ARSA に相当 field なし → degenerate."""

    def test_submitter_word_degenerate(self) -> None:
        assert _c('submitter:"Tokyo University"') == "(-*:*)"


class TestTxSearchTier2Degenerate:
    """Tier 2 (submitter / publication) は TXSearch に相当 field なし → degenerate."""

    def test_submitter_degenerate(self) -> None:
        assert _c("submitter:foo", "txsearch") == "(-*:*)"

    def test_publication_degenerate(self) -> None:
        assert _c("publication:cancer", "txsearch") == "(-*:*)"


class TestArsaTier3Trad:
    """Trad Tier 3 (5 field) は ARSA フィールド名にマップ."""

    @pytest.mark.parametrize(
        ("dsl", "expected"),
        [
            ("division:BCT", 'Division:"BCT"'),
            ("molecular_type:DNA", 'MolecularType:"DNA"'),
            ("feature_gene_name:BRCA1", 'FeatureQualifier:"BRCA1"'),
            ('reference_journal:"Nature Methods"', 'ReferenceJournal:"Nature Methods"'),
        ],
    )
    def test_trad_field_maps(self, dsl: str, expected: str) -> None:
        assert _c(dsl, "arsa") == expected

    def test_sequence_length_eq(self) -> None:
        assert _c("sequence_length:5000", "arsa") == 'SequenceLength:"5000"'

    def test_sequence_length_range(self) -> None:
        assert _c("sequence_length:[100 TO 5000]", "arsa") == "SequenceLength:[100 TO 5000]"


class TestArsaTier3EsOnlyDegenerate:
    """ES-only Tier 3 (project_type / library_* / study_type / experiment_type / submission_type / grant_agency)
    は ARSA で degenerate."""

    @pytest.mark.parametrize(
        "dsl",
        [
            "project_type:BioProject",
            "library_strategy:WGS",
            "library_selection:RANDOM",
            "platform:ILLUMINA",
            "instrument_model:NovaSeq",
            "study_type:Cohort",
            "experiment_type:ChIP-Seq",
            "submission_type:metabolite",
            "grant_agency:JSPS",
            "package:MIGS.ba",
            "model:HiSeq",
            "type:sra-experiment",
            "external_link_label:GEO",
            "derived_from_id:SAMD00012345",
        ],
    )
    def test_es_tier3_degenerates_on_arsa(self, dsl: str) -> None:
        assert _c(dsl, "arsa") == "(-*:*)"


class TestArsaTaxonomyTier3Degenerate:
    """Taxonomy Tier 3 (10 field) は ARSA で degenerate."""

    @pytest.mark.parametrize(
        "dsl",
        [
            "rank:species",
            "lineage:Eukaryota",
            "kingdom:Animalia",
            "phylum:Chordata",
            "class:Mammalia",
            "order:Primates",
            "family:Hominidae",
            "genus:Homo",
            "species:sapiens",
            "common_name:human",
        ],
    )
    def test_taxonomy_degenerates_on_arsa(self, dsl: str) -> None:
        assert _c(dsl, "arsa") == "(-*:*)"


class TestTxSearchTier3Taxonomy:
    """Taxonomy Tier 3 (10 field) は TXSearch フィールド名にマップ."""

    @pytest.mark.parametrize(
        ("dsl", "expected"),
        [
            ("rank:species", 'rank:"species"'),
            ("lineage:Eukaryota", 'lineage:"Eukaryota"'),
            ("kingdom:Animalia", 'kingdom:"Animalia"'),
            ("phylum:Chordata", 'phylum:"Chordata"'),
            ("class:Mammalia", 'class:"Mammalia"'),
            ("order:Primates", 'order:"Primates"'),
            ("family:Hominidae", 'family:"Hominidae"'),
            ("genus:Homo", 'genus:"Homo"'),
            ("species:sapiens", 'species:"sapiens"'),
            ("common_name:human", 'common_name:"human"'),
        ],
    )
    def test_taxonomy_field_maps(self, dsl: str, expected: str) -> None:
        assert _c(dsl, "txsearch") == expected

    def test_wildcard_works_on_text_field(self) -> None:
        # rank は enum で wildcard 不可。text 型の kingdom で検証。
        assert _c("kingdom:Anim*", "txsearch") == "kingdom:Anim*"


class TestTxSearchTradTier3Degenerate:
    """Trad Tier 3 は TXSearch で degenerate."""

    @pytest.mark.parametrize(
        "dsl",
        [
            "division:BCT",
            "molecular_type:DNA",
            "sequence_length:5000",
            "feature_gene_name:BRCA1",
            "reference_journal:Nature",
        ],
    )
    def test_trad_degenerates_on_txsearch(self, dsl: str) -> None:
        assert _c(dsl, "txsearch") == "(-*:*)"


class TestTxSearchEsOnlyTier3Degenerate:
    """ES-only Tier 3 は TXSearch で degenerate."""

    @pytest.mark.parametrize(
        "dsl",
        [
            "project_type:BioProject",
            "library_strategy:WGS",
            "library_selection:RANDOM",
            "study_type:Cohort",
            "grant_agency:JSPS",
            "package:MIGS.ba",
            "model:HiSeq",
            "type:sra-experiment",
            "external_link_label:GEO",
            "derived_from_id:SAMD00012345",
        ],
    )
    def test_es_tier3_degenerates_on_txsearch(self, dsl: str) -> None:
        assert _c(dsl, "txsearch") == "(-*:*)"


class TestSolrBoolWithTier3Mixed:
    """Tier 3 と Tier 1 を bool で混ぜた時、ARSA 側が適切に degenerate を含む."""

    def test_trad_mixed(self) -> None:
        # division:BCT AND title:cancer → ARSA 両方あり
        assert _c("division:BCT AND title:cancer", "arsa") == ('(Division:"BCT" AND Definition:"cancer")')

    def test_taxonomy_mixed_on_txsearch(self) -> None:
        # rank:species AND title:Homo → TXSearch 両方あり
        assert _c("rank:species AND title:Homo", "txsearch") == ('(rank:"species" AND scientific_name:"Homo")')


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


class TestCompileFreeTextSolr:
    """compile_free_text_solr: トークンを quote し、operator (AND/OR) で連結して edismax ``q`` を返す."""

    def test_single_token(self) -> None:
        assert compile_free_text_solr("cancer") == '"cancer"'

    def test_multiple_tokens_default_and(self) -> None:
        # token 間は AND がデフォルト (DSL の明示 BoolOp とは独立)
        assert compile_free_text_solr("cancer, human") == '("cancer" AND "human")'

    def test_multiple_tokens_or_operator(self) -> None:
        # operator="OR" で token 間を OR 連結
        assert compile_free_text_solr("cancer, human", operator="OR") == '("cancer" OR "human")'

    def test_single_token_or_operator_omits_paren(self) -> None:
        # 1 token のときは連結も括弧も不要 (operator に依らない)
        assert compile_free_text_solr("cancer", operator="OR") == '"cancer"'

    def test_stray_quote_in_token_stripped(self) -> None:
        """stray double-quote は tokenize_keywords が strip する (escape されない)."""

        # 'say "hello"' は phrase で囲まれていないため、_split_raw_tokens では
        # 1 トークンとして扱われ、stray ``"`` が strip された ``say hello`` が
        # double-quote で wrap される
        assert compile_free_text_solr('say "hello"') == '"say hello"'

    def test_backslash_in_token_escaped(self) -> None:
        """backslash は escape_solr_phrase で重ねられる."""

        assert compile_free_text_solr("a\\b") == '"a\\\\b"'

    def test_empty_value_returns_match_all(self) -> None:

        assert compile_free_text_solr("") == "*:*"

    def test_whitespace_only_returns_match_all(self) -> None:

        assert compile_free_text_solr("   ") == "*:*"


class TestCompileToSolrFreeTextNode:
    """compile_to_solr(FreeText(...)) と AND 合成 AST の挙動."""

    def test_free_text_node_alone(self) -> None:

        assert compile_to_solr(FreeText("cancer"), dialect="arsa") == '"cancer"'
        assert compile_to_solr(FreeText("cancer"), dialect="txsearch") == '"cancer"'

    def test_and_of_adv_and_free_text_arsa(self) -> None:
        """``BoolOp(AND, [adv_ast, FreeText(q)])`` で ``(<adv> AND <q_tokens>)`` 形式の単一括弧."""

        adv_ast = FieldClause(
            field="title",
            value_kind="word",
            value="leukemia",
            position=Position(column=1, length=14),
        )
        composite = BoolOp(
            op="AND",
            children=(adv_ast, FreeText("cancer")),
            position=Position(column=1, length=14),
        )
        result = compile_to_solr(composite, dialect="arsa")
        assert result == '(Definition:"leukemia" AND "cancer")'

    def test_and_of_adv_and_free_text_txsearch(self) -> None:

        adv_ast = FieldClause(
            field="title",
            value_kind="word",
            value="Homo",
            position=Position(column=1, length=10),
        )
        composite = BoolOp(
            op="AND",
            children=(adv_ast, FreeText("sapiens")),
            position=Position(column=1, length=10),
        )
        result = compile_to_solr(composite, dialect="txsearch")
        assert result == '(scientific_name:"Homo" AND "sapiens")'

    def test_and_of_adv_and_empty_free_text_falls_back_to_match_all(self) -> None:
        """FreeText("") は ``*:*`` にフォールバック (handler は通常 q="" を None 化するが安全側)."""

        adv_ast = FieldClause(
            field="title",
            value_kind="word",
            value="cancer",
            position=Position(column=1, length=12),
        )
        composite = BoolOp(
            op="AND",
            children=(adv_ast, FreeText("")),
            position=Position(column=1, length=12),
        )
        result = compile_to_solr(composite, dialect="arsa")
        assert result == '(Definition:"cancer" AND *:*)'

    def test_free_text_multi_token_or_operator(self) -> None:
        """operator="OR" で FreeText の token 間が OR で連結される (token 区切りは
        カンマ。空白区切りでは 1 token のまま)。"""
        node = FreeText("cancer, tumor")
        assert compile_to_solr(node, dialect="arsa", free_text_operator="OR") == '("cancer" OR "tumor")'
        assert compile_to_solr(node, dialect="txsearch", free_text_operator="OR") == '("cancer" OR "tumor")'

    def test_and_of_adv_and_free_text_or_keeps_inner_or(self) -> None:
        """BoolOp(AND, [adv, FreeText("a, b")], free_text_operator="OR") は
        adv AND ("a" OR "b") 形に括弧で分離して残る (AND 句に inline されない)。"""
        adv_ast = FieldClause(
            field="title",
            value_kind="word",
            value="leukemia",
            position=Position(column=1, length=14),
        )
        composite = BoolOp(
            op="AND",
            children=(adv_ast, FreeText("apple, banana")),
            position=Position(column=1, length=14),
        )
        result = compile_to_solr(composite, dialect="arsa", free_text_operator="OR")
        assert result == '(Definition:"leukemia" AND ("apple" OR "banana"))'
