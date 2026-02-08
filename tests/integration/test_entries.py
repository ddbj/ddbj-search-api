"""Integration tests for GET /entries/ and GET /entries/{type}/."""
from fastapi.testclient import TestClient

from ddbj_search_api.schemas.common import DbType


# === Cross-type search: GET /entries/ ===


def test_entries_cross_type_returns_results(app: TestClient):
    """Cross-type search returns at least one result."""
    resp = app.get("/entries/", params={"perPage": 1})

    assert resp.status_code == 200
    body = resp.json()
    assert body["pagination"]["total"] > 0
    assert len(body["items"]) >= 1


def test_entries_cross_type_response_structure(app: TestClient):
    """Response has pagination and items with required fields."""
    resp = app.get("/entries/", params={"perPage": 1})
    body = resp.json()

    pagination = body["pagination"]
    assert "page" in pagination
    assert "perPage" in pagination
    assert "total" in pagination

    item = body["items"][0]
    assert "identifier" in item
    assert "type" in item


def test_entries_cross_type_trailing_slash(app: TestClient):
    """Both /entries/ and /entries return the same structure."""
    resp_slash = app.get("/entries/", params={"perPage": 1})
    resp_bare = app.get("/entries", params={"perPage": 1})

    assert resp_slash.status_code == 200
    assert resp_bare.status_code == 200
    assert (
        resp_slash.json()["pagination"]["total"]
        == resp_bare.json()["pagination"]["total"]
    )


def test_entries_cross_type_pagination(app: TestClient):
    """Page and perPage parameters control results."""
    resp_p1 = app.get("/entries/", params={"page": 1, "perPage": 2})
    resp_p2 = app.get("/entries/", params={"page": 2, "perPage": 2})

    assert resp_p1.status_code == 200
    assert resp_p2.status_code == 200

    items_p1 = resp_p1.json()["items"]
    items_p2 = resp_p2.json()["items"]

    assert len(items_p1) <= 2
    assert len(items_p2) <= 2

    if len(items_p1) > 0 and len(items_p2) > 0:
        ids_p1 = {item["identifier"] for item in items_p1}
        ids_p2 = {item["identifier"] for item in items_p2}
        assert ids_p1 != ids_p2


def test_entries_cross_type_keyword_search(app: TestClient):
    """Keyword search narrows results (total should be <= unfiltered)."""
    resp_all = app.get("/entries/", params={"perPage": 1})
    total_all = resp_all.json()["pagination"]["total"]

    resp_kw = app.get(
        "/entries/",
        params={"keywords": "human", "perPage": 1},
    )

    assert resp_kw.status_code == 200
    total_kw = resp_kw.json()["pagination"]["total"]
    assert total_kw <= total_all


def test_entries_cross_type_sort_date_published(app: TestClient):
    """Sort by datePublished:desc returns entries in descending order."""
    resp = app.get(
        "/entries/",
        params={"sort": "datePublished:desc", "perPage": 5},
    )

    assert resp.status_code == 200
    items = resp.json()["items"]
    dates = [
        item["datePublished"]
        for item in items
        if item.get("datePublished") is not None
    ]

    for i in range(len(dates) - 1):
        assert dates[i] >= dates[i + 1]


def test_entries_cross_type_deep_paging_limit(app: TestClient):
    """page * perPage > 10000 returns 400."""
    resp = app.get("/entries/", params={"page": 101, "perPage": 100})

    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == 400


def test_entries_cross_type_with_facets(app: TestClient):
    """includeFacets=true returns facets in response."""
    resp = app.get(
        "/entries/",
        params={"perPage": 1, "includeFacets": "true"},
    )

    assert resp.status_code == 200
    body = resp.json()
    facets = body["facets"]
    assert facets is not None
    assert "organism" in facets
    assert "status" in facets
    assert "accessibility" in facets
    assert "type" in facets


def test_entries_cross_type_db_xrefs_count(app: TestClient):
    """With dbXrefsLimit=0, items include dbXrefsCount with empty dbXrefs."""
    resp = app.get("/entries/", params={"perPage": 10, "dbXrefsLimit": 0})

    assert resp.status_code == 200
    items = resp.json()["items"]
    items_with_xrefs = [
        item for item in items
        if item.get("dbXrefsCount") is not None
    ]

    for item in items_with_xrefs:
        assert isinstance(item["dbXrefsCount"], dict)
        assert item["dbXrefs"] == []


# === Type-specific search: GET /entries/{type}/ ===


def test_entries_type_specific_returns_results(app: TestClient):
    """Each DB type endpoint returns data (if index has entries)."""
    for db_type in DbType:
        resp = app.get(
            f"/entries/{db_type.value}/",
            params={"perPage": 1},
        )

        assert resp.status_code == 200, f"Failed for {db_type.value}"
        body = resp.json()
        assert "pagination" in body
        assert "items" in body


def test_entries_type_specific_items_match_type(app: TestClient):
    """Items returned from type-specific search have the correct type."""
    resp = app.get("/entries/bioproject/", params={"perPage": 5})

    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert item["type"] == "bioproject"


