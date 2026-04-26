"""Integration tests for IT-DBLINK-* scenarios.

DBLinks API: cross-reference lookups via DuckDB. See
``tests/integration-scenarios.md § IT-DBLINK-*`` for the SSOT.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from ddbj_search_api.schemas.dblink import AccessionType
from tests.integration.conftest import (
    NONEXISTENT_ID,
    PUBLIC_BIOPROJECT_ID,
    require_accession,
)


class TestDbLinkTypesList:
    """IT-DBLINK-01: GET /dblink/ returns the AccessionType union from converter."""

    def test_returns_200(self, app: TestClient) -> None:
        """IT-DBLINK-01: endpoint reachable."""
        resp = app.get("/dblink/")
        assert resp.status_code == 200

    def test_types_match_accession_type_enum(self, app: TestClient) -> None:
        """IT-DBLINK-01: response set equals ``AccessionType`` enum exactly.

        Asserting the set rather than the count surfaces both unintended
        omissions (member dropped from the enum) and accidental
        additions (type leaked from another enum).
        """
        types = app.get("/dblink/").json()["types"]
        assert set(types) == {member.value for member in AccessionType}

    def test_types_are_unique(self, app: TestClient) -> None:
        """IT-DBLINK-01: no duplicate type values."""
        types = app.get("/dblink/").json()["types"]
        assert len(types) == len(set(types))


class TestDbLinkTargetFilter:
    """IT-DBLINK-02: target filter (single / multiple / nonexistent)."""

    def test_single_target_keeps_only_that_type(self, app: TestClient) -> None:
        """IT-DBLINK-02: target=biosample → all dbXrefs are biosample."""
        accession = require_accession("PUBLIC_BIOPROJECT_ID", PUBLIC_BIOPROJECT_ID)
        resp = app.get(
            f"/dblink/bioproject/{accession}",
            params={"target": "biosample"},
        )
        assert resp.status_code == 200
        for ref in resp.json()["dbXrefs"]:
            assert ref["type"] == "biosample"

    def test_multiple_targets_keep_only_those_types(self, app: TestClient) -> None:
        """IT-DBLINK-02: comma-separated target list narrows by union."""
        accession = require_accession("PUBLIC_BIOPROJECT_ID", PUBLIC_BIOPROJECT_ID)
        resp = app.get(
            f"/dblink/bioproject/{accession}",
            params={"target": "biosample,sra-study"},
        )
        assert resp.status_code == 200
        allowed = {"biosample", "sra-study"}
        for ref in resp.json()["dbXrefs"]:
            assert ref["type"] in allowed

    def test_invalid_target_type_returns_422(self, app: TestClient) -> None:
        """IT-DBLINK-02: unknown target type → 422 ProblemDetails."""
        accession = require_accession("PUBLIC_BIOPROJECT_ID", PUBLIC_BIOPROJECT_ID)
        resp = app.get(
            f"/dblink/bioproject/{accession}",
            params={"target": "__not_a_type__"},
        )
        assert resp.status_code == 422

    def test_unfiltered_returns_all_types(self, app: TestClient) -> None:
        """IT-DBLINK-02: with no target, response shape is intact."""
        accession = require_accession("PUBLIC_BIOPROJECT_ID", PUBLIC_BIOPROJECT_ID)
        resp = app.get(f"/dblink/bioproject/{accession}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["identifier"] == accession
        assert body["type"] == "bioproject"
        assert isinstance(body["dbXrefs"], list)


class TestDbLinkSortOrder:
    """IT-DBLINK-03: dbXrefs sorted by type ASC, then identifier ASC."""

    def test_sorted_by_type_then_identifier(self, app: TestClient) -> None:
        """IT-DBLINK-03: lexicographic sort is honoured."""
        accession = require_accession("PUBLIC_BIOPROJECT_ID", PUBLIC_BIOPROJECT_ID)
        refs = app.get(f"/dblink/bioproject/{accession}").json()["dbXrefs"]
        if len(refs) < 2:
            # The sort order is observable only with multiple rows.
            return
        keys = [(r["type"], r["identifier"]) for r in refs]
        assert keys == sorted(keys)

    def test_repeated_calls_return_same_order(self, app: TestClient) -> None:
        """IT-DBLINK-03: sort is stable across requests."""
        accession = require_accession("PUBLIC_BIOPROJECT_ID", PUBLIC_BIOPROJECT_ID)
        first = app.get(f"/dblink/bioproject/{accession}").json()["dbXrefs"]
        second = app.get(f"/dblink/bioproject/{accession}").json()["dbXrefs"]
        assert first == second


class TestDbLinkNoLinks:
    """IT-DBLINK-04: a nonexistent accession returns 200 + empty dbXrefs."""

    def test_nonexistent_returns_empty_list(self, app: TestClient) -> None:
        """IT-DBLINK-04: 200 even when no related entry exists."""
        resp = app.get(f"/dblink/bioproject/{NONEXISTENT_ID}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["identifier"] == NONEXISTENT_ID
        assert body["type"] == "bioproject"
        assert body["dbXrefs"] == []


class TestDbLinkBulkCounts:
    """IT-DBLINK-05: POST /dblink/counts respects the upper-bound contract."""

    def test_single_item_returns_counts_dict(self, app: TestClient) -> None:
        """IT-DBLINK-05: single-element batch produces a dict (possibly empty)."""
        accession = require_accession("PUBLIC_BIOPROJECT_ID", PUBLIC_BIOPROJECT_ID)
        resp = app.post(
            "/dblink/counts",
            json={"items": [{"type": "bioproject", "id": accession}]},
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["identifier"] == accession
        assert items[0]["type"] == "bioproject"
        assert isinstance(items[0]["counts"], dict)

    def test_nonexistent_yields_empty_counts(self, app: TestClient) -> None:
        """IT-DBLINK-05: nonexistent IDs come back with ``counts == {}`` (not 404)."""
        resp = app.post(
            "/dblink/counts",
            json={"items": [{"type": "bioproject", "id": NONEXISTENT_ID}]},
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert items[0]["counts"] == {}

    def test_at_upper_bound_returns_200(self, app: TestClient) -> None:
        """IT-DBLINK-05: 100 items (the documented upper bound) → 200."""
        items = [{"type": "bioproject", "id": NONEXISTENT_ID}] * 100
        resp = app.post("/dblink/counts", json={"items": items})
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 100

    def test_above_upper_bound_returns_422(self, app: TestClient) -> None:
        """IT-DBLINK-05: 101 items breaks the upper bound → 422."""
        items = [{"type": "bioproject", "id": NONEXISTENT_ID}] * 101
        resp = app.post("/dblink/counts", json={"items": items})
        assert resp.status_code == 422

    def test_empty_items_returns_422(self, app: TestClient) -> None:
        """IT-DBLINK-05: empty array also fails Pydantic min_length=1."""
        resp = app.post("/dblink/counts", json={"items": []})
        assert resp.status_code == 422


class TestDbLinkInvalidPathType:
    """IT-DBLINK-06: invalid ``{type}`` is rejected as 422."""

    def test_unknown_path_type_returns_422(self, app: TestClient) -> None:
        """IT-DBLINK-06: ``/dblink/__not_a_type__/X`` fails AccessionType enum."""
        resp = app.get("/dblink/__not_a_type__/X")
        assert resp.status_code == 422

    def test_uppercase_known_type_returns_422(self, app: TestClient) -> None:
        """IT-DBLINK-06: enum values are case-sensitive (lowercase canonical)."""
        # ``BioProject`` is not a documented AccessionType (canonical is ``bioproject``).
        resp = app.get("/dblink/BioProject/X")
        assert resp.status_code == 422


class TestDbLinkCountsInvalidPayload:
    """IT-DBLINK-07: ``POST /dblink/counts`` rejects bad payloads atomically."""

    def test_invalid_type_in_single_item_returns_422(self, app: TestClient) -> None:
        """IT-DBLINK-07: AccessionType-外 ``type`` in any item → 422."""
        resp = app.post(
            "/dblink/counts",
            json={"items": [{"type": "__not_a_type__", "id": "X"}]},
        )
        assert resp.status_code == 422

    def test_mixed_valid_and_invalid_returns_422(self, app: TestClient) -> None:
        """IT-DBLINK-07: a single bad item taints the whole request."""
        resp = app.post(
            "/dblink/counts",
            json={
                "items": [
                    {"type": "bioproject", "id": "PRJDB1"},
                    {"type": "__not_a_type__", "id": "X"},
                ],
            },
        )
        assert resp.status_code == 422

    def test_missing_id_field_returns_422(self, app: TestClient) -> None:
        """IT-DBLINK-07: items missing the ``id`` field fail Pydantic."""
        resp = app.post(
            "/dblink/counts",
            json={"items": [{"type": "bioproject"}]},
        )
        assert resp.status_code == 422
