"""Integration tests for IT-DBPORTAL-* scenarios (Solr-backed).

/db-portal/cross-search 8-DB fan-out and /db-portal/search?db=trad |
taxonomy. All tests in this module require Solr (ARSA + TXSearch),
hence the module-level ``staging_only`` marker.

See ``tests/integration-scenarios.md § IT-DBPORTAL-*``.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.staging_only


class TestArsaMolecularType:
    """IT-DBPORTAL-01: ARSA exposes MolecularType."""

    def test_molecular_type_present_in_some_hits(self, app: TestClient) -> None:
        """IT-DBPORTAL-01: at least one trad hit carries MolecularType."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "trad", "q": "*", "perPage": 20},
        )
        assert resp.status_code == 200
        hits = resp.json()["hits"]
        assert len(hits) > 0
        assert any("MolecularType" in hit for hit in hits)


class TestArsaSequenceLength:
    """IT-DBPORTAL-02: ARSA exposes SequenceLength."""

    def test_sequence_length_present_in_some_hits(self, app: TestClient) -> None:
        """IT-DBPORTAL-02: at least one trad hit carries SequenceLength."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "trad", "q": "*", "perPage": 20},
        )
        assert resp.status_code == 200
        hits = resp.json()["hits"]
        assert len(hits) > 0
        assert any("SequenceLength" in hit for hit in hits)


class TestArsaOrganismIdentifier:
    """IT-DBPORTAL-03: organism.identifier extracted from ``db_xref="taxon:..."``."""

    def test_organism_identifier_no_taxon_prefix(self, app: TestClient) -> None:
        """IT-DBPORTAL-03: organism.identifier holds the bare numeric ID."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "trad", "q": "cancer", "perPage": 20},
        )
        assert resp.status_code == 200
        for hit in resp.json()["hits"]:
            organism = hit.get("organism") or {}
            ident = organism.get("identifier")
            if ident is not None:
                assert not str(ident).startswith("taxon:"), f"prefixed: {ident}"


class TestSolrDescriptionAlwaysNull:
    """IT-DBPORTAL-04: trad / taxonomy ``description`` always null."""

    def test_trad_description_null(self, app: TestClient) -> None:
        """IT-DBPORTAL-04: every trad hit has description == null."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "trad", "q": "*", "perPage": 20},
        )
        assert resp.status_code == 200
        for hit in resp.json()["hits"]:
            assert hit.get("description") is None

    def test_taxonomy_description_null(self, app: TestClient) -> None:
        """IT-DBPORTAL-04: every taxonomy hit has description == null."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "taxonomy", "q": "*", "perPage": 20},
        )
        assert resp.status_code == 200
        for hit in resp.json()["hits"]:
            assert hit.get("description") is None


class TestTxsearchLineageSelfRemoval:
    """IT-DBPORTAL-05: TXSearch lineage drops the leading self-name."""

    def test_lineage_first_not_scientific_name(self, app: TestClient) -> None:
        """IT-DBPORTAL-05: ``lineage[0] != scientific_name`` on every hit."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "taxonomy", "q": "Homo", "perPage": 20},
        )
        assert resp.status_code == 200
        for hit in resp.json()["hits"]:
            lineage = hit.get("lineage") or []
            sci = hit.get("scientific_name")
            if lineage and sci:
                assert lineage[0] != sci, f"lineage[0]={lineage[0]} sci={sci}"


class TestSolrUfAllowlistCompleteness:
    """IT-DBPORTAL-06: Tier 3 fields are accepted by edismax (uf allowlist)."""

    def test_tier3_field_query_accepted(self, app: TestClient) -> None:
        """IT-DBPORTAL-06: ``adv=MolecularType:DNA`` is accepted by Solr."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "trad", "adv": "MolecularType:DNA", "perPage": 5},
        )
        assert resp.status_code == 200


