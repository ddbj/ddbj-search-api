"""Integration tests for entry detail endpoints.

- GET /entries/{type}/{id}          (frontend-oriented, truncated dbXrefs)
- GET /entries/{type}/{id}.json     (raw ES document)
- GET /entries/{type}/{id}.jsonld   (JSON-LD format)
- GET /entries/{type}/{id}/dbxrefs.json (full dbXrefs)
"""
from typing import Tuple

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def bioproject_id(app: TestClient) -> Tuple[str, str]:
    """Dynamically fetch a bioproject entry ID from ES."""
    resp = app.get(
        "/entries/bioproject/",
        params={"perPage": 1},
    )
    body = resp.json()
    if body["pagination"]["total"] == 0:
        pytest.skip("No bioproject entries in ES")
    entry_id = body["items"][0]["identifier"]

    return "bioproject", entry_id


@pytest.fixture(scope="session")
def biosample_id(app: TestClient) -> Tuple[str, str]:
    """Dynamically fetch a biosample entry ID from ES."""
    resp = app.get(
        "/entries/biosample/",
        params={"perPage": 1},
    )
    body = resp.json()
    if body["pagination"]["total"] == 0:
        pytest.skip("No biosample entries in ES")
    entry_id = body["items"][0]["identifier"]

    return "biosample", entry_id


# === GET /entries/{type}/{id} (detail) ===


def test_entry_detail_returns_200(
    app: TestClient,
    bioproject_id: Tuple[str, str],
):
    """Detail endpoint returns 200 for a known entry."""
    db_type, entry_id = bioproject_id
    resp = app.get(f"/entries/{db_type}/{entry_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["identifier"] == entry_id
    assert body["type"] == db_type


def test_entry_detail_has_db_xrefs_count(
    app: TestClient,
    bioproject_id: Tuple[str, str],
):
    """Detail response includes dbXrefsCount."""
    db_type, entry_id = bioproject_id
    resp = app.get(f"/entries/{db_type}/{entry_id}")
    body = resp.json()

    assert "dbXrefsCount" in body
    assert isinstance(body["dbXrefsCount"], dict)


def test_entry_detail_not_found(app: TestClient):
    """Non-existent ID returns 404."""
    resp = app.get("/entries/bioproject/NONEXISTENT_ID_99999")

    assert resp.status_code == 404
    body = resp.json()
    assert body["status"] == 404


# === GET /entries/{type}/{id}.json ===


def test_entry_json_returns_raw_document(
    app: TestClient,
    bioproject_id: Tuple[str, str],
):
    """The .json endpoint returns the raw ES document."""
    db_type, entry_id = bioproject_id
    resp = app.get(f"/entries/{db_type}/{entry_id}.json")

    assert resp.status_code == 200
    body = resp.json()
    assert body["identifier"] == entry_id
    assert body["type"] == db_type


def test_entry_json_contains_full_db_xrefs(
    app: TestClient,
    bioproject_id: Tuple[str, str],
):
    """The .json endpoint returns the full dbXrefs (no truncation)."""
    db_type, entry_id = bioproject_id
    resp = app.get(f"/entries/{db_type}/{entry_id}.json")
    body = resp.json()

    if "dbXrefs" in body:
        assert isinstance(body["dbXrefs"], list)
    # dbXrefsCount should NOT be present in raw format
    assert "dbXrefsCount" not in body


def test_entry_json_not_found(app: TestClient):
    """Non-existent ID returns 404 for .json endpoint."""
    resp = app.get("/entries/bioproject/NONEXISTENT_ID_99999.json")

    assert resp.status_code == 404


# === GET /entries/{type}/{id}.jsonld ===


def test_entry_jsonld_has_context_and_id(
    app: TestClient,
    bioproject_id: Tuple[str, str],
):
    """JSON-LD response includes @context and @id fields."""
    db_type, entry_id = bioproject_id
    resp = app.get(f"/entries/{db_type}/{entry_id}.jsonld")

    assert resp.status_code == 200
    body = resp.json()
    assert "@context" in body
    assert "@id" in body
    assert entry_id in body["@id"]


def test_entry_jsonld_content_type(
    app: TestClient,
    bioproject_id: Tuple[str, str],
):
    """JSON-LD response has application/ld+json content type."""
    db_type, entry_id = bioproject_id
    resp = app.get(f"/entries/{db_type}/{entry_id}.jsonld")

    assert "application/ld+json" in resp.headers["content-type"]


def test_entry_jsonld_not_found(app: TestClient):
    """Non-existent ID returns 404 for .jsonld endpoint."""
    resp = app.get("/entries/bioproject/NONEXISTENT_ID_99999.jsonld")

    assert resp.status_code == 404


# === GET /entries/{type}/{id}/dbxrefs.json ===


def test_dbxrefs_json_returns_full_xrefs(
    app: TestClient,
    bioproject_id: Tuple[str, str],
):
    """The dbxrefs.json endpoint returns a dbXrefs array."""
    db_type, entry_id = bioproject_id
    resp = app.get(f"/entries/{db_type}/{entry_id}/dbxrefs.json")

    assert resp.status_code == 200
    body = resp.json()
    assert "dbXrefs" in body
    assert isinstance(body["dbXrefs"], list)


def test_dbxrefs_json_not_found(app: TestClient):
    """Non-existent ID returns 404 for dbxrefs.json endpoint."""
    resp = app.get("/entries/bioproject/NONEXISTENT_ID_99999/dbxrefs.json")

    assert resp.status_code == 404


# === Cross-type detail tests ===


def test_entry_detail_biosample(
    app: TestClient,
    biosample_id: Tuple[str, str],
):
    """Detail endpoint works for biosample type too."""
    db_type, entry_id = biosample_id
    resp = app.get(f"/entries/{db_type}/{entry_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["identifier"] == entry_id
    assert body["type"] == db_type


# === Bug fix verification: dbXrefsLimit default ===


def test_entry_detail_db_xrefs_limit_default(
    app: TestClient,
    bioproject_id: Tuple[str, str],
):
    """Default dbXrefsLimit works correctly after bug fix.

    dbXrefs should be a list (not a dict).
    """
    db_type, entry_id = bioproject_id
    resp = app.get(f"/entries/{db_type}/{entry_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["dbXrefs"], list)
    for xref in body["dbXrefs"]:
        assert isinstance(xref, dict)


# === dbXrefsCount consistency ===


def test_entry_detail_db_xrefs_count_consistency(
    app: TestClient,
    bioproject_id: Tuple[str, str],
):
    """Sum of dbXrefsCount matches total xrefs from dbxrefs.json."""
    db_type, entry_id = bioproject_id

    detail_resp = app.get(f"/entries/{db_type}/{entry_id}")
    assert detail_resp.status_code == 200
    detail_body = detail_resp.json()
    count_by_type = detail_body.get("dbXrefsCount", {})
    total_from_count = sum(count_by_type.values())

    xrefs_resp = app.get(f"/entries/{db_type}/{entry_id}/dbxrefs.json")
    assert xrefs_resp.status_code == 200
    xrefs_body = xrefs_resp.json()
    total_from_xrefs = len(xrefs_body["dbXrefs"])

    assert total_from_count == total_from_xrefs
