"""Tests for Entry Detail API routing, responses, and parameter validation.

Tests mock ES client functions and DuckDB functions to verify routing,
response construction, streaming, JSON-LD injection, dbXrefs tail injection,
and parameter validation.
"""

from __future__ import annotations

import collections.abc
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ddbj_search_api.routers.entry_detail import _inject_jsonld_prefix
from tests.unit.conftest import make_mock_stream_response
from tests.unit.strategies import db_type_values

# === Routing: GET /entries/{type}/{id} ===


class TestEntryDetailRouting:
    """GET /entries/{type}/{id}: route exists for all types."""

    @pytest.mark.parametrize("db_type", db_type_values)
    def test_route_exists(
        self,
        app_with_entry_detail: TestClient,
        mock_es_get_source_stream: AsyncMock,
        db_type: str,
    ) -> None:
        body = json.dumps(
            {
                "identifier": "TEST001",
                "type": db_type,
            }
        ).encode()
        mock_es_get_source_stream.return_value = make_mock_stream_response(body)
        resp = app_with_entry_detail.get(f"/entries/{db_type}/TEST001")
        assert resp.status_code == 200

    def test_invalid_type_returns_404(
        self,
        app_with_entry_detail: TestClient,
    ) -> None:
        """Invalid DB type in path returns 404 Not Found."""
        resp = app_with_entry_detail.get("/entries/invalid-type/TEST001")
        assert resp.status_code == 404


# === Routing: GET /entries/{type}/{id}.json ===


class TestEntryJsonRouting:
    """GET /entries/{type}/{id}.json: raw ES document route."""

    @pytest.mark.parametrize("db_type", db_type_values)
    def test_route_exists(
        self,
        app_with_entry_detail: TestClient,
        mock_es_get_source_stream: AsyncMock,
        db_type: str,
    ) -> None:
        body = json.dumps({"identifier": "TEST001"}).encode()
        mock_es_get_source_stream.return_value = make_mock_stream_response(body)
        resp = app_with_entry_detail.get(f"/entries/{db_type}/TEST001.json")
        assert resp.status_code == 200


# === Routing: GET /entries/{type}/{id}.jsonld ===


class TestEntryJsonLdRouting:
    """GET /entries/{type}/{id}.jsonld: JSON-LD route."""

    @pytest.mark.parametrize("db_type", db_type_values)
    def test_route_exists(
        self,
        app_with_entry_detail: TestClient,
        mock_es_get_source_stream: AsyncMock,
        db_type: str,
    ) -> None:
        body = json.dumps({"identifier": "TEST001"}).encode()
        mock_es_get_source_stream.return_value = make_mock_stream_response(body)
        resp = app_with_entry_detail.get(f"/entries/{db_type}/TEST001.jsonld")
        assert resp.status_code == 200


# === Routing: GET /entries/{type}/{id}/dbxrefs.json ===


class TestDbxrefsFullRouting:
    """GET /entries/{type}/{id}/dbxrefs.json: full dbXrefs route."""

    @pytest.mark.parametrize("db_type", db_type_values)
    def test_route_exists(
        self,
        app_with_entry_detail: TestClient,
        mock_es_head_exists: AsyncMock,
        db_type: str,
    ) -> None:
        mock_es_head_exists.return_value = True
        resp = app_with_entry_detail.get(
            f"/entries/{db_type}/TEST001/dbxrefs.json",
        )
        assert resp.status_code == 200


# === Entry detail response ===


