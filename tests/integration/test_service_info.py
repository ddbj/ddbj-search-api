"""Integration tests for GET /service-info."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_service_info_returns_ok_status(app: TestClient) -> None:
    """ES is reachable so elasticsearch field should be 'ok'."""
    resp = app.get("/service-info")

    assert resp.status_code == 200
    body = resp.json()
    assert body["elasticsearch"] == "ok"


def test_service_info_response_structure(app: TestClient) -> None:
    """Response contains required fields: name, version, description,
    elasticsearch."""
    resp = app.get("/service-info")
    body = resp.json()

    assert "name" in body
    assert "version" in body
    assert "description" in body
    assert "elasticsearch" in body
    assert isinstance(body["name"], str)
    assert isinstance(body["version"], str)
