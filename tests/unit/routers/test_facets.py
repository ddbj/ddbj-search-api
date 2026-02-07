"""Tests for Facets API: GET /facets and GET /facets/{type}.

Tests cover routing, response structure, ES query construction,
search filter validation, BioProject-specific parameters, and errors.
"""
from typing import Any, Dict
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from tests.unit.conftest import (make_es_search_response,
                                 make_facets_aggregations)
from tests.unit.strategies import db_type_values


# === Helpers ===


def _facets_aggs_with_data() -> Dict[str, Any]:
    """Build aggregation data with sample buckets."""

    return make_facets_aggregations(
        organism=[
            {"key": "Homo sapiens", "doc_count": 100},
            {"key": "Mus musculus", "doc_count": 50},
        ],
        status=[
            {"key": "public", "doc_count": 120},
        ],
        accessibility=[
            {"key": "unrestricted", "doc_count": 150},
        ],
        type_buckets=[
            {"key": "bioproject", "doc_count": 80},
            {"key": "biosample", "doc_count": 70},
        ],
    )


# === Routing: GET /facets ===


class TestFacetsRouting:
    """GET /facets: cross-type facet route."""

    def test_route_exists(self, app_with_facets: TestClient) -> None:
        resp = app_with_facets.get("/facets")
        assert resp.status_code == 200


# === Routing: GET /facets/{type} ===


class TestFacetsTypeRouting:
    """GET /facets/{type}: all 12 types are routed."""

    @pytest.mark.parametrize("db_type", db_type_values)
    def test_route_exists(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
        db_type: str,
    ) -> None:
        mock_es_search_facets.return_value = make_es_search_response(
            aggregations=make_facets_aggregations(),
        )
        resp = app_with_facets.get(f"/facets/{db_type}")
        assert resp.status_code == 200

    def test_invalid_type_returns_error(
        self,
        app_with_facets: TestClient,
    ) -> None:
        resp = app_with_facets.get("/facets/invalid-type")
        assert resp.status_code in (404, 422)


# === Response structure ===


