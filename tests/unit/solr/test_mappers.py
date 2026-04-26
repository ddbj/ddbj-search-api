"""Tests for ddbj_search_api.solr.mappers.

Covers ARSA (Trad) and TXSearch (NCBI Taxonomy) Solr response mappers:
doc → DbPortalHit, envelope (total, hardLimitReached, hasNext),
date parsing, list-field flattening, and DB-specific extras passthrough.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from ddbj_search_api.schemas.db_portal import DbPortalHitBase, DbPortalHitsResponse
from ddbj_search_api.solr.mappers import (
    _parse_arsa_date,
    arsa_docs_to_hits,
    arsa_docs_to_lightweight_hits,
    arsa_response_to_envelope,
    txsearch_docs_to_hits,
    txsearch_docs_to_lightweight_hits,
    txsearch_response_to_envelope,
)


def _solr_envelope(docs: list[dict[str, Any]], num_found: int) -> dict[str, Any]:
    return {
        "responseHeader": {"status": 0, "QTime": 5},
        "response": {"numFound": num_found, "start": 0, "docs": docs},
    }


# === _parse_arsa_date ===


class TestParseArsaDate:
    def test_valid(self) -> None:
        assert _parse_arsa_date("20050411") == "2005-04-11"

    def test_recent(self) -> None:
        assert _parse_arsa_date("20260423") == "2026-04-23"

    def test_none(self) -> None:
        assert _parse_arsa_date(None) is None

    def test_empty(self) -> None:
        assert _parse_arsa_date("") is None

    def test_too_short(self) -> None:
        assert _parse_arsa_date("2005") is None

    def test_too_long(self) -> None:
        assert _parse_arsa_date("2005041101") is None

    def test_non_digit(self) -> None:
        assert _parse_arsa_date("abcd1234") is None

    def test_mixed(self) -> None:
        assert _parse_arsa_date("2005-4-11") is None


# === ARSA docs to hits ===


class TestArsaDocsToHits:
    def test_empty_list(self) -> None:
        assert arsa_docs_to_hits([]) == []

    def test_minimal_doc(self) -> None:
        doc = {"PrimaryAccessionNumber": "AY967397"}
        hits = arsa_docs_to_hits([doc])
        assert len(hits) == 1
        h = hits[0]
        assert h.identifier == "AY967397"
        assert h.type == "trad"
        assert h.title is None

    def test_type_always_trad(self) -> None:
        hits = arsa_docs_to_hits([{"PrimaryAccessionNumber": "X"}, {"PrimaryAccessionNumber": "Y"}])
        assert all(h.type == "trad" for h in hits)

    def test_title_from_definition(self) -> None:
        h = arsa_docs_to_hits([{"PrimaryAccessionNumber": "X", "Definition": "cool seq"}])[0]
        assert h.title == "cool seq"

    def test_organism_wrapped_as_name(self) -> None:
        h = arsa_docs_to_hits([{"PrimaryAccessionNumber": "X", "Organism": "Homo sapiens"}])[0]
        assert h.organism is not None
        assert h.organism.name == "Homo sapiens"
        assert h.organism.identifier is None

    def test_organism_missing_is_none(self) -> None:
        h = arsa_docs_to_hits([{"PrimaryAccessionNumber": "X"}])[0]
        assert h.organism is None

    def test_date_parsed(self) -> None:
        h = arsa_docs_to_hits([{"PrimaryAccessionNumber": "X", "Date": "20050411"}])[0]
        assert h.date_published == "2005-04-11"

    def test_date_invalid_becomes_none(self) -> None:
        h = arsa_docs_to_hits([{"PrimaryAccessionNumber": "X", "Date": "abc"}])[0]
        assert h.date_published is None

    def test_url_uses_accession(self) -> None:
        h = arsa_docs_to_hits([{"PrimaryAccessionNumber": "AY967397"}])[0]
        assert h.url == "https://getentry.ddbj.nig.ac.jp/getentry/na/AY967397/"

    def test_description_always_none(self) -> None:
        """description は空: Definition は title、Organism/Division は別 field で提示する."""
        h = arsa_docs_to_hits(
            [
                {
                    "PrimaryAccessionNumber": "X",
                    "Definition": "def",
                    "Organism": "orga",
                    "Division": "SYN",
                },
            ],
        )[0]
        assert h.description is None

    def test_description_is_none_even_without_other_fields(self) -> None:
        h = arsa_docs_to_hits([{"PrimaryAccessionNumber": "X"}])[0]
        assert h.description is None

    def test_organism_identifier_from_feature_taxon(self) -> None:
        """GenBank ``source`` feature's ``/db_xref="taxon:NNNN"`` lifts to organism.identifier."""
        h = arsa_docs_to_hits(
            [
                {
                    "PrimaryAccessionNumber": "X",
                    "Organism": "synthetic construct",
                    "Feature": [
                        'source\n1..726\n/organism="synthetic construct"\n/db_xref="taxon:32630"',
                        'CDS\n1..726\n/product="unknown protein"',
                    ],
                },
            ],
        )[0]
        assert h.organism is not None
        assert h.organism.identifier == "32630"
        assert h.organism.name == "synthetic construct"

    def test_organism_identifier_missing_when_feature_has_no_taxon(self) -> None:
        h = arsa_docs_to_hits(
            [
                {
                    "PrimaryAccessionNumber": "X",
                    "Organism": "no taxon",
                    "Feature": ['source\n1..100\n/organism="no taxon"'],
                },
            ],
        )[0]
        assert h.organism is not None
        assert h.organism.identifier is None

    def test_organism_identifier_missing_when_feature_absent(self) -> None:
        h = arsa_docs_to_hits([{"PrimaryAccessionNumber": "X", "Organism": "orga"}])[0]
        assert h.organism is not None
        assert h.organism.identifier is None

    def test_division_passthrough(self) -> None:
        h = arsa_docs_to_hits([{"PrimaryAccessionNumber": "X", "Division": "SYN"}])[0]
        dumped = h.model_dump(by_alias=True)
        assert dumped["division"] == "SYN"

    def test_molecular_type_passthrough(self) -> None:
        h = arsa_docs_to_hits([{"PrimaryAccessionNumber": "X", "MolecularType": "DNA"}])[0]
        dumped = h.model_dump(by_alias=True)
        assert dumped["molecularType"] == "DNA"

    def test_sequence_length_int(self) -> None:
        h = arsa_docs_to_hits([{"PrimaryAccessionNumber": "X", "SequenceLength": 73308}])[0]
        dumped = h.model_dump(by_alias=True)
        assert dumped["sequenceLength"] == 73308

    def test_sequence_length_string_digit(self) -> None:
        """ARSA may serialize SequenceLength as a string; accept digit strings."""
        h = arsa_docs_to_hits([{"PrimaryAccessionNumber": "X", "SequenceLength": "726"}])[0]
        dumped = h.model_dump(by_alias=True)
        assert dumped["sequenceLength"] == 726

    def test_sequence_length_invalid_becomes_none(self) -> None:
        h = arsa_docs_to_hits([{"PrimaryAccessionNumber": "X", "SequenceLength": "abc"}])[0]
        dumped = h.model_dump(by_alias=True)
        assert dumped["sequenceLength"] is None

    def test_same_as_and_db_xrefs_none(self) -> None:
        h = arsa_docs_to_hits([{"PrimaryAccessionNumber": "X"}])[0]
        assert h.same_as is None
        assert h.db_xrefs is None

    def test_primary_accession_missing_raises(self) -> None:
        # identifier is required on DbPortalHit; passing a doc without
        # PrimaryAccessionNumber should not silently produce an invalid hit.
        with pytest.raises(ValidationError):
            arsa_docs_to_hits([{"Definition": "no accession"}])


