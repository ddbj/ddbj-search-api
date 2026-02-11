"""Tests for GET /service-info endpoint."""

from __future__ import annotations

import importlib.metadata
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


class TestGetServiceInfo:
    """GET /service-info: returns service metadata."""

    def test_status_code_200(self, app: TestClient) -> None:
        with patch(
            "ddbj_search_api.routers.service_info.es_ping",
            new_callable=AsyncMock,
            return_value=True,
        ):
            resp = app.get("/service-info")
        assert resp.status_code == 200

    def test_response_has_required_fields(self, app: TestClient) -> None:
        with patch(
            "ddbj_search_api.routers.service_info.es_ping",
            new_callable=AsyncMock,
            return_value=True,
        ):
            body = app.get("/service-info").json()
        assert "name" in body
        assert "version" in body
        assert "description" in body
        assert "elasticsearch" in body

    def test_name_is_ddbj_search_api(self, app: TestClient) -> None:
        with patch(
            "ddbj_search_api.routers.service_info.es_ping",
            new_callable=AsyncMock,
            return_value=True,
        ):
            body = app.get("/service-info").json()
        assert body["name"] == "DDBJ Search API"

    def test_version_matches_package(self, app: TestClient) -> None:
        with patch(
            "ddbj_search_api.routers.service_info.es_ping",
            new_callable=AsyncMock,
            return_value=True,
        ):
            body = app.get("/service-info").json()
        expected = importlib.metadata.version("ddbj-search-api")
        assert body["version"] == expected

    def test_content_type_is_json(self, app: TestClient) -> None:
        with patch(
            "ddbj_search_api.routers.service_info.es_ping",
            new_callable=AsyncMock,
            return_value=True,
        ):
            resp = app.get("/service-info")
        assert "application/json" in resp.headers["content-type"]

    def test_x_request_id_present(self, app: TestClient) -> None:
        with patch(
            "ddbj_search_api.routers.service_info.es_ping",
            new_callable=AsyncMock,
            return_value=True,
        ):
            resp = app.get("/service-info")
        assert "X-Request-ID" in resp.headers

    def test_cors_header_present(self, app: TestClient) -> None:
        with patch(
            "ddbj_search_api.routers.service_info.es_ping",
            new_callable=AsyncMock,
            return_value=True,
        ):
            resp = app.get(
                "/service-info",
                headers={"Origin": "https://example.com"},
            )
        assert resp.headers.get("access-control-allow-origin") == "*"


class TestElasticsearchStatus:
    """GET /service-info: elasticsearch field reflects ES health."""

    def test_ok_when_es_is_healthy(self, app: TestClient) -> None:
        with patch(
            "ddbj_search_api.routers.service_info.es_ping",
            new_callable=AsyncMock,
            return_value=True,
        ):
            body = app.get("/service-info").json()
        assert body["elasticsearch"] == "ok"

    def test_unavailable_when_es_is_down(self, app: TestClient) -> None:
        with patch(
            "ddbj_search_api.routers.service_info.es_ping",
            new_callable=AsyncMock,
            return_value=False,
        ):
            body = app.get("/service-info").json()
        assert body["elasticsearch"] == "unavailable"

    def test_always_returns_200(self, app: TestClient) -> None:
        with patch(
            "ddbj_search_api.routers.service_info.es_ping",
            new_callable=AsyncMock,
            return_value=False,
        ):
            resp = app.get("/service-info")
        assert resp.status_code == 200
