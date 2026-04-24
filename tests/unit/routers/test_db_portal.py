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

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ddbj_search_api.config import AppConfig
from ddbj_search_api.cursor import CursorPayload, encode_cursor
from ddbj_search_api.schemas.db_portal import (
    DbPortalCountError,
    DbPortalErrorType,
)
from tests.unit.conftest import (
    make_es_search_response,
    make_solr_arsa_response,
    make_solr_txsearch_response,
)

_SOLR_DBS = ("trad", "taxonomy")
_ES_DBS = ("sra", "bioproject", "biosample", "jga", "gea", "metabobank")
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
    """q / adv exclusivity (400) and AP3 DSL parse/validate error surfacing."""

    def test_q_and_adv_together_returns_400(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "foo", "adv": "title:cancer"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.invalid_query_combination.value

    def test_adv_parse_error_returns_400_unexpected_token(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        # `type=bioproject` is not a valid DSL (no `:` and uses `=`).
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"adv": "type=bioproject"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.unexpected_token.value

    def test_adv_unknown_field_returns_400(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"adv": "foo:bar"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.unknown_field.value

    def test_adv_invalid_operator_returns_400(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"adv": "date:cancer*"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.invalid_operator_for_field.value

    def test_adv_with_db_parse_error_still_400(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"adv": "type=bioproject", "db": "sra"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.unexpected_token.value

    def test_q_and_adv_with_db_returns_400_first(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        """Exclusivity check has priority over DSL parse."""
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "foo", "adv": "bar", "db": "sra"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.invalid_query_combination.value

    def test_advanced_search_not_implemented_never_emitted(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        """AP3 完了後、501 advanced-search-not-implemented は返らない."""
        for params in (
            {"adv": "foo:bar"},
            {"adv": "title:cancer^2"},
            {"adv": "type=bioproject"},
        ):
            resp = app_with_db_portal.get("/db-portal/search", params=params)
            assert resp.status_code != 501
            assert resp.json()["type"] != DbPortalErrorType.advanced_search_not_implemented.value


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

    def test_solr_dbs_return_count_from_solr_mock(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=1234)
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=77)
        mock_txsearch_search_db_portal.return_value = make_solr_txsearch_response(num_found=9)
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "cancer"})
        body = resp.json()
        by_db = {e["db"]: e for e in body["databases"]}
        assert by_db["trad"]["count"] == 77
        assert by_db["trad"]["error"] is None
        assert by_db["taxonomy"]["count"] == 9
        assert by_db["taxonomy"]["error"] is None

    def test_es_backed_dbs_return_count(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=1234)
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "cancer"})
        body = resp.json()
        by_db = {e["db"]: e for e in body["databases"]}
        for db in _ES_DBS:
            assert by_db[db]["count"] == 1234
            assert by_db[db]["error"] is None

    def test_all_backends_timeout_returns_502(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.side_effect = httpx.TimeoutException("timeout")
        mock_arsa_search_db_portal.side_effect = httpx.TimeoutException("timeout")
        mock_txsearch_search_db_portal.side_effect = httpx.TimeoutException("timeout")
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "cancer"})
        assert resp.status_code == 502

    def test_partial_success_returns_200(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        # First ES call succeeds, rest timeout; Solr mocks keep their
        # default empty-success responses so the overall response is 200.
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

    def test_upstream_5xx_all_backends_returns_502(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
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
        mock_arsa_search_db_portal.side_effect = error
        mock_txsearch_search_db_portal.side_effect = error
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "cancer"})
        assert resp.status_code == 502

    def test_connect_error_mapped(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        # First ES call succeeds so overall response is 200; the first
        # ES DB (sra) exercises the success path, the rest are
        # connection_refused.  Solr mocks stay on default success.
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

    @pytest.mark.parametrize("db", _ES_DBS)
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
        db=st.sampled_from(_ES_DBS),
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

    def test_400_adv_invalid_dsl_shape(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"adv": "foo:bar"},
        )
        assert resp.status_code == 400
        assert resp.headers["content-type"].startswith("application/problem+json")
        body = resp.json()
        assert body["type"] == DbPortalErrorType.unknown_field.value
        assert body["title"] == "Bad Request"
        assert body["status"] == 400
        assert "detail" in body
        # column 情報が自然言語で detail に埋め込まれる (source.md §AP1 決定)
        assert "column" in body["detail"]

    def test_400_cursor_not_supported_shape(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "trad", "cursor": "abc.def"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.cursor_not_supported.value
        assert body["title"] == "Bad Request"
        assert body["status"] == 400
        assert "trad" in body["detail"]

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


# === Solr cross-search error mapping ===


class TestDbPortalSolrCrossSearchErrors:
    """Solr cross-search count error classification per backend."""

    def test_arsa_timeout_mapped(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        mock_arsa_search_db_portal.side_effect = httpx.TimeoutException("timeout")
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "x"})
        assert resp.status_code == 200
        by_db = {e["db"]: e for e in resp.json()["databases"]}
        assert by_db["trad"]["count"] is None
        assert by_db["trad"]["error"] == DbPortalCountError.timeout.value

    def test_arsa_connection_refused_mapped(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        mock_arsa_search_db_portal.side_effect = httpx.ConnectError("refused")
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "x"})
        by_db = {e["db"]: e for e in resp.json()["databases"]}
        assert by_db["trad"]["error"] == DbPortalCountError.connection_refused.value

    def test_arsa_5xx_mapped_to_upstream_5xx(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        mock_response = httpx.Response(
            status_code=502,
            request=httpx.Request("GET", "http://arsa/select"),
        )
        mock_arsa_search_db_portal.side_effect = httpx.HTTPStatusError(
            "502",
            request=mock_response.request,
            response=mock_response,
        )
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "x"})
        by_db = {e["db"]: e for e in resp.json()["databases"]}
        assert by_db["trad"]["error"] == DbPortalCountError.upstream_5xx.value

    def test_txsearch_timeout_mapped(
        self,
        app_with_db_portal: TestClient,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        mock_txsearch_search_db_portal.side_effect = httpx.TimeoutException("timeout")
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "x"})
        by_db = {e["db"]: e for e in resp.json()["databases"]}
        assert by_db["taxonomy"]["error"] == DbPortalCountError.timeout.value

    def test_arsa_unexpected_response_shape_unknown(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        mock_arsa_search_db_portal.return_value = {"no_response_key": True}
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "x"})
        by_db = {e["db"]: e for e in resp.json()["databases"]}
        assert by_db["trad"]["error"] == DbPortalCountError.unknown.value


