"""Tests for ddbj_search_api.search.dsl.compiler_solr (Stage 3b).

ARSA / TXSearch 両 dialect をカバー。
SSOT: search-backends.md §バックエンド変換 (L520).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ddbj_search_api.schemas.db_portal import DbPortalDb
from ddbj_search_api.search.dsl import parse
from ddbj_search_api.search.dsl.compiler_solr import SolrDialect, compile_to_solr
from ddbj_search_api.search.dsl.validator import validate


def _c(dsl: str, dialect: SolrDialect = "arsa") -> str:
    ast = parse(dsl)
    validate(ast, mode="cross")
    return compile_to_solr(ast, dialect=dialect)


def _c_single(dsl: str, dialect: SolrDialect, db: DbPortalDb) -> str:
    """Tier 3 (single mode required) を single mode で compile するヘルパ."""
    ast = parse(dsl)
    validate(ast, mode="single", db=db)
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


"""=== Tier 2 / Tier 3 ==="""


class TestArsaTier2Publication:
    """Tier 2 publication は ARSA で ReferencePubmedID にマップ."""

    def test_publication_word(self) -> None:
        assert _c("publication:12345678") == 'ReferencePubmedID:"12345678"'

    def test_publication_wildcard(self) -> None:
        assert _c("publication:123*") == "ReferencePubmedID:123*"


class TestArsaTier2SubmitterDegenerate:
    """Tier 2 submitter は ARSA に相当 field なし → degenerate."""

    def test_submitter_word_degenerate(self) -> None:
        assert _c('submitter:"Tokyo University"') == "(-*:*)"


class TestTxSearchTier2Degenerate:
    """Tier 2 (submitter / publication) は TXSearch に相当 field なし → degenerate."""

    def test_submitter_degenerate(self) -> None:
        assert _c("submitter:foo", "txsearch") == "(-*:*)"

    def test_publication_degenerate(self) -> None:
        assert _c("publication:123", "txsearch") == "(-*:*)"


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
        assert _c_single(dsl, "arsa", DbPortalDb.trad) == expected

    def test_sequence_length_eq(self) -> None:
        assert _c_single("sequence_length:5000", "arsa", DbPortalDb.trad) == 'SequenceLength:"5000"'

    def test_sequence_length_range(self) -> None:
        assert _c_single("sequence_length:[100 TO 5000]", "arsa", DbPortalDb.trad) == "SequenceLength:[100 TO 5000]"


class TestArsaTier3EsOnlyDegenerate:
    """ES-only Tier 3 (project_type / library_* / study_type / experiment_type / submission_type / grant_agency)
    は ARSA で degenerate."""

    @pytest.mark.parametrize(
        ("dsl", "db"),
        [
            ("project_type:BioProject", DbPortalDb.bioproject),
            ("library_strategy:WGS", DbPortalDb.sra),
            ("platform:ILLUMINA", DbPortalDb.sra),
            ("instrument_model:NovaSeq", DbPortalDb.sra),
            ("study_type:Cohort", DbPortalDb.jga),
            ("experiment_type:ChIP-Seq", DbPortalDb.gea),
            ("submission_type:metabolite", DbPortalDb.metabobank),
            ("grant_agency:JSPS", DbPortalDb.bioproject),
        ],
    )
    def test_es_tier3_degenerates_on_arsa(self, dsl: str, db: DbPortalDb) -> None:
        assert _c_single(dsl, "arsa", db) == "(-*:*)"


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
        assert _c_single(dsl, "arsa", DbPortalDb.taxonomy) == "(-*:*)"


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
        assert _c_single(dsl, "txsearch", DbPortalDb.taxonomy) == expected

    def test_wildcard_works_on_text_field(self) -> None:
        # rank は enum で wildcard 不可。text 型の kingdom で検証。
        assert _c_single("kingdom:Anim*", "txsearch", DbPortalDb.taxonomy) == "kingdom:Anim*"


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
        assert _c_single(dsl, "txsearch", DbPortalDb.trad) == "(-*:*)"


class TestTxSearchEsOnlyTier3Degenerate:
    """ES-only Tier 3 は TXSearch で degenerate."""

    @pytest.mark.parametrize(
        ("dsl", "db"),
        [
            ("project_type:BioProject", DbPortalDb.bioproject),
            ("library_strategy:WGS", DbPortalDb.sra),
            ("study_type:Cohort", DbPortalDb.jga),
            ("grant_agency:JSPS", DbPortalDb.bioproject),
        ],
    )
    def test_es_tier3_degenerates_on_txsearch(self, dsl: str, db: DbPortalDb) -> None:
        assert _c_single(dsl, "txsearch", db) == "(-*:*)"


class TestSolrBoolWithTier3Mixed:
    """Tier 3 と Tier 1 を bool で混ぜた時、ARSA 側が適切に degenerate を含む."""

    def test_trad_mixed(self) -> None:
        # division:BCT AND title:cancer → ARSA 両方あり
        assert _c_single("division:BCT AND title:cancer", "arsa", DbPortalDb.trad) == (
            '(Division:"BCT" AND Definition:"cancer")'
        )

    def test_taxonomy_mixed_on_txsearch(self) -> None:
        # rank:species AND title:Homo → TXSearch 両方あり
        assert _c_single("rank:species AND title:Homo", "txsearch", DbPortalDb.taxonomy) == (
            '(rank:"species" AND scientific_name:"Homo")'
        )


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
