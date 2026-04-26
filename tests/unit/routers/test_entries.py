"""Tests for Entries API routing and parameter validation.

Implementation tests (TestEntries*Search, etc.) use mocked ES and
DuckDB functions, verifying the full request → response flow.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from ddbj_search_api.cursor import CursorPayload, encode_cursor
from tests.unit.conftest import make_es_search_response
from tests.unit.strategies import db_type_values, valid_per_page

# === Routing: GET /entries/{type}/ ===


class TestEntriesTypeRouting:
    """GET /entries/{type}/ : every DbType value reaches the type-specific route."""

    @pytest.mark.parametrize("db_type", db_type_values)
    def test_type_route_exists(self, app_with_es: TestClient, db_type: str) -> None:
        resp = app_with_es.get(f"/entries/{db_type}/")
        assert resp.status_code == 200


# === Pagination parameter validation (FastAPI level) ===


class TestPaginationValidation:
    """perPage and page query parameter validation."""

    def test_per_page_0_returns_422(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/", params={"perPage": 0})
        assert resp.status_code == 422

    def test_per_page_1_accepted(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/", params={"perPage": 1})
        assert resp.status_code == 200

    def test_per_page_100_accepted(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/", params={"perPage": 100})
        assert resp.status_code == 200

    def test_per_page_101_returns_422(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/", params={"perPage": 101})
        assert resp.status_code == 422

    def test_per_page_negative_returns_422(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/", params={"perPage": -1})
        assert resp.status_code == 422

    def test_page_0_returns_422(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/", params={"page": 0})
        assert resp.status_code == 422

    def test_page_1_accepted(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/", params={"page": 1})
        assert resp.status_code == 200

    def test_page_negative_returns_422(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/", params={"page": -1})
        assert resp.status_code == 422


class TestPaginationValidationPBT:
    """Property-based pagination validation tests."""

    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(per_page=st.integers(max_value=0))
    def test_per_page_le_0_returns_422(self, app_with_es: TestClient, per_page: int) -> None:
        resp = app_with_es.get("/entries/", params={"perPage": per_page})
        assert resp.status_code == 422

    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(per_page=st.integers(min_value=101, max_value=10000))
    def test_per_page_gt_100_returns_422(self, app_with_es: TestClient, per_page: int) -> None:
        resp = app_with_es.get("/entries/", params={"perPage": per_page})
        assert resp.status_code == 422

    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(page=st.integers(max_value=0))
    def test_page_le_0_returns_422(self, app_with_es: TestClient, page: int) -> None:
        resp = app_with_es.get("/entries/", params={"page": page})
        assert resp.status_code == 422


# === Validation error response format ===


class TestValidationErrorFormat:
    """Validation errors return RFC 7807 ProblemDetails."""

    def test_422_has_problem_details_fields(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/", params={"perPage": -1})
        body = resp.json()
        assert body["status"] == 422
        assert body["title"] == "Unprocessable Entity"
        assert "detail" in body
        assert "requestId" in body
        assert "timestamp" in body
        assert "instance" in body

    def test_422_content_type_is_problem_json(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/", params={"perPage": -1})
        assert "application/problem+json" in resp.headers["content-type"]


# === Invalid type in path ===


class TestInvalidTypeInPath:
    """Invalid {type} in path returns 404."""

    def test_unknown_type_returns_404(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/unknown-type/")
        assert resp.status_code == 404


# === Date parameter validation ===


class TestEntriesDateValidation:
    """Date parameter format validation (YYYY-MM-DD only)."""

    def test_valid_date_accepted(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/", params={"datePublishedFrom": "2024-01-15"})
        assert resp.status_code == 200

    def test_slash_date_returns_422(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/", params={"datePublishedFrom": "2024/01/15"})
        assert resp.status_code == 422

    def test_no_dash_date_returns_422(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/", params={"datePublishedFrom": "20240115"})
        assert resp.status_code == 422

    def test_datetime_returns_422(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get(
            "/entries/",
            params={"datePublishedFrom": "2024-01-15T00:00:00"},
        )
        assert resp.status_code == 422


# === objectTypes parameter validation ===


class TestEntriesBioProjectObjectTypesValidation:
    """objectTypes parameter validation for BioProject."""

    def test_single_bioproject_accepted(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/bioproject/", params={"objectTypes": "BioProject"})
        assert resp.status_code == 200

    def test_single_umbrella_accepted(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get(
            "/entries/bioproject/",
            params={"objectTypes": "UmbrellaBioProject"},
        )
        assert resp.status_code == 200

    def test_both_accepted(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get(
            "/entries/bioproject/",
            params={"objectTypes": "BioProject,UmbrellaBioProject"},
        )
        assert resp.status_code == 200

    def test_legacy_umbrella_param_rejected(self, app_with_es: TestClient) -> None:
        """``umbrella`` is no longer accepted; the unknown-query guard
        rejects it with 422 (docs/api-spec.md § エンドポイント固有の
        パラメータ)."""
        resp = app_with_es.get("/entries/bioproject/", params={"umbrella": "TRUE"})
        assert resp.status_code == 422

    def test_lowercase_rejected(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/bioproject/", params={"objectTypes": "bioproject"})
        assert resp.status_code == 422

    def test_unknown_value_rejected(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get(
            "/entries/bioproject/",
            params={"objectTypes": "BioProject,Foo"},
        )
        assert resp.status_code == 422

    def test_empty_rejected(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/bioproject/", params={"objectTypes": ""})
        assert resp.status_code == 422

    def test_trailing_comma_rejected(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get(
            "/entries/bioproject/",
            params={"objectTypes": "BioProject,"},
        )
        assert resp.status_code == 422


# === Implementation tests: search flow ===


class TestEntriesSearch:
    """Basic search flow: ES returns results → 200 with correct shape."""

    def test_empty_result_returns_200(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["pagination"]["total"] == 0
        assert body["pagination"]["page"] == 1
        assert body["pagination"]["perPage"] == 10
        assert body["facets"] is None

    def test_result_with_hits(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        mock_es_search.return_value = make_es_search_response(
            hits=[
                {
                    "_source": {
                        "identifier": "PRJDB1",
                        "type": "bioproject",
                        "title": "Test project",
                    },
                },
                {
                    "_source": {
                        "identifier": "SAMD1",
                        "type": "biosample",
                        "title": "Test sample",
                    },
                },
            ],
            total=2,
        )
        resp = app_with_es.get("/entries/")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["pagination"]["total"] == 2
        assert body["items"][0]["identifier"] == "PRJDB1"
        assert body["items"][1]["identifier"] == "SAMD1"
        # dbXrefs come from DuckDB (mocked empty)
        assert body["items"][0]["dbXrefs"] == []
        assert body["items"][0]["dbXrefsCount"] == {}

    def test_pagination_params_passed_to_es(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        app_with_es.get("/entries/", params={"page": 3, "perPage": 20})
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        # page=3, perPage=20 → from=40, size=20
        assert body["from"] == 40
        assert body["size"] == 20

    def test_default_pagination(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        app_with_es.get("/entries/")
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        # page=1, perPage=10 → from=0, size=10
        assert body["from"] == 0
        assert body["size"] == 10

    def test_keywords_passed_to_es(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        app_with_es.get("/entries/", params={"keywords": "cancer"})
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        assert "query" in body
        assert body["query"] != {"match_all": {}}

    def test_cross_type_uses_entries_index(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        app_with_es.get("/entries/")
        call_args = mock_es_search.call_args
        index = call_args[1]["index"] if "index" in call_args[1] else call_args[0][1]
        assert index == "entries"

    def test_organism_filter_passed_to_es(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        app_with_es.get("/entries/", params={"organism": "9606"})
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        filters = body["query"]["bool"]["filter"]
        term_filters = [f for f in filters if "term" in f and "organism.identifier" in f["term"]]
        assert len(term_filters) == 1
        assert term_filters[0]["term"]["organism.identifier"] == "9606"

    def test_keyword_operator_or(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        app_with_es.get(
            "/entries/",
            params={"keywords": "cancer,tumor", "keywordOperator": "OR"},
        )
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        assert "should" in body["query"]["bool"]
        assert "minimum_should_match" in body["query"]["bool"]

    def test_keyword_operator_and_default(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        app_with_es.get(
            "/entries/",
            params={"keywords": "cancer,tumor"},
        )
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        assert "must" in body["query"]["bool"]

    def test_keyword_operator_invalid_returns_422(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/", params={"keywordOperator": "INVALID"})
        assert resp.status_code == 422


# === includeProperties ===


class TestEntriesIncludeProperties:
    """includeProperties parameter behaviour."""

    def test_include_properties_true_by_default(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        mock_es_search.return_value = make_es_search_response(
            hits=[
                {
                    "_source": {
                        "identifier": "PRJDB1",
                        "type": "bioproject",
                        "properties": {"key": "val"},
                    },
                }
            ],
            total=1,
        )
        resp = app_with_es.get("/entries/")
        body = resp.json()
        assert body["items"][0].get("properties") is not None

    def test_include_properties_false_excludes_field(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        mock_es_search.return_value = make_es_search_response(
            hits=[
                {
                    "_source": {
                        "identifier": "PRJDB1",
                        "type": "bioproject",
                    },
                }
            ],
            total=1,
        )
        resp = app_with_es.get("/entries/", params={"includeProperties": "false"})
        body = resp.json()
        assert "properties" not in body["items"][0]

    def test_include_properties_false_sends_source_excludes(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        app_with_es.get("/entries/", params={"includeProperties": "false"})
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        source = body["_source"]
        assert isinstance(source, dict)
        assert "properties" in source["excludes"]


# === types parameter validation ===


class TestEntriesTypesFilter:
    """types parameter validation and filtering."""

    def test_valid_types_filter(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        resp = app_with_es.get("/entries/", params={"types": "bioproject,biosample"})
        assert resp.status_code == 200
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        filters = body["query"]["bool"]["filter"]
        type_filter = [f for f in filters if "terms" in f and "type" in f["terms"]]
        assert len(type_filter) == 1
        assert set(type_filter[0]["terms"]["type"]) == {
            "bioproject",
            "biosample",
        }

    def test_single_type_filter(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        resp = app_with_es.get("/entries/", params={"types": "bioproject"})
        assert resp.status_code == 200

    def test_empty_types_rejected_by_pattern(
        self,
        app_with_es: TestClient,
    ) -> None:
        # `?types=` (空文字) は TypesFilterQuery の pattern 違反で 422。
        # 「指定しない」ケースは ?types= ではなくクエリ自体を省略する運用。
        resp = app_with_es.get("/entries/", params={"types": ""})
        assert resp.status_code == 422

    def test_invalid_type_returns_422(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/", params={"types": "invalid-type"})
        assert resp.status_code == 422

    def test_mixed_valid_invalid_types_returns_422(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/", params={"types": "bioproject,invalid"})
        assert resp.status_code == 422

    def test_all_12_types_accepted(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        all_types = ",".join(db_type_values)
        resp = app_with_es.get("/entries/", params={"types": all_types})
        assert resp.status_code == 200


# === Empty/whitespace keywords ===


class TestEntriesEmptyKeywords:
    """Empty and whitespace-only keywords behaviour."""

    _STATUS_ONLY_QUERY = {"bool": {"filter": [{"term": {"status": "public"}}]}}

    def test_empty_keywords_treated_as_no_filter(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        resp = app_with_es.get("/entries/", params={"keywords": ""})
        assert resp.status_code == 200
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        # keyword 無し → status filter のみの bool 句 (match_all ではない)
        assert body["query"] == self._STATUS_ONLY_QUERY

    def test_whitespace_keywords_treated_as_no_filter(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        resp = app_with_es.get("/entries/", params={"keywords": "   "})
        assert resp.status_code == 200
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        assert body["query"] == self._STATUS_ONLY_QUERY

    def test_comma_only_keywords_treated_as_no_filter(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        resp = app_with_es.get("/entries/", params={"keywords": ","})
        assert resp.status_code == 200
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        assert body["query"] == self._STATUS_ONLY_QUERY


# === Deep paging ===


class TestEntriesDeepPaging:
    """Deep paging limit: page * perPage > 10000 → 400."""

    def test_page_100_per_page_100_ok(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/", params={"page": 100, "perPage": 100})
        assert resp.status_code == 200

    def test_page_101_per_page_100_returns_400(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/", params={"page": 101, "perPage": 100})
        assert resp.status_code == 400

    @settings(
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        max_examples=30,
    )
    @given(
        data=st.data(),
    )
    def test_pbt_deep_paging_rejected(self, app_with_es: TestClient, data: st.DataObject) -> None:
        per_page = data.draw(valid_per_page)
        # Ensure page * per_page > 10000
        min_page = (10000 // per_page) + 1
        page = data.draw(st.integers(min_value=min_page, max_value=min_page + 1000))
        resp = app_with_es.get("/entries/", params={"page": page, "perPage": per_page})
        assert resp.status_code == 400


# === Sort validation ===


class TestEntriesSortValidation:
    """sort parameter validation."""

    def test_valid_sort_accepted(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/", params={"sort": "datePublished:asc"})
        assert resp.status_code == 200

    def test_valid_sort_date_modified(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/", params={"sort": "dateModified:desc"})
        assert resp.status_code == 200

    def test_invalid_sort_field_returns_422(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/", params={"sort": "invalidField:asc"})
        assert resp.status_code == 422

    def test_invalid_sort_format_returns_422(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/", params={"sort": "bad-format"})
        assert resp.status_code == 422

    def test_sort_passed_to_es(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        app_with_es.get("/entries/", params={"sort": "datePublished:asc"})
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        assert "sort" in body
        assert body["sort"] == [
            {"datePublished": {"order": "asc"}},
            {"identifier": {"order": "asc"}},
        ]


# === keywordFields validation ===


class TestEntriesKeywordFieldsValidation:
    """keywordFields parameter validation."""

    def test_invalid_keyword_fields_returns_422(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get(
            "/entries/",
            params={"keywords": "test", "keywordFields": "badField"},
        )
        assert resp.status_code == 422

    def test_valid_keyword_fields_accepted(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get(
            "/entries/",
            params={"keywords": "test", "keywordFields": "title,description"},
        )
        assert resp.status_code == 200


# === Facets ===


class TestEntriesFacets:
    """includeFacets parameter."""

    def test_include_facets_true(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        mock_es_search.return_value = make_es_search_response(
            total=0,
            aggregations={
                "type": {"buckets": [{"key": "bioproject", "doc_count": 5}]},
                "organism": {"buckets": []},
                "accessibility": {"buckets": []},
            },
        )
        resp = app_with_es.get("/entries/", params={"includeFacets": "true"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["facets"] is not None
        assert "organism" in body["facets"]

    def test_include_facets_false(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/", params={"includeFacets": "false"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["facets"] is None

    def test_include_facets_default_false(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["facets"] is None

    def test_include_facets_triggers_aggs_in_es(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        mock_es_search.return_value = make_es_search_response(
            total=0,
            aggregations={
                "type": {"buckets": []},
                "organism": {"buckets": []},
                "accessibility": {"buckets": []},
            },
        )
        app_with_es.get("/entries/", params={"includeFacets": "true"})
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        assert "aggs" in body


# === Type-specific search ===


class TestEntriesTypeSearch:
    """GET /entries/{type}/ uses correct ES index."""

    def test_bioproject_uses_bioproject_index(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        app_with_es.get("/entries/bioproject/")
        call_args = mock_es_search.call_args
        index = call_args[1]["index"] if "index" in call_args[1] else call_args[0][1]
        assert index == "bioproject"

    def test_sra_study_uses_sra_study_index(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        app_with_es.get("/entries/sra-study/")
        call_args = mock_es_search.call_args
        index = call_args[1]["index"] if "index" in call_args[1] else call_args[0][1]
        assert index == "sra-study"

    def test_bioproject_object_types_single_emits_term(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        app_with_es.get(
            "/entries/bioproject/",
            params={"objectTypes": "UmbrellaBioProject"},
        )
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        filters = body["query"]["bool"]["filter"]
        obj_filters = [f for f in filters if "term" in f and "objectType" in f["term"]]
        assert len(obj_filters) == 1
        assert obj_filters[0]["term"]["objectType"] == "UmbrellaBioProject"

    def test_bioproject_object_types_both_emits_terms(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        app_with_es.get(
            "/entries/bioproject/",
            params={"objectTypes": "BioProject,UmbrellaBioProject"},
        )
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        filters = body["query"]["bool"]["filter"]
        obj_filters = [f for f in filters if "terms" in f and "objectType" in f["terms"]]
        assert len(obj_filters) == 1
        assert obj_filters[0]["terms"]["objectType"] == [
            "BioProject",
            "UmbrellaBioProject",
        ]

    def test_bioproject_organization_filter(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        app_with_es.get(
            "/entries/bioproject/",
            params={"organization": "DDBJ"},
        )
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        filters = body["query"]["bool"]["filter"]
        nested = [f for f in filters if "nested" in f]
        assert len(nested) == 1
        assert nested[0]["nested"]["path"] == "organization"

    def test_bioproject_publication_filter(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        app_with_es.get(
            "/entries/bioproject/",
            params={"publication": "genomics"},
        )
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        filters = body["query"]["bool"]["filter"]
        nested = [f for f in filters if "nested" in f]
        assert len(nested) == 1
        assert nested[0]["nested"]["path"] == "publication"

    def test_bioproject_grant_filter(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        app_with_es.get(
            "/entries/bioproject/",
            params={"grant": "JSPS"},
        )
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        filters = body["query"]["bool"]["filter"]
        nested = [f for f in filters if "nested" in f]
        assert len(nested) == 1
        assert nested[0]["nested"]["path"] == "grant"

    def test_type_facets_no_type_field(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        """Type-specific facets should NOT include 'type' facet."""
        mock_es_search.return_value = make_es_search_response(
            total=0,
            aggregations={
                "organism": {"buckets": []},
                "accessibility": {"buckets": []},
            },
        )
        resp = app_with_es.get(
            "/entries/biosample/",
            params={"includeFacets": "true"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["facets"] is not None
        assert body["facets"].get("type") is None


# === dbXrefs truncation ===


class TestEntriesDbXrefs:
    """dbXrefs truncation and dbXrefsCount in list results.

    Search results use DuckDB for dbXrefs: ES returns ``_source`` without
    dbXrefs, and DuckDB provides truncated xrefs + per-type counts.
    """

    def test_db_xrefs_from_duckdb(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        """DuckDB provides per-type limited xrefs and full counts."""
        mock_es_search.return_value = make_es_search_response(
            hits=[
                {
                    "_source": {
                        "identifier": "PRJDB1",
                        "type": "bioproject",
                    },
                }
            ],
            total=1,
        )
        # Mock reflects per-type limit: 3 biosample + 2 sra-study (limit=3 per type)
        with (
            patch(
                "ddbj_search_api.routers.entries.get_linked_ids_limited_bulk",
                return_value={
                    ("bioproject", "PRJDB1"): [
                        ("biosample", "SAMD0"),
                        ("biosample", "SAMD1"),
                        ("biosample", "SAMD2"),
                        ("sra-study", "DRP0"),
                        ("sra-study", "DRP1"),
                    ]
                },
            ),
            patch(
                "ddbj_search_api.routers.entries.count_linked_ids_bulk",
                return_value={("bioproject", "PRJDB1"): {"biosample": 200, "sra-study": 50}},
            ),
        ):
            resp = app_with_es.get("/entries/", params={"dbXrefsLimit": 3})

        assert resp.status_code == 200
        body = resp.json()
        item = body["items"][0]
        assert len(item["dbXrefs"]) == 5
        assert item["dbXrefsCount"]["biosample"] == 200
        assert item["dbXrefsCount"]["sra-study"] == 50

    def test_db_xrefs_limit_0(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        mock_es_search.return_value = make_es_search_response(
            hits=[
                {
                    "_source": {
                        "identifier": "PRJDB1",
                        "type": "bioproject",
                    },
                }
            ],
            total=1,
        )
        with (
            patch(
                "ddbj_search_api.routers.entries.get_linked_ids_limited_bulk",
                return_value={("bioproject", "PRJDB1"): []},
            ),
            patch(
                "ddbj_search_api.routers.entries.count_linked_ids_bulk",
                return_value={("bioproject", "PRJDB1"): {"biosample": 50}},
            ),
        ):
            resp = app_with_es.get("/entries/", params={"dbXrefsLimit": 0})

        assert resp.status_code == 200
        body = resp.json()
        item = body["items"][0]
        assert item["dbXrefs"] == []
        assert item["dbXrefsCount"]["biosample"] == 50

    def test_db_xrefs_count_correct_with_multiple_types(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        mock_es_search.return_value = make_es_search_response(
            hits=[
                {
                    "_source": {
                        "identifier": "PRJDB1",
                        "type": "bioproject",
                    },
                }
            ],
            total=1,
        )
        with (
            patch(
                "ddbj_search_api.routers.entries.get_linked_ids_limited_bulk",
                return_value={
                    ("bioproject", "PRJDB1"): [("biosample", f"SAMD{i}") for i in range(5)]
                    + [("sra-study", f"SRP{i}") for i in range(3)]
                },
            ),
            patch(
                "ddbj_search_api.routers.entries.count_linked_ids_bulk",
                return_value={("bioproject", "PRJDB1"): {"biosample": 5, "sra-study": 3}},
            ),
        ):
            resp = app_with_es.get("/entries/", params={"dbXrefsLimit": 100})

        assert resp.status_code == 200
        body = resp.json()
        item = body["items"][0]
        assert item["dbXrefsCount"]["biosample"] == 5
        assert item["dbXrefsCount"]["sra-study"] == 3

    def test_no_db_xrefs(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        """Entry without dbXrefs: DuckDB returns empty defaults."""
        mock_es_search.return_value = make_es_search_response(
            hits=[
                {
                    "_source": {
                        "identifier": "PRJDB1",
                        "type": "bioproject",
                        "title": "No xrefs",
                    },
                }
            ],
            total=1,
        )
        resp = app_with_es.get("/entries/")
        assert resp.status_code == 200
        body = resp.json()
        item = body["items"][0]
        assert item["dbXrefs"] == []
        assert item["dbXrefsCount"] == {}


# === _source filter: dbXrefs always excluded ===


class TestEntriesSourceFilter:
    """_source filter always excludes dbXrefs from ES request."""

    def test_fields_param_excludes_db_xrefs(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        """fields=identifier,type,dbXrefs → _source should NOT contain dbXrefs."""
        app_with_es.get(
            "/entries/",
            params={"fields": "identifier,type,dbXrefs"},
        )
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        source = body["_source"]
        assert isinstance(source, list)
        assert "dbXrefs" not in source

    def test_fields_param_passes_other_fields(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        """fields=identifier,type → _source contains requested fields."""
        app_with_es.get(
            "/entries/",
            params={"fields": "identifier,type"},
        )
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        source = body["_source"]
        assert isinstance(source, list)
        assert "identifier" in source
        assert "type" in source

    def test_no_fields_excludes_db_xrefs(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        """Default (no fields) → _source excludes dbXrefs."""
        app_with_es.get("/entries/")
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        source = body["_source"]
        assert isinstance(source, dict)
        assert "dbXrefs" in source["excludes"]


# === Facet aggregation size ===


class TestEntriesFacetAggSize:
    """Facet aggregations use size=50."""

    def test_facet_aggs_have_size_50(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        mock_es_search.return_value = make_es_search_response(
            total=0,
            aggregations={
                "type": {"buckets": []},
                "organism": {"buckets": []},
                "accessibility": {"buckets": []},
            },
        )
        app_with_es.get("/entries/", params={"includeFacets": "true"})
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        aggs = body["aggs"]
        for agg_name in ("organism", "accessibility", "type"):
            assert aggs[agg_name]["terms"]["size"] == 50, f"{agg_name} should have size=50"
        # status facet は常に public になるので aggs に含めない
        assert "status" not in aggs


# === ES error handling ===


class TestEntriesEsError:
    """ES errors are handled gracefully."""

    def test_es_error_returns_500(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        mock_es_search.side_effect = Exception("ES connection refused")
        resp = app_with_es.get("/entries/")
        assert resp.status_code == 500


# === Date range edge cases ===


class TestEntriesDateRangeEdgeCases:
    """Date range parameter edge cases."""

    def test_from_after_to_accepted(self, app_with_es: TestClient) -> None:
        """from > to is accepted (ES returns 0 results, not an error)."""
        resp = app_with_es.get(
            "/entries/",
            params={
                "datePublishedFrom": "2025-12-31",
                "datePublishedTo": "2024-01-01",
            },
        )
        assert resp.status_code == 200

    def test_date_modified_from_after_to_accepted(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get(
            "/entries/",
            params={
                "dateModifiedFrom": "2025-06-01",
                "dateModifiedTo": "2024-06-01",
            },
        )
        assert resp.status_code == 200

    def test_same_from_and_to_accepted(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get(
            "/entries/",
            params={
                "datePublishedFrom": "2024-06-15",
                "datePublishedTo": "2024-06-15",
            },
        )
        assert resp.status_code == 200

    def test_feb_30_returns_422(self, app_with_es: TestClient) -> None:
        """Feb 30 passes regex but fails semantic date validation."""
        resp = app_with_es.get("/entries/", params={"datePublishedFrom": "2024-02-30"})
        assert resp.status_code == 422

    def test_month_13_returns_422(self, app_with_es: TestClient) -> None:
        """Month 13 passes regex but fails semantic date validation."""
        resp = app_with_es.get("/entries/", params={"datePublishedFrom": "2024-13-01"})
        assert resp.status_code == 422

    def test_feb_29_leap_year_accepted(self, app_with_es: TestClient) -> None:
        """Feb 29 in a leap year is a valid date."""
        resp = app_with_es.get("/entries/", params={"datePublishedFrom": "2024-02-29"})
        assert resp.status_code == 200

    def test_feb_29_non_leap_year_returns_422(self, app_with_es: TestClient) -> None:
        """Feb 29 in a non-leap year is invalid."""
        resp = app_with_es.get("/entries/", params={"datePublishedFrom": "2023-02-29"})
        assert resp.status_code == 422

    def test_day_00_returns_422(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/", params={"datePublishedFrom": "2024-01-00"})
        assert resp.status_code == 422

    def test_only_from_accepted(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/", params={"datePublishedFrom": "2024-01-01"})
        assert resp.status_code == 200

    def test_only_to_accepted(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/", params={"datePublishedTo": "2024-12-31"})
        assert resp.status_code == 200


# === PBT: search parameter combinations ===


class TestEntriesSearchPBT:
    """Property-based tests for search parameter combinations."""

    @settings(
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        max_examples=20,
    )
    @given(
        page=st.integers(min_value=1, max_value=100),
        per_page=st.integers(min_value=1, max_value=100),
    )
    def test_valid_pagination_always_accepted(self, app_with_es: TestClient, page: int, per_page: int) -> None:
        """Any valid page/perPage within range returns 200 or 400 (deep paging)."""
        resp = app_with_es.get("/entries/", params={"page": page, "perPage": per_page})
        if page * per_page > 10000:
            assert resp.status_code == 400
        else:
            assert resp.status_code == 200

    @settings(
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        max_examples=20,
    )
    @given(
        sort=st.sampled_from(
            [
                "datePublished:asc",
                "datePublished:desc",
                "dateModified:asc",
                "dateModified:desc",
            ]
        ),
    )
    def test_valid_sort_always_accepted(self, app_with_es: TestClient, sort: str) -> None:
        resp = app_with_es.get("/entries/", params={"sort": sort})
        assert resp.status_code == 200

    @settings(
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        max_examples=20,
    )
    @given(
        field=st.text(
            alphabet=st.characters(whitelist_categories=("L",)),  # type: ignore[arg-type]
            min_size=1,
            max_size=10,
        ),
    )
    def test_invalid_sort_field_always_422(self, app_with_es: TestClient, field: str) -> None:
        assume(field not in ("datePublished", "dateModified"))
        resp = app_with_es.get("/entries/", params={"sort": f"{field}:asc"})
        assert resp.status_code == 422

    @settings(
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        max_examples=20,
    )
    @given(
        operator=st.sampled_from(["AND", "OR"]),
    )
    def test_valid_keyword_operator_accepted(self, app_with_es: TestClient, operator: str) -> None:
        resp = app_with_es.get(
            "/entries/",
            params={"keywords": "test", "keywordOperator": operator},
        )
        assert resp.status_code == 200


# ===================================================================
# Cursor-based pagination
# ===================================================================


def _make_cursor_token(
    pit_id: str | None = None,
    search_after: list[object] | None = None,
) -> str:
    payload = CursorPayload(
        pit_id=pit_id,
        search_after=search_after or ["2026-01-15", "SAMD00001"],
        sort=[{"datePublished": {"order": "desc"}}, {"identifier": {"order": "asc"}}],
        query={"match_all": {}},
    )

    return encode_cursor(payload)


class TestCursorExclusivity:
    """cursor + search/page params -> 400."""

    def test_cursor_with_page_returns_400(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get(
            "/entries/",
            params={"cursor": _make_cursor_token(), "page": "2"},
        )
        assert resp.status_code == 400
        assert "page" in resp.json()["detail"]

    def test_cursor_with_keywords_returns_400(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get(
            "/entries/",
            params={"cursor": _make_cursor_token(), "keywords": "test"},
        )
        assert resp.status_code == 400
        assert "keywords" in resp.json()["detail"]

    def test_cursor_with_sort_returns_400(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get(
            "/entries/",
            params={"cursor": _make_cursor_token(), "sort": "datePublished:asc"},
        )
        assert resp.status_code == 400
        assert "sort" in resp.json()["detail"]

    def test_cursor_with_organism_returns_400(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get(
            "/entries/",
            params={"cursor": _make_cursor_token(), "organism": "9606"},
        )
        assert resp.status_code == 400
        assert "organism" in resp.json()["detail"]

    def test_cursor_with_types_returns_400(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get(
            "/entries/",
            params={"cursor": _make_cursor_token(), "types": "bioproject"},
        )
        assert resp.status_code == 400
        assert "types" in resp.json()["detail"]

    def test_cursor_with_include_facets_returns_400(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get(
            "/entries/",
            params={"cursor": _make_cursor_token(), "includeFacets": "true"},
        )
        assert resp.status_code == 400
        assert "includeFacets" in resp.json()["detail"]

    def test_cursor_with_per_page_allowed(
        self,
        app_with_es: TestClient,
        mock_es_open_pit: AsyncMock,
        mock_es_search_with_pit: AsyncMock,
    ) -> None:
        mock_es_search_with_pit.return_value = make_es_search_response()
        resp = app_with_es.get(
            "/entries/",
            params={"cursor": _make_cursor_token(), "perPage": "50"},
        )
        assert resp.status_code == 200

    @pytest.mark.parametrize(
        ("endpoint", "param", "value"),
        [
            # bioproject 型グループ
            ("bioproject", "objectTypes", "BioProject"),
            ("bioproject", "externalLinkLabel", "GEO"),
            ("bioproject", "projectType", "metagenome"),
            # biosample 型グループ
            ("biosample", "derivedFromId", "SAMD00012345"),
            ("biosample", "host", "Homo sapiens"),
            ("biosample", "strain", "K12"),
            ("biosample", "isolate", "patient-1"),
            ("biosample", "geoLocName", "Japan"),
            ("biosample", "collectionDate", "2020-05-01"),
            # sra-* 型グループ (sra-experiment を代表として使う)
            ("sra-experiment", "libraryStrategy", "WGS"),
            ("sra-experiment", "librarySource", "GENOMIC"),
            ("sra-experiment", "librarySelection", "RANDOM"),
            ("sra-experiment", "platform", "ILLUMINA"),
            ("sra-experiment", "instrumentModel", "HiSeq"),
            ("sra-experiment", "libraryLayout", "PAIRED"),
            ("sra-experiment", "analysisType", "ALIGNMENT"),
            ("sra-experiment", "derivedFromId", "SAMD00012345"),
            ("sra-experiment", "libraryName", "lib1"),
            ("sra-experiment", "libraryConstructionProtocol", "PCR-free"),
            # jga-* 型グループ (jga-study を代表として使う)
            ("jga-study", "studyType", "GWAS"),
            ("jga-study", "datasetType", "WGS"),
            ("jga-study", "vendor", "Illumina"),
            # gea / metabobank
            ("gea", "experimentType", "RNA-Seq"),
            ("metabobank", "submissionType", "open"),
        ],
    )
    def test_cursor_with_type_specific_param_returns_400(
        self,
        app_with_es: TestClient,
        endpoint: str,
        param: str,
        value: str,
    ) -> None:
        """cursor 排他リスト (docs/api-spec.md § カーソルベース) の
        type-specific filter / nested / text param をすべて parametrize し、
        cursor と併用したら 400 になることを担保する。"""
        resp = app_with_es.get(
            f"/entries/{endpoint}/",
            params={"cursor": _make_cursor_token(), param: value},
        )
        assert resp.status_code == 400
        assert param in resp.json()["detail"]

    @pytest.mark.parametrize(
        "param",
        ["organization", "publication", "grant"],
    )
    def test_cursor_with_common_nested_param_returns_400(
        self,
        app_with_es: TestClient,
        param: str,
    ) -> None:
        """共通 nested (organization/publication/grant) も cursor と
        排他 (docs/api-spec.md § カーソルベース)。"""
        resp = app_with_es.get(
            "/entries/",
            params={"cursor": _make_cursor_token(), param: "DDBJ"},
        )
        assert resp.status_code == 400
        assert param in resp.json()["detail"]

    @pytest.mark.parametrize("param", ["facets"])
    def test_cursor_with_facets_returns_400(
        self,
        app_with_es: TestClient,
        param: str,
    ) -> None:
        resp = app_with_es.get(
            "/entries/",
            params={"cursor": _make_cursor_token(), param: "organism"},
        )
        assert resp.status_code == 400
        assert param in resp.json()["detail"]


class TestCursorOffsetMode:
    """Offset responses include nextCursor and hasNext."""

    def test_offset_response_has_next_cursor(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        hits = [
            {
                "_source": {"identifier": f"PRJDB{i}", "type": "bioproject"},
                "sort": [f"2026-01-{i:02d}", f"PRJDB{i}"],
            }
            for i in range(1, 11)
        ]
        mock_es_search.return_value = make_es_search_response(hits=hits, total=100)
        resp = app_with_es.get("/entries/", params={"perPage": "10"})
        data = resp.json()
        assert resp.status_code == 200
        assert data["pagination"]["nextCursor"] is not None
        assert data["pagination"]["hasNext"] is True

    def test_last_page_has_no_next_cursor(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        hits = [
            {
                "_source": {"identifier": "PRJDB1", "type": "bioproject"},
                "sort": ["2026-01-01", "PRJDB1"],
            },
        ]
        mock_es_search.return_value = make_es_search_response(hits=hits, total=1)
        resp = app_with_es.get("/entries/", params={"perPage": "10"})
        data = resp.json()
        assert data["pagination"]["nextCursor"] is None
        assert data["pagination"]["hasNext"] is False

    def test_empty_results_has_no_next_cursor(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        mock_es_search.return_value = make_es_search_response(hits=[], total=0)
        resp = app_with_es.get("/entries/")
        data = resp.json()
        assert data["pagination"]["nextCursor"] is None
        assert data["pagination"]["hasNext"] is False

    def test_offset_response_has_page_field(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        mock_es_search.return_value = make_es_search_response(hits=[], total=0)
        resp = app_with_es.get("/entries/", params={"page": "3"})
        data = resp.json()
        assert data["pagination"]["page"] == 3


class TestCursorMode:
    """Cursor-based pagination with search_after + PIT."""

    def test_cursor_without_pit_opens_pit(
        self,
        app_with_es: TestClient,
        mock_es_open_pit: AsyncMock,
        mock_es_search_with_pit: AsyncMock,
    ) -> None:
        token = _make_cursor_token(pit_id=None)
        mock_es_search_with_pit.return_value = make_es_search_response(
            hits=[],
            total=0,
        )
        resp = app_with_es.get("/entries/", params={"cursor": token})
        assert resp.status_code == 200
        mock_es_open_pit.assert_called_once()

    def test_cursor_with_pit_reuses_pit(
        self,
        app_with_es: TestClient,
        mock_es_open_pit: AsyncMock,
        mock_es_search_with_pit: AsyncMock,
    ) -> None:
        token = _make_cursor_token(pit_id="existing_pit_123")
        mock_es_search_with_pit.return_value = make_es_search_response(
            hits=[],
            total=0,
        )
        resp = app_with_es.get("/entries/", params={"cursor": token})
        assert resp.status_code == 200
        mock_es_open_pit.assert_not_called()

    def test_cursor_response_has_null_page(
        self,
        app_with_es: TestClient,
        mock_es_open_pit: AsyncMock,
        mock_es_search_with_pit: AsyncMock,
    ) -> None:
        token = _make_cursor_token()
        mock_es_search_with_pit.return_value = make_es_search_response(
            hits=[],
            total=0,
        )
        resp = app_with_es.get("/entries/", params={"cursor": token})
        data = resp.json()
        assert data["pagination"]["page"] is None

    def test_cursor_response_has_next_cursor(
        self,
        app_with_es: TestClient,
        mock_es_open_pit: AsyncMock,
        mock_es_search_with_pit: AsyncMock,
    ) -> None:
        token = _make_cursor_token(pit_id="pit_abc")
        hits = [
            {
                "_source": {"identifier": f"SAMD{i:05d}", "type": "biosample"},
                "sort": [f"2026-01-{i:02d}", f"SAMD{i:05d}"],
            }
            for i in range(1, 11)
        ]
        mock_es_search_with_pit.return_value = {
            "pit_id": "pit_abc_updated",
            "hits": {
                "total": {"value": 100, "relation": "eq"},
                "hits": hits,
            },
        }
        resp = app_with_es.get("/entries/", params={"cursor": token, "perPage": "10"})
        data = resp.json()
        assert data["pagination"]["nextCursor"] is not None
        assert data["pagination"]["hasNext"] is True

    def test_invalid_cursor_returns_400(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/", params={"cursor": "garbage_token"})
        assert resp.status_code == 400
        assert "cursor" in resp.json()["detail"].lower()

    def test_expired_pit_returns_400(
        self,
        app_with_es: TestClient,
        mock_es_open_pit: AsyncMock,
        mock_es_search_with_pit: AsyncMock,
    ) -> None:
        token = _make_cursor_token(pit_id="expired_pit")
        mock_es_search_with_pit.side_effect = httpx.HTTPStatusError(
            "Not Found",
            request=httpx.Request("POST", "http://localhost:9200/_search"),
            response=httpx.Response(404),
        )
        resp = app_with_es.get("/entries/", params={"cursor": token})
        assert resp.status_code == 400
        assert "expired" in resp.json()["detail"].lower()

    def test_cursor_on_type_endpoint(
        self,
        app_with_es: TestClient,
        mock_es_open_pit: AsyncMock,
        mock_es_search_with_pit: AsyncMock,
    ) -> None:
        token = _make_cursor_token()
        mock_es_search_with_pit.return_value = make_es_search_response(
            hits=[],
            total=0,
        )
        resp = app_with_es.get("/entries/biosample/", params={"cursor": token})
        assert resp.status_code == 200

    def test_cursor_with_page_on_type_endpoint_returns_400(
        self,
        app_with_es: TestClient,
    ) -> None:
        resp = app_with_es.get(
            "/entries/biosample/",
            params={"cursor": _make_cursor_token(), "page": "2"},
        )
        assert resp.status_code == 400


class TestCursorBackwardCompatibility:
    """Existing offset pagination still works with new fields."""

    def test_offset_still_works(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        mock_es_search.return_value = make_es_search_response(hits=[], total=0)
        resp = app_with_es.get("/entries/", params={"page": "1", "perPage": "10"})
        assert resp.status_code == 200
        data = resp.json()
        assert "page" in data["pagination"]
        assert "perPage" in data["pagination"]
        assert "total" in data["pagination"]
        assert "nextCursor" in data["pagination"]
        assert "hasNext" in data["pagination"]


# === includeDbXrefs parameter ===


class TestEntriesIncludeDbXrefs:
    """includeDbXrefs parameter controls DuckDB access in search endpoints."""

    def test_search_include_db_xrefs_false(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        """includeDbXrefs=false omits dbXrefs and dbXrefsCount from items."""
        mock_es_search.return_value = make_es_search_response(
            hits=[
                {
                    "_source": {
                        "identifier": "PRJDB1",
                        "type": "bioproject",
                    },
                    "sort": [1.0, "PRJDB1"],
                },
            ],
            total=1,
        )
        resp = app_with_es.get("/entries/?includeDbXrefs=false")
        assert resp.status_code == 200
        data = resp.json()
        item = data["items"][0]
        assert item.get("dbXrefs") is None
        assert item.get("dbXrefsCount") is None

    def test_search_include_db_xrefs_false_skips_duckdb(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        """includeDbXrefs=false does not call DuckDB functions."""
        mock_es_search.return_value = make_es_search_response(
            hits=[
                {
                    "_source": {
                        "identifier": "PRJDB1",
                        "type": "bioproject",
                    },
                    "sort": [1.0, "PRJDB1"],
                },
            ],
            total=1,
        )
        with (
            patch(
                "ddbj_search_api.routers.entries.get_linked_ids_limited_bulk",
            ) as mock_bulk_xrefs,
            patch(
                "ddbj_search_api.routers.entries.count_linked_ids_bulk",
            ) as mock_bulk_counts,
        ):
            resp = app_with_es.get("/entries/?includeDbXrefs=false")

        assert resp.status_code == 200
        mock_bulk_xrefs.assert_not_called()
        mock_bulk_counts.assert_not_called()

    def test_cursor_with_include_db_xrefs_false(
        self,
        app_with_es: TestClient,
        mock_es_open_pit: AsyncMock,
        mock_es_search_with_pit: AsyncMock,
    ) -> None:
        """includeDbXrefs=false works with cursor-based pagination."""
        mock_es_search_with_pit.return_value = make_es_search_response(
            hits=[
                {
                    "_source": {
                        "identifier": "PRJDB1",
                        "type": "bioproject",
                    },
                    "sort": [1.0, "PRJDB1"],
                },
            ],
            total=1,
        )
        token = _make_cursor_token()
        with (
            patch(
                "ddbj_search_api.routers.entries.get_linked_ids_limited_bulk",
            ) as mock_bulk_xrefs,
            patch(
                "ddbj_search_api.routers.entries.count_linked_ids_bulk",
            ) as mock_bulk_counts,
        ):
            resp = app_with_es.get(
                "/entries/",
                params={"cursor": token, "includeDbXrefs": "false"},
            )

        assert resp.status_code == 200
        data = resp.json()
        item = data["items"][0]
        assert item.get("dbXrefs") is None
        assert item.get("dbXrefsCount") is None
        mock_bulk_xrefs.assert_not_called()
        mock_bulk_counts.assert_not_called()


# === Status mode (visibility) ===


def _extract_status_filter(body: dict[str, Any]) -> dict[str, Any]:
    """Pull the status filter clause out of an ES query body."""
    filters: list[dict[str, Any]] = body["query"]["bool"]["filter"]
    for f in filters:
        if "term" in f and "status" in f["term"]:
            return f
        if "terms" in f and "status" in f["terms"]:
            return f
    raise AssertionError(f"No status filter found in {filters}")


class TestEntriesStatusMode:
    """keywords が accession ID に完全一致するとき suppressed を許可する。

    docs/api-spec.md § データ可視性 (status 制御) の判定ルールが
    /entries/ router で正しく配線されていることを検証する。
    """

    def test_no_keywords_uses_public_only(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        app_with_es.get("/entries/")
        body = mock_es_search.call_args[0][2]
        assert _extract_status_filter(body) == {"term": {"status": "public"}}

    def test_free_text_uses_public_only(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        app_with_es.get("/entries/", params={"keywords": "cancer"})
        body = mock_es_search.call_args[0][2]
        assert _extract_status_filter(body) == {"term": {"status": "public"}}

    @pytest.mark.parametrize(
        "accession",
        [
            "PRJDB1234",
            "SAMD00000001",
            "DRA000001",
            "JGAS000001",
        ],
    )
    def test_accession_exact_match_allows_suppressed(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
        accession: str,
    ) -> None:
        app_with_es.get("/entries/", params={"keywords": accession})
        body = mock_es_search.call_args[0][2]
        assert _extract_status_filter(body) == {
            "terms": {"status": ["public", "suppressed"]},
        }

    def test_accession_with_quotes_allows_suppressed(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        app_with_es.get("/entries/", params={"keywords": '"PRJDB1234"'})
        body = mock_es_search.call_args[0][2]
        assert _extract_status_filter(body) == {
            "terms": {"status": ["public", "suppressed"]},
        }

    def test_accession_with_other_filter_still_allows_suppressed(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        """accession 完全一致 + organism 併用でも suppressed は許可される
        (docs/api-spec.md § データ可視性 のルール)。
        """
        app_with_es.get(
            "/entries/",
            params={"keywords": "PRJDB1234", "organism": "9606"},
        )
        body = mock_es_search.call_args[0][2]
        assert _extract_status_filter(body) == {
            "terms": {"status": ["public", "suppressed"]},
        }

    def test_multi_token_keywords_uses_public_only(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        """accession を含んでいても複数トークンなら public のみ。"""
        app_with_es.get("/entries/", params={"keywords": "PRJDB1234,cancer"})
        body = mock_es_search.call_args[0][2]
        assert _extract_status_filter(body) == {"term": {"status": "public"}}

    def test_wildcard_keywords_uses_public_only(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        app_with_es.get("/entries/", params={"keywords": "PRJDB*"})
        body = mock_es_search.call_args[0][2]
        assert _extract_status_filter(body) == {"term": {"status": "public"}}

    def test_non_db_type_accession_uses_public_only(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        """DbType に含まれない accession (GSE, MTBKS 等) は通常キーワード扱い。"""
        app_with_es.get("/entries/", params={"keywords": "GSE12345"})
        body = mock_es_search.call_args[0][2]
        assert _extract_status_filter(body) == {"term": {"status": "public"}}


class TestFacetsAlwaysPublicOnly:
    """/facets 系は常に public_only で集計する。"""

    def test_facets_uses_public_only(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        """includeFacets=true の場合も public_only が適用される
        (/entries/ の facets は /entries/ 系のルールが優先する前提、
        今回の仕様では keywords が accession なら suppressed も
        検索結果に含まれる)。"""
        # keywords が accession 完全一致なら suppressed 許可が entries 側の挙動
        app_with_es.get(
            "/entries/",
            params={"keywords": "cancer", "includeFacets": "true"},
        )
        body = mock_es_search.call_args[0][2]
        assert _extract_status_filter(body) == {"term": {"status": "public"}}
        # facet aggs には status が含まれない
        if "aggs" in body:
            assert "status" not in body["aggs"]


# ===================================================================
# Type group filter: cross-type rejection / type group commonality
# ===================================================================


def _es_filters(call_args: Any) -> list[dict[str, Any]]:
    """Pull bool.filter clauses from the ES query body of the latest call."""
    body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
    return list(body["query"]["bool"]["filter"])


class TestEntriesCrossTypeRejections:
    """Cross-type endpoint (`GET /entries/`) は type-specific filter /
    型グループ限定 nested / text match を 422 で拒否する
    (docs/api-spec.md § エンドポイント固有のパラメータ)。"""

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
            ("projectType", "metagenome"),
            ("host", "Homo sapiens"),
            ("strain", "K12"),
            ("isolate", "patient-1"),
            ("geoLocName", "Japan"),
            ("collectionDate", "2020"),
            ("libraryName", "lib1"),
            ("libraryConstructionProtocol", "PCR-free"),
            ("vendor", "Illumina"),
        ],
    )
    def test_type_specific_filter_rejected_on_cross_type(
        self,
        app_with_es: TestClient,
        param: tuple[str, str],
    ) -> None:
        name, value = param
        resp = app_with_es.get(f"/entries/?{name}={value}")
        assert resp.status_code == 422


class TestEntriesCrossTypeNestedAccepted:
    """organization / publication / grant は cross-type endpoint でも
    受け付けられ、ES query body に nested clause として反映される。"""

    @pytest.mark.parametrize(
        ("name", "nested_path", "sub_field"),
        [
            ("organization", "organization", "organization.name"),
            ("publication", "publication", "publication.title"),
            ("grant", "grant", "grant.title"),
        ],
    )
    def test_common_nested_filter_reflected(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
        name: str,
        nested_path: str,
        sub_field: str,
    ) -> None:
        resp = app_with_es.get(f"/entries/?{name}=DDBJ")
        assert resp.status_code == 200
        nested = [c for c in _es_filters(mock_es_search.call_args) if "nested" in c]
        match = [c for c in nested if c["nested"]["path"] == nested_path]
        assert len(match) == 1
        assert match[0]["nested"]["query"] == {"match": {sub_field: "DDBJ"}}


class TestEntriesTypeGroupCommonality:
    """型グループ内の各 type で同じ type-specific param が受理される
    (docs/api-spec.md § エンドポイント固有のパラメータ — 型グループ単位)。

    field を持たない type は ES 側で match なしになるが、router 層では
    422 にせず ES query に渡すことを確認する。
    """

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
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
        endpoint: str,
    ) -> None:
        resp = app_with_es.get(f"/entries/{endpoint}/?libraryStrategy=WGS")
        assert resp.status_code == 200
        filters = _es_filters(mock_es_search.call_args)
        assert any("libraryStrategy.keyword" in (f.get("term") or {}) for f in filters)

    @pytest.mark.parametrize(
        "endpoint",
        ["jga-study", "jga-dataset", "jga-policy", "jga-dac"],
    )
    def test_jga_group_accepts_study_type(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
        endpoint: str,
    ) -> None:
        resp = app_with_es.get(f"/entries/{endpoint}/?studyType=GWAS")
        assert resp.status_code == 200
        filters = _es_filters(mock_es_search.call_args)
        assert any("studyType.keyword" in (f.get("term") or {}) for f in filters)


class TestEntriesTypeGroupRejections:
    """型グループ外の param は 422 (docs/api-spec.md § 型グループ単位)。"""

    @pytest.mark.parametrize(
        ("endpoint", "param"),
        [
            # bioproject endpoint は sra/biosample/jga 系を拒否
            ("bioproject", "libraryStrategy"),
            ("bioproject", "host"),
            ("bioproject", "studyType"),
            # biosample endpoint は sra/jga 系を拒否
            ("biosample", "libraryStrategy"),
            ("biosample", "platform"),
            ("biosample", "studyType"),
            ("biosample", "objectTypes"),
            # sra-* endpoint は biosample-only / bioproject 系を拒否
            ("sra-experiment", "objectTypes"),
            ("sra-experiment", "host"),
            ("sra-experiment", "strain"),
            # jga-* endpoint は sra-only を拒否
            ("jga-study", "libraryStrategy"),
            ("jga-study", "host"),
            ("jga-study", "objectTypes"),
            # gea / metabobank も型グループ外を拒否
            ("gea", "objectTypes"),
            ("gea", "libraryStrategy"),
            ("gea", "studyType"),
            ("metabobank", "objectTypes"),
            ("metabobank", "libraryStrategy"),
            ("metabobank", "host"),
        ],
    )
    def test_out_of_group_param_returns_422(
        self,
        app_with_es: TestClient,
        endpoint: str,
        param: str,
    ) -> None:
        resp = app_with_es.get(f"/entries/{endpoint}/?{param}=X")
        assert resp.status_code == 422


class TestEntriesNewNestedFilters:
    """型グループ限定 nested (externalLinkLabel / derivedFromId) が
    type-specific endpoint で ES query に nested として反映される。"""

    def test_external_link_label_on_bioproject(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        resp = app_with_es.get("/entries/bioproject/?externalLinkLabel=GEO")
        assert resp.status_code == 200
        nested = [c for c in _es_filters(mock_es_search.call_args) if "nested" in c]
        match = [c for c in nested if c["nested"]["path"] == "externalLink"]
        assert len(match) == 1
        assert match[0]["nested"]["query"] == {"match": {"externalLink.label": "GEO"}}

    def test_external_link_label_on_jga_study(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        resp = app_with_es.get("/entries/jga-study/?externalLinkLabel=dbGaP")
        assert resp.status_code == 200
        nested = [c for c in _es_filters(mock_es_search.call_args) if "nested" in c]
        assert any(c["nested"]["path"] == "externalLink" for c in nested)

    def test_derived_from_id_on_biosample(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        resp = app_with_es.get("/entries/biosample/?derivedFromId=SAMD00012345")
        assert resp.status_code == 200
        nested = [c for c in _es_filters(mock_es_search.call_args) if "nested" in c]
        match = [c for c in nested if c["nested"]["path"] == "derivedFrom"]
        assert len(match) == 1
        assert match[0]["nested"]["query"] == {"match": {"derivedFrom.identifier": "SAMD00012345"}}

    def test_derived_from_id_on_sra_experiment(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        resp = app_with_es.get("/entries/sra-experiment/?derivedFromId=SAMD00001")
        assert resp.status_code == 200
        nested = [c for c in _es_filters(mock_es_search.call_args) if "nested" in c]
        assert any(c["nested"]["path"] == "derivedFrom" for c in nested)


class TestEntriesTermFilterReflected:
    """型グループ別の term filter が ES query body に反映される。"""

    @pytest.mark.parametrize(
        ("endpoint", "param", "es_field", "value"),
        [
            ("sra-experiment", "libraryStrategy", "libraryStrategy.keyword", "WGS"),
            ("sra-experiment", "librarySource", "librarySource.keyword", "GENOMIC"),
            ("sra-experiment", "librarySelection", "librarySelection.keyword", "RANDOM"),
            ("sra-experiment", "platform", "platform.keyword", "ILLUMINA"),
            ("sra-experiment", "instrumentModel", "instrumentModel.keyword", "HiSeq"),
            ("sra-experiment", "libraryLayout", "libraryLayout.keyword", "PAIRED"),
            ("sra-analysis", "analysisType", "analysisType.keyword", "ALIGNMENT"),
            ("jga-study", "studyType", "studyType.keyword", "GWAS"),
            ("jga-dataset", "datasetType", "datasetType.keyword", "WGS"),
            ("gea", "experimentType", "experimentType.keyword", "RNA-Seq"),
            ("metabobank", "submissionType", "submissionType.keyword", "open"),
        ],
    )
    def test_term_filter_reflected(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
        endpoint: str,
        param: str,
        es_field: str,
        value: str,
    ) -> None:
        resp = app_with_es.get(f"/entries/{endpoint}/?{param}={value}")
        assert resp.status_code == 200
        filters = _es_filters(mock_es_search.call_args)
        match = [f for f in filters if "term" in f and es_field in f["term"]]
        assert len(match) == 1
        assert match[0]["term"][es_field] == value


class TestEntriesTextMatchReflected:
    """型グループ別の text match が ES query body に反映され、auto-phrase
    機構を通って ``match`` / ``match_phrase`` が組まれる。"""

    @pytest.mark.parametrize(
        ("endpoint", "param", "es_field"),
        [
            ("bioproject", "projectType", "projectType"),
            ("biosample", "host", "host"),
            ("biosample", "strain", "strain"),
            ("biosample", "isolate", "isolate"),
            ("biosample", "geoLocName", "geoLocName"),
            ("biosample", "collectionDate", "collectionDate"),
            ("sra-experiment", "libraryName", "libraryName"),
            ("sra-experiment", "libraryConstructionProtocol", "libraryConstructionProtocol"),
            ("jga-study", "vendor", "vendor"),
        ],
    )
    def test_text_match_reflected_with_simple_token(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
        endpoint: str,
        param: str,
        es_field: str,
    ) -> None:
        # 単一 token (記号なし) は ``match`` clause + operator=and を組む
        resp = app_with_es.get(f"/entries/{endpoint}/?{param}=cancer")
        assert resp.status_code == 200
        filters = _es_filters(mock_es_search.call_args)
        match = [f for f in filters if isinstance(f.get("match"), dict) and es_field in f["match"]]
        assert len(match) == 1
        assert match[0]["match"][es_field]["query"] == "cancer"
        assert match[0]["match"][es_field]["operator"] == "and"

    def test_text_match_auto_phrase_on_hyphen(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        """``host=HIF-1`` は記号 ``-`` を含むので auto-phrase で
        ``match_phrase`` clause を作る (docs/api-spec.md § text match)。"""
        resp = app_with_es.get("/entries/biosample/?host=HIF-1")
        assert resp.status_code == 200
        filters = _es_filters(mock_es_search.call_args)
        phrase = [f for f in filters if isinstance(f.get("match_phrase"), dict) and "host" in f["match_phrase"]]
        assert len(phrase) == 1
        assert phrase[0]["match_phrase"]["host"] == "HIF-1"

    def test_text_match_keyword_operator_or_propagates(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        """``keywordOperator=OR`` は text match の operator にも伝搬する
        (docs/api-spec.md § text match — keywordOperator 連動)。"""
        resp = app_with_es.get("/entries/biosample/?host=Homo sapiens&keywordOperator=OR")
        assert resp.status_code == 200
        filters = _es_filters(mock_es_search.call_args)
        match = [f for f in filters if isinstance(f.get("match"), dict) and "host" in f["match"]]
        assert len(match) == 1
        assert match[0]["match"]["host"]["operator"] == "or"


class TestEntriesFacetsPick:
    """``facets`` クエリパラメータが entries 側 (`includeFacets=true`) でも
    動く (router の wiring 確認、build_facet_aggs のロジック自体は
    es/test_query.py で網羅)。"""

    def test_default_returns_common_facets_only(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        mock_es_search.return_value = make_es_search_response(
            aggregations={
                "type": {"buckets": []},
                "organism": {"buckets": []},
                "accessibility": {"buckets": []},
            },
        )
        app_with_es.get("/entries/?includeFacets=true")
        body = mock_es_search.call_args[0][2]
        assert set(body["aggs"]) == {"organism", "accessibility", "type"}

    def test_explicit_facets_replaces_default(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        """明示指定は完全置換 (docs/api-spec.md § ファセット集計対象の選択)。
        common facet (organism/accessibility) は自動付与されない。"""
        mock_es_search.return_value = make_es_search_response(
            aggregations={"objectType": {"buckets": []}},
        )
        app_with_es.get("/entries/bioproject/?includeFacets=true&facets=objectType")
        body = mock_es_search.call_args[0][2]
        assert set(body["aggs"]) == {"objectType"}

    def test_empty_facets_yields_no_aggs(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        app_with_es.get("/entries/bioproject/?includeFacets=true&facets=")
        body = mock_es_search.call_args[0][2]
        assert "aggs" not in body

    def test_typo_returns_422(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/?includeFacets=true&facets=organisms")
        assert resp.status_code == 422

    def test_type_mismatch_returns_400(self, app_with_es: TestClient) -> None:
        """`/entries/bioproject/` で sra-experiment 専用 facet を投げると
        400 (allowlist は通るが applicability で reject)。"""
        resp = app_with_es.get(
            "/entries/bioproject/?includeFacets=true&facets=libraryStrategy",
        )
        assert resp.status_code == 400

    def test_cross_type_accepts_any_allowlisted_facet(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        """cross-type endpoint は any allowlisted facet を受理する
        (該当 index でのみ集計、他は空 buckets)。"""
        resp = app_with_es.get("/entries/?includeFacets=true&facets=libraryStrategy")
        assert resp.status_code == 200
        body = mock_es_search.call_args[0][2]
        assert "libraryStrategy" in body["aggs"]

    def test_facets_ignored_when_include_facets_false(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        """``facets=...`` 指定があっても ``includeFacets=false`` なら
        集計しない (docs/api-spec.md § ファセット集計対象の選択)。"""
        app_with_es.get("/entries/bioproject/?facets=objectType")
        body = mock_es_search.call_args[0][2]
        assert "aggs" not in body
