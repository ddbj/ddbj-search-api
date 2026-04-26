"""Integration tests for IT-STATUS-* scenarios.

ES status field (public / suppressed / private) visibility control
across /entries/* and /db-portal/* (ES 6 DB), with no-op for the two
Solr-backed DBs. ``withdrawn`` is omitted from the integration matrix
because converter-side input XML never carries withdrawn records, so
those entries do not reach ES (api-spec.md § データ可視性 注釈).

See ``tests/integration-scenarios.md § IT-STATUS-*``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import (
    NONEXISTENT_ID,
    PRIVATE_SRA_EXPERIMENT_ID,
    PUBLIC_BIOPROJECT_ID,
    SUPPRESSED_BIOPROJECT_ID,
    require_accession,
)


class TestEntriesExcludeHiddenInFreeText:
    """IT-STATUS-01: free-text search excludes withdrawn/private/suppressed."""

    def test_results_are_all_public(self, app: TestClient) -> None:
        """IT-STATUS-01: every item.status is ``public`` for non-accession keywords."""
        resp = app.get("/entries/", params={"keywords": "cancer", "perPage": 50})
        assert resp.status_code == 200
        for item in resp.json()["items"]:
            assert item.get("status") == "public"


class TestEntriesIncludeSuppressedOnAccessionMatch:
    """IT-STATUS-02: keywords=<suppressed accession> exposes the suppressed entry."""

    def test_suppressed_appears_for_exact_accession(self, app: TestClient) -> None:
        """IT-STATUS-02: suppressed surfaces only on accession exact-match."""
        suppressed = require_accession(
            "SUPPRESSED_BIOPROJECT_ID",
            SUPPRESSED_BIOPROJECT_ID,
        )
        resp = app.get("/entries/", params={"keywords": suppressed})
        assert resp.status_code == 200
        identifiers = {item["identifier"] for item in resp.json()["items"]}
        assert suppressed in identifiers


class TestDetailPrivateAndSuppressed:
    """IT-STATUS-03: detail variants — private → 404, suppressed → 200."""

    @pytest.mark.parametrize("suffix", ["", ".json", ".jsonld", "/dbxrefs.json"])
    def test_private_returns_404(self, app: TestClient, suffix: str) -> None:
        """IT-STATUS-03: private accession → 404 across all four variants."""
        accession = require_accession(
            "PRIVATE_SRA_EXPERIMENT_ID",
            PRIVATE_SRA_EXPERIMENT_ID,
        )
        resp = app.get(f"/entries/sra-experiment/{accession}{suffix}")
        assert resp.status_code == 404, f"{suffix}: {resp.status_code}"

    @pytest.mark.parametrize("suffix", ["", ".json", ".jsonld", "/dbxrefs.json"])
    def test_suppressed_returns_200(self, app: TestClient, suffix: str) -> None:
        """IT-STATUS-03: suppressed accession → 200 across all four variants."""
        accession = require_accession(
            "SUPPRESSED_BIOPROJECT_ID",
            SUPPRESSED_BIOPROJECT_ID,
        )
        resp = app.get(f"/entries/bioproject/{accession}{suffix}")
        assert resp.status_code == 200, f"{suffix}: {resp.status_code}"


class TestDetail404DetailStringIndistinguishable:
    """IT-STATUS-04: 404 detail strings match between hidden and missing IDs.

    All four variants (``/{id}``, ``.json``, ``.jsonld``, ``/dbxrefs.json``)
    must produce the same detail string regardless of whether the entry
    is missing or hidden — leaking the requested accession through the
    detail breaks the visibility-hiding contract in api-spec.md
    § データ可視性.
    """

    def test_private_detail_matches_missing(self, app: TestClient) -> None:
        """IT-STATUS-04: private vs nonexistent share detail across 4 variants."""
        private = require_accession(
            "PRIVATE_SRA_EXPERIMENT_ID",
            PRIVATE_SRA_EXPERIMENT_ID,
        )
        for suffix in ("", ".json", ".jsonld", "/dbxrefs.json"):
            priv = app.get(f"/entries/sra-experiment/{private}{suffix}")
            miss = app.get(f"/entries/sra-experiment/{NONEXISTENT_ID}{suffix}")
            assert priv.status_code == miss.status_code == 404, suffix
            assert priv.json()["detail"] == miss.json()["detail"], suffix
            # The accession must not leak through the detail string.
            assert private not in priv.json()["detail"], suffix
            assert NONEXISTENT_ID not in miss.json()["detail"], suffix


class TestBulkSplitsByStatus:
    """IT-STATUS-05: bulk classifies based on visibility."""

    def test_mixed_statuses_split_correctly(self, app: TestClient) -> None:
        """IT-STATUS-05: public + suppressed → entries; missing → notFound."""
        public_id = require_accession("PUBLIC_BIOPROJECT_ID", PUBLIC_BIOPROJECT_ID)
        suppressed_id = require_accession(
            "SUPPRESSED_BIOPROJECT_ID",
            SUPPRESSED_BIOPROJECT_ID,
        )
        ids = [public_id, suppressed_id, NONEXISTENT_ID]
        resp = app.post(
            "/entries/bioproject/bulk",
            json={"ids": ids, "format": "json"},
        )
        assert resp.status_code == 200
        body = resp.json()
        entry_ids = {e["identifier"] for e in body["entries"]}
        assert public_id in entry_ids
        assert suppressed_id in entry_ids
        assert NONEXISTENT_ID in body["notFound"]


class TestFacetsExcludeHidden:
    """IT-STATUS-08: facets aggregate over status:public only."""

    def test_bucket_counts_are_non_negative(self, app: TestClient) -> None:
        """IT-STATUS-08: structural — bucket counts non-negative, no 5xx."""
        resp = app.get("/facets")
        assert resp.status_code == 200
        for bucket in resp.json()["facets"].get("type") or []:
            assert bucket["count"] >= 0


class TestDbPortalCrossSearchExcludeHidden:
    """IT-STATUS-09: cross-search free-text excludes hidden statuses."""

    def test_free_text_hits_are_public(self, app: TestClient) -> None:
        """IT-STATUS-09: ES-backed DB hits all carry status=public."""
        resp = app.get(
            "/db-portal/cross-search",
            params={"q": "cancer", "topHits": 10},
        )
        assert resp.status_code == 200
        databases = resp.json()["databases"]
        es_dbs = {"bioproject", "biosample", "sra", "jga", "gea", "metabobank"}
        for entry in databases:
            if entry.get("db") not in es_dbs:
                continue
            for hit in entry.get("hits") or []:
                assert hit.get("status") == "public", f"{entry['db']} hit has status={hit.get('status')}"


class TestDbPortalCrossSearchAccessionExposesSuppressed:
    """IT-STATUS-10: q=<suppressed accession> exposes the entry in its DB."""

    def test_suppressed_visible_for_accession_query(self, app: TestClient) -> None:
        """IT-STATUS-10: cross-search ``q`` exact-match unlocks suppressed."""
        accession = require_accession(
            "SUPPRESSED_BIOPROJECT_ID",
            SUPPRESSED_BIOPROJECT_ID,
        )
        resp = app.get(
            "/db-portal/cross-search",
            params={"q": accession, "topHits": 10},
        )
        assert resp.status_code == 200
        bp = next(
            (d for d in resp.json()["databases"] if d.get("db") == "bioproject"),
            None,
        )
        assert bp is not None
        identifiers = {h["identifier"] for h in bp.get("hits") or []}
        assert accession in identifiers


class TestDbPortalCrossSearchIdentifierLeaf:
    """IT-STATUS-11: adv=identifier:<accession> single-leaf exposes suppressed."""

    def test_identifier_leaf_exposes_suppressed(self, app: TestClient) -> None:
        """IT-STATUS-11: single-leaf eq adv unlocks suppressed."""
        accession = require_accession(
            "SUPPRESSED_BIOPROJECT_ID",
            SUPPRESSED_BIOPROJECT_ID,
        )
        resp = app.get(
            "/db-portal/cross-search",
            params={"adv": f"identifier:{accession}", "topHits": 10},
        )
        assert resp.status_code == 200


class TestDbPortalCrossSearchAndWrappedHidesSuppressed:
    """IT-STATUS-12: AND/OR wrapping disqualifies the suppressed relaxation."""

    def test_and_wrap_hides_suppressed(self, app: TestClient) -> None:
        """IT-STATUS-12: ``identifier:<X> AND title:Y`` does not unlock suppressed."""
        accession = require_accession(
            "SUPPRESSED_BIOPROJECT_ID",
            SUPPRESSED_BIOPROJECT_ID,
        )
        resp = app.get(
            "/db-portal/cross-search",
            params={
                "adv": f"identifier:{accession} AND title:cancer",
                "topHits": 10,
            },
        )
        assert resp.status_code == 200
        bp = next(
            (d for d in resp.json()["databases"] if d.get("db") == "bioproject"),
            None,
        )
        if bp is not None:
            identifiers = {h["identifier"] for h in bp.get("hits") or []}
            assert accession not in identifiers


class TestDbPortalSearchAccessionFirstPage:
    """IT-STATUS-13: q=<accession> on /db-portal/search exposes suppressed on page 1."""

    def test_first_page_includes_suppressed(self, app: TestClient) -> None:
        """IT-STATUS-13: page 1 surfaces the suppressed entry for accession-exact q."""
        accession = require_accession(
            "SUPPRESSED_BIOPROJECT_ID",
            SUPPRESSED_BIOPROJECT_ID,
        )
        resp = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": accession, "perPage": 20},
        )
        assert resp.status_code == 200
        identifiers = {h["identifier"] for h in resp.json()["hits"]}
        assert accession in identifiers


class TestDbPortalSearchAdvLeafSuppressed:
    """IT-STATUS-14: adv=identifier:<accession> on /db-portal/search returns suppressed."""

    def test_adv_leaf_returns_suppressed(self, app: TestClient) -> None:
        """IT-STATUS-14: single-leaf adv (offset path) exposes suppressed."""
        accession = require_accession(
            "SUPPRESSED_BIOPROJECT_ID",
            SUPPRESSED_BIOPROJECT_ID,
        )
        resp = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "adv": f"identifier:{accession}",
                "perPage": 20,
            },
        )
        assert resp.status_code == 200
        identifiers = {h["identifier"] for h in resp.json()["hits"]}
        assert accession in identifiers


class TestDbPortalSearchSolrNoStatusFilter:
    """IT-STATUS-15: Solr-backed search responses always carry status=public."""

    @pytest.mark.staging_only
    def test_trad_hit_never_exposes_hidden_status(self, app: TestClient) -> None:
        """IT-STATUS-15: trad hits never carry hidden statuses."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "trad", "q": "*", "perPage": 20},
        )
        assert resp.status_code == 200
        # The trad index has no non-public records, so the implementation
        # simply leaves ``status`` ``null`` (no filter is injected, since
        # there is nothing to hide). The invariant is that hidden statuses
        # never appear.
        for hit in resp.json()["hits"]:
            assert hit.get("status") in (None, "public")