class TestCrossSearchEightDbFanout:
    """IT-DBPORTAL-07: cross-search fan-out across 8 DBs."""

    def test_eight_dbs_present(self, app: TestClient) -> None:
        """IT-DBPORTAL-07: every documented DB key is present in the response."""
        resp = app.get(
            "/db-portal/cross-search",
            params={"q": "cancer", "topHits": 10},
        )
        assert resp.status_code == 200
        databases = resp.json()["databases"]
        # ``DbPortalCrossSearchResponse.databases`` is fixed length 8 with one
        # entry per documented DB.
        present_dbs = {entry.get("db") for entry in databases}
        expected = {
            "bioproject",
            "biosample",
            "sra",
            "jga",
            "gea",
            "metabobank",
            "trad",
            "taxonomy",
        }
        assert expected <= present_dbs, f"missing DBs: {expected - present_dbs}"


class TestCrossSearchTopHitsBoundary:
    """IT-DBPORTAL-08: topHits 0 / 50 / 51."""

    def test_top_hits_zero_returns_200(self, app: TestClient) -> None:
        """IT-DBPORTAL-08: topHits=0 → 200 (count-only mode)."""
        resp = app.get(
            "/db-portal/cross-search",
            params={"q": "cancer", "topHits": 0},
        )
        assert resp.status_code == 200

    def test_top_hits_fifty_returns_200(self, app: TestClient) -> None:
        """IT-DBPORTAL-08: topHits=50 (max) → 200."""
        resp = app.get(
            "/db-portal/cross-search",
            params={"q": "cancer", "topHits": 50},
        )
        assert resp.status_code == 200

    def test_top_hits_fifty_one_returns_422(self, app: TestClient) -> None:
        """IT-DBPORTAL-08: topHits=51 → 422."""
        resp = app.get(
            "/db-portal/cross-search",
            params={"q": "cancer", "topHits": 51},
        )
        assert resp.status_code == 422


class TestSearchTradCursorNotSupported:
    """IT-DBPORTAL-09: db=trad cursor 不可."""

    def test_trad_cursor_returns_400(self, app: TestClient) -> None:
        """IT-DBPORTAL-09: trad search rejects cursor with the slug."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "trad", "q": "cancer", "cursor": "any-token"},
        )
        assert resp.status_code == 400
        assert "cursor-not-supported" in resp.json().get("type", "")


class TestSearchTaxonomyCursorNotSupported:
    """IT-DBPORTAL-10: db=taxonomy cursor 不可."""

    def test_taxonomy_cursor_returns_400(self, app: TestClient) -> None:
        """IT-DBPORTAL-10: taxonomy search rejects cursor with the slug."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "taxonomy", "q": "Homo", "cursor": "any-token"},
        )
        assert resp.status_code == 400
        assert "cursor-not-supported" in resp.json().get("type", "")


class TestSearchSolrPerPageAllowlist:
    """IT-DBPORTAL-11: db=trad/taxonomy perPage は {20, 50, 100} のみ."""

    def test_perpage_20_succeeds(self, app: TestClient) -> None:
        """IT-DBPORTAL-11: trad perPage=20 → 200."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "trad", "q": "cancer", "perPage": 20},
        )
        assert resp.status_code == 200

    def test_perpage_30_returns_422(self, app: TestClient) -> None:
        """IT-DBPORTAL-11: trad perPage=30 → 422 (allowlist violation)."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "trad", "q": "cancer", "perPage": 30},
        )
        assert resp.status_code == 422


class TestCrossSearchPerBackendTimeout:
    """IT-DBPORTAL-12: per-backend timeout — partial failure does not 502."""

    def test_response_within_overall_timeout(self, app: TestClient) -> None:
        """IT-DBPORTAL-12: cross-search returns under the documented overall timeout."""
        start = time.time()
        resp = app.get(
            "/db-portal/cross-search",
            params={"q": "cancer", "topHits": 10},
        )
        elapsed = time.time() - start
        assert resp.status_code in {200, 502}
        # Documented overall timeout is 20s; allow generous slack for slow CI.
        assert elapsed < 30.0
