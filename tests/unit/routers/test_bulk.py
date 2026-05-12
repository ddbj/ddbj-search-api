"""Tests for Bulk API: POST /entries/{type}/bulk.

Tests cover routing, request validation, JSON/NDJSON response formats,
ES interaction, error handling, and property-based invariants.

The bulk router calls ``es_mget_source`` twice per request:
  1. visibility check  (kwargs: ``source_includes=["status"]``)
  2. body fetch        (kwargs: ``source_excludes=["dbXrefs"]``)

and one ``get_linked_ids_bulk`` for all visible ids' dbXrefs.  Tests
mock both externals; the visibility call is distinguished from the
body call by inspecting ``source_includes``.
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
from tests.unit.strategies import db_type_values, short_id

# === Helpers ===


def _make_source(id_: str) -> dict[str, Any]:
    """Build a minimal ES _source document for tests."""
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


def _make_mget_side_effect(
    statuses: dict[str, str | None],
    bodies: dict[str, dict[str, Any]] | None = None,
) -> object:
    """Build an ``es_mget_source`` side_effect from id -> status / body maps.

    The router's first call uses ``source_includes=["status"]`` (we
    return ``{"status": <s>}``); the second uses ``source_excludes=
    ["dbXrefs"]`` (we return the body from ``bodies`` or
    ``_make_source(id_)``).  ``status=None`` or absent means missing.
    """
    bodies = bodies or {}

    async def _se(
        _client: object,
        _index: str,
        ids: list[str],
        **kwargs: object,
    ) -> dict[str, dict[str, Any] | None]:
        if kwargs.get("source_includes") == ["status"]:
            return {id_: (None if statuses.get(id_) is None else {"status": statuses[id_]}) for id_ in ids}
        # Body fetch: visible ids only (router filters with visibility map)
        return {id_: bodies.get(id_, _make_source(id_)) for id_ in ids}

    return _se


def _set_found_and_not_found(
    mock: AsyncMock,
    found_ids: list[str],
    not_found_ids: list[str],
    bodies: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Configure ``mock_es_mget_source_bulk`` so ``found_ids`` are public
    and ``not_found_ids`` are missing.
    """
    statuses: dict[str, str | None] = dict.fromkeys(found_ids, "public")
    statuses.update(dict.fromkeys(not_found_ids))
    mock.side_effect = _make_mget_side_effect(statuses, bodies)


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
        mock_es_mget_source_bulk: AsyncMock,
    ) -> None:
        ids = ["PRJDB001", "PRJDB002"]
        _set_found_and_not_found(mock_es_mget_source_bulk, ids, [])
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
        mock_es_mget_source_bulk: AsyncMock,
    ) -> None:
        found = ["PRJDB001"]
        not_found = ["MISSING001", "MISSING002"]
        _set_found_and_not_found(mock_es_mget_source_bulk, found, not_found)
        resp = _bulk_post(app_with_bulk, found + not_found)
        data = resp.json()
        assert len(data["entries"]) == 1
        assert set(data["notFound"]) == set(not_found)

    def test_all_not_found(
        self,
        app_with_bulk: TestClient,
        mock_es_mget_source_bulk: AsyncMock,
    ) -> None:
        """All IDs missing: entries=[], notFound=[all]."""
        ids = ["MISSING001", "MISSING002"]
        _set_found_and_not_found(mock_es_mget_source_bulk, [], ids)
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

    def test_dbxrefs_empty_when_duckdb_returns_nothing(
        self,
        app_with_bulk: TestClient,
        mock_es_mget_source_bulk: AsyncMock,
    ) -> None:
        """With the default empty DuckDB mock, dbXrefs is `[]`."""
        _set_found_and_not_found(mock_es_mget_source_bulk, ["PRJDB001"], [])
        resp = _bulk_post(app_with_bulk, ["PRJDB001"])
        data = resp.json()
        assert data["entries"][0]["dbXrefs"] == []
        assert "dbXrefsCount" not in data["entries"][0]

    def test_dbxrefs_populated_from_duckdb_bulk(
        self,
        app_with_bulk: TestClient,
        mock_es_mget_source_bulk: AsyncMock,
        mock_dblink_bulk: MagicMock,
    ) -> None:
        """Bulk returns dbXrefs from the DuckDB bulk query."""
        _set_found_and_not_found(mock_es_mget_source_bulk, ["PRJDB001"], [])
        mock_dblink_bulk.return_value = {
            ("bioproject", "PRJDB001"): [("biosample", "SAMD001")],
        }
        resp = _bulk_post(app_with_bulk, ["PRJDB001"])
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
        mock_es_mget_source_bulk: AsyncMock,
    ) -> None:
        _set_found_and_not_found(mock_es_mget_source_bulk, ["PRJDB001"], [])
        resp = _bulk_post(app_with_bulk, ["PRJDB001"], format_="ndjson")
        assert "application/x-ndjson" in resp.headers["content-type"]

    def test_one_entry_per_line(
        self,
        app_with_bulk: TestClient,
        mock_es_mget_source_bulk: AsyncMock,
    ) -> None:
        ids = ["PRJDB001", "PRJDB002", "PRJDB003"]
        _set_found_and_not_found(mock_es_mget_source_bulk, ids, [])
        resp = _bulk_post(app_with_bulk, ids, format_="ndjson")
        lines = resp.text.strip().split("\n")
        assert len(lines) == 3
        for line in lines:
            parsed = json.loads(line)
            assert "identifier" in parsed

    def test_not_found_skipped(
        self,
        app_with_bulk: TestClient,
        mock_es_mget_source_bulk: AsyncMock,
    ) -> None:
        """IDs not found are silently skipped in NDJSON output."""
        found = ["PRJDB001"]
        not_found = ["MISSING001", "MISSING002"]
        _set_found_and_not_found(mock_es_mget_source_bulk, found, not_found)
        resp = _bulk_post(app_with_bulk, found + not_found, format_="ndjson")
        lines = resp.text.strip().split("\n")
        assert len(lines) == 1
        assert json.loads(lines[0])["identifier"] == "PRJDB001"

    def test_each_line_is_valid_json(
        self,
        app_with_bulk: TestClient,
        mock_es_mget_source_bulk: AsyncMock,
    ) -> None:
        ids = ["PRJDB001", "PRJDB002"]
        _set_found_and_not_found(mock_es_mget_source_bulk, ids, [])
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
        mock_es_mget_source_bulk: AsyncMock,
    ) -> None:
        """All IDs missing: empty NDJSON output."""
        _set_found_and_not_found(mock_es_mget_source_bulk, [], ["MISSING001"])
        resp = _bulk_post(app_with_bulk, ["MISSING001"], format_="ndjson")
        assert resp.text == ""