# === TXSearch docs to hits ===


class TestTxsearchDocsToHits:
    def test_empty_list(self) -> None:
        assert txsearch_docs_to_hits([]) == []

    def test_type_always_taxonomy(self) -> None:
        hits = txsearch_docs_to_hits([{"tax_id": "1"}, {"tax_id": "2"}])
        assert all(h.type == "taxonomy" for h in hits)

    def test_identifier_from_tax_id(self) -> None:
        h = txsearch_docs_to_hits([{"tax_id": "9606"}])[0]
        assert h.identifier == "9606"

    def test_identifier_coerced_to_str(self) -> None:
        h = txsearch_docs_to_hits([{"tax_id": 9606}])[0]
        assert h.identifier == "9606"

    def test_title_from_scientific_name(self) -> None:
        h = txsearch_docs_to_hits([{"tax_id": "9606", "scientific_name": "Homo sapiens"}])[0]
        assert h.title == "Homo sapiens"

    def test_organism_self_reference(self) -> None:
        h = txsearch_docs_to_hits([{"tax_id": "9606", "scientific_name": "Homo sapiens"}])[0]
        assert h.organism is not None
        assert h.organism.name == "Homo sapiens"
        assert h.organism.identifier == "9606"

    def test_date_published_always_none(self) -> None:
        h = txsearch_docs_to_hits([{"tax_id": "9606", "scientific_name": "Homo sapiens"}])[0]
        assert h.date_published is None

    def test_url_uses_tax_id(self) -> None:
        h = txsearch_docs_to_hits([{"tax_id": "9606"}])[0]
        assert h.url == "https://ddbj.nig.ac.jp/resource/taxonomy/9606"

    def test_rank_passthrough(self) -> None:
        h = txsearch_docs_to_hits([{"tax_id": "9606", "rank": "species"}])[0]
        dumped = h.model_dump(by_alias=True)
        assert dumped["rank"] == "species"

    def test_rank_missing_passthrough_none(self) -> None:
        h = txsearch_docs_to_hits([{"tax_id": "9606"}])[0]
        dumped = h.model_dump(by_alias=True)
        assert dumped.get("rank") is None

    def test_common_name_first_element(self) -> None:
        # TXSearch stores common_name as a multi-valued string list;
        # we expose the first value as a scalar for the UI.
        h = txsearch_docs_to_hits([{"tax_id": "9606", "common_name": ["human"]}])[0]
        dumped = h.model_dump(by_alias=True)
        assert dumped["commonName"] == "human"

    def test_common_name_string_passthrough(self) -> None:
        h = txsearch_docs_to_hits([{"tax_id": "9606", "common_name": "human"}])[0]
        dumped = h.model_dump(by_alias=True)
        assert dumped["commonName"] == "human"

    def test_common_name_missing_passthrough_none(self) -> None:
        h = txsearch_docs_to_hits([{"tax_id": "9606"}])[0]
        dumped = h.model_dump(by_alias=True)
        assert dumped.get("commonName") is None

    def test_japanese_name_first_element(self) -> None:
        h = txsearch_docs_to_hits([{"tax_id": "9606", "japanese_name": ["ヒト"]}])[0]
        dumped = h.model_dump(by_alias=True)
        assert dumped["japaneseName"] == "ヒト"

    def test_japanese_name_missing_passthrough_none(self) -> None:
        h = txsearch_docs_to_hits([{"tax_id": "9606"}])[0]
        dumped = h.model_dump(by_alias=True)
        assert dumped.get("japaneseName") is None

    def test_description_always_none(self) -> None:
        """description は空: common_name / rank / lineage は独立 field として露出する."""
        h = txsearch_docs_to_hits(
            [
                {
                    "tax_id": "9606",
                    "scientific_name": "Homo sapiens",
                    "common_name": ["human"],
                    "rank": "species",
                    "lineage": ["Homo sapiens", "Homo", "Hominidae"],
                },
            ],
        )[0]
        assert h.description is None

    def test_description_all_missing_is_none(self) -> None:
        h = txsearch_docs_to_hits([{"tax_id": "9606"}])[0]
        assert h.description is None

    def test_lineage_drops_self_when_head_matches_scientific_name(self) -> None:
        """TXSearch は lineage に自身の scientific_name を含めるが、NCBI 流の祖先のみ返す."""
        h = txsearch_docs_to_hits(
            [
                {
                    "tax_id": "9606",
                    "scientific_name": "Homo sapiens",
                    "lineage": ["Homo sapiens", "Homo", "Homininae", "Hominidae"],
                },
            ],
        )[0]
        dumped = h.model_dump(by_alias=True)
        assert dumped["lineage"] == ["Homo", "Homininae", "Hominidae"]

    def test_lineage_preserved_when_head_does_not_match(self) -> None:
        """稀に head が自身でない doc もそのまま返す (自己除去は head match 時だけ)."""
        h = txsearch_docs_to_hits(
            [
                {
                    "tax_id": "9606",
                    "scientific_name": "Homo sapiens",
                    "lineage": ["Boreoeutheria", "Eutheria"],
                },
            ],
        )[0]
        dumped = h.model_dump(by_alias=True)
        assert dumped["lineage"] == ["Boreoeutheria", "Eutheria"]

    def test_lineage_string_passthrough(self) -> None:
        """string 形式の lineage はそのまま (list でないので head チェック対象外)."""
        h = txsearch_docs_to_hits(
            [
                {
                    "tax_id": "9606",
                    "scientific_name": "Homo sapiens",
                    "lineage": "Homo sapiens; Homo",
                },
            ],
        )[0]
        dumped = h.model_dump(by_alias=True)
        assert dumped["lineage"] == "Homo sapiens; Homo"

    def test_lineage_missing_is_none(self) -> None:
        h = txsearch_docs_to_hits([{"tax_id": "9606", "scientific_name": "Homo sapiens"}])[0]
        dumped = h.model_dump(by_alias=True)
        assert dumped.get("lineage") is None

    def test_same_as_and_db_xrefs_none(self) -> None:
        h = txsearch_docs_to_hits([{"tax_id": "9606"}])[0]
        assert h.same_as is None
        assert h.db_xrefs is None

    def test_tax_id_missing_raises(self) -> None:
        with pytest.raises(ValidationError):
            txsearch_docs_to_hits([{"scientific_name": "no id"}])