# === ARSA (Trad) DB-specific hits ===


class TestDbPortalTradSpecificSearch:
    """GET /db-portal/search?q=...&db=trad — ARSA proxy."""

    def test_returns_trad_hits(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(
            num_found=2,
            docs=[
                {
                    "PrimaryAccessionNumber": "AY967397",
                    "Definition": "Synthetic construct FTT0951",
                    "Organism": "synthetic construct",
                    "Division": "SYN",
                    "Date": "20050411",
                },
                {
                    "PrimaryAccessionNumber": "AY967398",
                    "Definition": "Another def",
                    "Organism": "Homo sapiens",
                    "Division": "PRI",
                    "Date": "20060101",
                },
            ],
        )
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "x", "db": "trad"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert body["hardLimitReached"] is False
        assert body["hits"][0]["identifier"] == "AY967397"
        assert body["hits"][0]["type"] == "trad"
        assert body["hits"][0]["title"] == "Synthetic construct FTT0951"
        assert body["hits"][0]["organism"]["name"] == "synthetic construct"
        assert body["hits"][0]["datePublished"] == "2005-04-11"
        assert body["hits"][0]["division"] == "SYN"
        assert body["hits"][0]["url"] == "https://getentry.ddbj.nig.ac.jp/getentry/na/AY967397/"

    def test_hard_limit_boundary_10000(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=10_000)
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "x", "db": "trad"})
        assert resp.json()["hardLimitReached"] is True

    def test_page_and_per_page_echoed(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "x", "db": "trad", "page": 3, "perPage": 50},
        )
        body = resp.json()
        assert body["page"] == 3
        assert body["perPage"] == 50

    def test_next_cursor_always_null(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=1000)
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "x", "db": "trad"})
        assert resp.json()["nextCursor"] is None

    def test_arsa_called_with_core_and_shards(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=0)
        app_with_db_portal.get("/db-portal/search", params={"q": "x", "db": "trad"})
        call = mock_arsa_search_db_portal.call_args
        assert call.kwargs["core"] == "collection1"
        assert call.kwargs["base_url"] == "http://mock-arsa:51981/solr"
        assert call.kwargs["params"]["shards"] == "mock-arsa:51981/solr/collection1"
        assert call.kwargs["params"]["defType"] == "edismax"

    def test_deep_paging_returns_400(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "x", "db": "trad", "page": 500, "perPage": 100},
        )
        assert resp.status_code == 400

    def test_sort_date_published_translated_to_solr(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=0)
        app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "x", "db": "trad", "sort": "datePublished:desc"},
        )
        params = mock_arsa_search_db_portal.call_args.kwargs["params"]
        assert params["sort"] == "Date desc"


