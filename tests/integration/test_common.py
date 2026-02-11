"""Integration tests for cross-cutting concerns.

X-Request-ID, CORS, RFC 7807 error structure, invalid DB type.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

# === X-Request-ID ===


def test_request_id_auto_generated(app: TestClient) -> None:
    """Response includes X-Request-ID even when client does not send it."""
    resp = app.get("/service-info")

    assert resp.status_code == 200
    request_id = resp.headers.get("X-Request-ID")
    assert request_id is not None
    assert len(request_id) > 0


def test_request_id_echoes_client_value(app: TestClient) -> None:
    """Server echoes back the client-supplied X-Request-ID."""
    client_id = f"test-{uuid.uuid4()}"
    resp = app.get(
        "/service-info",
        headers={"X-Request-ID": client_id},
    )

    assert resp.status_code == 200
    assert resp.headers["X-Request-ID"] == client_id


# === CORS ===


def test_cors_allow_origin(app: TestClient) -> None:
    """Response includes Access-Control-Allow-Origin: * when Origin is sent."""
    resp = app.get(
        "/service-info",
        headers={"Origin": "https://example.com"},
    )

    assert resp.headers.get("access-control-allow-origin") == "*"


# === RFC 7807 Error Structure ===


def test_error_404_rfc7807_structure(app: TestClient) -> None:
    """404 error conforms to RFC 7807 Problem Details."""
    resp = app.get("/entries/bioproject/NONEXISTENT_ID_99999")

    assert resp.status_code == 404
    body = resp.json()
    assert body["type"] == "about:blank"
    assert body["title"] == "Not Found"
    assert body["status"] == 404
    assert "detail" in body
    assert "instance" in body
    assert "timestamp" in body
    assert "requestId" in body


def test_error_404_request_id_in_body(app: TestClient) -> None:
    """RFC 7807 error body requestId matches X-Request-ID header."""
    client_id = f"test-{uuid.uuid4()}"
    resp = app.get(
        "/entries/bioproject/NONEXISTENT_ID_99999",
        headers={"X-Request-ID": client_id},
    )

    assert resp.status_code == 404
    body = resp.json()
    assert body["requestId"] == client_id
    assert resp.headers["X-Request-ID"] == client_id


# === Invalid DB type ===


def test_invalid_db_type_returns_404(app: TestClient) -> None:
    """Invalid DB type in path returns 404 Not Found."""
    resp = app.get("/entries/invalid-type/")

    assert resp.status_code == 404
    body = resp.json()
    assert body["status"] == 404
