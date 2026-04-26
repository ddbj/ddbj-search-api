"""Integration tests for IT-SEARCH-* scenarios.

GET /entries/ (cross-type) and GET /entries/{type}/ (per-type) — pagination,
sort, fields, types, keywords, type-specific filters, nested filters, and
text-match filters. See ``tests/integration-scenarios.md § IT-SEARCH-*``.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

# Every per-type endpoint documented in api-spec.md / db-portal-api-spec.md.
_ALL_TYPES = (
    "bioproject",
    "biosample",
    "sra-submission",
    "sra-study",
    "sra-experiment",
    "sra-run",
    "sra-sample",
    "sra-analysis",
    "jga-study",
    "jga-dataset",
    "jga-dac",
    "jga-policy",
    "gea",
    "metabobank",
)


class TestCrossTypeSearchSuccess:
    """IT-SEARCH-01: GET /entries/ returns paginated results."""

    def test_returns_200_with_required_keys(self, app: TestClient) -> None:
        """IT-SEARCH-01: total + items present, perPage capped."""
        resp = app.get("/entries/", params={"perPage": 5})
        assert resp.status_code == 200
        body = resp.json()
        assert "total" in body
        assert isinstance(body["total"], int)
        assert "items" in body
        assert isinstance(body["items"], list)
        assert len(body["items"]) <= 5


class TestPerTypeSearchSuccess:
    """IT-SEARCH-02: per-type endpoints succeed for all 14 documented types."""

    def test_each_type_returns_200(self, app: TestClient) -> None:
        """IT-SEARCH-02: every documented type endpoint is reachable."""
        for type_ in _ALL_TYPES:
            resp = app.get(f"/entries/{type_}/", params={"perPage": 1})
            assert resp.status_code == 200, f"type={type_} failed with {resp.status_code}"


class TestPagination:
    """IT-SEARCH-03 / 04: page * perPage limits."""

    def test_within_deep_paging_limit_succeeds(self, app: TestClient) -> None:
        """IT-SEARCH-03: page * perPage == 10000 (boundary) succeeds."""
        resp = app.get("/entries/", params={"page": 100, "perPage": 100})
        assert resp.status_code == 200

    def test_exceeding_deep_paging_limit_returns_400(self, app: TestClient) -> None:
        """IT-SEARCH-04: page * perPage > 10000 → 400 ProblemDetails."""
        resp = app.get("/entries/", params={"page": 101, "perPage": 100})
        assert resp.status_code == 400
        body = resp.json()
        assert body["status"] == 400


class TestCursorPagination:
    """IT-SEARCH-05/06/07: cursor pagination basics, tampering, expiry."""

    def test_first_page_includes_pagination_block(self, app: TestClient) -> None:
        """IT-SEARCH-05: response carries pagination metadata (nextCursor / hasNext)."""
        resp = app.get("/entries/", params={"perPage": 5})
        assert resp.status_code == 200
        body = resp.json()
        # Pagination is exposed either at the top level or under a "pagination" key.
        has_pagination = "nextCursor" in body or "pagination" in body
        assert has_pagination

    def test_invalid_cursor_token_returns_400(self, app: TestClient) -> None:
        """IT-SEARCH-06: a tampered cursor token yields 400 (not 5xx)."""
        resp = app.get("/entries/", params={"cursor": "not-a-valid-token"})
        assert resp.status_code == 400

    def test_cursor_with_page_returns_400(self, app: TestClient) -> None:
        """IT-SEARCH-06: cursor + page is mutually exclusive → 400."""
        resp = app.get("/entries/", params={"cursor": "anything", "page": 2})
        assert resp.status_code == 400


class TestSortParameter:
    """IT-SEARCH-08: sort parsing (valid + invalid direction / field)."""

    def test_valid_sort_succeeds(self, app: TestClient) -> None:
        """IT-SEARCH-08: documented sort form ``field:direction`` works."""
        resp = app.get(
            "/entries/",
            params={"sort": "datePublished:desc", "perPage": 5},
        )
        assert resp.status_code == 200

    def test_invalid_direction_returns_422(self, app: TestClient) -> None:
        """IT-SEARCH-08: unknown direction → 422."""
        resp = app.get("/entries/", params={"sort": "datePublished:foo"})
        assert resp.status_code == 422


class TestFieldsFilter:
    """IT-SEARCH-09: fields parameter limits returned keys."""

    def test_fields_filter_includes_requested(self, app: TestClient) -> None:
        """IT-SEARCH-09: requested fields are present in items."""
        resp = app.get(
            "/entries/",
            params={"fields": "identifier,type", "perPage": 3},
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        if not items:
            return
        for item in items:
            assert "identifier" in item
            assert "type" in item


class TestTypesFilter:
    """IT-SEARCH-10: types comma-separated narrows the search."""

    def test_types_filter_returns_only_specified(self, app: TestClient) -> None:
        """IT-SEARCH-10: types=bioproject,biosample restricts the result set."""
        resp = app.get(
            "/entries/",
            params={"types": "bioproject,biosample", "perPage": 20},
        )
        assert resp.status_code == 200
        for item in resp.json()["items"]:
            assert item["type"] in {"bioproject", "biosample"}

    def test_single_type_total_le_combined(self, app: TestClient) -> None:
        """IT-SEARCH-10: total(bioproject) <= total(bioproject,biosample)."""
        only_bp = app.get("/entries/", params={"types": "bioproject"}).json()["total"]
        combined = app.get(
            "/entries/",
            params={"types": "bioproject,biosample"},
        ).json()["total"]
        assert only_bp <= combined


class TestKeywordOperators:
    """IT-SEARCH-11: AND / OR / NOT and quoted-phrase semantics."""

    def test_and_total_le_single(self, app: TestClient) -> None:
        """IT-SEARCH-11: total(A AND B) <= total(A)."""
        a = app.get("/entries/", params={"keywords": "cancer"}).json()["total"]
        a_and_b = app.get(
            "/entries/", params={"keywords": "cancer AND brain"}
        ).json()["total"]
        assert a_and_b <= a

    def test_or_total_ge_single(self, app: TestClient) -> None:
        """IT-SEARCH-11: total(A OR B) >= total(A)."""
        a = app.get("/entries/", params={"keywords": "cancer"}).json()["total"]
        a_or_b = app.get(
            "/entries/", params={"keywords": "cancer OR brain"}
        ).json()["total"]
        assert a_or_b >= a

    def test_not_total_le_single(self, app: TestClient) -> None:
        """IT-SEARCH-11: total(A NOT B) <= total(A)."""
        a = app.get("/entries/", params={"keywords": "cancer"}).json()["total"]
        a_not_b = app.get(
            "/entries/", params={"keywords": "cancer NOT brain"}
        ).json()["total"]
        assert a_not_b <= a


class TestArrayFieldContractInSearch:
    """IT-SEARCH-12: required list fields surface as keys (possibly empty)."""

    def test_default_items_carry_db_xrefs_key(self, app: TestClient) -> None:
        """IT-SEARCH-12: dbXrefs key present on default response items."""
        items = app.get("/entries/", params={"perPage": 3}).json().get("items", [])
        if not items:
            return
        for item in items:
            assert "dbXrefs" in item


class TestTypeSpecificObjectTypesFilter:
    """IT-SEARCH-13: BioProject objectTypes filter."""

    def test_bioproject_object_types_filter_returns_200(self, app: TestClient) -> None:
        """IT-SEARCH-13: objectTypes value is accepted on /entries/bioproject/."""
        resp = app.get(
            "/entries/bioproject/",
            params={"objectTypes": "Umbrella", "perPage": 5},
        )
        assert resp.status_code == 200

    def test_object_types_on_cross_type_returns_422(self, app: TestClient) -> None:
        """IT-SEARCH-13: objectTypes is invalid on cross-type /entries/."""
        resp = app.get("/entries/", params={"objectTypes": "Umbrella"})
        assert resp.status_code == 422


class TestFacetsParamAllowlist:
    """IT-SEARCH-14: facets parameter allowlist control."""

    def test_facets_filter_explicit_pair(self, app: TestClient) -> None:
        """IT-SEARCH-14: explicit facets list populates the requested aggregations."""
        resp = app.get(
            "/entries/",
            params={
                "facets": "organization,publication",
                "includeFacets": "true",
                "perPage": 3,
            },
        )
        assert resp.status_code == 200
        facets = resp.json().get("facets") or {}
        for name in ("organization", "publication"):
            assert name in facets


class TestNestedFieldFilters:
    """IT-SEARCH-15: organization / publication / grant available cross-type."""

    def test_organization_on_cross_type_returns_200(self, app: TestClient) -> None:
        """IT-SEARCH-15: organization filter works on cross-type."""
        resp = app.get(
            "/entries/", params={"organization": "DDBJ", "perPage": 1}
        )
        assert resp.status_code == 200

    def test_organization_on_per_type_returns_200(self, app: TestClient) -> None:
        """IT-SEARCH-15: organization filter works on per-type."""
        resp = app.get(
            "/entries/bioproject/",
            params={"organization": "DDBJ", "perPage": 1},
        )
        assert resp.status_code == 200


class TestNestedFieldGroupRestriction:
    """IT-SEARCH-16: type-group-restricted nested filters (externalLinkLabel etc.)."""

    def test_external_link_label_on_cross_type_returns_422(self, app: TestClient) -> None:
        """IT-SEARCH-16: externalLinkLabel rejected on /entries/."""
        resp = app.get("/entries/", params={"externalLinkLabel": "github"})
        assert resp.status_code == 422

    def test_external_link_label_on_bioproject_returns_200(self, app: TestClient) -> None:
        """IT-SEARCH-16: externalLinkLabel allowed on bioproject."""
        resp = app.get(
            "/entries/bioproject/",
            params={"externalLinkLabel": "github", "perPage": 1},
        )
        assert resp.status_code == 200

    def test_external_link_label_on_biosample_returns_422(self, app: TestClient) -> None:
        """IT-SEARCH-16: externalLinkLabel rejected on biosample (out of group)."""
        resp = app.get(
            "/entries/biosample/",
            params={"externalLinkLabel": "github", "perPage": 1},
        )
        assert resp.status_code == 422


class TestTextMatchFields:
    """IT-SEARCH-17: text-match fields work on the documented type group."""

    def test_host_on_biosample_returns_200(self, app: TestClient) -> None:
        """IT-SEARCH-17: host filter allowed on biosample."""
        resp = app.get(
            "/entries/biosample/", params={"host": "Homo sapiens", "perPage": 1}
        )
        assert resp.status_code == 200


class TestTextMatchCrossTypeRejected:
    """IT-SEARCH-18: text-match fields are not allowed on cross-type."""

    def test_host_on_cross_type_returns_422(self, app: TestClient) -> None:
        """IT-SEARCH-18: host rejected on /entries/."""
        resp = app.get("/entries/", params={"host": "Homo sapiens"})
        assert resp.status_code == 422