# === ARSA envelope ===


class TestArsaDocsToLightweightHits:
    """ARSA → 12-field lightweight DbPortalHit (cross-search)."""

    _LIGHTWEIGHT_FIELDS = {
        "identifier",
        "type",
        "url",
        "title",
        "description",
        "organism",
        "status",
        "accessibility",
        "dateCreated",
        "dateModified",
        "datePublished",
        "isPartOf",
    }

    def test_empty_input_returns_empty_list(self) -> None:
        assert arsa_docs_to_lightweight_hits([]) == []

    def test_full_doc_maps_12_fields(self) -> None:
        hits = arsa_docs_to_lightweight_hits(
            [
                {
                    "PrimaryAccessionNumber": "GL589895",
                    "Definition": "Mus musculus scaffold",
                    "Organism": "Mus musculus",
                    "Date": "20150313",
                    "Feature": ['source 1..1000\n/db_xref="taxon:10090"'],
                    # Trad-only extras to verify they get dropped.
                    "Division": "CON",
                    "MolecularType": "DNA",
                    "SequenceLength": 635881,
                },
            ],
        )
        assert len(hits) == 1
        h = hits[0]
        dumped = h.model_dump(by_alias=True)
        assert set(dumped.keys()) == self._LIGHTWEIGHT_FIELDS
        assert dumped["identifier"] == "GL589895"
        assert dumped["type"] == "trad"
        assert dumped["url"] == "https://getentry.ddbj.nig.ac.jp/getentry/na/GL589895/"
        assert dumped["title"] == "Mus musculus scaffold"
        assert dumped["description"] is None
        assert dumped["organism"] == {"identifier": "10090", "name": "Mus musculus"}
        assert dumped["datePublished"] == "2015-03-13"
        assert dumped["status"] == "public"
        assert dumped["accessibility"] == "public-access"
        assert dumped["isPartOf"] == "trad"
        assert dumped["dateCreated"] is None
        assert dumped["dateModified"] is None

    def test_missing_organism_yields_none(self) -> None:
        hits = arsa_docs_to_lightweight_hits(
            [{"PrimaryAccessionNumber": "X1", "Definition": "title"}],
        )
        assert hits[0].organism is None

    def test_missing_date_yields_none(self) -> None:
        hits = arsa_docs_to_lightweight_hits(
            [{"PrimaryAccessionNumber": "X1", "Definition": "title"}],
        )
        assert hits[0].date_published is None

    def test_invalid_date_yields_none(self) -> None:
        hits = arsa_docs_to_lightweight_hits(
            [{"PrimaryAccessionNumber": "X1", "Date": "not a date"}],
        )
        assert hits[0].date_published is None


