"""Tests for ddbj_search_api.routers.dblink."""

from __future__ import annotations

import collections.abc
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ddbj_search_api.schemas.dblink import AccessionType


@pytest.fixture
def mock_iter_linked_ids() -> collections.abc.Iterator[MagicMock]:
    """Patch iter_linked_ids in the dblink router."""
    with patch(
        "ddbj_search_api.routers.dblink.iter_linked_ids",
        side_effect=lambda *_args, **_kwargs: iter([]),
    ) as mock:
        yield mock


@pytest.fixture
def mock_dblink_db_path() -> collections.abc.Iterator[None]:
    """Ensure DBLINK_DB_PATH.exists() returns True."""
    with (
        tempfile.NamedTemporaryFile(suffix=".duckdb") as f,
        patch(
            "ddbj_search_api.routers.dblink.DBLINK_DB_PATH",
            Path(f.name),
        ),
    ):
        yield


@pytest.fixture
def app_with_dblink(
    app: TestClient,
    mock_iter_linked_ids: object,
    mock_dblink_db_path: object,
) -> TestClient:
    """TestClient with iter_linked_ids mocked."""

    return app


# --- GET /dblink/ ---


class TestListTypes:
    def test_returns_200(self, app: TestClient) -> None:
        resp = app.get("/dblink/")
        assert resp.status_code == 200

    def test_returns_21_types(self, app: TestClient) -> None:
        resp = app.get("/dblink/")
        data = resp.json()
        assert "types" in data
        assert len(data["types"]) == 21

    def test_types_are_sorted(self, app: TestClient) -> None:
        resp = app.get("/dblink/")
        types = resp.json()["types"]
        assert types == sorted(types)

    def test_contains_expected_types(self, app: TestClient) -> None:
        resp = app.get("/dblink/")
        types = set(resp.json()["types"])
        assert "bioproject" in types
        assert "hum-id" in types
        assert "insdc" in types

    def test_trailing_slash_both_work(self, app: TestClient) -> None:
        resp_slash = app.get("/dblink/")
        resp_no_slash = app.get("/dblink")
        assert resp_slash.status_code == 200
        assert resp_no_slash.status_code == 200
        assert resp_slash.json() == resp_no_slash.json()


# --- GET /dblink/{type}/{id} ---


class TestGetLinks:
    def test_returns_200_with_links(self, app: TestClient) -> None:
        with (
            tempfile.NamedTemporaryFile(suffix=".duckdb") as f,
            patch(
                "ddbj_search_api.routers.dblink.DBLINK_DB_PATH",
                Path(f.name),
            ),
            patch(
                "ddbj_search_api.routers.dblink.iter_linked_ids",
                side_effect=lambda *_args, **_kwargs: iter([("jga-study", "JGAS000101")]),
            ),
        ):
            resp = app.get("/dblink/hum-id/hum0014")

        assert resp.status_code == 200
        data = resp.json()
        assert data["identifier"] == "hum0014"
        assert data["type"] == "hum-id"
        assert len(data["dbXrefs"]) == 1
        link = data["dbXrefs"][0]
        assert link["identifier"] == "JGAS000101"
        assert link["type"] == "jga-study"
        assert "url" in link

    def test_returns_200_with_empty_db_xrefs(self, app_with_dblink: TestClient) -> None:
        resp = app_with_dblink.get("/dblink/hum-id/hum0014")
        assert resp.status_code == 200
        data = resp.json()
        assert data["dbXrefs"] == []

    def test_response_structure(self, app_with_dblink: TestClient) -> None:
        resp = app_with_dblink.get("/dblink/bioproject/PRJDB100")
        data = resp.json()
        assert "identifier" in data
        assert "type" in data
        assert "dbXrefs" in data

    def test_trailing_slash_works(self, app_with_dblink: TestClient) -> None:
        resp_no_slash = app_with_dblink.get("/dblink/hum-id/hum0014")
        resp_slash = app_with_dblink.get("/dblink/hum-id/hum0014/")
        assert resp_no_slash.status_code == 200
        assert resp_slash.status_code == 200
        assert resp_no_slash.json() == resp_slash.json()


class TestGetLinksInvalidType:
    def test_invalid_type_returns_422(self, app: TestClient) -> None:
        resp = app.get("/dblink/invalid-type/some-id")
        assert resp.status_code == 422

    def test_empty_type_returns_404(self, app: TestClient) -> None:
        # /dblink//some-id: 空パスセグメントは 404 または 307 (Starlette のスラッシュ正規化)
        resp = app.get("/dblink//some-id")
        assert resp.status_code in {404, 307}


