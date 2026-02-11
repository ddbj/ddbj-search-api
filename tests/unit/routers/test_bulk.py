"""Tests for Bulk API: POST /entries/{type}/bulk.

Tests cover routing, request validation, JSON/NDJSON response formats,
ES interaction, error handling, and property-based invariants.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st

from ddbj_search_api.config import AppConfig
from ddbj_search_api.es import get_es_client
from ddbj_search_api.main import create_app
from tests.unit.conftest import make_mock_stream_response
from tests.unit.strategies import db_type_values, short_id

# === Helpers ===


def _make_source(id_: str) -> dict[str, Any]:
    """Build a minimal ES _source document."""

    return {
        "identifier": id_,
        "type": "bioproject",
        "title": f"Title for {id_}",
    }


def _bulk_post(
    client: TestClient,
    ids: list[str],
    db_type: str = "bioproject",
    format_: str | None = None,
) -> Any:
    """POST to /entries/{type}/bulk with optional format."""
    params = {}
    if format_ is not None:
        params["format"] = format_

    return client.post(
        f"/entries/{db_type}/bulk",
        json={"ids": ids},
        params=params,
    )


def _setup_found_and_not_found(
    mock: AsyncMock,
    found_ids: list[str],
    not_found_ids: list[str],
) -> None:
    """Configure mock to return found entries for found_ids, None for not_found_ids."""
    found_set = set(found_ids)

    async def _side_effect(_client: Any, _index: str, id_: str) -> Any:
        if id_ in found_set:
            source = _make_source(id_)

            return make_mock_stream_response(json.dumps(source).encode())

        return None

    mock.side_effect = _side_effect


# === Routing ===


class TestBulkRouting:
    """POST /entries/{type}/bulk: route exists for all types."""

    @pytest.mark.parametrize("db_type", db_type_values)
    def test_route_exists(
        self,
        app_with_bulk: TestClient,
        db_type: str,
    ) -> None:
        resp = _bulk_post(app_with_bulk, ["TEST001"], db_type=db_type)
        assert resp.status_code == 200

    def test_invalid_type_returns_404(self, app: TestClient) -> None:
        """Invalid DB type in path returns 404 Not Found."""
        resp = app.post(
            "/entries/invalid-type/bulk",
            json={"ids": ["TEST001"]},
        )
        assert resp.status_code == 404


# === Request body validation ===


class TestBulkRequestValidation:
    """BulkRequest body validation at HTTP level."""

    def test_empty_body_returns_422(self, app: TestClient) -> None:
        resp = app.post("/entries/bioproject/bulk")
        assert resp.status_code == 422

    def test_missing_ids_returns_422(self, app: TestClient) -> None:
        resp = app.post("/entries/bioproject/bulk", json={})
        assert resp.status_code == 422

    def test_ids_not_list_returns_422(self, app: TestClient) -> None:
        resp = app.post(
            "/entries/bioproject/bulk",
            json={"ids": "NOT_A_LIST"},
        )
        assert resp.status_code == 422

    def test_1001_ids_returns_422(self, app: TestClient) -> None:
        ids = [f"PRJDB{i}" for i in range(1001)]
        resp = app.post("/entries/bioproject/bulk", json={"ids": ids})
        assert resp.status_code == 422

    def test_1000_ids_accepted(
        self,
        app_with_bulk: TestClient,
    ) -> None:
        ids = [f"PRJDB{i}" for i in range(1000)]
        resp = _bulk_post(app_with_bulk, ids)
        assert resp.status_code == 200

    def test_empty_ids_accepted(
        self,
        app_with_bulk: TestClient,
    ) -> None:
        resp = _bulk_post(app_with_bulk, [])
        assert resp.status_code == 200


# === format query parameter ===


class TestBulkFormatParameter:
    """format query parameter validation."""

    def test_json_accepted(self, app_with_bulk: TestClient) -> None:
        resp = _bulk_post(app_with_bulk, ["TEST001"], format_="json")
        assert resp.status_code == 200

    def test_ndjson_accepted(self, app_with_bulk: TestClient) -> None:
        resp = _bulk_post(app_with_bulk, ["TEST001"], format_="ndjson")
        assert resp.status_code == 200

    def test_invalid_format_returns_422(self, app: TestClient) -> None:
        resp = app.post(
            "/entries/bioproject/bulk",
            params={"format": "csv"},
            json={"ids": ["TEST001"]},
        )
        assert resp.status_code == 422


# === JSON format responses ===


class TestBulkJsonResponse:
    """format=json: {"entries":[...], "notFound":[...]}."""

    def test_all_found_returns_entries_and_empty_not_found(
        self,
        app_with_bulk: TestClient,
        mock_es_get_source_stream_bulk: AsyncMock,
    ) -> None:
        ids = ["PRJDB001", "PRJDB002"]
        _setup_found_and_not_found(
            mock_es_get_source_stream_bulk,
            ids,
            [],
        )
        resp = _bulk_post(app_with_bulk, ids)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["entries"]) == 2
        assert data["notFound"] == []
        assert data["entries"][0]["identifier"] == "PRJDB001"
        assert data["entries"][1]["identifier"] == "PRJDB002"

    def test_partial_not_found(
        self,
        app_with_bulk: TestClient,
        mock_es_get_source_stream_bulk: AsyncMock,
    ) -> None:
        found = ["PRJDB001"]
        not_found = ["MISSING001", "MISSING002"]
        _setup_found_and_not_found(
            mock_es_get_source_stream_bulk,
            found,
            not_found,
        )
        resp = _bulk_post(app_with_bulk, found + not_found)
        data = resp.json()
        assert len(data["entries"]) == 1
        assert set(data["notFound"]) == set(not_found)

    def test_all_not_found(
        self,
        app_with_bulk: TestClient,
        mock_es_get_source_stream_bulk: AsyncMock,
    ) -> None:
        """All IDs missing: entries=[], notFound=[all]."""
        ids = ["MISSING001", "MISSING002"]
        # mock default is None (not found)
        resp = _bulk_post(app_with_bulk, ids)
        data = resp.json()
        assert data["entries"] == []
        assert set(data["notFound"]) == set(ids)

    def test_empty_ids_returns_empty_response(
        self,
        app_with_bulk: TestClient,
    ) -> None:
        resp = _bulk_post(app_with_bulk, [])
        data = resp.json()
        assert data == {"entries": [], "notFound": []}

    def test_content_type_is_json(
        self,
        app_with_bulk: TestClient,
    ) -> None:
        resp = _bulk_post(app_with_bulk, [])
        assert "application/json" in resp.headers["content-type"]

    def test_dbxrefs_not_truncated(
        self,
        app_with_bulk: TestClient,
        mock_es_get_source_stream_bulk: AsyncMock,
    ) -> None:
        """Bulk returns full dbXrefs (no truncation, no dbXrefsCount)."""
        source = {
            "identifier": "PRJDB001",
            "type": "bioproject",
            "dbXrefs": [{"type": "biosample", "identifier": f"SAMD{i}"} for i in range(200)],
        }

        async def _side_effect(_c: Any, _i: str, _id: str) -> Any:
            return make_mock_stream_response(json.dumps(source).encode())

        mock_es_get_source_stream_bulk.side_effect = _side_effect
        resp = _bulk_post(app_with_bulk, ["PRJDB001"])
        data = resp.json()
        assert len(data["entries"][0]["dbXrefs"]) == 200
        assert "dbXrefsCount" not in data["entries"][0]


# === NDJSON format responses ===


class TestBulkNdjsonResponse:
    """format=ndjson: one entry per line, no notFound."""

    def test_content_type_is_ndjson(
        self,
        app_with_bulk: TestClient,
        mock_es_get_source_stream_bulk: AsyncMock,
    ) -> None:
        _setup_found_and_not_found(
            mock_es_get_source_stream_bulk,
            ["PRJDB001"],
            [],
        )
        resp = _bulk_post(app_with_bulk, ["PRJDB001"], format_="ndjson")
        assert "application/x-ndjson" in resp.headers["content-type"]

    def test_one_entry_per_line(
        self,
        app_with_bulk: TestClient,
        mock_es_get_source_stream_bulk: AsyncMock,
    ) -> None:
        ids = ["PRJDB001", "PRJDB002", "PRJDB003"]
        _setup_found_and_not_found(
            mock_es_get_source_stream_bulk,
            ids,
            [],
        )
        resp = _bulk_post(app_with_bulk, ids, format_="ndjson")
        lines = resp.text.strip().split("\n")
        assert len(lines) == 3
        for line in lines:
            parsed = json.loads(line)
            assert "identifier" in parsed

    def test_not_found_skipped(
        self,
        app_with_bulk: TestClient,
        mock_es_get_source_stream_bulk: AsyncMock,
    ) -> None:
        """IDs not found are silently skipped in NDJSON output."""
        found = ["PRJDB001"]
        not_found = ["MISSING001", "MISSING002"]
        _setup_found_and_not_found(
            mock_es_get_source_stream_bulk,
            found,
            not_found,
        )
        resp = _bulk_post(
            app_with_bulk,
            found + not_found,
            format_="ndjson",
        )
        lines = resp.text.strip().split("\n")
        assert len(lines) == 1
        assert json.loads(lines[0])["identifier"] == "PRJDB001"

    def test_each_line_is_valid_json(
        self,
        app_with_bulk: TestClient,
        mock_es_get_source_stream_bulk: AsyncMock,
    ) -> None:
        ids = ["PRJDB001", "PRJDB002"]
        _setup_found_and_not_found(
            mock_es_get_source_stream_bulk,
            ids,
            [],
        )
        resp = _bulk_post(app_with_bulk, ids, format_="ndjson")
        for line in resp.text.strip().split("\n"):
            json.loads(line)  # should not raise

    def test_empty_ids_returns_empty_body(
        self,
        app_with_bulk: TestClient,
    ) -> None:
        resp = _bulk_post(app_with_bulk, [], format_="ndjson")
        assert resp.text == ""

    def test_all_not_found_returns_empty_body(
        self,
        app_with_bulk: TestClient,
    ) -> None:
        """All IDs missing: empty NDJSON output."""
        resp = _bulk_post(
            app_with_bulk,
            ["MISSING001"],
            format_="ndjson",
        )
        assert resp.text == ""


# === ES interaction ===


class TestBulkEsInteraction:
    """Verify ES client is called correctly."""

    def test_calls_with_correct_index(
        self,
        app_with_bulk: TestClient,
        mock_es_get_source_stream_bulk: AsyncMock,
    ) -> None:
        _bulk_post(app_with_bulk, ["TEST001"], db_type="biosample")
        call_args = mock_es_get_source_stream_bulk.call_args
        assert call_args[0][1] == "biosample"

    def test_calls_per_id(
        self,
        app_with_bulk: TestClient,
        mock_es_get_source_stream_bulk: AsyncMock,
    ) -> None:
        ids = ["A001", "A002", "A003"]
        _bulk_post(app_with_bulk, ids)
        assert mock_es_get_source_stream_bulk.call_count == 3
        called_ids = [call[0][2] for call in mock_es_get_source_stream_bulk.call_args_list]
        assert called_ids == ids


# === ES error handling ===


class TestBulkEsError:
    """ES errors during streaming propagate as exceptions.

    Since Bulk API uses StreamingResponse, errors occur after the
    HTTP 200 header has been sent, so they cannot be converted to a
    500 status code.  The connection is broken instead.
    """

    def test_es_error_raises_during_streaming(self) -> None:
        config = AppConfig()
        with patch(
            "ddbj_search_api.routers.bulk.es_get_source_stream",
            new_callable=AsyncMock,
        ) as mock:
            mock.side_effect = Exception("ES down")
            fake_client = AsyncMock(spec=httpx.AsyncClient)
            application = create_app(config)
            application.dependency_overrides[get_es_client] = lambda: fake_client
            client = TestClient(application, raise_server_exceptions=True)
            with pytest.raises(Exception, match="ES down"):
                client.post(
                    "/entries/bioproject/bulk",
                    json={"ids": ["TEST001"]},
                )


# === PBT ===


class TestBulkPBT:
    """Property-based tests for Bulk API invariants."""

    @settings(max_examples=20, deadline=None)
    @given(
        found=st.lists(short_id, min_size=0, max_size=10),
        not_found=st.lists(short_id, min_size=0, max_size=10),
    )
    def test_entries_plus_not_found_equals_ids(
        self,
        found: list[str],
        not_found: list[str],
    ) -> None:
        """len(entries) + len(notFound) == len(ids) (JSON format)."""
        # Deduplicate to avoid collisions between found and not_found
        all_ids = list(dict.fromkeys(found + not_found))
        found_set = set(found)
        actual_found = [x for x in all_ids if x in found_set]
        actual_not_found = [x for x in all_ids if x not in found_set]

        config = AppConfig()
        with patch(
            "ddbj_search_api.routers.bulk.es_get_source_stream",
            new_callable=AsyncMock,
        ) as mock:
            _setup_found_and_not_found(mock, actual_found, actual_not_found)
            fake_client = AsyncMock(spec=httpx.AsyncClient)
            application = create_app(config)
            application.dependency_overrides[get_es_client] = lambda: fake_client
            client = TestClient(application, raise_server_exceptions=False)
            resp = _bulk_post(client, all_ids)
            data = resp.json()
            assert len(data["entries"]) + len(data["notFound"]) == len(all_ids)