class TestTxsearchDocsToLightweightHits:
    """TXSearch → 12-field lightweight DbPortalHit (cross-search)."""

    _LIGHTWEIGHT_FIELDS = {
        "identifier",
        "type",
        "url",
        "title",
        "description",
        "organism",
        "status",
        "accessibility",
        "dateCreated",
        "dateModified",
        "datePublished",
        "isPartOf",
    }

    def test_empty_input_returns_empty_list(self) -> None:
        assert txsearch_docs_to_lightweight_hits([]) == []

    def test_full_doc_maps_12_fields(self) -> None:
        hits = txsearch_docs_to_lightweight_hits(
            [
                {
                    "tax_id": 9606,
                    "scientific_name": "Homo sapiens",
                    # Taxonomy-only extras to verify they get dropped.
                    "rank": "species",
                    "common_name": ["human"],
                    "japanese_name": ["ヒト"],
                    "lineage": ["Homo sapiens", "Homo", "Hominidae"],
                },
            ],
        )
        assert len(hits) == 1
        h = hits[0]
        dumped = h.model_dump(by_alias=True)
        assert set(dumped.keys()) == self._LIGHTWEIGHT_FIELDS
        assert dumped["identifier"] == "9606"
        assert dumped["type"] == "taxonomy"
        assert dumped["url"] == "https://ddbj.nig.ac.jp/resource/taxonomy/9606"
        assert dumped["title"] == "Homo sapiens"
        assert dumped["description"] is None
        assert dumped["organism"] == {"identifier": "9606", "name": "Homo sapiens"}
        assert dumped["status"] == "public"
        assert dumped["accessibility"] == "public-access"
        assert dumped["isPartOf"] == "taxonomy"
        # TXSearch has no published date concept.
        assert dumped["datePublished"] is None
        assert dumped["dateCreated"] is None
        assert dumped["dateModified"] is None

    def test_only_tax_id_organism_kept(self) -> None:
        hits = txsearch_docs_to_lightweight_hits([{"tax_id": 1}])
        assert hits[0].organism is not None
        assert hits[0].organism.identifier == "1"
        assert hits[0].organism.name is None