# === ES interaction ===


class TestBulkEsInteraction:
    """Verify ES client is called correctly."""

    def test_calls_with_correct_index(
        self,
        app_with_bulk: TestClient,
        mock_es_mget_source_bulk: AsyncMock,
    ) -> None:
        _set_found_and_not_found(mock_es_mget_source_bulk, ["TEST001"], [])
        _bulk_post(app_with_bulk, ["TEST001"], db_type="biosample")
        # Every call must target the biosample index.
        for call in mock_es_mget_source_bulk.call_args_list:
            args = call.args
            kwargs = call.kwargs
            index = kwargs.get("index", args[1] if len(args) >= 2 else None)
            assert index == "biosample"

    def test_mget_called_twice_per_request(
        self,
        app_with_bulk: TestClient,
        mock_es_mget_source_bulk: AsyncMock,
    ) -> None:
        """Visibility + 1 body chunk for small N (<= 50 ids)."""
        ids = ["A001", "A002", "A003"]
        _set_found_and_not_found(mock_es_mget_source_bulk, ids, [])
        _bulk_post(app_with_bulk, ids)
        assert mock_es_mget_source_bulk.await_count == 2

    def test_mget_chunked_above_chunk_size(
        self,
        app_with_bulk: TestClient,
        mock_es_mget_source_bulk: AsyncMock,
    ) -> None:
        """N=51 visible ids -> 1 visibility + 2 body chunks = 3 mget calls."""
        ids = [f"PRJDB{i:04d}" for i in range(51)]
        _set_found_and_not_found(mock_es_mget_source_bulk, ids, [])
        _bulk_post(app_with_bulk, ids)
        # 1 visibility + ceil(51 / 50) = 2 body chunks
        assert mock_es_mget_source_bulk.await_count == 3

    def test_visibility_call_uses_source_includes_status(
        self,
        app_with_bulk: TestClient,
        mock_es_mget_source_bulk: AsyncMock,
    ) -> None:
        """The first call narrows the projection to ``status`` only."""
        _set_found_and_not_found(mock_es_mget_source_bulk, ["PRJDB1"], [])
        _bulk_post(app_with_bulk, ["PRJDB1"])
        first_call = mock_es_mget_source_bulk.call_args_list[0]
        assert first_call.kwargs.get("source_includes") == ["status"]

    def test_body_call_uses_source_excludes_dbxrefs(
        self,
        app_with_bulk: TestClient,
        mock_es_mget_source_bulk: AsyncMock,
    ) -> None:
        """The body call drops ``dbXrefs`` so we don't pull it from ES twice."""
        _set_found_and_not_found(mock_es_mget_source_bulk, ["PRJDB1"], [])
        _bulk_post(app_with_bulk, ["PRJDB1"])
        body_calls = [
            c for c in mock_es_mget_source_bulk.call_args_list if c.kwargs.get("source_excludes") == ["dbXrefs"]
        ]
        assert len(body_calls) >= 1

    def test_visibility_passes_all_ids_in_one_call(
        self,
        app_with_bulk: TestClient,
        mock_es_mget_source_bulk: AsyncMock,
    ) -> None:
        ids = ["PRJDB1", "PRJDB2", "PRJDB3"]
        _set_found_and_not_found(mock_es_mget_source_bulk, ids, [])
        _bulk_post(app_with_bulk, ids)
        first_call = mock_es_mget_source_bulk.call_args_list[0]
        passed_ids = first_call.kwargs.get("ids", first_call.args[2] if len(first_call.args) >= 3 else None)
        assert passed_ids == ids