class TestEntryDetailResponse:
    """GET /entries/{type}/{id}: response structure and 404."""

    def test_200_with_truncated_dbxrefs(
        self,
        app_with_entry_detail: TestClient,
        mock_es_get_source_stream: AsyncMock,
    ) -> None:
        body = json.dumps(
            {
                "identifier": "PRJDB1",
                "type": "bioproject",
            }
        ).encode()
        mock_es_get_source_stream.return_value = make_mock_stream_response(body)

        with (
            patch(
                "ddbj_search_api.routers.entry_detail.get_linked_ids_limited",
                return_value=[("biosample", "BS1")],
            ),
            patch(
                "ddbj_search_api.routers.entry_detail.count_linked_ids",
                return_value={"biosample": 5},
            ),
        ):
            resp = app_with_entry_detail.get("/entries/bioproject/PRJDB1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["identifier"] == "PRJDB1"
        assert len(data["dbXrefs"]) == 1
        assert data["dbXrefs"][0]["identifier"] == "BS1"
        assert data["dbXrefs"][0]["type"] == "biosample"
        assert data["dbXrefsCount"] == {"biosample": 5}

    def test_404_when_not_found(
        self,
        app_with_entry_detail: TestClient,
        mock_es_get_source_stream: AsyncMock,
    ) -> None:
        mock_es_get_source_stream.return_value = None
        resp = app_with_entry_detail.get("/entries/bioproject/NOTEXIST")
        assert resp.status_code == 404

    def test_empty_dbxrefs(
        self,
        app_with_entry_detail: TestClient,
        mock_es_get_source_stream: AsyncMock,
    ) -> None:
        body = json.dumps(
            {
                "identifier": "PRJDB1",
                "type": "bioproject",
            }
        ).encode()
        mock_es_get_source_stream.return_value = make_mock_stream_response(body)
        resp = app_with_entry_detail.get("/entries/bioproject/PRJDB1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["dbXrefs"] == []
        assert data["dbXrefsCount"] == {}


# === Entry JSON response ===


class TestEntryJsonResponse:
    """GET /entries/{type}/{id}.json: streaming raw ES document + DuckDB dbXrefs."""

    def test_200_with_content_type(
        self,
        app_with_entry_detail: TestClient,
        mock_es_get_source_stream: AsyncMock,
    ) -> None:
        body = json.dumps({"identifier": "PRJDB1", "type": "bioproject"}).encode()
        mock_es_get_source_stream.return_value = make_mock_stream_response(body)
        resp = app_with_entry_detail.get("/entries/bioproject/PRJDB1.json")
        assert resp.status_code == 200
        assert "application/json" in resp.headers["content-type"]
        data = resp.json()
        assert data["identifier"] == "PRJDB1"
        # dbXrefs is injected from DuckDB (empty mock)
        assert data["dbXrefs"] == []

    def test_404_when_not_found(
        self,
        app_with_entry_detail: TestClient,
        mock_es_get_source_stream: AsyncMock,
    ) -> None:
        mock_es_get_source_stream.return_value = None
        resp = app_with_entry_detail.get("/entries/bioproject/NOTEXIST.json")
        assert resp.status_code == 404

    def test_source_excludes_dbxrefs(
        self,
        app_with_entry_detail: TestClient,
        mock_es_get_source_stream: AsyncMock,
    ) -> None:
        """ES is called with source_excludes=dbXrefs."""
        body = json.dumps({"identifier": "PRJDB1"}).encode()
        mock_es_get_source_stream.return_value = make_mock_stream_response(body)
        app_with_entry_detail.get("/entries/bioproject/PRJDB1.json")
        call_kwargs = mock_es_get_source_stream.call_args
        assert call_kwargs.kwargs.get("source_excludes") == "dbXrefs"


# === Entry JSON-LD response ===


class TestEntryJsonLdResponse:
    """GET /entries/{type}/{id}.jsonld: JSON-LD with injection."""

    def test_200_with_ld_json_content_type(
        self,
        app_with_entry_detail: TestClient,
        mock_es_get_source_stream: AsyncMock,
    ) -> None:
        body = json.dumps({"identifier": "PRJDB1", "type": "bioproject"}).encode()
        mock_es_get_source_stream.return_value = make_mock_stream_response(body)
        resp = app_with_entry_detail.get("/entries/bioproject/PRJDB1.jsonld")
        assert resp.status_code == 200
        assert "application/ld+json" in resp.headers["content-type"]

    def test_context_and_id_injected(
        self,
        app_with_entry_detail: TestClient,
        mock_es_get_source_stream: AsyncMock,
    ) -> None:
        body = json.dumps({"identifier": "PRJDB1", "type": "bioproject"}).encode()
        mock_es_get_source_stream.return_value = make_mock_stream_response(body)
        resp = app_with_entry_detail.get("/entries/bioproject/PRJDB1.jsonld")
        data = resp.json()
        assert "@context" in data
        assert "@id" in data
        assert data["@id"].endswith("/entries/bioproject/PRJDB1")

    def test_original_fields_preserved(
        self,
        app_with_entry_detail: TestClient,
        mock_es_get_source_stream: AsyncMock,
    ) -> None:
        body = json.dumps(
            {
                "identifier": "PRJDB1",
                "type": "bioproject",
                "title": "Test Project",
            }
        ).encode()
        mock_es_get_source_stream.return_value = make_mock_stream_response(body)
        resp = app_with_entry_detail.get("/entries/bioproject/PRJDB1.jsonld")
        data = resp.json()
        assert data["identifier"] == "PRJDB1"
        assert data["title"] == "Test Project"

    def test_dbxrefs_injected(
        self,
        app_with_entry_detail: TestClient,
        mock_es_get_source_stream: AsyncMock,
    ) -> None:
        body = json.dumps({"identifier": "PRJDB1", "type": "bioproject"}).encode()
        mock_es_get_source_stream.return_value = make_mock_stream_response(body)
        resp = app_with_entry_detail.get("/entries/bioproject/PRJDB1.jsonld")
        data = resp.json()
        # DuckDB mock returns empty, so dbXrefs should be empty list
        assert data["dbXrefs"] == []

    def test_404_when_not_found(
        self,
        app_with_entry_detail: TestClient,
        mock_es_get_source_stream: AsyncMock,
    ) -> None:
        mock_es_get_source_stream.return_value = None
        resp = app_with_entry_detail.get("/entries/bioproject/NOTEXIST.jsonld")
        assert resp.status_code == 404


# === dbXrefs full response ===


class TestDbxrefsFullResponse:
    """GET /entries/{type}/{id}/dbxrefs.json: DuckDB streaming dbXrefs."""

    def test_200_with_dbxrefs(
        self,
        app_with_entry_detail: TestClient,
        mock_es_head_exists: AsyncMock,
    ) -> None:
        mock_es_head_exists.return_value = True
        resp = app_with_entry_detail.get(
            "/entries/bioproject/PRJDB1/dbxrefs.json",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "dbXrefs" in data

    def test_uses_es_head_for_existence(
        self,
        app_with_entry_detail: TestClient,
        mock_es_head_exists: AsyncMock,
    ) -> None:
        """ES HEAD is used to check document existence."""
        mock_es_head_exists.return_value = True
        app_with_entry_detail.get("/entries/bioproject/PRJDB1/dbxrefs.json")
        mock_es_head_exists.assert_awaited_once()

    def test_404_when_not_found(
        self,
        app_with_entry_detail: TestClient,
        mock_es_head_exists: AsyncMock,
    ) -> None:
        mock_es_head_exists.return_value = False
        resp = app_with_entry_detail.get(
            "/entries/bioproject/NOTEXIST/dbxrefs.json",
        )
        assert resp.status_code == 404


# === _inject_jsonld_prefix ===


class TestInjectJsonLdPrefix:
    """Unit tests for the JSON-LD prefix injection helper."""

    @pytest.mark.asyncio
    async def test_single_chunk_injection(self) -> None:
        async def _stream() -> collections.abc.AsyncIterator[bytes]:
            yield b'{"identifier":"X"}'

        chunks = []
        async for chunk in _inject_jsonld_prefix(
            _stream(),
            "http://ctx",
            "http://id",
        ):
            chunks.append(chunk)

        result = json.loads(b"".join(chunks))
        assert result["@context"] == "http://ctx"
        assert result["@id"] == "http://id"
        assert result["identifier"] == "X"

    @pytest.mark.asyncio
    async def test_multi_chunk_injection(self) -> None:
        """Brace in first chunk, rest in second."""

        async def _stream() -> collections.abc.AsyncIterator[bytes]:
            yield b'{"ident'
            yield b'ifier":"X"}'

        chunks = []
        async for chunk in _inject_jsonld_prefix(
            _stream(),
            "http://ctx",
            "http://id",
        ):
            chunks.append(chunk)

        result = json.loads(b"".join(chunks))
        assert result["@context"] == "http://ctx"
        assert result["@id"] == "http://id"
        assert result["identifier"] == "X"

    @pytest.mark.asyncio
    async def test_special_chars_in_url(self) -> None:
        """URLs with special chars are properly JSON-escaped."""

        async def _stream() -> collections.abc.AsyncIterator[bytes]:
            yield b'{"key":"val"}'

        chunks = []
        async for chunk in _inject_jsonld_prefix(
            _stream(),
            "http://example.com/a&b",
            "http://example.com/c?d=1",
        ):
            chunks.append(chunk)

        result = json.loads(b"".join(chunks))
        assert result["@context"] == "http://example.com/a&b"
        assert result["@id"] == "http://example.com/c?d=1"


# === dbXrefsLimit parameter validation ===


class TestDbXrefsLimitValidation:
    """dbXrefsLimit query parameter boundary values."""

    def test_minus_1_returns_422(
        self,
        app_with_entry_detail: TestClient,
    ) -> None:
        resp = app_with_entry_detail.get(
            "/entries/bioproject/PRJDB1",
            params={"dbXrefsLimit": -1},
        )
        assert resp.status_code == 422

    def test_0_accepted(
        self,
        app_with_entry_detail: TestClient,
        mock_es_get_source_stream: AsyncMock,
    ) -> None:
        body = json.dumps({"identifier": "PRJDB1", "type": "bioproject"}).encode()
        mock_es_get_source_stream.return_value = make_mock_stream_response(body)
        resp = app_with_entry_detail.get(
            "/entries/bioproject/PRJDB1",
            params={"dbXrefsLimit": 0},
        )
        assert resp.status_code == 200

    def test_1000_accepted(
        self,
        app_with_entry_detail: TestClient,
        mock_es_get_source_stream: AsyncMock,
    ) -> None:
        body = json.dumps({"identifier": "PRJDB1", "type": "bioproject"}).encode()
        mock_es_get_source_stream.return_value = make_mock_stream_response(body)
        resp = app_with_entry_detail.get(
            "/entries/bioproject/PRJDB1",
            params={"dbXrefsLimit": 1000},
        )
        assert resp.status_code == 200

    def test_1001_returns_422(
        self,
        app_with_entry_detail: TestClient,
    ) -> None:
        resp = app_with_entry_detail.get(
            "/entries/bioproject/PRJDB1",
            params={"dbXrefsLimit": 1001},
        )
        assert resp.status_code == 422


class TestDbXrefsLimitValidationPBT:
    """Property-based tests for dbXrefsLimit boundaries."""

    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(limit=st.integers(max_value=-1))
    def test_negative_returns_422(
        self,
        app_with_entry_detail: TestClient,
        limit: int,
        mock_es_get_source_stream: AsyncMock,
    ) -> None:
        resp = app_with_entry_detail.get(
            "/entries/bioproject/PRJDB1",
            params={"dbXrefsLimit": limit},
        )
        assert resp.status_code == 422

    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(limit=st.integers(min_value=1001, max_value=10000))
    def test_over_1000_returns_422(
        self,
        app_with_entry_detail: TestClient,
        limit: int,
        mock_es_get_source_stream: AsyncMock,
    ) -> None:
        resp = app_with_entry_detail.get(
            "/entries/bioproject/PRJDB1",
            params={"dbXrefsLimit": limit},
        )
        assert resp.status_code == 422

    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(limit=st.integers(min_value=0, max_value=1000))
    def test_valid_range_accepted(
        self,
        app_with_entry_detail: TestClient,
        limit: int,
        mock_es_get_source_stream: AsyncMock,
    ) -> None:
        body = json.dumps({"identifier": "PRJDB1", "type": "bioproject"}).encode()
        mock_es_get_source_stream.return_value = make_mock_stream_response(body)
        resp = app_with_entry_detail.get(
            "/entries/bioproject/PRJDB1",
            params={"dbXrefsLimit": limit},
        )
        assert resp.status_code != 422