# === TXSearch (Taxonomy) DB-specific hits ===


class TestDbPortalTaxonomySpecificSearch:
    """GET /db-portal/search?q=...&db=taxonomy — TXSearch proxy."""

    def test_returns_taxonomy_hits(
        self,
        app_with_db_portal: TestClient,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        mock_txsearch_search_db_portal.return_value = make_solr_txsearch_response(
            num_found=1,
            docs=[
                {
                    "tax_id": "9606",
                    "scientific_name": "Homo sapiens",
                    "common_name": ["human"],
                    "japanese_name": ["ヒト"],
                    "rank": "species",
                    "lineage": ["Homo sapiens", "Homo", "Hominidae"],
                },
            ],
        )
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "Homo", "db": "taxonomy"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        hit = body["hits"][0]
        assert hit["identifier"] == "9606"
        assert hit["type"] == "taxonomy"
        assert hit["title"] == "Homo sapiens"
        assert hit["organism"] == {"name": "Homo sapiens", "identifier": "9606"}
        assert hit["datePublished"] is None
        assert hit["url"] == "https://ddbj.nig.ac.jp/resource/taxonomy/9606"
        assert hit["rank"] == "species"
        assert hit["commonName"] == "human"
        assert hit["japaneseName"] == "ヒト"

    def test_hard_limit_boundary(
        self,
        app_with_db_portal: TestClient,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        mock_txsearch_search_db_portal.return_value = make_solr_txsearch_response(num_found=10_001)
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "x", "db": "taxonomy"})
        assert resp.json()["hardLimitReached"] is True

    def test_txsearch_called_with_full_url(
        self,
        app_with_db_portal: TestClient,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        mock_txsearch_search_db_portal.return_value = make_solr_txsearch_response(num_found=0)
        app_with_db_portal.get("/db-portal/search", params={"q": "x", "db": "taxonomy"})
        call = mock_txsearch_search_db_portal.call_args
        assert call.kwargs["url"] == "http://mock-txsearch/solr-rgm/ncbi_taxonomy/select"
        assert "shards" not in call.kwargs["params"]


# === Cursor not supported for Solr DBs ===


class TestDbPortalCursorNotSupportedForSolr:
    """db=trad / db=taxonomy + cursor → 400 cursor-not-supported."""

    @pytest.mark.parametrize("db", _SOLR_DBS)
    def test_cursor_with_solr_db_returns_400(
        self,
        app_with_db_portal: TestClient,
        db: str,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": db, "cursor": "abc.def"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.cursor_not_supported.value
        assert db in body["detail"]

    def test_cursor_with_es_db_unaffected(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        # ES-backed DB with invalid cursor still hits the existing path
        # (generic 400, not cursor_not_supported).
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "bioproject", "cursor": "not.a.valid.cursor"},
        )
        assert resp.status_code == 400
        assert resp.json()["type"] == "about:blank"


# === Solr error propagation for DB-specific search ===


class TestDbPortalSolrErrorPropagation:
    """Solr upstream errors surface as 502 on db-specific search."""

    def test_arsa_timeout_returns_502(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        mock_arsa_search_db_portal.side_effect = httpx.TimeoutException("timeout")
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "x", "db": "trad"})
        assert resp.status_code == 502

    def test_arsa_5xx_returns_502(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        mock_response = httpx.Response(
            status_code=503,
            request=httpx.Request("GET", "http://arsa/select"),
        )
        mock_arsa_search_db_portal.side_effect = httpx.HTTPStatusError(
            "503",
            request=mock_response.request,
            response=mock_response,
        )
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "x", "db": "trad"})
        assert resp.status_code == 502

    def test_txsearch_timeout_returns_502(
        self,
        app_with_db_portal: TestClient,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        mock_txsearch_search_db_portal.side_effect = httpx.TimeoutException("timeout")
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "x", "db": "taxonomy"})
        assert resp.status_code == 502