class TestFacetsResponse:
    """GET /facets response structure."""

    def test_returns_facets_structure(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        mock_es_search_facets.return_value = make_es_search_response(
            aggregations=_facets_aggs_with_data(),
        )
        resp = app_with_facets.get("/facets")
        assert resp.status_code == 200
        data = resp.json()
        assert "facets" in data
        facets = data["facets"]
        assert "organism" in facets
        assert "status" in facets
        assert "accessibility" in facets

    def test_cross_type_includes_type_facet(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        mock_es_search_facets.return_value = make_es_search_response(
            aggregations=_facets_aggs_with_data(),
        )
        resp = app_with_facets.get("/facets")
        facets = resp.json()["facets"]
        assert "type" in facets
        assert isinstance(facets["type"], list)

    def test_content_type_is_json(
        self,
        app_with_facets: TestClient,
    ) -> None:
        resp = app_with_facets.get("/facets")
        assert "application/json" in resp.headers["content-type"]

    def test_no_pagination_or_items(
        self,
        app_with_facets: TestClient,
    ) -> None:
        """Facets response should not contain pagination or items."""
        resp = app_with_facets.get("/facets")
        data = resp.json()
        assert "pagination" not in data
        assert "items" not in data

    def test_facet_bucket_structure(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        mock_es_search_facets.return_value = make_es_search_response(
            aggregations=_facets_aggs_with_data(),
        )
        resp = app_with_facets.get("/facets")
        buckets = resp.json()["facets"]["organism"]
        assert len(buckets) == 2
        assert buckets[0]["value"] == "Homo sapiens"
        assert buckets[0]["count"] == 100


# === ES query construction ===


class TestFacetsEsQuery:
    """Verify ES query sent by facets endpoint."""

    def test_size_is_zero(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        app_with_facets.get("/facets")
        call_args = mock_es_search_facets.call_args
        body = call_args[0][2]
        assert body["size"] == 0

    def test_uses_entries_index(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        app_with_facets.get("/facets")
        call_args = mock_es_search_facets.call_args
        index = call_args[0][1]
        assert index == "entries"

    def test_aggs_include_type_for_cross_type(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        app_with_facets.get("/facets")
        body = mock_es_search_facets.call_args[0][2]
        assert "type" in body["aggs"]

    def test_keywords_reflected_in_query(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        app_with_facets.get("/facets?keywords=cancer")
        body = mock_es_search_facets.call_args[0][2]
        query = body["query"]
        assert "bool" in query

    def test_types_filter_reflected(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        app_with_facets.get("/facets?types=bioproject,biosample")
        body = mock_es_search_facets.call_args[0][2]
        query = body["query"]
        assert "bool" in query
        filters = query["bool"]["filter"]
        type_filter = [f for f in filters if "terms" in f and "type" in f["terms"]]
        assert len(type_filter) == 1


# === Search filter validation ===


class TestFacetsSearchFilter:
    """Search filter validation on facets endpoints."""

    def test_invalid_keyword_fields_returns_422(
        self,
        app_with_facets: TestClient,
    ) -> None:
        resp = app_with_facets.get("/facets?keywordFields=invalid_field")
        assert resp.status_code == 422

    def test_valid_keyword_fields_accepted(
        self,
        app_with_facets: TestClient,
    ) -> None:
        resp = app_with_facets.get("/facets?keywordFields=title,description")
        assert resp.status_code == 200


# === Date parameter validation ===


class TestFacetsDateValidation:
    """Date parameter format validation (YYYY-MM-DD only)."""

    def test_valid_date_accepted(
        self, app_with_facets: TestClient
    ) -> None:
        resp = app_with_facets.get(
            "/facets", params={"datePublishedFrom": "2024-01-15"}
        )
        assert resp.status_code == 200

    def test_slash_date_returns_422(
        self, app_with_facets: TestClient
    ) -> None:
        resp = app_with_facets.get(
            "/facets", params={"datePublishedFrom": "2024/01/15"}
        )
        assert resp.status_code == 422

    def test_no_dash_date_returns_422(
        self, app_with_facets: TestClient
    ) -> None:
        resp = app_with_facets.get(
            "/facets", params={"datePublishedFrom": "20240115"}
        )
        assert resp.status_code == 422

    def test_datetime_returns_422(
        self, app_with_facets: TestClient
    ) -> None:
        resp = app_with_facets.get(
            "/facets",
            params={"datePublishedFrom": "2024-01-15T00:00:00"},
        )
        assert resp.status_code == 422


# === Umbrella parameter validation ===


class TestFacetsUmbrellaValidation:
    """umbrella parameter validation for BioProject facets."""

    def test_umbrella_true_accepted(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        mock_es_search_facets.return_value = make_es_search_response(
            aggregations=make_facets_aggregations(object_type=[]),
        )
        resp = app_with_facets.get(
            "/facets/bioproject", params={"umbrella": "TRUE"}
        )
        assert resp.status_code == 200

    def test_umbrella_false_accepted(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        mock_es_search_facets.return_value = make_es_search_response(
            aggregations=make_facets_aggregations(object_type=[]),
        )
        resp = app_with_facets.get(
            "/facets/bioproject", params={"umbrella": "FALSE"}
        )
        assert resp.status_code == 200

    def test_umbrella_lowercase_accepted(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        mock_es_search_facets.return_value = make_es_search_response(
            aggregations=make_facets_aggregations(object_type=[]),
        )
        resp = app_with_facets.get(
            "/facets/bioproject", params={"umbrella": "true"}
        )
        assert resp.status_code == 200

    def test_umbrella_mixed_case_accepted(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        mock_es_search_facets.return_value = make_es_search_response(
            aggregations=make_facets_aggregations(object_type=[]),
        )
        resp = app_with_facets.get(
            "/facets/bioproject", params={"umbrella": "True"}
        )
        assert resp.status_code == 200

    def test_umbrella_invalid_returns_422(
        self,
        app_with_facets: TestClient,
    ) -> None:
        resp = app_with_facets.get(
            "/facets/bioproject", params={"umbrella": "invalid"}
        )
        assert resp.status_code == 422

    def test_umbrella_empty_returns_422(
        self,
        app_with_facets: TestClient,
    ) -> None:
        resp = app_with_facets.get(
            "/facets/bioproject", params={"umbrella": ""}
        )
        assert resp.status_code == 422


# === Type-specific facets ===


class TestFacetsTypeResponse:
    """GET /facets/{type}: type-specific facets."""

    def test_type_facet_not_included(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        """Type-specific facets should not include the 'type' facet."""
        mock_es_search_facets.return_value = make_es_search_response(
            aggregations=make_facets_aggregations(),
        )
        resp = app_with_facets.get("/facets/biosample")
        facets = resp.json()["facets"]
        assert facets.get("type") is None

    def test_uses_correct_index(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        mock_es_search_facets.return_value = make_es_search_response(
            aggregations=make_facets_aggregations(),
        )
        app_with_facets.get("/facets/biosample")
        index = mock_es_search_facets.call_args[0][1]
        assert index == "biosample"

    def test_bioproject_includes_object_type(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        mock_es_search_facets.return_value = make_es_search_response(
            aggregations=make_facets_aggregations(
                object_type=[
                    {"key": "BioProject", "doc_count": 100},
                    {"key": "UmbrellaBioProject", "doc_count": 10},
                ],
            ),
        )
        resp = app_with_facets.get("/facets/bioproject")
        facets = resp.json()["facets"]
        assert facets.get("objectType") is not None

    def test_non_bioproject_excludes_object_type(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        mock_es_search_facets.return_value = make_es_search_response(
            aggregations=make_facets_aggregations(),
        )
        resp = app_with_facets.get("/facets/biosample")
        facets = resp.json()["facets"]
        assert facets.get("objectType") is None


# === BioProject extra parameters ===


class TestFacetsBioProjectExtra:
    """BioProject-specific filter parameters for facets."""

    def test_umbrella_filter_reflected(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        mock_es_search_facets.return_value = make_es_search_response(
            aggregations=make_facets_aggregations(object_type=[]),
        )
        app_with_facets.get("/facets/bioproject?umbrella=TRUE")
        body = mock_es_search_facets.call_args[0][2]
        query = body["query"]
        assert "bool" in query
        filters = query["bool"]["filter"]
        object_type_filter = [
            f for f in filters
            if "term" in f and "objectType" in f["term"]
        ]
        assert len(object_type_filter) == 1

    def test_organization_filter_reflected(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        mock_es_search_facets.return_value = make_es_search_response(
            aggregations=make_facets_aggregations(object_type=[]),
        )
        app_with_facets.get("/facets/bioproject?organization=DDBJ")
        body = mock_es_search_facets.call_args[0][2]
        query = body["query"]
        assert "bool" in query
        filters = query["bool"]["filter"]
        nested_filters = [f for f in filters if "nested" in f]
        assert len(nested_filters) == 1

    def test_publication_filter_reflected(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        mock_es_search_facets.return_value = make_es_search_response(
            aggregations=make_facets_aggregations(object_type=[]),
        )
        app_with_facets.get("/facets/bioproject?publication=genomics")
        body = mock_es_search_facets.call_args[0][2]
        query = body["query"]
        filters = query["bool"]["filter"]
        nested_filters = [f for f in filters if "nested" in f]
        assert len(nested_filters) == 1

    def test_grant_filter_reflected(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        mock_es_search_facets.return_value = make_es_search_response(
            aggregations=make_facets_aggregations(object_type=[]),
        )
        app_with_facets.get("/facets/bioproject?grant=JSPS")
        body = mock_es_search_facets.call_args[0][2]
        query = body["query"]
        filters = query["bool"]["filter"]
        nested_filters = [f for f in filters if "nested" in f]
        assert len(nested_filters) == 1


# === ES error handling ===


class TestFacetsEsError:
    """ES errors propagate as 500."""

    def test_es_error_returns_500(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        mock_es_search_facets.side_effect = Exception("ES down")
        resp = app_with_facets.get("/facets")
        assert resp.status_code == 500

    def test_type_es_error_returns_500(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        mock_es_search_facets.side_effect = Exception("ES down")
        resp = app_with_facets.get("/facets/bioproject")
        assert resp.status_code == 500
