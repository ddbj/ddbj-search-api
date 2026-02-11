"""Integration tests for GET /facets and GET /facets/{type}."""

from __future__ import annotations

from fastapi.testclient import TestClient

# === Cross-type facets: GET /facets ===


def test_facets_cross_type_returns_all_facets(app: TestClient) -> None:
    """Cross-type facets include organism, status, accessibility, type."""
    resp = app.get("/facets")

    assert resp.status_code == 200
    facets = resp.json()["facets"]
    assert "organism" in facets
    assert "status" in facets
    assert "accessibility" in facets
    assert "type" in facets


def test_facets_cross_type_structure(app: TestClient) -> None:
    """Each facet is a list of {value, count} buckets."""
    resp = app.get("/facets")
    facets = resp.json()["facets"]

    for key in ("organism", "status", "accessibility", "type"):
        buckets = facets[key]
        assert isinstance(buckets, list)
        if len(buckets) > 0:
            bucket = buckets[0]
            assert "value" in bucket
            assert "count" in bucket
            assert isinstance(bucket["count"], int)


def test_facets_cross_type_keyword_narrows_counts(app: TestClient) -> None:
    """Keyword filter reduces facet counts."""
    resp_all = app.get("/facets")
    resp_kw = app.get("/facets", params={"keywords": "human"})

    assert resp_all.status_code == 200
    assert resp_kw.status_code == 200

    type_all = resp_all.json()["facets"]["type"]
    type_kw = resp_kw.json()["facets"]["type"]

    total_all = sum(b["count"] for b in type_all)
    total_kw = sum(b["count"] for b in type_kw)
    assert total_kw <= total_all


# === Type-specific facets: GET /facets/{type} ===


def test_facets_type_specific_no_type_facet(app: TestClient) -> None:
    """Type-specific facets do NOT include the type facet."""
    resp = app.get("/facets/bioproject")

    assert resp.status_code == 200
    facets = resp.json()["facets"]
    assert facets.get("type") is None
    assert "organism" in facets
    assert "status" in facets
    assert "accessibility" in facets


def test_facets_bioproject_has_object_type(app: TestClient) -> None:
    """BioProject facets include objectType facet."""
    resp = app.get("/facets/bioproject")

    assert resp.status_code == 200
    facets = resp.json()["facets"]
    assert "objectType" in facets
    assert isinstance(facets["objectType"], list)


def test_facets_non_bioproject_no_object_type(app: TestClient) -> None:
    """Non-bioproject type facets do NOT include objectType."""
    resp = app.get("/facets/biosample")

    assert resp.status_code == 200
    facets = resp.json()["facets"]
    assert facets.get("objectType") is None


# === datePublishedFrom / datePublishedTo ===


def test_facets_date_published_filter(app: TestClient) -> None:
    """Date filter narrows facet counts."""
    resp_all = app.get("/facets")
    resp_date = app.get(
        "/facets",
        params={
            "datePublishedFrom": "2020-01-01",
            "datePublishedTo": "2020-12-31",
        },
    )

    assert resp_all.status_code == 200
    assert resp_date.status_code == 200

    type_all = resp_all.json()["facets"]["type"]
    type_date = resp_date.json()["facets"]["type"]

    total_all = sum(b["count"] for b in type_all)
    total_date = sum(b["count"] for b in type_date)
    assert total_date <= total_all


# === types parameter ===


def test_facets_types_filter(app: TestClient) -> None:
    """types parameter filters facet counts to specified types."""
    resp = app.get(
        "/facets",
        params={"types": "bioproject"},
    )

    assert resp.status_code == 200
    facets = resp.json()["facets"]
    type_buckets = facets["type"]
    type_values = {b["value"] for b in type_buckets}
    assert type_values == {"bioproject"}


# === organism filter ===


def test_facets_organism_filter(app: TestClient) -> None:
    """organism parameter narrows facet counts."""
    resp_all = app.get("/facets")
    resp_org = app.get("/facets", params={"organism": "9606"})

    assert resp_all.status_code == 200
    assert resp_org.status_code == 200

    type_all = resp_all.json()["facets"]["type"]
    type_org = resp_org.json()["facets"]["type"]

    total_all = sum(b["count"] for b in type_all)
    total_org = sum(b["count"] for b in type_org)
    assert total_org <= total_all