class TestGetLinksInvalidTarget:
    def test_invalid_target_returns_422(self, app_with_dblink: TestClient) -> None:
        resp = app_with_dblink.get("/dblink/hum-id/hum0014", params={"target": "invalid"})
        assert resp.status_code == 422

    def test_mixed_valid_invalid_target_returns_422(self, app_with_dblink: TestClient) -> None:
        resp = app_with_dblink.get("/dblink/hum-id/hum0014", params={"target": "jga-study,bogus"})
        assert resp.status_code == 422

    def test_invalid_target_returns_rfc7807(self, app_with_dblink: TestClient) -> None:
        resp = app_with_dblink.get("/dblink/hum-id/hum0014", params={"target": "invalid"})
        data = resp.json()
        assert data["type"] == "about:blank"
        assert data["title"] == "Unprocessable Entity"
        assert data["status"] == 422
        assert "detail" in data

    def test_invalid_target_detail_contains_value(self, app_with_dblink: TestClient) -> None:
        resp = app_with_dblink.get("/dblink/hum-id/hum0014", params={"target": "bogus-value"})
        data = resp.json()
        assert "bogus-value" in data["detail"]


class TestGetLinksDbMissing:
    def test_returns_500_when_db_missing(self, app: TestClient) -> None:
        with patch(
            "ddbj_search_api.routers.dblink.DBLINK_DB_PATH",
            Path("/nonexistent/path/dblink.duckdb"),
        ):
            resp = app.get("/dblink/hum-id/hum0014")

        assert resp.status_code == 500


class TestGetLinksTargetFilter:
    def test_target_filters_results(self, app: TestClient) -> None:
        """Target parameter is passed to iter_linked_ids for SQL-level filtering."""

        def _mock_iter(*_args: object, **kwargs: object) -> collections.abc.Iterator[tuple[str, str]]:
            # Simulate SQL-level target filtering
            target: list[str] | None = kwargs.get("target")  # type: ignore[assignment]
            all_rows = [("jga-study", "JGAS000101"), ("bioproject", "PRJDB100")]
            if target is not None:
                return iter([(t, a) for t, a in all_rows if t in target])

            return iter(all_rows)

        with (
            tempfile.NamedTemporaryFile(suffix=".duckdb") as f,
            patch(
                "ddbj_search_api.routers.dblink.DBLINK_DB_PATH",
                Path(f.name),
            ),
            patch(
                "ddbj_search_api.routers.dblink.iter_linked_ids",
                side_effect=_mock_iter,
            ),
        ):
            resp = app.get("/dblink/hum-id/hum0014", params={"target": "jga-study"})

        data = resp.json()
        assert len(data["dbXrefs"]) == 1
        assert data["dbXrefs"][0]["type"] == "jga-study"

    def test_no_target_returns_all(self, app: TestClient) -> None:
        with (
            tempfile.NamedTemporaryFile(suffix=".duckdb") as f,
            patch(
                "ddbj_search_api.routers.dblink.DBLINK_DB_PATH",
                Path(f.name),
            ),
            patch(
                "ddbj_search_api.routers.dblink.iter_linked_ids",
                side_effect=lambda *_args, **_kwargs: iter(
                    [
                        ("bioproject", "PRJDB100"),
                        ("jga-study", "JGAS000101"),
                    ]
                ),
            ),
        ):
            resp = app.get("/dblink/hum-id/hum0014")

        data = resp.json()
        assert len(data["dbXrefs"]) == 2


class TestGetLinksPBT:
    @given(acc_type=st.sampled_from([e.value for e in AccessionType]))
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_any_valid_type_returns_200(self, app_with_dblink: TestClient, acc_type: str) -> None:
        resp = app_with_dblink.get(f"/dblink/{acc_type}/test-id")
        assert resp.status_code == 200

    @given(acc_type=st.sampled_from([e.value for e in AccessionType]))
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_any_valid_accession_type_as_target_returns_200(self, app_with_dblink: TestClient, acc_type: str) -> None:
        resp = app_with_dblink.get("/dblink/hum-id/hum0014", params={"target": acc_type})
        assert resp.status_code == 200