class TestArsaResponseToEnvelope:
    def test_total_from_num_found(self) -> None:
        env = arsa_response_to_envelope(_solr_envelope([], 42), page=1, per_page=20, sort=None)
        assert env.total == 42

    def test_hits_length_matches_docs(self) -> None:
        docs = [{"PrimaryAccessionNumber": f"X{i}"} for i in range(3)]
        env = arsa_response_to_envelope(_solr_envelope(docs, 3), page=1, per_page=20, sort=None)
        assert len(env.hits) == 3

    def test_hard_limit_reached_at_10000(self) -> None:
        env = arsa_response_to_envelope(_solr_envelope([], 10_000), page=1, per_page=20, sort=None)
        assert env.hard_limit_reached is True

    def test_hard_limit_not_reached_at_9999(self) -> None:
        env = arsa_response_to_envelope(_solr_envelope([], 9_999), page=1, per_page=20, sort=None)
        assert env.hard_limit_reached is False

    def test_has_next_when_more_pages(self) -> None:
        env = arsa_response_to_envelope(_solr_envelope([], 50), page=1, per_page=20, sort=None)
        assert env.has_next is True

    def test_has_next_false_on_last_page(self) -> None:
        env = arsa_response_to_envelope(_solr_envelope([], 40), page=2, per_page=20, sort=None)
        assert env.has_next is False

    def test_next_cursor_always_null(self) -> None:
        env = arsa_response_to_envelope(_solr_envelope([], 500), page=1, per_page=20, sort=None)
        assert env.next_cursor is None

    def test_page_and_per_page_echoed(self) -> None:
        env = arsa_response_to_envelope(_solr_envelope([], 5), page=3, per_page=50, sort="datePublished:desc")
        assert env.page == 3
        assert env.per_page == 50