# === AP5: parallel fan-out + per-backend timeouts ===


def _delayed_es_response(delay: float, total: int = 0) -> Any:
    """Return an async side_effect that sleeps then yields an ES response."""

    async def _run(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        await asyncio.sleep(delay)
        return make_es_search_response(total=total)

    return _run


def _delayed_arsa_response(delay: float, num_found: int = 0) -> Any:
    async def _run(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        await asyncio.sleep(delay)
        return make_solr_arsa_response(num_found=num_found)

    return _run


def _delayed_txsearch_response(delay: float, num_found: int = 0) -> Any:
    async def _run(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        await asyncio.sleep(delay)
        return make_solr_txsearch_response(num_found=num_found)

    return _run


class TestDbPortalCrossSearchAP5Parallelization:
    """AP5: parallel fan-out + per-backend timeouts + total timeout.

    Mock boundary stays at ``es_search`` / ``arsa_search`` / ``txsearch_search``
    (AsyncMock). ``asyncio.wait_for`` and ``asyncio.wait`` are internal
    implementation details and are NOT mocked — they exercise real
    wall-clock cancellation via ``asyncio.sleep`` injected into the
    upstream mocks.
    """

    def test_es_per_db_timeout_does_not_block_solr(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
        config: AppConfig,
    ) -> None:
        """Short ``es_search_timeout`` cancels only ES tasks; Solr succeeds."""
        object.__setattr__(config, "es_search_timeout", 0.05)
        object.__setattr__(config, "arsa_timeout", 5.0)
        object.__setattr__(config, "txsearch_timeout", 5.0)
        object.__setattr__(config, "cross_search_total_timeout", 10.0)
        mock_es_search_db_portal.side_effect = _delayed_es_response(delay=2.0)
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=42)
        mock_txsearch_search_db_portal.return_value = make_solr_txsearch_response(num_found=7)

        resp = app_with_db_portal.get("/db-portal/search", params={"q": "x"})

        assert resp.status_code == 200
        by_db = {e["db"]: e for e in resp.json()["databases"]}
        for es_db in _ES_DBS:
            assert by_db[es_db]["count"] is None
            assert by_db[es_db]["error"] == DbPortalCountError.timeout.value
        assert by_db["trad"]["count"] == 42
        assert by_db["trad"]["error"] is None
        assert by_db["taxonomy"]["count"] == 7
        assert by_db["taxonomy"]["error"] is None

    def test_arsa_timeout_independent_of_txsearch(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
        config: AppConfig,
    ) -> None:
        """Per-backend timeout for ARSA fires without touching TXSearch."""
        object.__setattr__(config, "es_search_timeout", 5.0)
        object.__setattr__(config, "arsa_timeout", 0.05)
        object.__setattr__(config, "txsearch_timeout", 5.0)
        object.__setattr__(config, "cross_search_total_timeout", 10.0)
        mock_es_search_db_portal.return_value = make_es_search_response(total=3)
        mock_arsa_search_db_portal.side_effect = _delayed_arsa_response(delay=2.0)
        mock_txsearch_search_db_portal.return_value = make_solr_txsearch_response(num_found=11)

        resp = app_with_db_portal.get("/db-portal/search", params={"q": "x"})

        assert resp.status_code == 200
        by_db = {e["db"]: e for e in resp.json()["databases"]}
        assert by_db["trad"]["error"] == DbPortalCountError.timeout.value
        assert by_db["taxonomy"]["count"] == 11
        assert by_db["taxonomy"]["error"] is None

    def test_txsearch_timeout_shorter_than_arsa_fires_first(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
        config: AppConfig,
    ) -> None:
        """Same upstream delay, different per-backend budgets → only TXSearch dies."""
        object.__setattr__(config, "es_search_timeout", 5.0)
        object.__setattr__(config, "arsa_timeout", 2.0)
        object.__setattr__(config, "txsearch_timeout", 0.05)
        object.__setattr__(config, "cross_search_total_timeout", 10.0)
        mock_es_search_db_portal.return_value = make_es_search_response(total=3)
        mock_arsa_search_db_portal.side_effect = _delayed_arsa_response(
            delay=0.2,
            num_found=77,
        )
        mock_txsearch_search_db_portal.side_effect = _delayed_txsearch_response(delay=0.2)

        resp = app_with_db_portal.get("/db-portal/search", params={"q": "x"})

        assert resp.status_code == 200
        by_db = {e["db"]: e for e in resp.json()["databases"]}
        assert by_db["trad"]["count"] == 77
        assert by_db["trad"]["error"] is None
        assert by_db["taxonomy"]["error"] == DbPortalCountError.timeout.value

    def test_total_timeout_all_backends_slow_returns_502(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
        config: AppConfig,
    ) -> None:
        """Everyone misses the total deadline → all error=timeout → 502."""
        object.__setattr__(config, "es_search_timeout", 10.0)
        object.__setattr__(config, "arsa_timeout", 10.0)
        object.__setattr__(config, "txsearch_timeout", 10.0)
        object.__setattr__(config, "cross_search_total_timeout", 0.1)
        mock_es_search_db_portal.side_effect = _delayed_es_response(delay=5.0)
        mock_arsa_search_db_portal.side_effect = _delayed_arsa_response(delay=5.0)
        mock_txsearch_search_db_portal.side_effect = _delayed_txsearch_response(delay=5.0)

        resp = app_with_db_portal.get("/db-portal/search", params={"q": "x"})

        assert resp.status_code == 502

    def test_partial_completion_before_total_timeout_preserved(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
        config: AppConfig,
    ) -> None:
        """Fast ES DBs finish; slow Solr DBs get cancelled at total deadline.

        This is the core of decision C2 (asyncio.wait + ALL_COMPLETED):
        a ``wait_for(gather(...))`` wrapping would lose the ES success
        results when the total deadline fires.
        """
        object.__setattr__(config, "es_search_timeout", 10.0)
        object.__setattr__(config, "arsa_timeout", 10.0)
        object.__setattr__(config, "txsearch_timeout", 10.0)
        object.__setattr__(config, "cross_search_total_timeout", 0.3)
        mock_es_search_db_portal.return_value = make_es_search_response(total=4)
        mock_arsa_search_db_portal.side_effect = _delayed_arsa_response(delay=5.0)
        mock_txsearch_search_db_portal.side_effect = _delayed_txsearch_response(delay=5.0)

        resp = app_with_db_portal.get("/db-portal/search", params={"q": "x"})

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["databases"]) == 8
        by_db = {e["db"]: e for e in body["databases"]}
        for es_db in _ES_DBS:
            assert by_db[es_db]["count"] == 4
            assert by_db[es_db]["error"] is None
        assert by_db["trad"]["count"] is None
        assert by_db["trad"]["error"] == DbPortalCountError.timeout.value
        assert by_db["taxonomy"]["count"] is None
        assert by_db["taxonomy"]["error"] == DbPortalCountError.timeout.value

    def test_total_timeout_longer_than_individual_all_succeed(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
        config: AppConfig,
    ) -> None:
        """All backends finish well before total; no cancellations."""
        object.__setattr__(config, "es_search_timeout", 5.0)
        object.__setattr__(config, "arsa_timeout", 5.0)
        object.__setattr__(config, "txsearch_timeout", 5.0)
        object.__setattr__(config, "cross_search_total_timeout", 10.0)
        mock_es_search_db_portal.side_effect = _delayed_es_response(delay=0.05, total=9)
        mock_arsa_search_db_portal.side_effect = _delayed_arsa_response(delay=0.05, num_found=13)
        mock_txsearch_search_db_portal.side_effect = _delayed_txsearch_response(delay=0.05, num_found=2)

        resp = app_with_db_portal.get("/db-portal/search", params={"q": "x"})

        assert resp.status_code == 200
        by_db = {e["db"]: e for e in resp.json()["databases"]}
        for es_db in _ES_DBS:
            assert by_db[es_db]["count"] == 9
        assert by_db["trad"]["count"] == 13
        assert by_db["taxonomy"]["count"] == 2
        for entry in resp.json()["databases"]:
            assert entry["error"] is None

    def test_parallel_execution_wall_clock(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
        config: AppConfig,
    ) -> None:
        """Parallel fan-out: wall-clock ≈ slowest backend, not sum.

        With 8 backends at 0.3 s each, sequential dispatch would take
        ≥ 2.4 s.  Parallel dispatch should complete well under 1 s.
        """
        object.__setattr__(config, "es_search_timeout", 5.0)
        object.__setattr__(config, "arsa_timeout", 5.0)
        object.__setattr__(config, "txsearch_timeout", 5.0)
        object.__setattr__(config, "cross_search_total_timeout", 10.0)
        mock_es_search_db_portal.side_effect = _delayed_es_response(delay=0.3, total=1)
        mock_arsa_search_db_portal.side_effect = _delayed_arsa_response(delay=0.3, num_found=1)
        mock_txsearch_search_db_portal.side_effect = _delayed_txsearch_response(delay=0.3, num_found=1)

        start = time.perf_counter()
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "x"})
        elapsed = time.perf_counter() - start

        assert resp.status_code == 200
        # Loose bound: parallel wall-clock should be << 8 * 0.3s = 2.4s.
        assert elapsed < 1.5, f"cross-search took {elapsed:.2f}s; expected parallel fan-out"

    def test_single_es_success_rest_timeout_returns_200(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
        config: AppConfig,
    ) -> None:
        """Any single success across 8 DBs is enough for HTTP 200."""
        object.__setattr__(config, "es_search_timeout", 5.0)
        object.__setattr__(config, "arsa_timeout", 0.05)
        object.__setattr__(config, "txsearch_timeout", 0.05)
        object.__setattr__(config, "cross_search_total_timeout", 10.0)
        mock_es_search_db_portal.return_value = make_es_search_response(total=1)
        mock_arsa_search_db_portal.side_effect = _delayed_arsa_response(delay=2.0)
        mock_txsearch_search_db_portal.side_effect = _delayed_txsearch_response(delay=2.0)

        resp = app_with_db_portal.get("/db-portal/search", params={"q": "x"})

        assert resp.status_code == 200


