"""Tests for Entries API routing and parameter validation.

All handlers currently raise NotImplementedError (501), so these tests
verify routing, trailing slash, and parameter validation at the HTTP level.

Implementation tests (TestEntries*Search, etc.) use mocked ES and
verify the full request → response flow.
"""
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ddbj_search_api.schemas.common import DbType
from tests.unit.conftest import make_es_search_response
from tests.unit.strategies import db_type_values


# === Routing: GET /entries/ ===


class TestEntriesRouting:
    """GET /entries/ and GET /entries: route exists."""

    def test_slash_returns_200(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries/")
        assert resp.status_code == 200

    def test_no_slash_returns_200(self, app_with_es: TestClient) -> None:
        resp = app_with_es.get("/entries")
        assert resp.status_code == 200

    def test_trailing_slash_same_response(
        self, app_with_es: TestClient
    ) -> None:
        r1 = app_with_es.get("/entries/")
        r2 = app_with_es.get("/entries")
        assert r1.status_code == r2.status_code


# === Routing: GET /entries/{type}/ ===


class TestEntriesTypeRouting:
    """GET /entries/{type}/ : all 12 types are routed."""

    @pytest.mark.parametrize("db_type", db_type_values)
    def test_type_route_exists(
        self, app_with_es: TestClient, db_type: str
    ) -> None:
        resp = app_with_es.get(f"/entries/{db_type}/")
        assert resp.status_code == 200

    @pytest.mark.parametrize("db_type", db_type_values)
    def test_type_trailing_slash(
        self, app_with_es: TestClient, db_type: str
    ) -> None:
        r1 = app_with_es.get(f"/entries/{db_type}/")
        r2 = app_with_es.get(f"/entries/{db_type}")
        assert r1.status_code == r2.status_code


# === Pagination parameter validation (FastAPI level) ===


class TestPaginationValidation:
    """perPage and page query parameter validation."""

    def test_per_page_0_returns_422(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get("/entries/", params={"perPage": 0})
        assert resp.status_code == 422

    def test_per_page_1_accepted(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get("/entries/", params={"perPage": 1})
        assert resp.status_code != 422

    def test_per_page_100_accepted(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get("/entries/", params={"perPage": 100})
        assert resp.status_code != 422

    def test_per_page_101_returns_422(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get("/entries/", params={"perPage": 101})
        assert resp.status_code == 422

    def test_per_page_negative_returns_422(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get("/entries/", params={"perPage": -1})
        assert resp.status_code == 422

    def test_page_0_returns_422(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get("/entries/", params={"page": 0})
        assert resp.status_code == 422

    def test_page_1_accepted(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get("/entries/", params={"page": 1})
        assert resp.status_code != 422

    def test_page_negative_returns_422(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get("/entries/", params={"page": -1})
        assert resp.status_code == 422


class TestPaginationValidationPBT:
    """Property-based pagination validation tests."""

    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(per_page=st.integers(max_value=0))
    def test_per_page_le_0_returns_422(
        self, app_with_es: TestClient, per_page: int
    ) -> None:
        resp = app_with_es.get("/entries/", params={"perPage": per_page})
        assert resp.status_code == 422

    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(per_page=st.integers(min_value=101, max_value=10000))
    def test_per_page_gt_100_returns_422(
        self, app_with_es: TestClient, per_page: int
    ) -> None:
        resp = app_with_es.get("/entries/", params={"perPage": per_page})
        assert resp.status_code == 422

    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(page=st.integers(max_value=0))
    def test_page_le_0_returns_422(
        self, app_with_es: TestClient, page: int
    ) -> None:
        resp = app_with_es.get("/entries/", params={"page": page})
        assert resp.status_code == 422


# === Validation error response format ===


class TestValidationErrorFormat:
    """Validation errors return RFC 7807 ProblemDetails."""

    def test_422_has_problem_details_fields(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get("/entries/", params={"perPage": -1})
        body = resp.json()
        assert body["status"] == 422
        assert body["title"] == "Unprocessable Entity"
        assert "detail" in body
        assert "requestId" in body
        assert "timestamp" in body
        assert "instance" in body

    def test_422_content_type_is_problem_json(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get("/entries/", params={"perPage": -1})
        assert "application/problem+json" in resp.headers["content-type"]


# === Invalid type in path ===


class TestInvalidTypeInPath:
    """Invalid {type} in path returns 404."""

    def test_unknown_type_returns_404(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get("/entries/unknown-type/")
        assert resp.status_code == 404


# === Date parameter validation ===


class TestEntriesDateValidation:
    """Date parameter format validation (YYYY-MM-DD only)."""

    def test_valid_date_accepted(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get(
            "/entries/", params={"datePublishedFrom": "2024-01-15"}
        )
        assert resp.status_code == 200

    def test_slash_date_returns_422(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get(
            "/entries/", params={"datePublishedFrom": "2024/01/15"}
        )
        assert resp.status_code == 422

    def test_no_dash_date_returns_422(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get(
            "/entries/", params={"datePublishedFrom": "20240115"}
        )
        assert resp.status_code == 422

    def test_datetime_returns_422(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get(
            "/entries/",
            params={"datePublishedFrom": "2024-01-15T00:00:00"},
        )
        assert resp.status_code == 422


# === Umbrella parameter validation ===


class TestEntriesBioProjectUmbrellaValidation:
    """umbrella parameter validation for BioProject."""

    def test_umbrella_true_accepted(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get(
            "/entries/bioproject/", params={"umbrella": "TRUE"}
        )
        assert resp.status_code == 200

    def test_umbrella_false_accepted(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get(
            "/entries/bioproject/", params={"umbrella": "FALSE"}
        )
        assert resp.status_code == 200

    def test_umbrella_lowercase_accepted(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get(
            "/entries/bioproject/", params={"umbrella": "true"}
        )
        assert resp.status_code == 200

    def test_umbrella_mixed_case_accepted(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get(
            "/entries/bioproject/", params={"umbrella": "True"}
        )
        assert resp.status_code == 200

    def test_umbrella_invalid_returns_422(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get(
            "/entries/bioproject/", params={"umbrella": "invalid"}
        )
        assert resp.status_code == 422

    def test_umbrella_empty_returns_422(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get(
            "/entries/bioproject/", params={"umbrella": ""}
        )
        assert resp.status_code == 422


# === Implementation tests: search flow ===


class TestEntriesSearch:
    """Basic search flow: ES returns results → 200 with correct shape."""

    def test_empty_result_returns_200(
        self, app_with_es: TestClient
    ) -> None:
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
        term_filters = [
            f for f in filters
            if "term" in f and "organism.identifier" in f["term"]
        ]
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

    def test_keyword_operator_invalid_returns_422(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get(
            "/entries/", params={"keywordOperator": "INVALID"}
        )
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
            hits=[{
                "_source": {
                    "identifier": "PRJDB1",
                    "type": "bioproject",
                    "properties": {"key": "val"},
                },
                "fields": {},
            }],
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
            hits=[{
                "_source": {
                    "identifier": "PRJDB1",
                    "type": "bioproject",
                },
                "fields": {},
            }],
            total=1,
        )
        resp = app_with_es.get(
            "/entries/", params={"includeProperties": "false"}
        )
        body = resp.json()
        assert "properties" not in body["items"][0]

    def test_include_properties_false_sends_source_excludes(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        app_with_es.get(
            "/entries/", params={"includeProperties": "false"}
        )
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
        resp = app_with_es.get(
            "/entries/", params={"types": "bioproject,biosample"}
        )
        assert resp.status_code == 200
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        filters = body["query"]["bool"]["filter"]
        type_filter = [
            f for f in filters
            if "terms" in f and "type" in f["terms"]
        ]
        assert len(type_filter) == 1
        assert set(type_filter[0]["terms"]["type"]) == {
            "bioproject", "biosample",
        }

    def test_single_type_filter(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        resp = app_with_es.get(
            "/entries/", params={"types": "bioproject"}
        )
        assert resp.status_code == 200

    def test_empty_types_uses_match_all(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        """Empty types string is treated as no filter."""
        app_with_es.get("/entries/", params={"types": ""})
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        assert body["query"] == {"match_all": {}}

    def test_invalid_type_returns_422(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get(
            "/entries/", params={"types": "invalid-type"}
        )
        assert resp.status_code == 422

    def test_mixed_valid_invalid_types_returns_422(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get(
            "/entries/", params={"types": "bioproject,invalid"}
        )
        assert resp.status_code == 422

    def test_all_12_types_accepted(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        all_types = ",".join(db_type_values)
        resp = app_with_es.get(
            "/entries/", params={"types": all_types}
        )
        assert resp.status_code == 200


# === Empty/whitespace keywords ===


class TestEntriesEmptyKeywords:
    """Empty and whitespace-only keywords behaviour."""

    def test_empty_keywords_treated_as_no_filter(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        resp = app_with_es.get("/entries/", params={"keywords": ""})
        assert resp.status_code == 200
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        assert body["query"] == {"match_all": {}}

    def test_whitespace_keywords_treated_as_no_filter(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        resp = app_with_es.get("/entries/", params={"keywords": "   "})
        assert resp.status_code == 200
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        assert body["query"] == {"match_all": {}}

    def test_comma_only_keywords_treated_as_no_filter(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        resp = app_with_es.get("/entries/", params={"keywords": ","})
        assert resp.status_code == 200
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        assert body["query"] == {"match_all": {}}


# === Deep paging ===


class TestEntriesDeepPaging:
    """Deep paging limit: page * perPage > 10000 → 400."""

    def test_page_100_per_page_100_ok(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get(
            "/entries/", params={"page": 100, "perPage": 100}
        )
        assert resp.status_code == 200

    def test_page_101_per_page_100_returns_400(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get(
            "/entries/", params={"page": 101, "perPage": 100}
        )
        assert resp.status_code == 400

    @settings(
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        max_examples=30,
    )
    @given(
        data=st.data(),
    )
    def test_pbt_deep_paging_rejected(
        self, app_with_es: TestClient, data: st.DataObject
    ) -> None:
        per_page = data.draw(st.integers(min_value=1, max_value=100))
        # Ensure page * per_page > 10000
        min_page = (10000 // per_page) + 1
        page = data.draw(
            st.integers(min_value=min_page, max_value=min_page + 1000)
        )
        resp = app_with_es.get(
            "/entries/", params={"page": page, "perPage": per_page}
        )
        assert resp.status_code == 400


# === Sort validation ===


class TestEntriesSortValidation:
    """sort parameter validation."""

    def test_valid_sort_accepted(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get(
            "/entries/", params={"sort": "datePublished:asc"}
        )
        assert resp.status_code == 200

    def test_valid_sort_date_modified(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get(
            "/entries/", params={"sort": "dateModified:desc"}
        )
        assert resp.status_code == 200

    def test_invalid_sort_field_returns_422(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get(
            "/entries/", params={"sort": "invalidField:asc"}
        )
        assert resp.status_code == 422

    def test_invalid_sort_format_returns_422(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get(
            "/entries/", params={"sort": "bad-format"}
        )
        assert resp.status_code == 422

    def test_sort_passed_to_es(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        app_with_es.get(
            "/entries/", params={"sort": "datePublished:asc"}
        )
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        assert "sort" in body
        assert body["sort"] == [{"datePublished": {"order": "asc"}}]


# === keywordFields validation ===


class TestEntriesKeywordFieldsValidation:
    """keywordFields parameter validation."""

    def test_invalid_keyword_fields_returns_422(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get(
            "/entries/",
            params={"keywords": "test", "keywordFields": "badField"},
        )
        assert resp.status_code == 422

    def test_valid_keyword_fields_accepted(
        self, app_with_es: TestClient
    ) -> None:
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
                "status": {"buckets": []},
                "accessibility": {"buckets": []},
            },
        )
        resp = app_with_es.get(
            "/entries/", params={"includeFacets": "true"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["facets"] is not None
        assert "organism" in body["facets"]

    def test_include_facets_false(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get(
            "/entries/", params={"includeFacets": "false"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["facets"] is None

    def test_include_facets_default_false(
        self, app_with_es: TestClient
    ) -> None:
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
                "status": {"buckets": []},
                "accessibility": {"buckets": []},
            },
        )
        app_with_es.get(
            "/entries/", params={"includeFacets": "true"}
        )
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

    def test_bioproject_extra_params(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        app_with_es.get(
            "/entries/bioproject/",
            params={"umbrella": "TRUE"},
        )
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        query = body["query"]
        # umbrella=TRUE → filter has objectType term
        assert query != {"match_all": {}}

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
                "status": {"buckets": []},
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

    Search results now use script_fields: ES returns ``_source`` without
    dbXrefs and ``fields`` with ``dbXrefsTruncated`` / ``dbXrefsCountByType``.
    """

    def test_db_xrefs_truncated(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        truncated = [
            {"type": "biosample", "identifier": f"SAMD{i}"}
            for i in range(10)
        ]
        mock_es_search.return_value = make_es_search_response(
            hits=[{
                "_source": {
                    "identifier": "PRJDB1",
                    "type": "bioproject",
                },
                "fields": {
                    "dbXrefsTruncated": truncated,
                    "dbXrefsCountByType": [{"biosample": 200}],
                },
            }],
            total=1,
        )
        resp = app_with_es.get(
            "/entries/", params={"dbXrefsLimit": 10}
        )
        assert resp.status_code == 200
        body = resp.json()
        item = body["items"][0]
        assert len(item["dbXrefs"]) == 10
        assert item["dbXrefsCount"]["biosample"] == 200

    def test_db_xrefs_limit_0(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        mock_es_search.return_value = make_es_search_response(
            hits=[{
                "_source": {
                    "identifier": "PRJDB1",
                    "type": "bioproject",
                },
                "fields": {
                    "dbXrefsCountByType": [{"biosample": 50}],
                },
            }],
            total=1,
        )
        resp = app_with_es.get(
            "/entries/", params={"dbXrefsLimit": 0}
        )
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
        truncated = (
            [{"type": "biosample", "identifier": f"SAMD{i}"} for i in range(5)]
            + [{"type": "sra-study", "identifier": f"SRP{i}"} for i in range(3)]
        )
        mock_es_search.return_value = make_es_search_response(
            hits=[{
                "_source": {
                    "identifier": "PRJDB1",
                    "type": "bioproject",
                },
                "fields": {
                    "dbXrefsTruncated": truncated,
                    "dbXrefsCountByType": [{"biosample": 5, "sra-study": 3}],
                },
            }],
            total=1,
        )
        resp = app_with_es.get(
            "/entries/", params={"dbXrefsLimit": 100}
        )
        assert resp.status_code == 200
        body = resp.json()
        item = body["items"][0]
        assert item["dbXrefsCount"]["biosample"] == 5
        assert item["dbXrefsCount"]["sra-study"] == 3

    def test_no_db_xrefs_in_source(
        self,
        app_with_es: TestClient,
        mock_es_search: AsyncMock,
    ) -> None:
        """Entry without dbXrefs: script_fields return empty defaults."""
        mock_es_search.return_value = make_es_search_response(
            hits=[{
                "_source": {
                    "identifier": "PRJDB1",
                    "type": "bioproject",
                    "title": "No xrefs",
                },
                "fields": {},
            }],
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
                "status": {"buckets": []},
                "accessibility": {"buckets": []},
            },
        )
        app_with_es.get(
            "/entries/", params={"includeFacets": "true"}
        )
        call_args = mock_es_search.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        aggs = body["aggs"]
        for agg_name in ("organism", "status", "accessibility", "type"):
            assert aggs[agg_name]["terms"]["size"] == 50, (
                f"{agg_name} should have size=50"
            )


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

    def test_from_after_to_accepted(
        self, app_with_es: TestClient
    ) -> None:
        """from > to is accepted (ES returns 0 results, not an error)."""
        resp = app_with_es.get(
            "/entries/",
            params={
                "datePublishedFrom": "2025-12-31",
                "datePublishedTo": "2024-01-01",
            },
        )
        assert resp.status_code == 200

    def test_date_modified_from_after_to_accepted(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get(
            "/entries/",
            params={
                "dateModifiedFrom": "2025-06-01",
                "dateModifiedTo": "2024-06-01",
            },
        )
        assert resp.status_code == 200

    def test_same_from_and_to_accepted(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get(
            "/entries/",
            params={
                "datePublishedFrom": "2024-06-15",
                "datePublishedTo": "2024-06-15",
            },
        )
        assert resp.status_code == 200

    def test_feb_30_returns_422(
        self, app_with_es: TestClient
    ) -> None:
        """Feb 30 passes regex but fails semantic date validation."""
        resp = app_with_es.get(
            "/entries/", params={"datePublishedFrom": "2024-02-30"}
        )
        assert resp.status_code == 422

    def test_month_13_returns_422(
        self, app_with_es: TestClient
    ) -> None:
        """Month 13 passes regex but fails semantic date validation."""
        resp = app_with_es.get(
            "/entries/", params={"datePublishedFrom": "2024-13-01"}
        )
        assert resp.status_code == 422

    def test_feb_29_leap_year_accepted(
        self, app_with_es: TestClient
    ) -> None:
        """Feb 29 in a leap year is a valid date."""
        resp = app_with_es.get(
            "/entries/", params={"datePublishedFrom": "2024-02-29"}
        )
        assert resp.status_code == 200

    def test_feb_29_non_leap_year_returns_422(
        self, app_with_es: TestClient
    ) -> None:
        """Feb 29 in a non-leap year is invalid."""
        resp = app_with_es.get(
            "/entries/", params={"datePublishedFrom": "2023-02-29"}
        )
        assert resp.status_code == 422

    def test_day_00_returns_422(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get(
            "/entries/", params={"datePublishedFrom": "2024-01-00"}
        )
        assert resp.status_code == 422

    def test_only_from_accepted(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get(
            "/entries/", params={"datePublishedFrom": "2024-01-01"}
        )
        assert resp.status_code == 200

    def test_only_to_accepted(
        self, app_with_es: TestClient
    ) -> None:
        resp = app_with_es.get(
            "/entries/", params={"datePublishedTo": "2024-12-31"}
        )
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
    def test_valid_pagination_always_accepted(
        self, app_with_es: TestClient, page: int, per_page: int
    ) -> None:
        """Any valid page/perPage within range returns 200 or 400 (deep paging)."""
        resp = app_with_es.get(
            "/entries/", params={"page": page, "perPage": per_page}
        )
        if page * per_page > 10000:
            assert resp.status_code == 400
        else:
            assert resp.status_code == 200

    @settings(
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        max_examples=20,
    )
    @given(
        sort=st.sampled_from([
            "datePublished:asc", "datePublished:desc",
            "dateModified:asc", "dateModified:desc",
        ]),
    )
    def test_valid_sort_always_accepted(
        self, app_with_es: TestClient, sort: str
    ) -> None:
        resp = app_with_es.get("/entries/", params={"sort": sort})
        assert resp.status_code == 200

    @settings(
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        max_examples=20,
    )
    @given(
        field=st.text(
            alphabet=st.characters(whitelist_categories=("L",)),
            min_size=1, max_size=10,
        ),
    )
    def test_invalid_sort_field_always_422(
        self, app_with_es: TestClient, field: str
    ) -> None:
        if field in ("datePublished", "dateModified"):
            return  # skip valid fields
        resp = app_with_es.get(
            "/entries/", params={"sort": f"{field}:asc"}
        )
        assert resp.status_code == 422

    @settings(
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        max_examples=20,
    )
    @given(
        operator=st.sampled_from(["AND", "OR"]),
    )
    def test_valid_keyword_operator_accepted(
        self, app_with_es: TestClient, operator: str
    ) -> None:
        resp = app_with_es.get(
            "/entries/",
            params={"keywords": "test", "keywordOperator": operator},
        )
        assert resp.status_code == 200