# === TXSearch envelope ===


class TestTxsearchResponseToEnvelope:
    def test_total_from_num_found(self) -> None:
        env = txsearch_response_to_envelope(_solr_envelope([], 12), page=1, per_page=20, sort=None)
        assert env.total == 12

    def test_hits_mapped(self) -> None:
        docs = [{"tax_id": "9606", "scientific_name": "Homo sapiens"}]
        env = txsearch_response_to_envelope(_solr_envelope(docs, 1), page=1, per_page=20, sort=None)
        assert env.hits[0].identifier == "9606"
        assert env.hits[0].type == "taxonomy"

    def test_hard_limit_reached_boundary(self) -> None:
        env = txsearch_response_to_envelope(_solr_envelope([], 10_000), page=1, per_page=20, sort=None)
        assert env.hard_limit_reached is True

    def test_hard_limit_below(self) -> None:
        env = txsearch_response_to_envelope(_solr_envelope([], 1), page=1, per_page=20, sort=None)
        assert env.hard_limit_reached is False

    def test_next_cursor_null(self) -> None:
        env = txsearch_response_to_envelope(_solr_envelope([], 100), page=1, per_page=20, sort=None)
        assert env.next_cursor is None

    def test_has_next_true(self) -> None:
        env = txsearch_response_to_envelope(_solr_envelope([], 100), page=1, per_page=20, sort=None)
        assert env.has_next is True

    def test_has_next_false(self) -> None:
        env = txsearch_response_to_envelope(_solr_envelope([], 5), page=1, per_page=20, sort=None)
        assert env.has_next is False


# === Envelope malformed / missing fields ===


class TestArsaEnvelopeMissingResponseFields:
    def test_missing_response_key_zero_total(self) -> None:
        # Defensive: if Solr ever returns no "response" key we degrade
        # to empty envelope instead of crashing with KeyError.
        env = arsa_response_to_envelope({"responseHeader": {}}, page=1, per_page=20, sort=None)
        assert env.total == 0
        assert env.hits == []

    def test_missing_docs(self) -> None:
        env = arsa_response_to_envelope(
            {"response": {"numFound": 5}},
            page=1,
            per_page=20,
            sort=None,
        )
        assert env.total == 5
        assert env.hits == []


# === PBT: mapper robustness ===


class TestMappersPBT:
    @given(acc=st.text(alphabet=st.characters(whitelist_categories=("L", "N")), min_size=1, max_size=15))
    def test_arsa_any_accession_produces_hit(self, acc: str) -> None:
        hits = arsa_docs_to_hits([{"PrimaryAccessionNumber": acc}])
        assert hits[0].identifier == acc
        assert hits[0].type == "trad"
        assert isinstance(hits[0], DbPortalHitBase)

    @given(tax_id=st.integers(min_value=1, max_value=10_000_000))
    def test_txsearch_any_tax_id_produces_hit(self, tax_id: int) -> None:
        hits = txsearch_docs_to_hits([{"tax_id": tax_id}])
        assert hits[0].identifier == str(tax_id)
        assert hits[0].type == "taxonomy"

    @given(num_found=st.integers(min_value=0, max_value=10_000_000))
    def test_envelope_total_matches_num_found(self, num_found: int) -> None:
        env = arsa_response_to_envelope(_solr_envelope([], num_found), page=1, per_page=20, sort=None)
        assert env.total == num_found
        assert env.hard_limit_reached == (num_found >= 10_000)
        assert isinstance(env, DbPortalHitsResponse)

    @given(
        page=st.integers(min_value=1, max_value=500),
        per_page=st.sampled_from([20, 50, 100]),
        total=st.integers(min_value=0, max_value=100_000),
    )
    def test_has_next_matches_offset(self, page: int, per_page: int, total: int) -> None:
        env = arsa_response_to_envelope(_solr_envelope([], total), page=page, per_page=per_page, sort=None)
        assert env.has_next == (page * per_page < total)
