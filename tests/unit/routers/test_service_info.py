"""Tests for GET /service-info endpoint.

This is the only fully implemented endpoint.
"""
import importlib.metadata

from fastapi.testclient import TestClient


class TestGetServiceInfo:
    """GET /service-info: returns service metadata."""

    def test_status_code_200(self, app: TestClient) -> None:
        resp = app.get("/service-info")
        assert resp.status_code == 200

    def test_response_has_required_fields(self, app: TestClient) -> None:
        body = app.get("/service-info").json()
        assert "name" in body
        assert "version" in body
        assert "description" in body

    def test_name_is_ddbj_search_api(self, app: TestClient) -> None:
        body = app.get("/service-info").json()
        assert body["name"] == "DDBJ Search API"

    def test_version_matches_package(self, app: TestClient) -> None:
        body = app.get("/service-info").json()
        expected = importlib.metadata.version("ddbj-search-api")
        assert body["version"] == expected

    def test_content_type_is_json(self, app: TestClient) -> None:
        resp = app.get("/service-info")
        assert "application/json" in resp.headers["content-type"]

    def test_x_request_id_present(self, app: TestClient) -> None:
        resp = app.get("/service-info")
        assert "X-Request-ID" in resp.headers

    def test_cors_header_present(self, app: TestClient) -> None:
        resp = app.get(
            "/service-info",
            headers={"Origin": "https://example.com"},
        )
        assert resp.headers.get("access-control-allow-origin") == "*"