# === ES error handling ===


class TestBulkEsError:
    """ES errors during streaming propagate as exceptions.

    Since Bulk API uses StreamingResponse, errors that happen after the
    visibility check (i.e. during body fetch) occur after the HTTP 200
    header has been sent, so they cannot be converted to a 500 status.
    The connection is broken instead.
    """

    def test_body_fetch_error_raises_during_streaming(
        self,
        mock_dblink_bulk: MagicMock,
    ) -> None:
        """First call succeeds (visibility), second call (body) raises."""
        config = AppConfig()
        call_count = {"n": 0}

        async def _side_effect(
            _client: object,
            _index: str,
            ids: list[str],
            **kwargs: object,
        ) -> dict[str, dict[str, Any] | None]:
            call_count["n"] += 1
            if call_count["n"] == 1:
                # visibility passes
                return {id_: {"status": "public"} for id_ in ids}
            # body fetch fails
            raise RuntimeError("ES down")

        with patch(
            "ddbj_search_api.routers.bulk.es_mget_source",
            new_callable=AsyncMock,
        ) as mock:
            mock.side_effect = _side_effect
            fake_client = AsyncMock(spec=httpx.AsyncClient)
            application = create_app(config)
            application.dependency_overrides[get_es_client] = lambda: fake_client
            client = TestClient(application, raise_server_exceptions=True)
            with pytest.raises(RuntimeError, match="ES down"):
                client.post(
                    "/entries/bioproject/bulk",
                    json={"ids": ["TEST001"]},
                )


# === Duplicate handling ===


class TestBulkDuplicateIds:
    """Duplicate IDs in request."""

    def test_duplicate_ids_collapse_to_one(
        self,
        app_with_bulk: TestClient,
        mock_es_mget_source_bulk: AsyncMock,
    ) -> None:
        """Duplicate IDs are deduplicated (api-spec.md § Bulk API)."""
        _set_found_and_not_found(mock_es_mget_source_bulk, ["PRJDB001"], [])
        resp = _bulk_post(app_with_bulk, ["PRJDB001", "PRJDB001"])
        data = resp.json()
        assert len(data["entries"]) == 1
        assert data["entries"][0]["identifier"] == "PRJDB001"


# === Race condition (delete between visibility check and body fetch) ===


class TestBulkRaceCondition:
    """A doc deleted after visibility but before body fetch ends in notFound."""

    def test_race_deleted_doc_moves_to_not_found(
        self,
        app_with_bulk: TestClient,
        mock_es_mget_source_bulk: AsyncMock,
    ) -> None:
        async def _side_effect(
            _client: object,
            _index: str,
            ids: list[str],
            **kwargs: object,
        ) -> dict[str, dict[str, Any] | None]:
            if kwargs.get("source_includes") == ["status"]:
                # All public
                return {id_: {"status": "public"} for id_ in ids}
            # Body fetch: PRJDB002 vanished
            return {
                "PRJDB001": _make_source("PRJDB001"),
                "PRJDB002": None,
            }

        mock_es_mget_source_bulk.side_effect = _side_effect
        resp = _bulk_post(app_with_bulk, ["PRJDB001", "PRJDB002"])
        data = resp.json()
        assert [e["identifier"] for e in data["entries"]] == ["PRJDB001"]
        assert data["notFound"] == ["PRJDB002"]


# === PBT ===


@pytest.fixture
def pbt_bulk_client(mock_dblink_bulk: MagicMock) -> TestClient:
    """TestClient reused across hypothesis examples."""
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
        all_ids = list(dict.fromkeys(found + not_found))
        if not all_ids:
            return  # BulkRequest enforces min_length=1
        found_set = set(found)
        actual_found = [x for x in all_ids if x in found_set]
        actual_not_found = [x for x in all_ids if x not in found_set]

        statuses: dict[str, str | None] = dict.fromkeys(actual_found, "public")
        statuses.update(dict.fromkeys(actual_not_found))
        with patch(
            "ddbj_search_api.routers.bulk.es_mget_source",
            new_callable=AsyncMock,
        ) as mock:
            mock.side_effect = _make_mget_side_effect(statuses)
            resp = _bulk_post(pbt_bulk_client, all_ids)
            data = resp.json()
            assert len(data["entries"]) + len(data["notFound"]) == len(all_ids)


