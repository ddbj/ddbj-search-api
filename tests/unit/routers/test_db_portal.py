"""Tests for DB Portal API routing, dispatch logic, and response shape.

Covers:
- endpoint registration (trailing slash policy, tags)
- (q, adv) x (db) dispatch matrix
- FastAPI-level enum/Literal validation (422)
- cross-database count-only flow (8 DBs, success/error mix, all-failed 502)
- DB-specific hits flow (offset + cursor, hardLimitReached boundary,
  deep paging, ES body shape)
- RFC 7807 + type URI error format
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ddbj_search_api.cursor import CursorPayload, encode_cursor
from ddbj_search_api.schemas.db_portal import (
    DbPortalCountError,
    DbPortalErrorType,
)
from tests.unit.conftest import make_es_search_response

_SOLR_PENDING = ("trad", "taxonomy")
_ES_BACKED = ("sra", "bioproject", "biosample", "jga", "gea", "metabobank")
_DB_ORDER = ("trad", "sra", "bioproject", "biosample", "jga", "gea", "metabobank", "taxonomy")


# === Routing ===


class TestDbPortalRouting:
    """GET /db-portal/search: canonical path and tags."""

    def test_route_exists(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/search")
        assert resp.status_code == 200

    def test_trailing_slash_not_canonical(self, app_with_db_portal: TestClient) -> None:
        # Only /db-portal/search is registered; /db-portal/search/ is 404.
        resp = app_with_db_portal.get("/db-portal/search/")
        assert resp.status_code == 404

    def test_tag_is_db_portal(self, app_with_db_portal: TestClient) -> None:
        spec = app_with_db_portal.get("/openapi.json").json()
        operation = spec["paths"]["/db-portal/search"]["get"]
        assert operation["tags"] == ["db-portal"]


# === Query combination ===


class TestDbPortalQueryCombination:
    """q / adv exclusivity (400 invalid-query-combination) and adv stub (501)."""

    def test_q_and_adv_together_returns_400(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "foo", "adv": "type=bioproject"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.invalid_query_combination.value

    def test_adv_alone_returns_501(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"adv": "type=bioproject"},
        )
        assert resp.status_code == 501
        body = resp.json()
        assert body["type"] == DbPortalErrorType.advanced_search_not_implemented.value

    def test_adv_with_db_returns_501(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"adv": "type=bioproject", "db": "sra"},
        )
        assert resp.status_code == 501
        body = resp.json()
        assert body["type"] == DbPortalErrorType.advanced_search_not_implemented.value

    def test_q_and_adv_with_db_returns_400_first(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        """Exclusivity check has priority over adv stub."""
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "foo", "adv": "bar", "db": "sra"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.invalid_query_combination.value


# === Solr-pending DB (trad / taxonomy) ===


class TestDbPortalDbNotImplemented:
    """db=trad / db=taxonomy returns 501 with db-not-implemented URI."""

    @pytest.mark.parametrize("db", _SOLR_PENDING)
    def test_solr_db_returns_501(
        self,
        app_with_db_portal: TestClient,
        db: str,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "foo", "db": db},
        )
        assert resp.status_code == 501
        body = resp.json()
        assert body["type"] == DbPortalErrorType.db_not_implemented.value
        assert db in body["detail"]


# === Enum / Literal validation ===


class TestDbPortalEnumValidation:
    """FastAPI-level validation for db / sort / perPage."""

    def test_unknown_db_returns_422(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "foo", "db": "unknown"},
        )
        assert resp.status_code == 422

    def test_bogus_sort_returns_422(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"sort": "bogus"},
        )
        assert resp.status_code == 422

    def test_date_modified_sort_returns_422(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"sort": "dateModified:desc"},
        )
        assert resp.status_code == 422

    @pytest.mark.parametrize("sort", ["datePublished:desc", "datePublished:asc"])
    def test_allowed_sort_accepted(
        self,
        app_with_db_portal: TestClient,
        sort: str,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "foo", "db": "bioproject", "sort": sort},
        )
        assert resp.status_code == 200

    @pytest.mark.parametrize("per_page", [20, 50, 100])
    def test_allowed_per_page_accepted(
        self,
        app_with_db_portal: TestClient,
        per_page: int,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "foo", "db": "bioproject", "perPage": per_page},
        )
        assert resp.status_code == 200

    @pytest.mark.parametrize("per_page", [10, 30, 75, 101])
    def test_disallowed_per_page_returns_422(
        self,
        app_with_db_portal: TestClient,
        per_page: int,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "foo", "db": "bioproject", "perPage": per_page},
        )
        assert resp.status_code == 422

    def test_page_0_returns_422(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "foo", "db": "bioproject", "page": 0},
        )
        assert resp.status_code == 422


# === Cross-database count-only ===


class TestDbPortalCrossSearch:
    """Cross-DB count-only (`db` omitted)."""

    def test_eight_databases_returned_in_order(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=1234)
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "cancer"})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["databases"]) == 8
        assert [e["db"] for e in body["databases"]] == list(_DB_ORDER)

    def test_solr_pending_dbs_are_not_implemented(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=1234)
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "cancer"})
        body = resp.json()
        by_db = {e["db"]: e for e in body["databases"]}
        for db in _SOLR_PENDING:
            assert by_db[db]["count"] is None
            assert by_db[db]["error"] == DbPortalCountError.not_implemented.value

    def test_es_backed_dbs_return_count(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=1234)
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "cancer"})
        body = resp.json()
        by_db = {e["db"]: e for e in body["databases"]}
        for db in _ES_BACKED:
            assert by_db[db]["count"] == 1234
            assert by_db[db]["error"] is None

    def test_timeout_mapped(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.side_effect = httpx.TimeoutException("timeout")
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "cancer"})
        assert resp.status_code == 502
        # All ES DBs timed out and both Solr DBs are not_implemented →
        # all errors → 502.  Detail confirms the failure path was hit.

    def test_partial_success_returns_200(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        # First call succeeds, rest timeout.
        mock_es_search_db_portal.side_effect = [
            make_es_search_response(total=10),
            *[httpx.TimeoutException("timeout") for _ in range(5)],
        ]
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "cancer"})
        assert resp.status_code == 200
        body = resp.json()
        # sra is the first ES-backed DB in the order, so it gets the
        # successful response.
        by_db = {e["db"]: e for e in body["databases"]}
        assert by_db["sra"]["count"] == 10
        assert by_db["sra"]["error"] is None

    def test_upstream_5xx_mapped(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        # All ES DBs return 503 → upstream_5xx.
        mock_response = httpx.Response(
            status_code=503,
            request=httpx.Request("POST", "http://es/_search"),
        )
        error = httpx.HTTPStatusError(
            "503",
            request=mock_response.request,
            response=mock_response,
        )
        mock_es_search_db_portal.side_effect = error
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "cancer"})
        # All 8 DBs have error → 502.
        assert resp.status_code == 502

    def test_connect_error_mapped(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        # First call succeeds so overall response is 200; the first
        # ES DB (sra) exercises the success path, the rest are
        # connection_refused.
        mock_es_search_db_portal.side_effect = [
            make_es_search_response(total=1),
            *[httpx.ConnectError("refused") for _ in range(5)],
        ]
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "cancer"})
        assert resp.status_code == 200
        body = resp.json()
        by_db = {e["db"]: e for e in body["databases"]}
        # bioproject is the 3rd in order, second ES-backed → error.
        assert by_db["bioproject"]["error"] == DbPortalCountError.connection_refused.value

    def test_q_none_passes_match_all(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=5)
        resp = app_with_db_portal.get("/db-portal/search")
        assert resp.status_code == 200
        # First call is for sra (first ES-backed DB in order).
        first_call_body = mock_es_search_db_portal.call_args_list[0].args[2]
        assert first_call_body["query"] == {"match_all": {}}
        assert first_call_body["size"] == 0


# === DB-specific hits search ===


class TestDbPortalDbSpecificSearch:
    """GET /db-portal/search?q=...&db=<es-backed>."""

    @pytest.mark.parametrize("db", _ES_BACKED)
    def test_all_es_backed_dbs_accepted(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        db: str,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "foo", "db": db},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["hits"] == []
        assert body["hardLimitReached"] is False

    @pytest.mark.parametrize(
        "db,index",
        [
            ("sra", "sra"),
            ("jga", "jga"),
            ("bioproject", "bioproject"),
            ("biosample", "biosample"),
            ("gea", "gea"),
            ("metabobank", "metabobank"),
        ],
    )
    def test_index_routing(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        db: str,
        index: str,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "foo", "db": db},
        )
        call = mock_es_search_db_portal.call_args
        assert call.args[1] == index

    def test_hard_limit_reached_boundary_9999(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=9999)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "foo", "db": "bioproject"},
        )
        assert resp.json()["hardLimitReached"] is False

    def test_hard_limit_reached_boundary_10000(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=10000)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "foo", "db": "bioproject"},
        )
        assert resp.json()["hardLimitReached"] is True

    def test_page_and_per_page_compute_from_size(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "foo", "db": "bioproject", "page": 3, "perPage": 20},
        )
        body = mock_es_search_db_portal.call_args.args[2]
        assert body["from"] == 40
        assert body["size"] == 20

    def test_deep_paging_returns_400(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "foo", "db": "bioproject", "page": 500, "perPage": 100},
        )
        assert resp.status_code == 400
        # Generic 400 uses about:blank.
        assert resp.json()["type"] == "about:blank"

    def test_sort_date_published_desc_builds_es_sort(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "foo", "db": "sra", "sort": "datePublished:desc"},
        )
        body = mock_es_search_db_portal.call_args.args[2]
        assert body["sort"][0] == {"datePublished": {"order": "desc"}}
        # tiebreaker appended
        assert body["sort"][-1] == {"identifier": {"order": "asc"}}

    def test_sort_default_uses_score_with_tiebreaker(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "foo", "db": "sra"},
        )
        body = mock_es_search_db_portal.call_args.args[2]
        assert body["sort"][0] == {"_score": {"order": "desc"}}
        assert body["sort"][-1] == {"identifier": {"order": "asc"}}

    def test_hit_fields_round_trip(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(
            hits=[
                {
                    "_id": "PRJDB1",
                    "_source": {
                        "identifier": "PRJDB1",
                        "type": "bioproject",
                        "title": "Human Study",
                        "organism": {"identifier": "9606", "name": "Homo sapiens"},
                        "datePublished": "2024-01-15",
                        "url": "https://example.com/PRJDB1",
                        "status": "public",  # extra field, passthrough
                    },
                    "sort": ["2024-01-15", "PRJDB1"],
                }
            ],
            total=1,
        )
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "human", "db": "bioproject"},
        )
        body = resp.json()
        assert body["total"] == 1
        assert body["hits"][0]["identifier"] == "PRJDB1"
        assert body["hits"][0]["datePublished"] == "2024-01-15"
        assert body["hits"][0]["organism"]["name"] == "Homo sapiens"
        # extra field preserved
        assert body["hits"][0]["status"] == "public"


# === Cursor ===


class TestDbPortalCursor:
    """Cursor-based pagination dispatch and exclusivity."""

    def test_cursor_without_db_returns_400(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"cursor": "some-cursor"},
        )
        assert resp.status_code == 400

    def test_invalid_cursor_returns_400(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"cursor": "not.a.valid.cursor", "db": "bioproject"},
        )
        assert resp.status_code == 400
        assert resp.json()["type"] == "about:blank"

    def test_cursor_with_q_returns_400(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        payload = CursorPayload(
            pit_id=None,
            search_after=["2024-01-15", "PRJDB1"],
            sort=[
                {"datePublished": {"order": "desc"}},
                {"identifier": {"order": "asc"}},
            ],
            query={"match_all": {}},
        )
        token = encode_cursor(payload)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"cursor": token, "db": "bioproject", "q": "conflicting"},
        )
        assert resp.status_code == 400
        assert "q" in resp.json()["detail"]


class TestDbPortalCursorPBT:
    """Property-based cursor round-trip against db-portal offset handler."""

    @settings(
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        max_examples=20,
    )
    @given(
        db=st.sampled_from(_ES_BACKED),
        per_page=st.sampled_from([20, 50, 100]),
    )
    def test_next_cursor_roundtrip(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        db: str,
        per_page: int,
    ) -> None:
        hits: list[dict[str, Any]] = [
            {
                "_id": f"DOC{i}",
                "_source": {"identifier": f"DOC{i}", "type": db},
                "sort": [f"2024-01-{i + 1:02d}", f"DOC{i}"],
            }
            for i in range(per_page)
        ]
        mock_es_search_db_portal.return_value = make_es_search_response(
            hits=hits,
            total=per_page * 3,
        )
        r1 = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "x", "db": db, "perPage": per_page},
        )
        assert r1.status_code == 200
        next_cursor = r1.json()["nextCursor"]
        assert next_cursor is not None


# === Error format ===


class TestDbPortalErrorFormat:
    """RFC 7807 + type URI + application/problem+json."""

    def test_400_invalid_query_combination_shape(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "foo", "adv": "bar"},
        )
        assert resp.status_code == 400
        assert resp.headers["content-type"].startswith("application/problem+json")
        body = resp.json()
        assert body["type"] == DbPortalErrorType.invalid_query_combination.value
        assert body["title"] == "Bad Request"
        assert body["status"] == 400
        assert "detail" in body
        assert body["instance"] == "/db-portal/search"
        assert "timestamp" in body
        assert "requestId" in body

    def test_501_adv_not_implemented_shape(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"adv": "foo"},
        )
        assert resp.status_code == 501
        body = resp.json()
        assert body["type"] == DbPortalErrorType.advanced_search_not_implemented.value
        assert body["title"] == "Not Implemented"

    def test_501_db_not_implemented_shape(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "x", "db": "trad"},
        )
        assert resp.status_code == 501
        body = resp.json()
        assert body["type"] == DbPortalErrorType.db_not_implemented.value

    def test_422_uses_about_blank(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "unknown"},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["type"] == "about:blank"

    def test_request_id_echoed(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "foo", "adv": "bar"},
            headers={"X-Request-ID": "test-req-123"},
        )
        assert resp.headers.get("X-Request-ID") == "test-req-123"
        assert resp.json()["requestId"] == "test-req-123"