class TestGetLinksEdgeCases:
    def test_whitespace_only_target_returns_200(self, app_with_dblink: TestClient) -> None:
        resp = app_with_dblink.get("/dblink/hum-id/hum0014", params={"target": " "})
        assert resp.status_code == 200
        data = resp.json()
        assert "dbXrefs" in data
        assert isinstance(data["dbXrefs"], list)

    def test_empty_target_returns_200(self, app_with_dblink: TestClient) -> None:
        resp = app_with_dblink.get("/dblink/hum-id/hum0014", params={"target": ""})
        assert resp.status_code == 200
        data = resp.json()
        assert "dbXrefs" in data
        assert isinstance(data["dbXrefs"], list)

    def test_comma_only_target_returns_200(self, app_with_dblink: TestClient) -> None:
        resp = app_with_dblink.get("/dblink/hum-id/hum0014", params={"target": ","})
        assert resp.status_code == 200
        data = resp.json()
        assert "dbXrefs" in data
        assert isinstance(data["dbXrefs"], list)

    def test_duplicate_target_accepted(self, app_with_dblink: TestClient) -> None:
        resp = app_with_dblink.get("/dblink/hum-id/hum0014", params={"target": "jga-study,jga-study"})
        assert resp.status_code == 200
        data = resp.json()
        assert "dbXrefs" in data
        assert isinstance(data["dbXrefs"], list)


# --- POST /dblink/counts ---


class TestBulkCounts:
    def test_returns_200(self, app: TestClient) -> None:
        with (
            tempfile.NamedTemporaryFile(suffix=".duckdb") as f,
            patch(
                "ddbj_search_api.routers.dblink.DBLINK_DB_PATH",
                Path(f.name),
            ),
            patch(
                "ddbj_search_api.routers.dblink.count_linked_ids_bulk",
                return_value={("bioproject", "PRJDB1"): {"biosample": 5}},
            ),
        ):
            resp = app.post(
                "/dblink/counts",
                json={"items": [{"type": "bioproject", "id": "PRJDB1"}]},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["identifier"] == "PRJDB1"
        assert data["items"][0]["type"] == "bioproject"
        assert data["items"][0]["counts"] == {"biosample": 5}

    def test_multiple_items(self, app: TestClient) -> None:
        with (
            tempfile.NamedTemporaryFile(suffix=".duckdb") as f,
            patch(
                "ddbj_search_api.routers.dblink.DBLINK_DB_PATH",
                Path(f.name),
            ),
            patch(
                "ddbj_search_api.routers.dblink.count_linked_ids_bulk",
                return_value={
                    ("bioproject", "PRJDB1"): {"biosample": 5},
                    ("biosample", "SAMD1"): {"sra-study": 2},
                },
            ),
        ):
            resp = app.post(
                "/dblink/counts",
                json={
                    "items": [
                        {"type": "bioproject", "id": "PRJDB1"},
                        {"type": "biosample", "id": "SAMD1"},
                    ]
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2

    def test_empty_items_returns_422(self, app: TestClient) -> None:
        resp = app.post("/dblink/counts", json={"items": []})
        assert resp.status_code == 422

    def test_over_100_items_returns_422(self, app: TestClient) -> None:
        items = [{"type": "bioproject", "id": f"PRJDB{i}"} for i in range(101)]
        resp = app.post("/dblink/counts", json={"items": items})
        assert resp.status_code == 422

    def test_100_items_accepted(self, app: TestClient) -> None:
        with (
            tempfile.NamedTemporaryFile(suffix=".duckdb") as f,
            patch(
                "ddbj_search_api.routers.dblink.DBLINK_DB_PATH",
                Path(f.name),
            ),
            patch(
                "ddbj_search_api.routers.dblink.count_linked_ids_bulk",
                return_value={("bioproject", f"PRJDB{i}"): {} for i in range(100)},
            ),
        ):
            items = [{"type": "bioproject", "id": f"PRJDB{i}"} for i in range(100)]
            resp = app.post("/dblink/counts", json={"items": items})

        assert resp.status_code == 200

    def test_db_missing_returns_500(self, app: TestClient) -> None:
        with patch(
            "ddbj_search_api.routers.dblink.DBLINK_DB_PATH",
            Path("/nonexistent/path/dblink.duckdb"),
        ):
            resp = app.post(
                "/dblink/counts",
                json={"items": [{"type": "bioproject", "id": "PRJDB1"}]},
            )

        assert resp.status_code == 500

    def test_invalid_type_returns_422(self, app: TestClient) -> None:
        resp = app.post(
            "/dblink/counts",
            json={"items": [{"type": "invalid-type", "id": "PRJDB1"}]},
        )
        assert resp.status_code == 422
