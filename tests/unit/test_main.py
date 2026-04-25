"""Tests for ddbj_search_api.main.

Tests the app factory, X-Request-ID middleware, CORS headers,
error handlers, and OpenAPI customisation.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ddbj_search_api.config import AppConfig
from ddbj_search_api.main import create_app
from tests._required_list_fields import (
    REQUIRED_LIST_FIELDS_BIOPROJECT,
    REQUIRED_LIST_FIELDS_BIOSAMPLE,
    REQUIRED_LIST_FIELDS_GEA,
    REQUIRED_LIST_FIELDS_JGA,
    REQUIRED_LIST_FIELDS_METABOBANK,
    REQUIRED_LIST_FIELDS_SRA,
)

# === App factory ===


class TestCreateApp:
    """create_app: returns a configured FastAPI instance."""

    def test_returns_fastapi_instance(self) -> None:
        app = create_app(AppConfig())
        assert isinstance(app, FastAPI)

    def test_title(self) -> None:
        app = create_app(AppConfig())
        assert app.title == "DDBJ Search API"

    def test_root_path_matches_url_prefix(self) -> None:
        config = AppConfig(url_prefix="/custom/prefix")
        app = create_app(config)
        assert app.root_path == "/custom/prefix"

    def test_redirect_slashes_disabled(self) -> None:
        app = create_app(AppConfig())
        assert app.router.redirect_slashes is False


# === X-Request-ID middleware ===


class TestXRequestIdMiddleware:
    """X-Request-ID: generated or echoed in every response."""

    def test_generates_uuid_when_not_provided(self, app: TestClient) -> None:
        resp = app.get("/service-info")
        request_id = resp.headers.get("X-Request-ID")
        assert request_id is not None
        uuid.UUID(request_id)  # raises ValueError if not valid UUID

    def test_echoes_client_provided_id(self, app: TestClient) -> None:
        resp = app.get(
            "/service-info",
            headers={"X-Request-ID": "my-custom-id"},
        )
        assert resp.headers["X-Request-ID"] == "my-custom-id"

    def test_present_on_error_responses(self, app: TestClient) -> None:
        resp = app.get("/nonexistent-path")
        assert "X-Request-ID" in resp.headers


# === CORS ===


class TestCORS:
    """CORS headers: allow all origins."""

    def test_access_control_allow_origin(self, app: TestClient) -> None:
        resp = app.get(
            "/service-info",
            headers={"Origin": "https://example.com"},
        )
        assert resp.headers.get("access-control-allow-origin") == "*"

    def test_preflight_request(self, app: TestClient) -> None:
        resp = app.options(
            "/service-info",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-Request-ID",
            },
        )
        assert resp.headers.get("access-control-allow-origin") == "*"
        assert "GET" in resp.headers.get("access-control-allow-methods", "")


# === Error handlers ===


class TestErrorHandlerNotImplemented:
    """NotImplementedError -> 501 with RFC 7807 ProblemDetails.

    Uses a temporary route that raises NotImplementedError, since all
    real endpoints are now implemented.
    """

    @pytest.fixture
    def app_with_not_implemented(self) -> TestClient:
        """Create an app with a route that raises NotImplementedError."""
        application = create_app(AppConfig())

        @application.get("/test-not-implemented")
        async def _raise_not_implemented() -> None:
            raise NotImplementedError

        return TestClient(application, raise_server_exceptions=False)

    def test_status_code_501(
        self,
        app_with_not_implemented: TestClient,
    ) -> None:
        resp = app_with_not_implemented.get("/test-not-implemented")
        assert resp.status_code == 501

    def test_problem_details_structure(
        self,
        app_with_not_implemented: TestClient,
    ) -> None:
        resp = app_with_not_implemented.get("/test-not-implemented")
        body = resp.json()
        assert body["type"] == "about:blank"
        assert body["title"] == "Not Implemented"
        assert body["status"] == 501
        assert "detail" in body
        assert "instance" in body
        assert "timestamp" in body
        assert "requestId" in body

    def test_content_type_is_problem_json(
        self,
        app_with_not_implemented: TestClient,
    ) -> None:
        resp = app_with_not_implemented.get("/test-not-implemented")
        assert "application/problem+json" in resp.headers["content-type"]

    def test_request_id_in_body_matches_header(
        self,
        app_with_not_implemented: TestClient,
    ) -> None:
        resp = app_with_not_implemented.get(
            "/test-not-implemented",
            headers={"X-Request-ID": "test-req-id"},
        )
        body = resp.json()
        assert body["requestId"] == "test-req-id"
        assert resp.headers["X-Request-ID"] == "test-req-id"


class TestErrorHandlerValidation:
    """RequestValidationError -> 422 with RFC 7807 ProblemDetails."""

    def test_invalid_per_page_returns_422(self, app: TestClient) -> None:
        resp = app.get("/entries/", params={"perPage": -1})
        assert resp.status_code == 422

    def test_validation_error_has_problem_details(self, app: TestClient) -> None:
        resp = app.get("/entries/", params={"perPage": -1})
        body = resp.json()
        assert body["status"] == 422
        assert body["title"] == "Unprocessable Entity"
        assert "detail" in body

    def test_invalid_db_type_returns_404(self, app: TestClient) -> None:
        """Invalid DB type in path returns 404 Not Found."""
        resp = app.get("/entries/invalid-type/PRJDB1")
        assert resp.status_code == 404
        body = resp.json()
        assert body["status"] == 404
        assert "invalid-type" in body["detail"]


class TestErrorHandlerNotFound:
    """404 for truly unknown paths."""

    def test_unknown_path_returns_404(self, app: TestClient) -> None:
        resp = app.get("/completely-unknown")
        assert resp.status_code == 404

    def test_404_has_problem_details(self, app: TestClient) -> None:
        resp = app.get("/completely-unknown")
        body = resp.json()
        assert body["status"] == 404
        assert "detail" in body


# === OpenAPI customisation ===


class TestOpenAPICustomisation:
    """OpenAPI schema: no FastAPI default validation error schemas."""

    def test_no_http_validation_error_schema(self) -> None:
        app = create_app(AppConfig())
        schema = app.openapi()
        schemas = schema.get("components", {}).get("schemas", {})
        assert "HTTPValidationError" not in schemas

    def test_no_validation_error_schema(self) -> None:
        app = create_app(AppConfig())
        schema = app.openapi()
        schemas = schema.get("components", {}).get("schemas", {})
        assert "ValidationError" not in schemas


# === OpenAPI required array fields ===
#
# converter で list[X] 必須化された項目は、子クラスの *DetailResponse / *EntryJsonLdResponse で
# 再宣言しなくても継承で required になる。SDK 利用者が見るのは OpenAPI スキーマ生成結果なので、
# raw / Detail / JsonLd すべての schema で `required` に含まれることを end-to-end 確認する。

_OPENAPI_REQUIRED_LIST_FIELDS: dict[str, list[str]] = {
    "BioProject": REQUIRED_LIST_FIELDS_BIOPROJECT,
    "BioProjectDetailResponse": REQUIRED_LIST_FIELDS_BIOPROJECT,
    "BioProjectEntryJsonLdResponse": REQUIRED_LIST_FIELDS_BIOPROJECT,
    "BioSample": REQUIRED_LIST_FIELDS_BIOSAMPLE,
    "BioSampleDetailResponse": REQUIRED_LIST_FIELDS_BIOSAMPLE,
    "BioSampleEntryJsonLdResponse": REQUIRED_LIST_FIELDS_BIOSAMPLE,
    "SRA": REQUIRED_LIST_FIELDS_SRA,
    "SraDetailResponse": REQUIRED_LIST_FIELDS_SRA,
    "SraEntryJsonLdResponse": REQUIRED_LIST_FIELDS_SRA,
    "JGA": REQUIRED_LIST_FIELDS_JGA,
    "JgaDetailResponse": REQUIRED_LIST_FIELDS_JGA,
    "JgaEntryJsonLdResponse": REQUIRED_LIST_FIELDS_JGA,
    "GEA": REQUIRED_LIST_FIELDS_GEA,
    "GeaDetailResponse": REQUIRED_LIST_FIELDS_GEA,
    "GeaEntryJsonLdResponse": REQUIRED_LIST_FIELDS_GEA,
    "MetaboBank": REQUIRED_LIST_FIELDS_METABOBANK,
    "MetaboBankDetailResponse": REQUIRED_LIST_FIELDS_METABOBANK,
    "MetaboBankEntryJsonLdResponse": REQUIRED_LIST_FIELDS_METABOBANK,
}


class TestOpenAPIRequiredArrayFields:
    """All entry-related OpenAPI schemas surface converter-required list fields in `required`."""

    @pytest.fixture(scope="class")
    def schema(self) -> dict[str, Any]:
        return create_app(AppConfig()).openapi()

    @pytest.mark.parametrize(
        ("schema_name", "field"),
        [(name, field) for name, fields in _OPENAPI_REQUIRED_LIST_FIELDS.items() for field in fields],
        ids=[f"{name}.{field}" for name, fields in _OPENAPI_REQUIRED_LIST_FIELDS.items() for field in fields],
    )
    def test_field_in_required(
        self,
        schema: dict[str, Any],
        schema_name: str,
        field: str,
    ) -> None:
        target = schema["components"]["schemas"][schema_name]
        required = target.get("required", [])
        assert field in required, f"OpenAPI schema {schema_name}: required does not include {field}"


# === Lifespan: Solr client ===


class TestLifespanSolrClient:
    """Solr client provisioned alongside ES client in the lifespan."""

    def test_solr_client_initialized(self) -> None:
        application = create_app(AppConfig())
        with TestClient(application):
            assert isinstance(application.state.solr_client, httpx.AsyncClient)

    def test_solr_client_closed_after_exit(self) -> None:
        application = create_app(AppConfig())
        with TestClient(application):
            client = application.state.solr_client
        assert client.is_closed is True

    def test_solr_client_timeout_uses_max_of_backend_timeouts(self) -> None:
        """Solr client's client-level timeout is the hard cap shared by ARSA
        and TXSearch; per-call ``asyncio.wait_for`` tightens it further.
        """
        config = AppConfig()
        object.__setattr__(config, "arsa_timeout", 30.0)
        object.__setattr__(config, "txsearch_timeout", 5.0)
        application = create_app(config)
        with TestClient(application):
            assert application.state.solr_client.timeout.read == 30.0

    def test_es_and_solr_clients_are_distinct(self) -> None:
        application = create_app(AppConfig())
        with TestClient(application):
            assert application.state.solr_client is not application.state.es_client