class TestDbPortalCrossSearchAP5PBT:
    """Property-based tests: response shape + DB order invariants."""

    _OUTCOME_STRATEGY = st.sampled_from(("success", "timeout", "connect_error", "http_5xx"))

    @settings(
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
        max_examples=10,
    )
    @given(outcomes=st.lists(_OUTCOME_STRATEGY, min_size=8, max_size=8))
    def test_databases_length_and_db_order_invariant(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
        config: AppConfig,
        outcomes: list[str],
    ) -> None:
        """For any outcome assignment across 8 DBs, response has 8 items in fixed order.

        When at least one DB succeeds → 200; when all fail → 502.
        """
        object.__setattr__(config, "es_search_timeout", 2.0)
        object.__setattr__(config, "arsa_timeout", 2.0)
        object.__setattr__(config, "txsearch_timeout", 2.0)
        object.__setattr__(config, "cross_search_total_timeout", 5.0)

        def _mk_error(outcome: str) -> Exception:
            if outcome == "timeout":
                return httpx.TimeoutException("timeout")
            if outcome == "connect_error":
                return httpx.ConnectError("refused")
            mock_response = httpx.Response(
                status_code=503,
                request=httpx.Request("POST", "http://upstream/"),
            )
            return httpx.HTTPStatusError(
                "503",
                request=mock_response.request,
                response=mock_response,
            )

        # Outcomes indexed by _DB_ORDER.
        outcomes_by_db = dict(zip(_DB_ORDER, outcomes, strict=True))

        def _es_side_effect(_client: Any, index: str, _body: dict[str, Any]) -> dict[str, Any]:
            outcome = outcomes_by_db[index]
            if outcome == "success":
                return make_es_search_response(total=0)
            raise _mk_error(outcome)

        async def _arsa_side_effect(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            outcome = outcomes_by_db["trad"]
            if outcome == "success":
                return make_solr_arsa_response(num_found=0)
            raise _mk_error(outcome)

        async def _txsearch_side_effect(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            outcome = outcomes_by_db["taxonomy"]
            if outcome == "success":
                return make_solr_txsearch_response(num_found=0)
            raise _mk_error(outcome)

        mock_es_search_db_portal.side_effect = _es_side_effect
        mock_arsa_search_db_portal.side_effect = _arsa_side_effect
        mock_txsearch_search_db_portal.side_effect = _txsearch_side_effect

        resp = app_with_db_portal.get("/db-portal/search", params={"q": "x"})

        all_failed = all(o != "success" for o in outcomes)
        if all_failed:
            assert resp.status_code == 502
            return
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["databases"]) == 8
        assert [e["db"] for e in body["databases"]] == list(_DB_ORDER)

    @settings(
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
        max_examples=5,
    )
    @given(delays=st.lists(st.floats(min_value=0.01, max_value=0.15), min_size=8, max_size=8))
    def test_order_invariant_under_random_completion_order(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
        config: AppConfig,
        delays: list[float],
    ) -> None:
        """Even when task completion order is randomised by delays, response
        keeps the canonical ``_DB_ORDER`` ordering.
        """
        object.__setattr__(config, "es_search_timeout", 2.0)
        object.__setattr__(config, "arsa_timeout", 2.0)
        object.__setattr__(config, "txsearch_timeout", 2.0)
        object.__setattr__(config, "cross_search_total_timeout", 5.0)

        delays_by_db = dict(zip(_DB_ORDER, delays, strict=True))

        async def _es_side_effect(_client: Any, index: str, _body: dict[str, Any]) -> dict[str, Any]:
            await asyncio.sleep(delays_by_db[index])
            return make_es_search_response(total=0)

        async def _arsa_side_effect(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            await asyncio.sleep(delays_by_db["trad"])
            return make_solr_arsa_response(num_found=0)

        async def _txsearch_side_effect(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            await asyncio.sleep(delays_by_db["taxonomy"])
            return make_solr_txsearch_response(num_found=0)

        mock_es_search_db_portal.side_effect = _es_side_effect
        mock_arsa_search_db_portal.side_effect = _arsa_side_effect
        mock_txsearch_search_db_portal.side_effect = _txsearch_side_effect

        resp = app_with_db_portal.get("/db-portal/search", params={"q": "x"})

        assert resp.status_code == 200
        body = resp.json()
        assert [e["db"] for e in body["databases"]] == list(_DB_ORDER)


# === AP3 Advanced Search DSL dispatch ===


class TestDbPortalAdvValidDispatch:
    """AP3 adv: valid DSL dispatch routes to ES / ARSA / TXSearch.

    Mock boundary is the HTTP client for each backend (upstream), so the
    parse → validate → compile → dispatch pipeline is exercised end-to-end
    inside the router.
    """

    def test_adv_cross_db_returns_8_counts(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=0)
        mock_txsearch_search_db_portal.return_value = make_solr_txsearch_response(num_found=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"adv": "title:cancer"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert [e["db"] for e in body["databases"]] == list(_DB_ORDER)
        assert all(e["count"] == 0 for e in body["databases"])

    def test_adv_cross_db_with_date_alias_fan_out(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=5)
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=3)
        mock_txsearch_search_db_portal.return_value = make_solr_txsearch_response(num_found=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"adv": "date:[2020-01-01 TO 2024-12-31]"},
        )
        assert resp.status_code == 200
        counts = {e["db"]: e["count"] for e in resp.json()["databases"]}
        assert counts["bioproject"] == 5
        assert counts["trad"] == 3
        # TXSearch degenerates date field → numFound=0 (mock returns 0)
        assert counts["taxonomy"] == 0

    def test_adv_with_db_bioproject_returns_hits(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(
            hits=[{"_id": "PRJDB1", "_source": {"identifier": "PRJDB1", "type": "bioproject"}}],
            total=1,
        )
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"adv": "title:cancer", "db": "bioproject"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["hits"][0]["identifier"] == "PRJDB1"

    def test_adv_with_db_trad_uses_arsa(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(
            docs=[{"PrimaryAccessionNumber": "AB000001", "Definition": "human sample"}],
            num_found=1,
        )
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"adv": 'title:"human"', "db": "trad"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1

    def test_adv_with_db_taxonomy_uses_txsearch(
        self,
        app_with_db_portal: TestClient,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        mock_txsearch_search_db_portal.return_value = make_solr_txsearch_response(
            docs=[{"tax_id": "9606", "scientific_name": "Homo sapiens"}],
            num_found=1,
        )
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"adv": "title:human", "db": "taxonomy"},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_adv_es_body_contains_compiled_query(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        app_with_db_portal.get(
            "/db-portal/search",
            params={"adv": "title:cancer", "db": "bioproject"},
        )
        # 最後の call args の body に compile_to_es 結果が入っていることを確認
        call_args = mock_es_search_db_portal.await_args
        assert call_args is not None
        body = call_args.args[2] if len(call_args.args) >= 3 else call_args.kwargs.get("body")
        assert body is not None
        assert body["query"] == {"match_phrase": {"title": "cancer"}}

    def test_adv_arsa_q_contains_compiled_solr(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=0)
        app_with_db_portal.get(
            "/db-portal/search",
            params={"adv": "title:cancer", "db": "trad"},
        )
        call_args = mock_arsa_search_db_portal.await_args
        assert call_args is not None
        params = call_args.kwargs.get("params")
        assert params is not None
        assert params["q"] == 'Definition:"cancer"'
        assert params["defType"] == "edismax"
        # uf パラメータで allowlist 制御 (defense-in-depth)
        assert "uf" in params

    def test_adv_cursor_with_solr_db_returns_cursor_not_supported(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"adv": "title:cancer", "db": "trad", "cursor": "abc.def"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.cursor_not_supported.value

    def test_adv_cursor_with_es_db_returns_400(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"adv": "title:cancer", "db": "bioproject", "cursor": "abc.def"},
        )
        assert resp.status_code == 400
        # adv + cursor 排他は about:blank
        body = resp.json()
        assert body["type"] == "about:blank"

    def test_adv_nest_depth_exceeded_returns_400(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        dsl = "title:a"
        for i in range(6):
            dsl = f"({dsl} AND title:v{i})"
        resp = app_with_db_portal.get("/db-portal/search", params={"adv": dsl})
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.nest_depth_exceeded.value

    def test_adv_missing_value_returns_400(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"adv": 'title:""'},
        )
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.missing_value.value

    def test_adv_invalid_date_returns_400(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"adv": "date_published:2024-99-99"},
        )
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.invalid_date_format.value

    def test_adv_over_max_length_returns_unexpected_token(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        dsl = "title:" + ("x" * 5000)
        resp = app_with_db_portal.get("/db-portal/search", params={"adv": dsl})
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.unexpected_token.value

    def test_adv_and_q_mutual_exclusion_preserved(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "foo", "adv": "title:cancer", "db": "bioproject"},
        )
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.invalid_query_combination.value
