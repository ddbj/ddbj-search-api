"""Integration tests for POST /entries/{type}/bulk."""
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def bioproject_ids(app: TestClient):
    """Fetch a few bioproject IDs for bulk tests."""
    resp = app.get(
        "/entries/bioproject/",
        params={"perPage": 3, "dbXrefsLimit": 0},
    )
    body = resp.json()
    if body["pagination"]["total"] == 0:
        pytest.skip("No bioproject entries in ES")

    return [item["identifier"] for item in body["items"]]


# === JSON format (default) ===


def test_bulk_json_returns_entries(
    app: TestClient,
    bioproject_ids: list,
):
    """Bulk endpoint returns entries and notFound in JSON format."""
    resp = app.post(
        "/entries/bioproject/bulk",
        json={"ids": bioproject_ids},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert "entries" in body
    assert "notFound" in body
    assert len(body["entries"]) == len(bioproject_ids)
    assert body["notFound"] == []


def test_bulk_json_not_found_ids(
    app: TestClient,
    bioproject_ids: list,
):
    """Non-existent IDs appear in the notFound array."""
    fake_ids = ["NONEXISTENT_1", "NONEXISTENT_2"]
    all_ids = bioproject_ids[:1] + fake_ids
    resp = app.post(
        "/entries/bioproject/bulk",
        json={"ids": all_ids},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["entries"]) == 1
    assert set(body["notFound"]) == set(fake_ids)


def test_bulk_json_all_not_found(app: TestClient):
    """All IDs missing: entries is empty, notFound has all IDs."""
    fake_ids = ["NONEXISTENT_A", "NONEXISTENT_B"]
    resp = app.post(
        "/entries/bioproject/bulk",
        json={"ids": fake_ids},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["entries"] == []
    assert set(body["notFound"]) == set(fake_ids)


# === NDJSON format ===


def test_bulk_ndjson_returns_lines(
    app: TestClient,
    bioproject_ids: list,
):
    """NDJSON format returns one entry per line."""
    resp = app.post(
        "/entries/bioproject/bulk",
        params={"format": "ndjson"},
        json={"ids": bioproject_ids},
    )

    assert resp.status_code == 200
    assert "application/x-ndjson" in resp.headers["content-type"]

    lines = [
        line for line in resp.text.strip().split("\n")
        if line.strip()
    ]
    assert len(lines) == len(bioproject_ids)

    for line in lines:
        entry = json.loads(line)
        assert "identifier" in entry


def test_bulk_ndjson_skips_not_found(app: TestClient):
    """NDJSON format silently skips non-existent IDs."""
    fake_ids = ["NONEXISTENT_X", "NONEXISTENT_Y"]
    resp = app.post(
        "/entries/bioproject/bulk",
        params={"format": "ndjson"},
        json={"ids": fake_ids},
    )

    assert resp.status_code == 200
    assert resp.text.strip() == ""


# === Empty IDs ===


def test_bulk_json_empty_ids(app: TestClient):
    """Empty IDs list returns empty entries and notFound."""
    resp = app.post(
        "/entries/bioproject/bulk",
        json={"ids": []},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["entries"] == []
    assert body["notFound"] == []


# === Content-Type ===


def test_bulk_json_content_type(
    app: TestClient,
    bioproject_ids: list,
):
    """JSON bulk response has application/json content type."""
    resp = app.post(
        "/entries/bioproject/bulk",
        json={"ids": bioproject_ids[:1]},
    )

    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]
