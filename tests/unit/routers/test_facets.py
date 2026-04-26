"""Tests for Facets API: GET /facets and GET /facets/{type}.

Tests cover routing, response structure, ES query construction,
search filter validation, BioProject-specific parameters, and errors.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from tests.unit.conftest import make_es_search_response, make_facets_aggregations
from tests.unit.strategies import db_type_values

# === Helpers ===


def _facets_aggs_with_data() -> dict[str, Any]:
    """Build aggregation data with sample buckets."""

    return make_facets_aggregations(
        organism=[
            {"key": "Homo sapiens", "doc_count": 100},
            {"key": "Mus musculus", "doc_count": 50},
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
    """GET /facets/{type}: every DbType is routed."""

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

    def test_invalid_type_returns_404(
        self,
        app_with_facets: TestClient,
    ) -> None:
        """Invalid facet type returns 404 (no matching route)."""
        resp = app_with_facets.get("/facets/invalid-type")
        assert resp.status_code == 404


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
        assert "accessibility" in facets
        # status facet は全件 public のため廃止 (docs/api-spec.md § データ可視性)
        assert "status" not in facets

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

    def test_invalid_types_returns_422(
        self,
        app_with_facets: TestClient,
    ) -> None:
        resp = app_with_facets.get("/facets?types=invalid-type")
        assert resp.status_code == 422


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

    def test_valid_date_accepted(self, app_with_facets: TestClient) -> None:
        resp = app_with_facets.get("/facets", params={"datePublishedFrom": "2024-01-15"})
        assert resp.status_code == 200

    def test_slash_date_returns_422(self, app_with_facets: TestClient) -> None:
        resp = app_with_facets.get("/facets", params={"datePublishedFrom": "2024/01/15"})
        assert resp.status_code == 422

    def test_no_dash_date_returns_422(self, app_with_facets: TestClient) -> None:
        resp = app_with_facets.get("/facets", params={"datePublishedFrom": "20240115"})
        assert resp.status_code == 422

    def test_datetime_returns_422(self, app_with_facets: TestClient) -> None:
        resp = app_with_facets.get(
            "/facets",
            params={"datePublishedFrom": "2024-01-15T00:00:00"},
        )
        assert resp.status_code == 422

    def test_from_after_to_accepted(self, app_with_facets: TestClient) -> None:
        """from > to is accepted (ES returns 0 results, not an error)."""
        resp = app_with_facets.get(
            "/facets",
            params={
                "datePublishedFrom": "2025-12-31",
                "datePublishedTo": "2024-01-01",
            },
        )
        assert resp.status_code == 200

    def test_same_from_and_to_accepted(self, app_with_facets: TestClient) -> None:
        resp = app_with_facets.get(
            "/facets",
            params={
                "datePublishedFrom": "2024-06-15",
                "datePublishedTo": "2024-06-15",
            },
        )
        assert resp.status_code == 200

    def test_feb_30_returns_422(self, app_with_facets: TestClient) -> None:
        resp = app_with_facets.get("/facets", params={"datePublishedFrom": "2024-02-30"})
        assert resp.status_code == 422

    def test_month_13_returns_422(self, app_with_facets: TestClient) -> None:
        resp = app_with_facets.get("/facets", params={"datePublishedFrom": "2024-13-01"})
        assert resp.status_code == 422


# === Organism filter ===


class TestFacetsOrganismFilter:
    """Organism filter on facets endpoint."""

    def test_organism_passed_to_es(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        app_with_facets.get("/facets?organism=9606")
        body = mock_es_search_facets.call_args[0][2]
        filters = body["query"]["bool"]["filter"]
        term_filters = [f for f in filters if "term" in f and "organism.identifier" in f["term"]]
        assert len(term_filters) == 1
        assert term_filters[0]["term"]["organism.identifier"] == "9606"


# === keywordOperator ===


class TestFacetsKeywordOperator:
    """keywordOperator validation on facets endpoint."""

    def test_keyword_operator_or(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        app_with_facets.get("/facets?keywords=cancer,tumor&keywordOperator=OR")
        body = mock_es_search_facets.call_args[0][2]
        assert "should" in body["query"]["bool"]

    def test_keyword_operator_invalid_returns_422(
        self,
        app_with_facets: TestClient,
    ) -> None:
        resp = app_with_facets.get("/facets?keywordOperator=INVALID")
        assert resp.status_code == 422


# === Empty/whitespace keywords ===


class TestFacetsEmptyKeywords:
    """Empty and whitespace-only keywords on facets endpoint."""

    def test_empty_keywords_accepted(
        self,
        app_with_facets: TestClient,
    ) -> None:
        resp = app_with_facets.get("/facets?keywords=")
        assert resp.status_code == 200

    def test_whitespace_keywords_accepted(
        self,
        app_with_facets: TestClient,
    ) -> None:
        resp = app_with_facets.get("/facets?keywords=%20%20")
        assert resp.status_code == 200

    def test_comma_only_keywords_accepted(
        self,
        app_with_facets: TestClient,
    ) -> None:
        resp = app_with_facets.get("/facets?keywords=,")
        assert resp.status_code == 200


# === objectTypes parameter validation ===


class TestFacetsObjectTypesValidation:
    """objectTypes parameter validation for BioProject facets."""

    def test_single_bioproject_accepted(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        mock_es_search_facets.return_value = make_es_search_response(
            aggregations=make_facets_aggregations(object_type=[]),
        )
        resp = app_with_facets.get(
            "/facets/bioproject",
            params={"objectTypes": "BioProject"},
        )
        assert resp.status_code == 200

    def test_single_umbrella_accepted(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        mock_es_search_facets.return_value = make_es_search_response(
            aggregations=make_facets_aggregations(object_type=[]),
        )
        resp = app_with_facets.get(
            "/facets/bioproject",
            params={"objectTypes": "UmbrellaBioProject"},
        )
        assert resp.status_code == 200

    def test_both_accepted(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        mock_es_search_facets.return_value = make_es_search_response(
            aggregations=make_facets_aggregations(object_type=[]),
        )
        resp = app_with_facets.get(
            "/facets/bioproject",
            params={"objectTypes": "BioProject,UmbrellaBioProject"},
        )
        assert resp.status_code == 200

    def test_unknown_value_rejected(
        self,
        app_with_facets: TestClient,
    ) -> None:
        resp = app_with_facets.get(
            "/facets/bioproject",
            params={"objectTypes": "Foo"},
        )
        assert resp.status_code == 422

    def test_lowercase_rejected(
        self,
        app_with_facets: TestClient,
    ) -> None:
        resp = app_with_facets.get(
            "/facets/bioproject",
            params={"objectTypes": "bioproject"},
        )
        assert resp.status_code == 422

    def test_empty_rejected(
        self,
        app_with_facets: TestClient,
    ) -> None:
        resp = app_with_facets.get(
            "/facets/bioproject",
            params={"objectTypes": ""},
        )
        assert resp.status_code == 422

    def test_legacy_umbrella_rejected(
        self,
        app_with_facets: TestClient,
    ) -> None:
        """``umbrella`` is no longer accepted; the unknown-query guard
        rejects it with 422 (docs/api-spec.md § エンドポイント固有の
        パラメータ)."""
        resp = app_with_facets.get("/facets/bioproject", params={"umbrella": "TRUE"})
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

    def test_bioproject_default_excludes_object_type(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        """``objectType`` is opt-in via ``facets=objectType``; default
        responses on ``/facets/bioproject`` do not include it
        (docs/api-spec.md § ファセット集計対象の選択)."""
        mock_es_search_facets.return_value = make_es_search_response(
            aggregations=make_facets_aggregations(),
        )
        resp = app_with_facets.get("/facets/bioproject")
        facets = resp.json()["facets"]
        assert facets.get("objectType") is None
        # Aggregation should not be requested either.
        body = mock_es_search_facets.call_args[0][2]
        assert "objectType" not in body.get("aggs", {})

    def test_bioproject_object_type_opt_in(
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
        resp = app_with_facets.get("/facets/bioproject?facets=objectType")
        facets = resp.json()["facets"]
        assert facets.get("objectType") is not None
        assert {b["value"] for b in facets["objectType"]} == {
            "BioProject",
            "UmbrellaBioProject",
        }
        body = mock_es_search_facets.call_args[0][2]
        assert "objectType" in body["aggs"]
        # facets=objectType means common facets are NOT requested.
        assert "organism" not in body["aggs"]
        assert "accessibility" not in body["aggs"]

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

    def test_object_types_single_filter_reflected(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        mock_es_search_facets.return_value = make_es_search_response(
            aggregations=make_facets_aggregations(object_type=[]),
        )
        app_with_facets.get("/facets/bioproject?objectTypes=UmbrellaBioProject")
        body = mock_es_search_facets.call_args[0][2]
        query = body["query"]
        assert "bool" in query
        filters = query["bool"]["filter"]
        object_type_filter = [f for f in filters if "term" in f and "objectType" in f["term"]]
        assert len(object_type_filter) == 1
        assert object_type_filter[0]["term"]["objectType"] == "UmbrellaBioProject"

    def test_object_types_both_filter_reflected(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        mock_es_search_facets.return_value = make_es_search_response(
            aggregations=make_facets_aggregations(object_type=[]),
        )
        app_with_facets.get("/facets/bioproject?objectTypes=UmbrellaBioProject,BioProject")
        body = mock_es_search_facets.call_args[0][2]
        query = body["query"]
        filters = query["bool"]["filter"]
        terms_filter = [f for f in filters if "terms" in f and "objectType" in f["terms"]]
        assert len(terms_filter) == 1
        assert terms_filter[0]["terms"]["objectType"] == [
            "BioProject",
            "UmbrellaBioProject",
        ]

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


# === Status visibility ===


class TestFacetsStatusPublicOnly:
    """/facets 系は常に status:public に絞り込んで集計する
    (docs/api-spec.md § データ可視性)。
    """

    def _extract_status_filter(self, body: dict[str, Any]) -> dict[str, Any]:
        filters: list[dict[str, Any]] = body["query"]["bool"]["filter"]
        for f in filters:
            if "term" in f and "status" in f["term"]:
                return f
        raise AssertionError(f"No status filter found in {filters}")

    def test_cross_facets_uses_public_only(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        app_with_facets.get("/facets")
        body = mock_es_search_facets.call_args[0][2]
        assert self._extract_status_filter(body) == {"term": {"status": "public"}}

    def test_type_facets_uses_public_only(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        app_with_facets.get("/facets/biosample")
        body = mock_es_search_facets.call_args[0][2]
        assert self._extract_status_filter(body) == {"term": {"status": "public"}}

    def test_accession_keyword_still_uses_public_only(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        """keywords が accession 完全一致でも /facets は public のみ集計する
        (suppressed が facet カウントに混ざらない)。
        """
        app_with_facets.get("/facets", params={"keywords": "PRJDB1234"})
        body = mock_es_search_facets.call_args[0][2]
        assert self._extract_status_filter(body) == {"term": {"status": "public"}}

    def test_status_not_in_aggs(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        app_with_facets.get("/facets")
        body = mock_es_search_facets.call_args[0][2]
        assert "status" not in body["aggs"]


# === facet pick semantics ===


class TestFacetsPick:
    """``facets`` query parameter selects which aggregations to compute.

    Default (omitted) returns common facets only; an empty string
    suppresses aggregation entirely; an explicit list opts in.
    """

    def test_default_returns_common_facets_only(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        app_with_facets.get("/facets/bioproject")
        body = mock_es_search_facets.call_args[0][2]
        assert set(body["aggs"]) == {"organism", "accessibility"}

    def test_default_cross_type_includes_type(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        app_with_facets.get("/facets")
        body = mock_es_search_facets.call_args[0][2]
        assert set(body["aggs"]) == {"organism", "accessibility", "type"}

    def test_empty_string_yields_no_aggs_key(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        # facets="" → resolve_requested_facets returns []
        # → build_facet_aggs returns {} → router omits aggs key entirely.
        app_with_facets.get("/facets/bioproject?facets=")
        body = mock_es_search_facets.call_args[0][2]
        assert "aggs" not in body

    def test_explicit_subset_returned(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        mock_es_search_facets.return_value = make_es_search_response(
            aggregations=make_facets_aggregations(
                include_common=False,
                organism=[{"key": "Homo sapiens", "doc_count": 1}],
            ),
        )
        # Note: include_common=False means accessibility is not in aggregations
        # but organism IS in aggregations. The router's build_facet_aggs
        # request reflects user choice, not the mocked response shape.
        app_with_facets.get("/facets/bioproject?facets=organism")
        body = mock_es_search_facets.call_args[0][2]
        assert set(body["aggs"]) == {"organism"}

    def test_typo_returns_422(
        self,
        app_with_facets: TestClient,
    ) -> None:
        resp = app_with_facets.get("/facets/bioproject?facets=organisms")
        assert resp.status_code == 422
        body = resp.json()
        assert body["status"] == 422

    def test_type_mismatch_returns_400(
        self,
        app_with_facets: TestClient,
    ) -> None:
        # libraryStrategy is valid in the allowlist but only applicable
        # to sra-experiment.
        resp = app_with_facets.get("/facets/bioproject?facets=libraryStrategy")
        assert resp.status_code == 400

    def test_cross_type_accepts_any_allowlisted_facet(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        # Cross-type endpoint is loose — type-specific facets are
        # accepted (aggregations against indices that lack the field
        # produce empty buckets at the ES layer).
        resp = app_with_facets.get("/facets?facets=libraryStrategy")
        assert resp.status_code == 200
        body = mock_es_search_facets.call_args[0][2]
        assert "libraryStrategy" in body["aggs"]

    def test_type_specific_endpoint_accepts_its_own_facet(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        resp = app_with_facets.get("/facets/sra-experiment?facets=libraryStrategy")
        assert resp.status_code == 200
        body = mock_es_search_facets.call_args[0][2]
        assert "libraryStrategy" in body["aggs"]

    def test_cross_type_excludes_type_in_explicit_request(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
    ) -> None:
        # Default cross-type adds ``type``; an explicit request without
        # ``type`` must NOT auto-add it (the user picked exactly what
        # they want).
        app_with_facets.get("/facets?facets=organism")
        body = mock_es_search_facets.call_args[0][2]
        assert set(body["aggs"]) == {"organism"}


class TestFacetsCrossTypeRejections:
    """Cross-type endpoint rejects type-specific filter parameters.

    docs/api-spec.md § エンドポイント固有のパラメータ
    """

    @pytest.mark.parametrize(
        "param",
        [
            ("objectTypes", "BioProject"),
            ("externalLinkLabel", "GEO"),
            ("derivedFromId", "SAMD00001"),
            ("libraryStrategy", "WGS"),
            ("librarySource", "GENOMIC"),
            ("librarySelection", "RANDOM"),
            ("platform", "ILLUMINA"),
            ("instrumentModel", "HiSeq"),
            ("libraryLayout", "PAIRED"),
            ("analysisType", "ALIGNMENT"),
            ("experimentType", "RNA-Seq"),
            ("studyType", "GWAS"),
            ("submissionType", "open"),
            ("datasetType", "WGS"),
            ("projectType", "genome"),
            ("host", "Homo sapiens"),
            ("strain", "K12"),
            ("isolate", "patient-1"),
            ("geoLocName", "Japan"),
            ("collectionDate", "2020"),
            ("libraryName", "lib"),
            ("libraryConstructionProtocol", "PCR-free"),
            ("vendor", "Illumina"),
        ],
    )
    def test_type_specific_filter_rejected_on_cross_type(
        self,
        app_with_facets: TestClient,
        param: tuple[str, str],
    ) -> None:
        name, value = param
        resp = app_with_facets.get(f"/facets?{name}={value}")
        assert resp.status_code == 422


class TestFacetsCrossTypeNestedAccepted:
    """organization / publication / grant work on the cross-type endpoint."""

    @pytest.mark.parametrize(
        ("name", "nested_path"),
        [
            ("organization", "organization"),
            ("publication", "publication"),
            ("grant", "grant"),
        ],
    )
    def test_nested_filter_reflected_in_es_query(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
        name: str,
        nested_path: str,
    ) -> None:
        resp = app_with_facets.get(f"/facets?{name}=DDBJ")
        assert resp.status_code == 200
        body = mock_es_search_facets.call_args[0][2]
        nested_clauses = [c for c in body["query"]["bool"]["filter"] if "nested" in c]
        assert any(c["nested"]["path"] == nested_path for c in nested_clauses)


class TestFacetsTypeGroupCommonality:
    """SRA-* / JGA-* endpoints share the same parameter set across the group."""

    @pytest.mark.parametrize(
        "endpoint",
        [
            "sra-submission",
            "sra-study",
            "sra-experiment",
            "sra-run",
            "sra-sample",
            "sra-analysis",
        ],
    )
    def test_sra_group_accepts_library_strategy(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
        endpoint: str,
    ) -> None:
        resp = app_with_facets.get(f"/facets/{endpoint}?libraryStrategy=WGS")
        assert resp.status_code == 200
        body = mock_es_search_facets.call_args[0][2]
        # Term filter on libraryStrategy.keyword is reflected in ES query
        # for every sra-* endpoint, even if the underlying index does not
        # actually have the field.
        filters = body["query"]["bool"]["filter"]
        assert any("libraryStrategy.keyword" in (f.get("term") or {}) for f in filters)

    @pytest.mark.parametrize(
        "endpoint",
        [
            "jga-study",
            "jga-dataset",
            "jga-policy",
            "jga-dac",
        ],
    )
    def test_jga_group_accepts_study_type(
        self,
        app_with_facets: TestClient,
        mock_es_search_facets: AsyncMock,
        endpoint: str,
    ) -> None:
        resp = app_with_facets.get(f"/facets/{endpoint}?studyType=GWAS")
        assert resp.status_code == 200
        body = mock_es_search_facets.call_args[0][2]
        filters = body["query"]["bool"]["filter"]
        assert any("studyType.keyword" in (f.get("term") or {}) for f in filters)


class TestFacetsTypeGroupRejections:
    """Parameters from another type group are rejected with 422."""

    @pytest.mark.parametrize(
        ("endpoint", "param"),
        [
            # bioproject endpoint should reject sra-* and biosample params.
            ("bioproject", "libraryStrategy"),
            ("bioproject", "host"),
            ("bioproject", "studyType"),
            # biosample endpoint should reject sra-* term-only params.
            ("biosample", "libraryStrategy"),
            ("biosample", "platform"),
            ("biosample", "studyType"),
            ("biosample", "objectTypes"),
            # sra-* endpoint should reject biosample-only params.
            ("sra-experiment", "objectTypes"),
            ("sra-experiment", "host"),
            ("sra-experiment", "strain"),
            # jga-* endpoint should reject sra-only params.
            ("jga-study", "libraryStrategy"),
            ("jga-study", "host"),
            ("jga-study", "objectTypes"),
            # gea endpoint should reject other type-specific params.
            ("gea", "objectTypes"),
            ("gea", "libraryStrategy"),
            ("gea", "studyType"),
            # metabobank endpoint should reject other type-specific params.
            ("metabobank", "objectTypes"),
            ("metabobank", "libraryStrategy"),
            ("metabobank", "host"),
        ],
    )
    def test_out_of_group_param_returns_422(
        self,
        app_with_facets: TestClient,
        endpoint: str,
        param: str,
    ) -> None:
        resp = app_with_facets.get(f"/facets/{endpoint}?{param}=X")
        assert resp.status_code == 422
