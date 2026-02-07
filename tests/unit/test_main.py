"""Tests for ddbj_search_api.main.

Tests the app factory, X-Request-ID middleware, CORS headers,
error handlers, and OpenAPI customisation.
"""
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ddbj_search_api.config import AppConfig
from ddbj_search_api.main import create_app


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

    def test_generates_uuid_when_not_provided(
        self, app: TestClient
    ) -> None:
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

    @pytest.fixture()
    def app_with_not_implemented(self) -> TestClient:
        """Create an app with a route that raises NotImplementedError."""
        application = create_app(AppConfig())

        @application.get("/test-not-implemented")
        async def _raise_not_implemented():  # type: ignore[no-untyped-def]
            raise NotImplementedError

        return TestClient(application, raise_server_exceptions=False)

    def test_status_code_501(
        self, app_with_not_implemented: TestClient,
    ) -> None:
        resp = app_with_not_implemented.get("/test-not-implemented")
        assert resp.status_code == 501

    def test_problem_details_structure(
        self, app_with_not_implemented: TestClient,
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
        self, app_with_not_implemented: TestClient,
    ) -> None:
        resp = app_with_not_implemented.get("/test-not-implemented")
        assert "application/problem+json" in resp.headers["content-type"]

    def test_request_id_in_body_matches_header(
        self, app_with_not_implemented: TestClient,
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

    def test_validation_error_has_problem_details(
        self, app: TestClient
    ) -> None:
        resp = app.get("/entries/", params={"perPage": -1})
        body = resp.json()
        assert body["status"] == 422
        assert body["title"] == "Unprocessable Entity"
        assert "detail" in body

    def test_invalid_db_type_returns_422(self, app: TestClient) -> None:
        resp = app.get("/entries/invalid-type/PRJDB1")
        assert resp.status_code == 422


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
