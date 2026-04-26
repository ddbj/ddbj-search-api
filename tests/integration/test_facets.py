"""Integration tests for IT-FACETS-* scenarios.

GET /facets, GET /facets/{type}, and the includeFacets toggle on
/entries/. See ``tests/integration-scenarios.md § IT-FACETS-*`` for the
SSOT.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

# Defaults documented in api-spec.md § ファセット集計対象の選択.
_CROSS_TYPE_DEFAULTS = {"organism", "accessibility", "type"}

# Strings unlikely to collide with any real facet name (used for 422/400 probes).
_UNKNOWN_FACET = "__definitely_not_a_facet__"


class TestCrossTypeFacets:
    """IT-FACETS-01: GET /facets returns cross-type aggregations."""

    def test_returns_200(self, app: TestClient) -> None:
        """IT-FACETS-01: endpoint reachable."""
        resp = app.get("/facets")
        assert resp.status_code == 200

    def test_response_shape(self, app: TestClient) -> None:
        """IT-FACETS-01: response has a facets dict."""
        body = app.get("/facets").json()
        assert "facets" in body
        assert isinstance(body["facets"], dict)

    def test_default_aggregations_present(self, app: TestClient) -> None:
        """IT-FACETS-01: cross-type defaults populated as lists (not None)."""
        facets = app.get("/facets").json()["facets"]
        for name in _CROSS_TYPE_DEFAULTS:
            assert facets.get(name) is not None, f"missing default facet: {name}"
            assert isinstance(facets[name], list)


class TestTypeSpecificFacets:
    """IT-FACETS-02: GET /facets/{type} surfaces type-specific aggregations."""

    def test_bioproject_returns_200(self, app: TestClient) -> None:
        """IT-FACETS-02: /facets/bioproject is reachable."""
        resp = app.get("/facets/bioproject")
        assert resp.status_code == 200

    def test_bioproject_object_type_via_facets_param(self, app: TestClient) -> None:
        """IT-FACETS-02: ``objectType`` is bioproject-specific and opt-in."""
        body = app.get("/facets/bioproject", params={"facets": "objectType"}).json()
        bucket = body["facets"].get("objectType")
        assert bucket is not None
        assert isinstance(bucket, list)


class TestCrossTypeWithTypeSpecificFacet:
    """IT-FACETS-03: cross-type endpoint accepts allowlisted type-specific names.

    Per api-spec.md § ファセット集計対象の選択: cross-type ``/facets``
    accepts any allowlisted facet name. Indices that don't carry the field
    simply yield empty buckets, rather than 422.
    """

    def test_object_type_accepted_in_cross_type(self, app: TestClient) -> None:
        """IT-FACETS-03: ``objectType`` is allowed on cross-type ``/facets``."""
        resp = app.get("/facets", params={"facets": "objectType"})
        assert resp.status_code == 200
        bucket = resp.json()["facets"].get("objectType")
        assert bucket is not None
        assert isinstance(bucket, list)


class TestEntriesIncludeFacets:
    """IT-FACETS-04: /entries/?includeFacets=true bundles items and facets."""

    def test_with_include_facets(self, app: TestClient) -> None:
        """IT-FACETS-04: includeFacets=true populates the facets dict."""
        resp = app.get("/entries/", params={"perPage": 5, "includeFacets": "true"})
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        facets = body.get("facets")
        assert facets is not None
        assert isinstance(facets, dict)
        # At least one cross-type default appears as a list.
        assert any(facets.get(name) is not None for name in _CROSS_TYPE_DEFAULTS)


class TestFacetsStatusFilter:
    """IT-FACETS-05: facet aggregation runs over status:public only.

    We cannot probe individual hidden statuses without staging-data
    coverage of withdrawn/private — see conftest.py constants. The
    invariant we *can* assert is structural: bucket counts are
    non-negative integers, and aggregations succeed (not 5xx).
    """

    def test_buckets_are_non_negative_integers(self, app: TestClient) -> None:
        """IT-FACETS-05: every bucket count is a non-negative int."""
        body = app.get("/facets").json()
        for name in _CROSS_TYPE_DEFAULTS:
            for bucket in body["facets"].get(name) or []:
                assert isinstance(bucket["count"], int)
                assert bucket["count"] >= 0


class TestOpenAPIFacetsSchema:
    """IT-FACETS-06: the published OpenAPI Facets schema dropped ``status``."""

    def test_facets_schema_has_no_status(self, app: TestClient) -> None:
        """IT-FACETS-06: ``status`` is no longer a Facets property (commit 40196f7)."""
        spec = app.get("/openapi.json").json()
        schemas = spec.get("components", {}).get("schemas", {})
        facets_schema = schemas.get("Facets")
        assert facets_schema is not None, "Facets schema missing from OpenAPI"
        assert "status" not in facets_schema.get("properties", {})

    def test_facets_schema_lists_organism_and_accessibility(self, app: TestClient) -> None:
        """IT-FACETS-06: defaults are still part of the schema."""
        spec = app.get("/openapi.json").json()
        schemas = spec.get("components", {}).get("schemas", {})
        facets_schema = schemas.get("Facets")
        assert facets_schema is not None
        properties = facets_schema.get("properties", {})
        assert "organism" in properties
        assert "accessibility" in properties


class TestFacetsAllowlistRejection:
    """IT-FACETS-07: unknown facet names are rejected (400 or 422)."""

    def test_unknown_facet_on_entries(self, app: TestClient) -> None:
        """IT-FACETS-07: /entries/ rejects an unknown facet name."""
        resp = app.get("/entries/", params={"facets": _UNKNOWN_FACET})
        # Pydantic-level: 422; query-time guard: 400. Either is acceptable.
        assert resp.status_code in {400, 422}

    def test_unknown_facet_on_facets_endpoint(self, app: TestClient) -> None:
        """IT-FACETS-07: /facets rejects an unknown facet name."""
        resp = app.get("/facets", params={"facets": _UNKNOWN_FACET})
        assert resp.status_code in {400, 422}