def test_entries_type_specific_trailing_slash(app: TestClient):
    """Both /entries/bioproject/ and /entries/bioproject work."""
    resp_slash = app.get("/entries/bioproject/", params={"perPage": 1})
    resp_bare = app.get("/entries/bioproject", params={"perPage": 1})

    assert resp_slash.status_code == 200
    assert resp_bare.status_code == 200
    assert (
        resp_slash.json()["pagination"]["total"]
        == resp_bare.json()["pagination"]["total"]
    )


def test_entries_type_specific_pagination(app: TestClient):
    """Pagination works for type-specific search."""
    resp = app.get(
        "/entries/bioproject/",
        params={"page": 1, "perPage": 2},
    )

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) <= 2


# === Bug fix verification: dbXrefsLimit default (100) ===


def test_entries_db_xrefs_limit_default(app: TestClient):
    """Default dbXrefsLimit=100 works correctly after bug fix.

    Items should have dbXrefs as a list (not a dict).
    """
    resp = app.get("/entries/", params={"perPage": 5})

    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert isinstance(item["dbXrefs"], list)
        for xref in item["dbXrefs"]:
            assert isinstance(xref, dict)


# === Validation: perPage, page boundaries ===


def test_entries_per_page_zero_returns_422(app: TestClient):
    """perPage=0 is out of range (1-100), returns 422."""
    resp = app.get("/entries/", params={"perPage": 0})

    assert resp.status_code == 422


def test_entries_per_page_101_returns_422(app: TestClient):
    """perPage=101 is out of range (1-100), returns 422."""
    resp = app.get("/entries/", params={"perPage": 101})

    assert resp.status_code == 422


def test_entries_page_zero_returns_422(app: TestClient):
    """page=0 is out of range (>=1), returns 422."""
    resp = app.get("/entries/", params={"page": 0})

    assert resp.status_code == 422


# === keywordOperator ===


def test_entries_keyword_operator_or(app: TestClient):
    """keywordOperator=OR returns results matching any keyword."""
    resp = app.get(
        "/entries/",
        params={
            "keywords": "human,mouse",
            "keywordOperator": "OR",
            "perPage": 1,
        },
    )

    assert resp.status_code == 200
    assert resp.json()["pagination"]["total"] > 0


# === datePublishedFrom / datePublishedTo ===


def test_entries_date_published_filter(app: TestClient):
    """Date filter narrows results."""
    resp_all = app.get("/entries/", params={"perPage": 1})
    total_all = resp_all.json()["pagination"]["total"]

    resp_date = app.get(
        "/entries/",
        params={
            "datePublishedFrom": "2020-01-01",
            "datePublishedTo": "2020-12-31",
            "perPage": 1,
        },
    )

    assert resp_date.status_code == 200
    total_date = resp_date.json()["pagination"]["total"]
    assert total_date <= total_all


# === fields parameter ===


def test_entries_fields_limits_response(app: TestClient):
    """fields parameter limits which fields are returned."""
    resp = app.get(
        "/entries/",
        params={"fields": "identifier,type", "perPage": 1},
    )

    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert "identifier" in item
    assert "type" in item


# === includeProperties=false ===


def test_entries_include_properties_false(app: TestClient):
    """includeProperties=false excludes 'properties' field from items."""
    resp_with = app.get(
        "/entries/bioproject/",
        params={"includeProperties": "true", "perPage": 5},
    )
    resp_without = app.get(
        "/entries/bioproject/",
        params={"includeProperties": "false", "perPage": 5},
    )

    assert resp_with.status_code == 200
    assert resp_without.status_code == 200
    assert (
        resp_with.json()["pagination"]["total"]
        == resp_without.json()["pagination"]["total"]
    )

    # Verify properties field is actually excluded
    for item in resp_without.json()["items"]:
        assert "properties" not in item, (
            f"properties should be excluded for {item.get('identifier')}"
        )


# === includeFacets=false (explicit) ===


def test_entries_include_facets_false(app: TestClient):
    """includeFacets=false (default) returns no facets."""
    resp = app.get(
        "/entries/",
        params={"includeFacets": "false", "perPage": 1},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body.get("facets") is None


# === types parameter (cross-type filtering) ===


def test_entries_types_filter(app: TestClient):
    """types parameter filters to specified types only."""
    resp = app.get(
        "/entries/",
        params={"types": "bioproject,biosample", "perPage": 10},
    )

    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert item["type"] in ("bioproject", "biosample")


# === Deep paging boundary value ===


def test_entries_deep_paging_boundary_ok(app: TestClient):
    """page=100, perPage=100 (exactly 10000) returns 200."""
    resp = app.get(
        "/entries/",
        params={"page": 100, "perPage": 100},
    )

    assert resp.status_code == 200


# === sort: dateModified ===


def test_entries_sort_date_modified_asc(app: TestClient):
    """sort=dateModified:asc returns entries in ascending order."""
    resp = app.get(
        "/entries/",
        params={"sort": "dateModified:asc", "perPage": 5},
    )

    assert resp.status_code == 200
    items = resp.json()["items"]
    dates = [
        item["dateModified"]
        for item in items
        if item.get("dateModified") is not None
    ]

    for i in range(len(dates) - 1):
        assert dates[i] <= dates[i + 1]


# === Invalid sort ===


def test_entries_invalid_sort_returns_422(app: TestClient):
    """Invalid sort field returns 422."""
    resp = app.get(
        "/entries/",
        params={"sort": "invalidField:asc"},
    )

    assert resp.status_code == 422
