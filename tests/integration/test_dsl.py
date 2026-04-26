"""Integration tests for IT-DSL-* scenarios.

ES DSL compilation behaviour exposed via /db-portal/cross-search,
/db-portal/search, and /db-portal/parse. See
``tests/integration-scenarios.md § IT-DSL-*``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class TestEsWildcardCaseInsensitive:
    """IT-DSL-01: ES wildcard matches are case-insensitive."""

    def test_lower_and_capital_yield_same_total(self, app: TestClient) -> None:
        """IT-DSL-01: total(``title:cancer*``) == total(``title:Cancer*``)."""
        lower = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "adv": "title:cancer*", "perPage": 20},
        )
        upper = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "adv": "title:Cancer*", "perPage": 20},
        )
        assert lower.status_code == 200
        assert upper.status_code == 200
        assert lower.json()["total"] == upper.json()["total"]


class TestCursorAdvExclusivityEs:
    """IT-DSL-02: cursor + adv on ES-backed DB → 400 ``cursor-not-supported``."""

    def test_cursor_and_adv_returns_400(self, app: TestClient) -> None:
        """IT-DSL-02: 400 with the slug when cursor accompanies adv."""
        resp = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "adv": "title:cancer",
                "cursor": "any-token",
            },
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "cursor-not-supported" in body.get("type", "")


class TestCursorAdvExclusivitySolr:
    """IT-DSL-03: cursor + adv on Solr (trad) → same slug."""

    @pytest.mark.staging_only
    def test_cursor_and_adv_on_trad_returns_400(self, app: TestClient) -> None:
        """IT-DSL-03: Solr DB also returns ``cursor-not-supported`` 400."""
        resp = app.get(
            "/db-portal/search",
            params={
                "db": "trad",
                "adv": "title:cancer",
                "cursor": "any-token",
            },
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "cursor-not-supported" in body.get("type", "")


class TestDbPortalParseAst:
    """IT-DSL-04: GET /db-portal/parse converts DSL → AST JSON."""

    def test_parse_returns_object(self, app: TestClient) -> None:
        """IT-DSL-04: parse output is a non-empty object (queryTree shape)."""
        resp = app.get(
            "/db-portal/parse",
            params={"adv": "title:cancer", "db": "bioproject"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, dict)
        assert len(body) > 0


class TestDbPortalParseOpenApiResponses:
    """IT-DSL-05: parse OpenAPI responses are {200, 400, 422, 500} (no 404)."""

    def test_parse_responses_exclude_404(self, app: TestClient) -> None:
        """IT-DSL-05: 404 is not in the documented response keys."""
        spec = app.get("/openapi.json").json()
        get_op = spec["paths"]["/db-portal/parse"].get("get")
        assert get_op is not None
        responses = set(get_op["responses"].keys())
        assert "404" not in responses
        assert "200" in responses
        assert "400" in responses


class TestSymbolWildcardAccepted:
    """IT-DSL-06: grammar accepts symbol-bearing wildcards (HIF-1*, COVID-19*)."""

    def test_hif1_wildcard_parses(self, app: TestClient) -> None:
        """IT-DSL-06: ``HIF-1*`` parses without a syntax error."""
        resp = app.get(
            "/db-portal/parse",
            params={"adv": "title:HIF-1*", "db": "bioproject"},
        )
        assert resp.status_code == 200

    def test_covid19_wildcard_parses(self, app: TestClient) -> None:
        """IT-DSL-06: ``COVID-19*`` parses without a syntax error."""
        resp = app.get(
            "/db-portal/parse",
            params={"adv": "title:COVID-19*", "db": "bioproject"},
        )
        assert resp.status_code == 200


class TestParseCrossModeTier3Rejection:
    """IT-DSL-07: cross-mode (db omitted) rejects Tier 3 fields."""

    def test_tier3_field_returns_400(self, app: TestClient) -> None:
        """IT-DSL-07: ``japanese_name`` (TXSearch-only) cross-mode → 400."""
        resp = app.get(
            "/db-portal/parse",
            params={"adv": "japanese_name:Homo"},
        )
        assert resp.status_code == 400


class TestParseSyntaxError:
    """IT-DSL-08: malformed DSL → 400 ``unexpected-token``."""

    def test_syntax_error_returns_400(self, app: TestClient) -> None:
        """IT-DSL-08: 400 with a parse-error type URI on a malformed adv."""
        resp = app.get(
            "/db-portal/parse",
            params={"adv": "title:::cancer", "db": "bioproject"},
        )
        assert resp.status_code == 400


class TestParseUnknownField:
    """IT-DSL-09: allowlist-外 fields → 400 ``unknown-field``."""

    def test_unknown_field_returns_400(self, app: TestClient) -> None:
        """IT-DSL-09: unrecognised field name → 400."""
        resp = app.get(
            "/db-portal/parse",
            params={"adv": "__not_a_field__:value", "db": "bioproject"},
        )
        assert resp.status_code == 400