# === includeDbXrefs parameter ===


class TestBulkIncludeDbXrefs:
    """includeDbXrefs parameter controls DuckDB access."""

    def test_include_db_xrefs_false_skips_duckdb(
        self,
        app_with_bulk: TestClient,
        mock_es_mget_source_bulk: AsyncMock,
        mock_dblink_bulk: MagicMock,
    ) -> None:
        """includeDbXrefs=false omits dbXrefs and skips DuckDB."""
        _set_found_and_not_found(mock_es_mget_source_bulk, ["PRJDB1"], [])
        resp = app_with_bulk.post(
            "/entries/bioproject/bulk?includeDbXrefs=false",
            json={"ids": ["PRJDB1"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["entries"]) == 1
        assert "dbXrefs" not in data["entries"][0]
        mock_dblink_bulk.assert_not_called()

    def test_include_db_xrefs_false_ndjson(
        self,
        app_with_bulk: TestClient,
        mock_es_mget_source_bulk: AsyncMock,
        mock_dblink_bulk: MagicMock,
    ) -> None:
        """includeDbXrefs=false works with NDJSON format."""
        _set_found_and_not_found(mock_es_mget_source_bulk, ["PRJDB1"], [])
        resp = app_with_bulk.post(
            "/entries/bioproject/bulk?includeDbXrefs=false&format=ndjson",
            json={"ids": ["PRJDB1"]},
        )
        assert resp.status_code == 200
        line = resp.text.strip()
        entry = json.loads(line)
        assert "dbXrefs" not in entry
        mock_dblink_bulk.assert_not_called()

    def test_include_db_xrefs_default_true(
        self,
        app_with_bulk: TestClient,
        mock_es_mget_source_bulk: AsyncMock,
    ) -> None:
        """Default includeDbXrefs=true includes dbXrefs."""
        _set_found_and_not_found(mock_es_mget_source_bulk, ["PRJDB1"], [])
        resp = _bulk_post(app_with_bulk, ["PRJDB1"])
        assert resp.status_code == 200
        data = resp.json()
        assert "dbXrefs" in data["entries"][0]


# === Status gating (docs/api-spec.md § データ可視性) ===


class TestBulkStatusGating:
    """Bulk API の status filter:
    public/suppressed のみ entries に出力、withdrawn/private/missing は
    notFound (JSON) / skip (NDJSON)。
    """

    def test_json_mixed_statuses(
        self,
        app_with_bulk: TestClient,
        mock_es_mget_source_bulk: AsyncMock,
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
    ) -> None:
        mock_es_mget_source_bulk.side_effect = _make_mget_side_effect(
            {
                "PRJDB_PUBLIC": "public",
                "PRJDB_WITHDRAWN": "withdrawn",
                "PRJDB_PRIVATE": "private",
            },
        )
        resp = _bulk_post(
            app_with_bulk,
            ["PRJDB_PUBLIC", "PRJDB_WITHDRAWN", "PRJDB_PRIVATE"],
            format_="ndjson",
        )
        assert resp.status_code == 200
        lines = [line for line in resp.text.split("\n") if line]
        returned_ids = [json.loads(line)["identifier"] for line in lines]
        assert returned_ids == ["PRJDB_PUBLIC"]

    def test_body_fetch_only_passes_visible_ids(
        self,
        app_with_bulk: TestClient,
        mock_es_mget_source_bulk: AsyncMock,
    ) -> None:
        """Visibility filter happens before body fetch.

        Tells the chunked-mget body call only the visible ids; hidden
        ids never reach the second `_mget` because they're already in
        ``not_found``.
        """
        mock_es_mget_source_bulk.side_effect = _make_mget_side_effect(
            {
                "PRJDB_PUBLIC": "public",
                "PRJDB_WITHDRAWN": "withdrawn",
            },
        )
        _bulk_post(app_with_bulk, ["PRJDB_PUBLIC", "PRJDB_WITHDRAWN"])
        body_calls = [
            c for c in mock_es_mget_source_bulk.call_args_list if c.kwargs.get("source_excludes") == ["dbXrefs"]
        ]
        assert len(body_calls) == 1
        passed_ids = body_calls[0].kwargs.get(
            "ids",
            body_calls[0].args[2] if len(body_calls[0].args) >= 3 else None,
        )
        assert passed_ids == ["PRJDB_PUBLIC"]
