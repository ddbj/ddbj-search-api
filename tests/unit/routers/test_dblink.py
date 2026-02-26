"""Tests for ddbj_search_api.routers.dblink."""

from __future__ import annotations

import collections.abc
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ddbj_search_api.schemas.dblink import AccessionType


@pytest.fixture
def mock_get_linked_ids() -> collections.abc.Iterator[object]:
    """Patch get_linked_ids in the dblink router."""
    with patch(
        "ddbj_search_api.routers.dblink.get_linked_ids",
    ) as mock:
        mock.return_value = []
        yield mock


@pytest.fixture
def app_with_dblink(app: TestClient, mock_get_linked_ids: object) -> TestClient:
    """TestClient with get_linked_ids mocked."""
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
        assert "umbrella-bioproject" in types

    def test_trailing_slash_both_work(self, app: TestClient) -> None:
        resp_slash = app.get("/dblink/")
        resp_no_slash = app.get("/dblink")
        assert resp_slash.status_code == 200
        assert resp_no_slash.status_code == 200
        assert resp_slash.json() == resp_no_slash.json()


# --- GET /dblink/{type}/{id} ---


class TestGetLinks:
    def test_returns_200_with_links(self, app: TestClient) -> None:
        with patch(
            "ddbj_search_api.routers.dblink.get_linked_ids",
            return_value=[("jga-study", "JGAS000101")],
        ):
            resp = app.get("/dblink/hum-id/hum0014")

        assert resp.status_code == 200
        data = resp.json()
        assert data["identifier"] == "hum0014"
        assert data["type"] == "hum-id"
        assert len(data["links"]) == 1
        link = data["links"][0]
        assert link["identifier"] == "JGAS000101"
        assert link["type"] == "jga-study"
        assert "url" in link

    def test_returns_200_with_empty_links(self, app_with_dblink: TestClient) -> None:
        resp = app_with_dblink.get("/dblink/hum-id/hum0014")
        assert resp.status_code == 200
        data = resp.json()
        assert data["links"] == []

    def test_response_structure(self, app_with_dblink: TestClient) -> None:
        resp = app_with_dblink.get("/dblink/bioproject/PRJDB100")
        data = resp.json()
        assert "identifier" in data
        assert "type" in data
        assert "links" in data

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
            "ddbj_search_api.routers.dblink.get_linked_ids",
            side_effect=FileNotFoundError("not found"),
        ):
            resp = app.get("/dblink/hum-id/hum0014")

        assert resp.status_code == 500


class TestGetLinksTargetFilter:
    def test_target_param_passed_to_client(self, app: TestClient) -> None:
        with patch("ddbj_search_api.routers.dblink.get_linked_ids", return_value=[]) as mock:
            app.get("/dblink/hum-id/hum0014", params={"target": "jga-study"})

        mock.assert_called_once()
        call_kwargs = mock.call_args
        assert call_kwargs[1]["target"] == ["jga-study"]

    def test_multiple_targets_passed(self, app: TestClient) -> None:
        with patch("ddbj_search_api.routers.dblink.get_linked_ids", return_value=[]) as mock:
            app.get("/dblink/hum-id/hum0014", params={"target": "jga-study,bioproject"})

        call_kwargs = mock.call_args
        target = call_kwargs[1]["target"]
        assert set(target) == {"jga-study", "bioproject"}

    def test_no_target_passes_none(self, app: TestClient) -> None:
        with patch("ddbj_search_api.routers.dblink.get_linked_ids", return_value=[]) as mock:
            app.get("/dblink/hum-id/hum0014")

        call_kwargs = mock.call_args
        assert call_kwargs[1]["target"] is None


class TestGetLinksPBT:
    @given(acc_type=st.sampled_from([e.value for e in AccessionType]))
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_any_valid_type_returns_200(self, app: TestClient, acc_type: str) -> None:
        with patch("ddbj_search_api.routers.dblink.get_linked_ids", return_value=[]):
            resp = app.get(f"/dblink/{acc_type}/test-id")

        assert resp.status_code == 200

    @given(acc_type=st.sampled_from([e.value for e in AccessionType]))
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_any_valid_accession_type_as_target_returns_200(self, app: TestClient, acc_type: str) -> None:
        with patch("ddbj_search_api.routers.dblink.get_linked_ids", return_value=[]):
            resp = app.get("/dblink/hum-id/hum0014", params={"target": acc_type})

        assert resp.status_code == 200


class TestGetLinksEdgeCases:
    def test_whitespace_only_target_returns_200(self, app_with_dblink: TestClient) -> None:
        resp = app_with_dblink.get("/dblink/hum-id/hum0014", params={"target": " "})
        assert resp.status_code == 200

    def test_empty_target_returns_200(self, app_with_dblink: TestClient) -> None:
        resp = app_with_dblink.get("/dblink/hum-id/hum0014", params={"target": ""})
        assert resp.status_code == 200

    def test_comma_only_target_returns_200(self, app_with_dblink: TestClient) -> None:
        resp = app_with_dblink.get("/dblink/hum-id/hum0014", params={"target": ","})
        assert resp.status_code == 200

    def test_duplicate_target_accepted(self, app_with_dblink: TestClient) -> None:
        resp = app_with_dblink.get("/dblink/hum-id/hum0014", params={"target": "jga-study,jga-study"})
        assert resp.status_code == 200
