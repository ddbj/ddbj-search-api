"""Tests for Bulk API: POST /entries/{type}/bulk.

Tests cover routing, request validation, JSON/NDJSON response formats,
ES interaction, error handling, and property-based invariants.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ddbj_search_api.config import AppConfig
from ddbj_search_api.es import get_es_client
from ddbj_search_api.main import create_app
from tests.unit.conftest import make_mock_stream_response
from tests.unit.strategies import db_type_values, short_id


@pytest.fixture(autouse=True)
def _mock_bulk_duckdb() -> Any:
    """Mock DuckDB iter_linked_ids and DBLINK_DB_PATH in bulk router."""
    with (
        patch(
            "ddbj_search_api.routers.bulk.iter_linked_ids",
            side_effect=lambda *_args, **_kwargs: iter([]),
        ),
        patch(
            "ddbj_search_api.routers.bulk.DBLINK_DB_PATH",
            MagicMock(exists=MagicMock(return_value=True)),
        ),
    ):
        yield


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

    async def _side_effect(_client: Any, _index: str, id_: str, **kwargs: Any) -> Any:
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

    def test_empty_ids_rejected(
        self,
        app_with_bulk: TestClient,
    ) -> None:
        resp = _bulk_post(app_with_bulk, [])
        assert resp.status_code == 422


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

    def test_empty_ids_returns_422(
        self,
        app_with_bulk: TestClient,
    ) -> None:
        resp = _bulk_post(app_with_bulk, [])
        assert resp.status_code == 422

    def test_content_type_is_json(
        self,
        app_with_bulk: TestClient,
    ) -> None:
        resp = _bulk_post(app_with_bulk, ["TEST001"])
        assert "application/json" in resp.headers["content-type"]

    def test_dbxrefs_from_duckdb(
        self,
        app_with_bulk: TestClient,
        mock_es_get_source_stream_bulk: AsyncMock,
    ) -> None:
        """Bulk returns dbXrefs from DuckDB (no truncation, no dbXrefsCount)."""
        source = {
            "identifier": "PRJDB001",
            "type": "bioproject",
        }

        async def _side_effect(_c: Any, _i: str, _id: str, **kwargs: Any) -> Any:
            return make_mock_stream_response(json.dumps(source).encode())

        mock_es_get_source_stream_bulk.side_effect = _side_effect
        resp = _bulk_post(app_with_bulk, ["PRJDB001"])
        data = resp.json()
        # With empty DuckDB mock, dbXrefs is empty
        assert data["entries"][0]["dbXrefs"] == []
        assert "dbXrefsCount" not in data["entries"][0]

    def test_dbxrefs_from_duckdb_with_data(
        self,
        mock_es_get_source_stream_bulk: AsyncMock,
    ) -> None:
        """Bulk returns actual dbXrefs when DuckDB returns results."""
        source = {
            "identifier": "PRJDB001",
            "type": "bioproject",
        }

        async def _side_effect(_c: Any, _i: str, _id: str, **kwargs: Any) -> Any:
            return make_mock_stream_response(json.dumps(source).encode())

        mock_es_get_source_stream_bulk.side_effect = _side_effect

        config = AppConfig()
        with patch(
            "ddbj_search_api.routers.bulk.iter_linked_ids",
            side_effect=lambda *_args, **_kwargs: iter([("biosample", "SAMD001")]),
        ):
            fake_client = AsyncMock(spec=httpx.AsyncClient)
            application = create_app(config)
            application.dependency_overrides[get_es_client] = lambda: fake_client
            client = TestClient(application, raise_server_exceptions=False)
            resp = _bulk_post(client, ["PRJDB001"])

        data = resp.json()
        assert len(data["entries"][0]["dbXrefs"]) == 1
        assert data["entries"][0]["dbXrefs"][0]["identifier"] == "SAMD001"
        assert data["entries"][0]["dbXrefs"][0]["type"] == "biosample"


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
            parsed = json.loads(line)
            assert "identifier" in parsed

    def test_empty_ids_returns_422(
        self,
        app_with_bulk: TestClient,
    ) -> None:
        resp = _bulk_post(app_with_bulk, [], format_="ndjson")
        assert resp.status_code == 422

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


class TestBulkDuplicateIds:
    """Duplicate IDs in request."""

    def test_duplicate_ids_collapse_to_one(
        self,
        app_with_bulk: TestClient,
        mock_es_get_source_stream_bulk: AsyncMock,
    ) -> None:
        """Duplicate IDs are deduplicated (api-spec.md § Bulk API)."""
        _setup_found_and_not_found(
            mock_es_get_source_stream_bulk,
            ["PRJDB001"],
            [],
        )
        resp = _bulk_post(app_with_bulk, ["PRJDB001", "PRJDB001"])
        data = resp.json()
        assert len(data["entries"]) == 1
        assert data["entries"][0]["identifier"] == "PRJDB001"


@pytest.fixture
def pbt_bulk_client() -> TestClient:
    """TestClient for PBT tests, created once and reused.

    Note: _mock_bulk_duckdb autouse fixture handles DuckDB mocking.
    """
    config = AppConfig()
    fake_client = AsyncMock(spec=httpx.AsyncClient)
    application = create_app(config)
    application.dependency_overrides[get_es_client] = lambda: fake_client

    return TestClient(application, raise_server_exceptions=False)


class TestBulkPBT:
    """Property-based tests for Bulk API invariants."""

    @settings(
        max_examples=20,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        found=st.lists(short_id, min_size=0, max_size=10),
        not_found=st.lists(short_id, min_size=0, max_size=10),
    )
    def test_entries_plus_not_found_equals_ids(
        self,
        pbt_bulk_client: TestClient,
        found: list[str],
        not_found: list[str],
    ) -> None:
        """len(entries) + len(notFound) == len(ids) (JSON format)."""
        # Deduplicate to avoid collisions between found and not_found
        all_ids = list(dict.fromkeys(found + not_found))
        if not all_ids:
            # BulkRequest enforces min_length=1; skip the trivially empty draw.
            return
        found_set = set(found)
        actual_found = [x for x in all_ids if x in found_set]
        actual_not_found = [x for x in all_ids if x not in found_set]

        with patch(
            "ddbj_search_api.routers.bulk.es_get_source_stream",
            new_callable=AsyncMock,
        ) as mock:
            _setup_found_and_not_found(mock, actual_found, actual_not_found)
            resp = _bulk_post(pbt_bulk_client, all_ids)
            data = resp.json()
            assert len(data["entries"]) + len(data["notFound"]) == len(all_ids)


# === includeDbXrefs parameter ===


class TestBulkIncludeDbXrefs:
    """includeDbXrefs parameter controls DuckDB access."""

    def test_include_db_xrefs_false_skips_duckdb(
        self,
        app_with_bulk: TestClient,
        mock_es_get_source_stream_bulk: AsyncMock,
    ) -> None:
        """includeDbXrefs=false omits dbXrefs and skips DuckDB."""
        source = _make_source("PRJDB1")
        mock_es_get_source_stream_bulk.return_value = make_mock_stream_response(
            json.dumps(source).encode(),
        )

        with patch(
            "ddbj_search_api.routers.bulk.iter_linked_ids",
        ) as mock_duckdb:
            resp = app_with_bulk.post(
                "/entries/bioproject/bulk?includeDbXrefs=false",
                json={"ids": ["PRJDB1"]},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["entries"]) == 1
        assert "dbXrefs" not in data["entries"][0]
        mock_duckdb.assert_not_called()

    def test_include_db_xrefs_false_ndjson(
        self,
        app_with_bulk: TestClient,
        mock_es_get_source_stream_bulk: AsyncMock,
    ) -> None:
        """includeDbXrefs=false works with NDJSON format."""
        source = _make_source("PRJDB1")
        mock_es_get_source_stream_bulk.return_value = make_mock_stream_response(
            json.dumps(source).encode(),
        )

        with patch(
            "ddbj_search_api.routers.bulk.iter_linked_ids",
        ) as mock_duckdb:
            resp = app_with_bulk.post(
                "/entries/bioproject/bulk?includeDbXrefs=false&format=ndjson",
                json={"ids": ["PRJDB1"]},
            )

        assert resp.status_code == 200
        line = resp.text.strip()
        entry = json.loads(line)
        assert "dbXrefs" not in entry
        mock_duckdb.assert_not_called()

    def test_include_db_xrefs_default_true(
        self,
        app_with_bulk: TestClient,
        mock_es_get_source_stream_bulk: AsyncMock,
    ) -> None:
        """Default includeDbXrefs=true includes dbXrefs."""
        source = _make_source("PRJDB1")
        mock_es_get_source_stream_bulk.return_value = make_mock_stream_response(
            json.dumps(source).encode(),
        )
        resp = _bulk_post(app_with_bulk, ["PRJDB1"])
        assert resp.status_code == 200
        data = resp.json()
        assert "dbXrefs" in data["entries"][0]


# === Status gating (docs/api-spec.md § データ可視性) ===


def _make_mget_side_effect(statuses: dict[str, str | None]) -> object:
    """Build an ``es_mget_source`` side_effect from an id → status map.

    ``None`` means the document is missing; any other value is treated
    as the document's ``status`` field.
    """

    async def _se(
        _client: object,
        _index: str,
        ids: list[str],
        **_kwargs: object,
    ) -> dict[str, dict[str, str] | None]:
        out: dict[str, dict[str, str] | None] = {}
        for id_ in ids:
            status = statuses.get(id_)
            out[id_] = None if status is None else {"status": status}
        return out

    return _se


class TestBulkStatusGating:
    """Bulk API の status filter: public/suppressed のみ entries に出力、
    withdrawn/private/missing は notFound (JSON) / skip (NDJSON)。
    """

    def test_json_mixed_statuses(
        self,
        app_with_bulk: TestClient,
        mock_es_mget_source_bulk: AsyncMock,
        mock_es_get_source_stream_bulk: AsyncMock,
    ) -> None:
        mock_es_mget_source_bulk.side_effect = _make_mget_side_effect(
            {
                "PRJDB_PUBLIC": "public",
                "PRJDB_SUPPRESSED": "suppressed",
                "PRJDB_WITHDRAWN": "withdrawn",
                "PRJDB_PRIVATE": "private",
                "PRJDB_MISSING": None,
            },
        )

        async def _stream_side_effect(_c: object, _i: str, id_: str, **_k: object) -> object:
            return make_mock_stream_response(json.dumps(_make_source(id_)).encode())

        mock_es_get_source_stream_bulk.side_effect = _stream_side_effect

        resp = _bulk_post(
            app_with_bulk,
            [
                "PRJDB_PUBLIC",
                "PRJDB_SUPPRESSED",
                "PRJDB_WITHDRAWN",
                "PRJDB_PRIVATE",
                "PRJDB_MISSING",
            ],
        )
        assert resp.status_code == 200
        data = resp.json()
        returned_ids = {e["identifier"] for e in data["entries"]}
        assert returned_ids == {"PRJDB_PUBLIC", "PRJDB_SUPPRESSED"}
        assert set(data["notFound"]) == {
            "PRJDB_WITHDRAWN",
            "PRJDB_PRIVATE",
            "PRJDB_MISSING",
        }

    def test_ndjson_hidden_statuses_skipped(
        self,
        app_with_bulk: TestClient,
        mock_es_mget_source_bulk: AsyncMock,
        mock_es_get_source_stream_bulk: AsyncMock,
    ) -> None:
        mock_es_mget_source_bulk.side_effect = _make_mget_side_effect(
            {
                "PRJDB_PUBLIC": "public",
                "PRJDB_WITHDRAWN": "withdrawn",
                "PRJDB_PRIVATE": "private",
            },
        )

        async def _stream_side_effect(_c: object, _i: str, id_: str, **_k: object) -> object:
            return make_mock_stream_response(json.dumps(_make_source(id_)).encode())

        mock_es_get_source_stream_bulk.side_effect = _stream_side_effect

        resp = _bulk_post(
            app_with_bulk,
            ["PRJDB_PUBLIC", "PRJDB_WITHDRAWN", "PRJDB_PRIVATE"],
            format_="ndjson",
        )
        assert resp.status_code == 200
        lines = [line for line in resp.text.split("\n") if line]
        returned_ids = [json.loads(line)["identifier"] for line in lines]
        assert returned_ids == ["PRJDB_PUBLIC"]

    def test_mget_called_with_all_ids(
        self,
        app_with_bulk: TestClient,
        mock_es_mget_source_bulk: AsyncMock,
        mock_es_get_source_stream_bulk: AsyncMock,
    ) -> None:
        """es_mget_source は一括で全 ID を 1 回で取得する (ラウンドトリップ節約)。"""

        async def _stream_side_effect(_c: object, _i: str, id_: str, **_k: object) -> object:
            return make_mock_stream_response(json.dumps(_make_source(id_)).encode())

        mock_es_get_source_stream_bulk.side_effect = _stream_side_effect

        ids = ["PRJDB1", "PRJDB2", "PRJDB3"]
        resp = _bulk_post(app_with_bulk, ids)
        assert resp.status_code == 200
        mock_es_mget_source_bulk.assert_awaited_once()
        call_args = mock_es_mget_source_bulk.call_args
        passed_ids = call_args[0][2] if len(call_args[0]) >= 3 else call_args.kwargs["ids"]
        assert passed_ids == ids

    def test_mget_uses_status_only_source_includes(
        self,
        app_with_bulk: TestClient,
        mock_es_mget_source_bulk: AsyncMock,
        mock_es_get_source_stream_bulk: AsyncMock,
    ) -> None:
        """es_mget_source は軽量化のため ``status`` のみを含める。"""

        async def _stream_side_effect(_c: object, _i: str, id_: str, **_k: object) -> object:
            return make_mock_stream_response(json.dumps(_make_source(id_)).encode())

        mock_es_get_source_stream_bulk.side_effect = _stream_side_effect

        _bulk_post(app_with_bulk, ["PRJDB1"])
        kwargs = mock_es_mget_source_bulk.call_args.kwargs
        assert kwargs.get("source_includes") == ["status"]
