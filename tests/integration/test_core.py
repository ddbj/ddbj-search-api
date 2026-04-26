"""Integration tests for IT-CORE-* scenarios.

Cross-endpoint HTTP-level invariants (X-Request-ID, RFC 7807, CORS,
trailing-slash equivalence, /service-info contract). See
``tests/integration-scenarios.md § IT-CORE-*`` for the SSOT.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient


class TestRequestIdHeader:
    """IT-CORE-01 / IT-CORE-02: X-Request-ID echo and auto-generation."""

    def test_echoes_client_supplied_id(self, app: TestClient) -> None:
        """IT-CORE-01: header echo when client supplies X-Request-ID."""
        custom_id = "it-core-01-echo"
        resp = app.get("/service-info", headers={"X-Request-ID": custom_id})
        assert resp.headers["X-Request-ID"] == custom_id

    def test_echoed_id_is_reflected_in_error_body(self, app: TestClient) -> None:
        """IT-CORE-01: error body's ``requestId`` matches the echoed header."""
        custom_id = "it-core-01-echo-error"
        resp = app.get("/__truly_not_a_route__", headers={"X-Request-ID": custom_id})
        assert resp.status_code == 404
        body = resp.json()
        assert body["requestId"] == custom_id
        assert resp.headers["X-Request-ID"] == custom_id

    def test_generates_uuid_when_header_missing(self, app: TestClient) -> None:
        """IT-CORE-02: auto-generate a UUID v4 when no header is supplied."""
        resp = app.get("/service-info")
        request_id = resp.headers.get("X-Request-ID")
        assert request_id is not None
        uuid_obj = uuid.UUID(request_id)
        assert uuid_obj.version == 4

    def test_two_calls_produce_distinct_ids(self, app: TestClient) -> None:
        """IT-CORE-02: each call gets its own ID (no caching)."""
        resp1 = app.get("/service-info")
        resp2 = app.get("/service-info")
        assert resp1.headers["X-Request-ID"] != resp2.headers["X-Request-ID"]


class TestRfc7807ProblemDetails:
    """IT-CORE-03: RFC 7807 Problem Details on error responses."""

    def test_404_has_required_keys(self, app: TestClient) -> None:
        """IT-CORE-03: type / title / status / detail are all present on 404."""
        resp = app.get("/__truly_not_a_route__")
        assert resp.status_code == 404
        assert "application/problem+json" in resp.headers["content-type"]
        body = resp.json()
        for key in ("type", "title", "status", "detail"):
            assert key in body, f"missing key: {key}"
        assert body["status"] == 404

    def test_422_has_required_keys(self, app: TestClient) -> None:
        """IT-CORE-03: same shape on Pydantic validation errors (422)."""
        resp = app.get("/entries/", params={"perPage": -1})
        assert resp.status_code == 422
        assert "application/problem+json" in resp.headers["content-type"]
        body = resp.json()
        for key in ("type", "title", "status", "detail"):
            assert key in body, f"missing key: {key}"
        assert body["status"] == 422

    def test_status_field_matches_http_status(self, app: TestClient) -> None:
        """IT-CORE-03: body ``status`` equals HTTP status code."""
        resp = app.get("/__not_a_route__")
        assert resp.json()["status"] == resp.status_code


class TestTrailingSlash:
    """IT-CORE-04: trailing-slash policy per ``docs/api-spec.md § Trailing Slash``.

    - List endpoints (``/entries/``, ``/entries/{type}/``, ``/dblink/``) accept
      both forms (canonical with-slash + no-slash alias).
    - Facets and db-portal endpoints accept only the no-slash form.
    - Individual-resource endpoints (``/entries/{type}/{id}``, ``/dblink/{type}/{id}``)
      have no trailing slash.
    """

    def test_entries_with_and_without_trailing_slash_match(self, app: TestClient) -> None:
        """IT-CORE-04: ``/entries`` and ``/entries/`` return the same total."""
        resp_no_slash = app.get("/entries", params={"perPage": 5})
        resp_slash = app.get("/entries/", params={"perPage": 5})
        assert resp_no_slash.status_code == 200
        assert resp_slash.status_code == 200
        assert resp_no_slash.json()["pagination"]["total"] == resp_slash.json()["pagination"]["total"]

    def test_dblink_with_and_without_trailing_slash_succeed(self, app: TestClient) -> None:
        """IT-CORE-04: both ``/dblink/`` (canonical) and ``/dblink`` (alias) are 200."""
        resp_slash = app.get("/dblink/")
        resp_no_slash = app.get("/dblink")
        assert resp_slash.status_code == 200
        assert resp_no_slash.status_code == 200

    def test_facets_no_slash_is_canonical(self, app: TestClient) -> None:
        """IT-CORE-04: ``/facets`` (no-slash) is the only supported form."""
        resp_no_slash = app.get("/facets")
        assert resp_no_slash.status_code == 200
        resp_slash = app.get("/facets/")
        assert resp_slash.status_code == 404


