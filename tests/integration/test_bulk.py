"""Integration tests for IT-BULK-* scenarios.

POST /entries/{type}/bulk in JSON-array and NDJSON formats. See
``tests/integration-scenarios.md § IT-BULK-*``.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.integration.conftest import (
    NONEXISTENT_ID,
    PUBLIC_BIOPROJECT_ID,
)


class TestBulkJsonFormat:
    """IT-BULK-01: format=json returns ``{entries, notFound}``."""

    def test_returns_entries_and_not_found(self, app: TestClient) -> None:
        """IT-BULK-01: both keys present in the response body."""
        resp = app.post(
            "/entries/bioproject/bulk",
            json={"ids": [PUBLIC_BIOPROJECT_ID], "format": "json"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "entries" in body
        assert "notFound" in body


class TestBulkNdjsonFormat:
    """IT-BULK-02: format=ndjson streams one JSON per line."""

    def test_ndjson_content_type(self, app: TestClient) -> None:
        """IT-BULK-02: NDJSON variant carries application/x-ndjson."""
        resp = app.post(
            "/entries/bioproject/bulk",
            json={"ids": [PUBLIC_BIOPROJECT_ID], "format": "ndjson"},
        )
        assert resp.status_code == 200
        assert "application/x-ndjson" in resp.headers["content-type"]


class TestBulkInvariant:
    """IT-BULK-03: ``len(entries) + len(notFound) == len(set(ids))``."""

    def test_invariant_holds_with_mix(self, app: TestClient) -> None:
        """IT-BULK-03: counts add up with mixed existing / missing IDs."""
        ids = [PUBLIC_BIOPROJECT_ID, NONEXISTENT_ID]
        resp = app.post(
            "/entries/bioproject/bulk",
            json={"ids": ids, "format": "json"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["entries"]) + len(body["notFound"]) == len(set(ids))


class TestBulkDuplicateIds:
    """IT-BULK-04: duplicate IDs collapse to a single response entry."""

    def test_duplicates_collapse_to_one(self, app: TestClient) -> None:
        """IT-BULK-04: each unique ID appears at most once across the response."""
        resp = app.post(
            "/entries/bioproject/bulk",
            json={"ids": [PUBLIC_BIOPROJECT_ID] * 5, "format": "json"},
        )
        assert resp.status_code == 200
        body = resp.json()
        identifiers = [e["identifier"] for e in body["entries"]]
        assert len(identifiers) == len(set(identifiers))


class TestBulkSizeLimits:
    """IT-BULK-05 / IT-BULK-06: ``ids`` upper bound (1000) and empty array."""

    def test_at_upper_bound_returns_200(self, app: TestClient) -> None:
        """IT-BULK-05: 1000 ids → 200."""
        ids = [PUBLIC_BIOPROJECT_ID] * 1000
        resp = app.post(
            "/entries/bioproject/bulk",
            json={"ids": ids, "format": "json"},
        )
        assert resp.status_code == 200

    def test_above_upper_bound_returns_422(self, app: TestClient) -> None:
        """IT-BULK-05: 1001 ids → 422."""
        ids = [PUBLIC_BIOPROJECT_ID] * 1001
        resp = app.post(
            "/entries/bioproject/bulk",
            json={"ids": ids, "format": "json"},
        )
        assert resp.status_code == 422

    def test_empty_ids_returns_422(self, app: TestClient) -> None:
        """IT-BULK-06: empty ids array fails Pydantic ``min_length=1``."""
        resp = app.post(
            "/entries/bioproject/bulk",
            json={"ids": [], "format": "json"},
        )
        assert resp.status_code == 422


class TestBulkNotFound:
    """IT-BULK-07: nonexistent IDs land in the ``notFound`` array."""

    def test_nonexistent_id_in_not_found(self, app: TestClient) -> None:
        """IT-BULK-07: nonexistent IDs propagate to ``notFound``."""
        resp = app.post(
            "/entries/bioproject/bulk",
            json={"ids": [NONEXISTENT_ID], "format": "json"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert NONEXISTENT_ID in body["notFound"]
        assert body["entries"] == []


class TestArrayFieldContractInBulk:
    """IT-BULK-08: required list fields surface as keys (possibly empty)."""

    def test_default_response_carries_db_xrefs_key(self, app: TestClient) -> None:
        """IT-BULK-08: dbXrefs key present on each returned entry."""
        resp = app.post(
            "/entries/bioproject/bulk",
            json={"ids": [PUBLIC_BIOPROJECT_ID], "format": "json"},
        )
        assert resp.status_code == 200
        body = resp.json()
        for entry in body["entries"]:
            assert "dbXrefs" in entry