class TestContentTypeByExtension:
    """IT-CORE-05: Content-Type changes by path extension.

    The full check (``.json`` → ``application/json``, ``.jsonld`` →
    ``application/ld+json``) requires a representative accession; it is
    covered in IT-DETAIL-01 once accession constants are populated. Here
    we only assert that error responses use ``application/problem+json``
    regardless of the requested extension.
    """

    def test_problem_json_on_jsonld_404(self, app: TestClient) -> None:
        """IT-CORE-05: error path served as application/problem+json even for .jsonld."""
        resp = app.get("/entries/bioproject/PRJDB_DOES_NOT_EXIST_99999.jsonld")
        assert resp.status_code == 404
        assert "application/problem+json" in resp.headers["content-type"]

    def test_problem_json_on_json_404(self, app: TestClient) -> None:
        """IT-CORE-05: error path served as application/problem+json even for .json."""
        resp = app.get("/entries/bioproject/PRJDB_DOES_NOT_EXIST_99999.json")
        assert resp.status_code == 404
        assert "application/problem+json" in resp.headers["content-type"]


class TestCors:
    """IT-CORE-06: CORS Access-Control-Allow-Origin: *."""

    def test_allow_origin_star_on_simple_request(self, app: TestClient) -> None:
        """IT-CORE-06: simple GET request carries Access-Control-Allow-Origin: *."""
        resp = app.get("/service-info", headers={"Origin": "https://example.com"})
        assert resp.headers.get("access-control-allow-origin") == "*"

    def test_allow_origin_star_on_preflight(self, app: TestClient) -> None:
        """IT-CORE-06: preflight OPTIONS request also carries the CORS header."""
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


class TestUnknownPath:
    """IT-CORE-07: unknown endpoint returns 404 + RFC 7807."""

    def test_completely_unknown_path_returns_404(self, app: TestClient) -> None:
        """IT-CORE-07: a path with no matching route → 404."""
        resp = app.get("/__truly_not_a_route__")
        assert resp.status_code == 404
        assert "application/problem+json" in resp.headers["content-type"]

    def test_unknown_path_under_known_prefix_returns_404(self, app: TestClient) -> None:
        """IT-CORE-07: unknown path under /entries/ (invalid {type}) → 404."""
        resp = app.get("/entries/__not_a_real_type__/PRJDB1")
        assert resp.status_code == 404


class TestServiceInfo:
    """IT-CORE-08: /service-info exposes name/version/description/elasticsearch."""

    def test_returns_200_with_required_top_level_keys(self, app: TestClient) -> None:
        """IT-CORE-08: minimum contract on the service-info payload."""
        resp = app.get("/service-info")
        assert resp.status_code == 200
        body = resp.json()
        for key in ("name", "version", "description", "elasticsearch"):
            assert key in body, f"missing key: {key}"

    def test_elasticsearch_section_is_status_string(self, app: TestClient) -> None:
        """IT-CORE-08: the elasticsearch field is the literal "ok" or "unavailable"."""
        resp = app.get("/service-info")
        es_info = resp.json()["elasticsearch"]
        # Per ``schemas.service_info.ElasticsearchStatus`` (Literal["ok", "unavailable"]).
        assert es_info in {"ok", "unavailable"}
        # We hit a real reachable ES, so it should report ok.
        assert es_info == "ok"
