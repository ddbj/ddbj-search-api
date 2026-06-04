"""Tests for DB Portal API routing, dispatch logic, and response shape.

Covers:
- endpoint registration (trailing slash policy, tags)
- q dispatch matrix (cross + single)
- FastAPI-level enum/Literal validation (422)
- cross-database fan-out flow (8 DBs, count + top hits, success/error mix, all-failed 502)
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
from ddbj_search_api.cursor import CursorPayload, decode_cursor, encode_cursor
from ddbj_search_api.routers.db_portal import clear_taxid_name_cache
from ddbj_search_api.schemas.db_portal import (
    DbPortalCountError,
    DbPortalErrorType,
)
from tests.unit.conftest import (
    get_es_search_body,
    get_es_search_index,
    make_es_search_response,
    make_solr_arsa_response,
    make_solr_txsearch_response,
)

_SOLR_DBS = ("trad", "taxonomy")
_ES_DBS = ("sra", "bioproject", "biosample", "jga", "gea", "metabobank")
_DB_ORDER = ("trad", "sra", "bioproject", "biosample", "jga", "gea", "metabobank", "taxonomy")


# === Routing ===


class TestDbPortalTrailingSlash:
    """Both endpoints reject trailing-slash paths; canonical paths are no-trailing."""

    def test_search_trailing_slash_not_canonical(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/search/")
        assert resp.status_code == 404

    def test_cross_search_trailing_slash_not_canonical(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/cross-search/")
        assert resp.status_code == 404


# === Query combination ===


class TestDbPortalQueryCombination:
    """Query parse / validate error surfacing (400).

    Both endpoints share the same query parsing pipeline, so they raise
    the same error slugs.  cross-search exercises Tier 1/2 paths and
    search exercises the full Tier 1/2/3 allowlist.
    """

    def test_unsupported_syntax_returns_400_unexpected_token_on_cross_search(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        # boost ``^`` は非対応構文。grammar が unexpected-token で reject する。
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": "title:cancer^2"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.unexpected_token.value

    def test_unknown_field_returns_400_on_cross_search(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": "foo:bar"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.unknown_field.value

    def test_invalid_operator_returns_400_on_cross_search(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": "date:cancer*"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.invalid_operator_for_field.value

    def test_unsupported_syntax_with_db_still_400(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "title:cancer^2", "db": "sra"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.unexpected_token.value


# === Endpoint-specific contract: cross-search rejects forbidden params, search requires db ===


class TestDbPortalCrossSearchUnexpectedParameter:
    """/db-portal/cross-search rejects forbidden params with 400 unexpected-parameter."""

    @pytest.mark.parametrize(
        "extra",
        [
            {"db": "sra"},
            {"cursor": "abc.def"},
            {"page": "2"},
            {"perPage": "20"},
            {"sort": "datePublished:desc"},
        ],
    )
    def test_forbidden_param_returns_400_unexpected_parameter(
        self,
        app_with_db_portal: TestClient,
        extra: dict[str, str],
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": "x", **extra},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.unexpected_parameter.value
        # detail mentions the offending parameter name.
        forbidden_name = next(iter(extra.keys()))
        assert forbidden_name in body["detail"]

    def test_first_unexpected_param_named_when_multiple(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        # When multiple forbidden params are present, only the first one
        # (in query string order) is reported.
        resp = app_with_db_portal.get(
            "/db-portal/cross-search?q=x&db=sra&cursor=abc",
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.unexpected_parameter.value
        assert "'db'" in body["detail"]


class TestDbPortalSearchMissingDb:
    """/db-portal/search returns 400 missing-db when db is omitted."""

    def test_missing_db_with_q(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "x"})
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.missing_db.value
        assert "/db-portal/cross-search" in body["detail"]

    def test_missing_db_with_field_clause_q(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "title:cancer"},
        )
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.missing_db.value

    def test_missing_db_takes_priority_over_query_parse(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        """missing-db is raised before query parsing kicks in."""
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "foo AND title:cancer"},
        )
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.missing_db.value


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
            params={"db": "bioproject", "sort": "bogus"},
        )
        assert resp.status_code == 422

    def test_date_modified_sort_returns_422(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "bioproject", "sort": "dateModified:desc"},
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


# === Cross-database fan-out ===


class TestDbPortalCrossSearch:
    """Cross-DB fan-out via /db-portal/cross-search."""

    def test_eight_databases_returned_in_order(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=1234)
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "cancer"})
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
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "cancer"})
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
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "cancer"})
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
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "cancer"})
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
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "cancer"})
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
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "cancer"})
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
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "cancer"})
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
        resp = app_with_db_portal.get("/db-portal/cross-search")
        assert resp.status_code == 200
        # First call is for sra (first ES-backed DB in order). Default
        # ``topHits=10``; ES ``size`` is overshot so post-filter de-dup
        # can still yield 10 unique hits when sameAs alias docs collide.
        # 倍率そのものは router の private constant なので、構造的不変条件
        # (top_hits=10 の整数倍 + overshoot) のみを確認する。
        # ``q`` 省略でも default で ``public_only`` status filter が付くため、
        # query は ``match_all`` ではなく ``bool.filter`` 1 本のみ。
        first_call_body = get_es_search_body(mock_es_search_db_portal, call_index=0)
        assert first_call_body["query"] == {
            "bool": {"filter": [{"term": {"status": "public"}}]},
        }
        observed_size = first_call_body["size"]
        assert observed_size >= 2 * 10, f"overshoot expected: size={observed_size}"
        assert observed_size % 10 == 0, f"size {observed_size} not an integer multiple of top_hits=10"


class TestDbPortalCrossSearchTopHits:
    """Cross-DB ``topHits`` parameter and per-DB ``hits`` envelope."""

    _LIGHTWEIGHT_FIELDS = {
        "identifier",
        "type",
        "url",
        "title",
        "description",
        "organism",
        "status",
        "accessibility",
        "dateCreated",
        "dateModified",
        "datePublished",
        "isPartOf",
    }

    @staticmethod
    def _es_hit(identifier: str, type_: str, **overrides: Any) -> dict[str, Any]:
        source: dict[str, Any] = {
            "identifier": identifier,
            "type": type_,
            "url": f"https://ddbj.example/{identifier}",
            "title": f"title-{identifier}",
            "description": None,
            "organism": None,
            "status": "public",
            "accessibility": "public-access",
            "dateCreated": None,
            "dateModified": None,
            "datePublished": None,
            "isPartOf": type_,
        }
        source.update(overrides)
        return {"_index": f"{type_}-test", "_id": identifier, "_source": source}

    def test_default_top_hits_is_ten(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=5)
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "x"})
        assert resp.status_code == 200
        body = get_es_search_body(mock_es_search_db_portal, call_index=0)
        # Default ``topHits=10`` 、ES ``size`` は de-dup overshoot 込み。倍率は
        # router private なので、整数倍 + overshoot の構造的不変条件で確認する。
        assert body["size"] >= 2 * 10
        assert body["size"] % 10 == 0
        # Source allowlist is the 12-field lightweight contract.
        assert set(body["_source"]) == self._LIGHTWEIGHT_FIELDS
        assert body["track_total_hits"] is True
        assert "sort" in body

    def test_top_hits_zero_returns_count_only(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=5)
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "x", "topHits": 0})
        assert resp.status_code == 200
        body = get_es_search_body(mock_es_search_db_portal, call_index=0)
        assert body["size"] == 0
        # No source filter / no sort / no track_total_hits in count-only path.
        assert "_source" not in body
        assert "sort" not in body
        assert "track_total_hits" not in body
        # Each DbPortalCount.hits is null in count-only mode.
        for entry in resp.json()["databases"]:
            assert entry["hits"] is None

    def test_top_hits_explicit_size_propagated(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=5)
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "x", "topHits": 25})
        assert resp.status_code == 200
        body = get_es_search_body(mock_es_search_db_portal, call_index=0)
        # Explicit ``topHits`` 値も de-dup overshoot 倍率で ES へ流れる。倍率自体
        # は router 内部実装、ここでは ``size >= 2 * top_hits`` + 整数倍を検証。
        assert body["size"] >= 2 * 25
        assert body["size"] % 25 == 0

    def test_top_hits_max_50_accepted(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "x", "topHits": 50})
        assert resp.status_code == 200

    def test_top_hits_above_50_returns_422(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "x", "topHits": 51})
        assert resp.status_code == 422

    def test_top_hits_negative_returns_422(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "x", "topHits": -1})
        assert resp.status_code == 422

    def test_top_hits_non_int_returns_422(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "x", "topHits": "many"})
        assert resp.status_code == 422

    def test_top_hits_param_in_allowlist(self, app_with_db_portal: TestClient) -> None:
        # ``topHits`` is on the allowlist; only forbidden params trigger
        # 400 ``unexpected-parameter``.
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "x", "topHits": 5})
        assert resp.status_code == 200

    def test_es_hits_returned_in_dbportalhit_shape(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        # Build per-DB ES responses keyed to call order:
        # sra → bioproject → biosample → jga → gea → metabobank.
        sra_hit = self._es_hit("DRR1", "sra-run")
        bp_hit = self._es_hit("PRJDB1", "bioproject")
        mock_es_search_db_portal.side_effect = [
            make_es_search_response(total=1, hits=[sra_hit]),
            make_es_search_response(total=1, hits=[bp_hit]),
            make_es_search_response(total=0),
            make_es_search_response(total=0),
            make_es_search_response(total=0),
            make_es_search_response(total=0),
        ]
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "x", "topHits": 5})
        assert resp.status_code == 200
        by_db = {e["db"]: e for e in resp.json()["databases"]}
        assert by_db["sra"]["count"] == 1
        assert len(by_db["sra"]["hits"]) == 1
        assert by_db["sra"]["hits"][0]["identifier"] == "DRR1"
        assert by_db["sra"]["hits"][0]["type"] == "sra-run"
        assert by_db["bioproject"]["hits"][0]["identifier"] == "PRJDB1"
        # Empty-hit ES DB still gets [] (not None) when topHits>=1.
        assert by_db["biosample"]["hits"] == []

    def test_arsa_lightweight_fixed_values(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(
            num_found=2,
            docs=[
                {
                    "PrimaryAccessionNumber": "GL589895",
                    "Definition": "Mus musculus scaffold",
                    "Organism": "Mus musculus",
                    "Date": "20150313",
                    "Feature": ['source 1..1000\n/db_xref="taxon:10090"'],
                    # Trad-only extras to verify they get dropped.
                    "Division": "CON",
                    "MolecularType": "DNA",
                    "SequenceLength": 635881,
                },
            ],
        )
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "x", "topHits": 3})
        assert resp.status_code == 200
        trad = next(e for e in resp.json()["databases"] if e["db"] == "trad")
        assert trad["count"] == 2
        assert len(trad["hits"]) == 1
        h = trad["hits"][0]
        assert h["identifier"] == "GL589895"
        assert h["type"] == "trad"
        assert h["url"].endswith("/GL589895/")
        assert h["title"] == "Mus musculus scaffold"
        assert h["organism"] == {"identifier": "10090", "name": "Mus musculus"}
        assert h["datePublished"] == "2015-03-13"
        # Fixed values per the Solr-side public-only contract.
        assert h["status"] == "public"
        assert h["accessibility"] == "public-access"
        assert h["isPartOf"] == "trad"
        # Date fields not in ARSA → null.
        assert h["dateCreated"] is None
        assert h["dateModified"] is None
        assert h["description"] is None
        # Trad-only extras must NOT leak into the lightweight schema.
        assert "division" not in h
        assert "molecularType" not in h
        assert "sequenceLength" not in h

    def test_txsearch_lightweight_fixed_values(
        self,
        app_with_db_portal: TestClient,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        mock_txsearch_search_db_portal.return_value = make_solr_txsearch_response(
            num_found=1,
            docs=[
                {
                    "tax_id": 9606,
                    "scientific_name": "Homo sapiens",
                    # Taxonomy-only extras to verify they get dropped.
                    "rank": "species",
                    "common_name": ["human"],
                    "japanese_name": ["ヒト"],
                    "lineage": ["Homo sapiens", "Homo", "Hominidae"],
                },
            ],
        )
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "x", "topHits": 3})
        assert resp.status_code == 200
        tax = next(e for e in resp.json()["databases"] if e["db"] == "taxonomy")
        assert tax["count"] == 1
        assert len(tax["hits"]) == 1
        h = tax["hits"][0]
        assert h["identifier"] == "9606"
        assert h["type"] == "taxonomy"
        assert h["url"] == "https://ddbj.nig.ac.jp/tx_search/9606?view=info"
        assert h["title"] == "Homo sapiens"
        assert h["organism"] == {"identifier": "9606", "name": "Homo sapiens"}
        # Fixed values + nulls.
        assert h["status"] == "public"
        assert h["accessibility"] == "public-access"
        assert h["isPartOf"] == "taxonomy"
        assert h["datePublished"] is None
        assert h["dateCreated"] is None
        assert h["dateModified"] is None
        # Taxonomy-only extras must NOT leak into the lightweight schema.
        assert "rank" not in h
        assert "commonName" not in h
        assert "japaneseName" not in h
        assert "lineage" not in h

    def test_per_db_error_returns_empty_hits(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        # All ES calls timeout; ES DB entries should have hits=[] (not None)
        # because topHits>=1.
        mock_es_search_db_portal.side_effect = httpx.TimeoutException("timeout")
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "x", "topHits": 5})
        # Solr mocks default to empty success → 200 (not 502 unless all 8 fail).
        assert resp.status_code == 200
        by_db = {e["db"]: e for e in resp.json()["databases"]}
        for db in _ES_DBS:
            assert by_db[db]["error"] == DbPortalCountError.timeout.value
            assert by_db[db]["hits"] == []

    def test_per_db_error_with_top_hits_zero_keeps_hits_null(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.side_effect = httpx.TimeoutException("timeout")
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "x", "topHits": 0})
        assert resp.status_code == 200
        by_db = {e["db"]: e for e in resp.json()["databases"]}
        for db in _ES_DBS:
            assert by_db[db]["error"] == DbPortalCountError.timeout.value
            assert by_db[db]["hits"] is None


class TestDbPortalAdvCrossSearchTopHits:
    """``topHits`` propagates through the q with field clauses cross-search path."""

    _LIGHTWEIGHT_FIELDS = {
        "identifier",
        "type",
        "url",
        "title",
        "description",
        "organism",
        "status",
        "accessibility",
        "dateCreated",
        "dateModified",
        "datePublished",
        "isPartOf",
    }

    def test_adv_es_body_uses_top_hits_size_and_lightweight_source(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=1)
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": "title:cancer", "topHits": 7},
        )
        assert resp.status_code == 200
        body = get_es_search_body(mock_es_search_db_portal, call_index=0)
        # field clause を含む q でも ES ``size`` は de-dup overshoot 倍率。倍率は
        # router private なので、整数倍 + overshoot の構造的不変条件で確認する。
        assert body["size"] >= 2 * 7
        assert body["size"] % 7 == 0
        assert set(body["_source"]) == self._LIGHTWEIGHT_FIELDS
        assert body["track_total_hits"] is True
        assert "sort" in body

    def test_adv_arsa_rows_match_top_hits_and_emit_lightweight_hits(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(
            num_found=1,
            docs=[
                {
                    "PrimaryAccessionNumber": "GL589895",
                    "Definition": "Mus musculus scaffold",
                    "Organism": "Mus musculus",
                    "Date": "20150313",
                },
            ],
        )
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": "title:cancer", "topHits": 4},
        )
        assert resp.status_code == 200
        params = mock_arsa_search_db_portal.call_args.kwargs["params"]
        assert params["rows"] == "4"
        trad = next(e for e in resp.json()["databases"] if e["db"] == "trad")
        assert len(trad["hits"]) == 1
        h = trad["hits"][0]
        assert h["status"] == "public"
        assert h["isPartOf"] == "trad"

    def test_adv_txsearch_rows_match_top_hits_and_emit_lightweight_hits(
        self,
        app_with_db_portal: TestClient,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        mock_txsearch_search_db_portal.return_value = make_solr_txsearch_response(
            num_found=1,
            docs=[{"tax_id": 9606, "scientific_name": "Homo sapiens"}],
        )
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": "title:human", "topHits": 6},
        )
        assert resp.status_code == 200
        params = mock_txsearch_search_db_portal.call_args.kwargs["params"]
        assert params["rows"] == "6"
        tax = next(e for e in resp.json()["databases"] if e["db"] == "taxonomy")
        assert len(tax["hits"]) == 1
        h = tax["hits"][0]
        assert h["status"] == "public"
        assert h["isPartOf"] == "taxonomy"

    def test_adv_top_hits_zero_uses_count_only_path(
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
            "/db-portal/cross-search",
            params={"q": "title:cancer", "topHits": 0},
        )
        assert resp.status_code == 200
        es_body = get_es_search_body(mock_es_search_db_portal, call_index=0)
        assert es_body["size"] == 0
        assert "_source" not in es_body
        assert "sort" not in es_body
        assert "track_total_hits" not in es_body
        arsa_params = mock_arsa_search_db_portal.call_args.kwargs["params"]
        assert arsa_params["rows"] == "0"
        tx_params = mock_txsearch_search_db_portal.call_args.kwargs["params"]
        assert tx_params["rows"] == "0"
        for entry in resp.json()["databases"]:
            assert entry["hits"] is None


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
        assert get_es_search_index(mock_es_search_db_portal) == index

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
        body = get_es_search_body(mock_es_search_db_portal)
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
        body = get_es_search_body(mock_es_search_db_portal)
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
        body = get_es_search_body(mock_es_search_db_portal)
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

    def test_cursor_without_db_returns_400_missing_db(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        """db is required on /db-portal/search; cursor + no db → missing-db."""
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"cursor": "some-cursor"},
        )
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.missing_db.value

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

    def test_cursor_with_keyword_operator_and_returns_400(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        """cursor + ``keywordOperator=AND`` (= default の OR 以外) は 400 排他."""
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
            params={"cursor": token, "db": "bioproject", "keywordOperator": "AND"},
        )
        assert resp.status_code == 400
        assert "keywordOperator" in resp.json()["detail"]

    def test_cursor_with_keyword_operator_default_or_passes(
        self,
        app_with_db_portal: TestClient,
        mock_es_open_pit_db_portal: AsyncMock,
        mock_es_search_with_pit_db_portal: AsyncMock,
    ) -> None:
        """cursor + ``keywordOperator`` 未指定 (= default OR) は 排他検出されず通る.

        Critical 級の回帰: default が AND のままだと、user 未指定でも default !=
        "OR" 判定で必ず 400 になる. default を OR に統一した後の sanity check.
        """
        mock_es_search_with_pit_db_portal.return_value = make_es_search_response(total=0)
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
            params={"cursor": token, "db": "bioproject"},
        )
        # default OR でも、cursor 排他に引っかからずに 200 を返すことを確認
        # (excluding ES 通信失敗等の他の理由による 5xx).
        assert resp.status_code == 200, resp.text


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
        # DbPortalHit discriminated union の type は subtype 付き (sra-study など)
        db_to_type = {
            "sra": "sra-study",
            "jga": "jga-study",
            "bioproject": "bioproject",
            "biosample": "biosample",
            "gea": "gea",
            "metabobank": "metabobank",
        }
        source_type = db_to_type[db]
        hits: list[dict[str, Any]] = [
            {
                "_id": f"DOC{i}",
                "_source": {"identifier": f"DOC{i}", "type": source_type},
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

    def test_400_adv_invalid_dsl_shape(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": "foo:bar"},
        )
        assert resp.status_code == 400
        assert resp.headers["content-type"].startswith("application/problem+json")
        body = resp.json()
        assert body["type"] == DbPortalErrorType.unknown_field.value
        assert body["title"] == "Bad Request"
        assert body["status"] == 400
        assert "detail" in body
        # column 情報が自然言語で detail に埋め込まれる (機械判別は type URI slug のみ)
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

    def test_400_unexpected_parameter_shape(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": "x", "db": "sra"},
        )
        assert resp.status_code == 400
        assert resp.headers["content-type"].startswith("application/problem+json")
        body = resp.json()
        assert body["type"] == DbPortalErrorType.unexpected_parameter.value
        assert body["title"] == "Bad Request"
        assert body["status"] == 400
        assert "db" in body["detail"]
        assert body["instance"] == "/db-portal/cross-search"

    def test_400_missing_db_shape(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "x"},
        )
        assert resp.status_code == 400
        assert resp.headers["content-type"].startswith("application/problem+json")
        body = resp.json()
        assert body["type"] == DbPortalErrorType.missing_db.value
        assert body["title"] == "Bad Request"
        assert body["status"] == 400
        assert body["instance"] == "/db-portal/search"

    def test_422_uses_about_blank(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "unknown"},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["type"] == "about:blank"

    def test_request_id_echoed(self, app_with_db_portal: TestClient) -> None:
        # エラーレスポンス (ProblemDetails) で X-Request-ID が echo されること。
        # unknown-field を発生させて 400 を取り、requestId フィールドを検証する。
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": "foo:bar"},
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
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "x"})
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
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "x"})
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
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "x"})
        by_db = {e["db"]: e for e in resp.json()["databases"]}
        assert by_db["trad"]["error"] == DbPortalCountError.upstream_5xx.value

    def test_txsearch_timeout_mapped(
        self,
        app_with_db_portal: TestClient,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        mock_txsearch_search_db_portal.side_effect = httpx.TimeoutException("timeout")
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "x"})
        by_db = {e["db"]: e for e in resp.json()["databases"]}
        assert by_db["taxonomy"]["error"] == DbPortalCountError.timeout.value

    def test_arsa_unexpected_response_shape_unknown(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        mock_arsa_search_db_portal.return_value = {"no_response_key": True}
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "x"})
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


class TestDbPortalOrganismIdTradResolution:
    """organism_id (TaxID) を trad で検索: TXSearch で学名解決 → organism_name に rewrite.

    ARSA に TaxID field が無いので、trad arm は TXSearch で TaxID→学名を解決し organism_name
    (Organism / Lineage) に rewrite してから ARSA に投げる。解決失敗 / wildcard は 0 件、
    TXSearch 障害 / 未設定は error。count レベルで挙動を固定する。
    """

    @pytest.fixture(autouse=True)
    def _clear_taxid_cache(self) -> None:
        # module-level cache がテスト間で漏れないよう各テスト前にクリアする。
        clear_taxid_name_cache()

    # --- single (db=trad) ---

    def test_single_resolves_taxid_to_organism_name(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        mock_txsearch_search_db_portal.return_value = make_solr_txsearch_response(
            docs=[{"tax_id": "9606", "scientific_name": "Homo sapiens"}],
        )
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=42)
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "organism_id:9606", "db": "trad"})
        assert resp.status_code == 200
        assert resp.json()["total"] == 42
        arsa_q = mock_arsa_search_db_portal.call_args.kwargs["params"]["q"]
        assert 'Organism:"Homo sapiens"' in arsa_q
        assert 'Lineage:"Homo sapiens"' in arsa_q

    def test_single_unresolved_taxid_is_zero_without_arsa_call(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        mock_txsearch_search_db_portal.return_value = make_solr_txsearch_response(docs=[])
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "organism_id:99999999", "db": "trad"})
        assert resp.status_code == 200
        assert resp.json()["total"] == 0
        assert mock_arsa_search_db_portal.call_count == 0

    def test_single_wildcard_taxid_is_zero_without_any_upstream_call(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "organism_id:96*", "db": "trad"})
        assert resp.status_code == 200
        assert resp.json()["total"] == 0
        # wildcard TaxID は学名解決できない → resolver も ARSA も叩かず 0 件。
        assert mock_txsearch_search_db_portal.call_count == 0
        assert mock_arsa_search_db_portal.call_count == 0

    def test_single_txsearch_failure_returns_502(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        mock_txsearch_search_db_portal.side_effect = httpx.ConnectError("refused")
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "organism_id:9606", "db": "trad"})
        assert resp.status_code == 502
        assert mock_arsa_search_db_portal.call_count == 0

    def test_single_txsearch_unconfigured_returns_502(
        self,
        app_with_db_portal: TestClient,
        config: AppConfig,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        object.__setattr__(config, "solr_txsearch_url", None)
        resp = app_with_db_portal.get("/db-portal/search", params={"q": "organism_id:9606", "db": "trad"})
        assert resp.status_code == 502
        # 未設定は resolver が round-trip 前に例外を投げる。
        assert mock_txsearch_search_db_portal.call_count == 0
        assert mock_arsa_search_db_portal.call_count == 0

    def test_single_organism_id_and_other_field_both_kept(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        mock_txsearch_search_db_portal.return_value = make_solr_txsearch_response(
            docs=[{"tax_id": "9606", "scientific_name": "Homo sapiens"}],
        )
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=7)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "organism_id:9606 AND title:genome", "db": "trad"},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 7
        arsa_q = mock_arsa_search_db_portal.call_args.kwargs["params"]["q"]
        assert 'Organism:"Homo sapiens"' in arsa_q
        assert "Definition" in arsa_q

    def test_single_cache_avoids_second_txsearch_call(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        mock_txsearch_search_db_portal.return_value = make_solr_txsearch_response(
            docs=[{"tax_id": "9606", "scientific_name": "Homo sapiens"}],
        )
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=1)
        for _ in range(2):
            resp = app_with_db_portal.get("/db-portal/search", params={"q": "organism_id:9606", "db": "trad"})
            assert resp.status_code == 200
        # 2 回目は cache ヒットで TXSearch を再度叩かない。
        assert mock_txsearch_search_db_portal.call_count == 1

    # --- cross-search ---

    def test_cross_trad_arm_returns_count_not_field_not_applicable(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        mock_txsearch_search_db_portal.return_value = make_solr_txsearch_response(
            docs=[{"tax_id": "9606", "scientific_name": "Homo sapiens"}],
            num_found=3,
        )
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=88)
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "organism_id:9606"})
        assert resp.status_code == 200
        by_db = {e["db"]: e for e in resp.json()["databases"]}
        assert by_db["trad"]["count"] == 88
        assert by_db["trad"]["error"] is None
        arsa_q = mock_arsa_search_db_portal.call_args.kwargs["params"]["q"]
        assert 'Organism:"Homo sapiens"' in arsa_q

    def test_cross_trad_arm_unresolved_is_zero(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        mock_txsearch_search_db_portal.return_value = make_solr_txsearch_response(docs=[])
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "organism_id:99999999"})
        assert resp.status_code == 200
        by_db = {e["db"]: e for e in resp.json()["databases"]}
        assert by_db["trad"]["count"] == 0
        assert by_db["trad"]["error"] is None
        assert mock_arsa_search_db_portal.call_count == 0

    def test_cross_trad_arm_txsearch_failure_is_error_but_overall_200(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        mock_txsearch_search_db_portal.side_effect = httpx.ConnectError("refused")
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "organism_id:9606"})
        # ES arm は成功するので全滅 502 にはならない。
        assert resp.status_code == 200
        by_db = {e["db"]: e for e in resp.json()["databases"]}
        assert by_db["trad"]["count"] is None
        assert by_db["trad"]["error"] is not None
        assert by_db["trad"]["error"] != "field_not_applicable"
        assert mock_arsa_search_db_portal.call_count == 0


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
        assert hit["url"] == "https://ddbj.nig.ac.jp/tx_search/9606?view=info"
        assert hit["rank"] == "species"
        assert hit["commonName"] == "human"

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


# === Parallel fan-out + per-backend timeouts ===


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


class TestDbPortalCrossSearchParallelization:
    """Parallel fan-out + per-backend timeouts + total timeout.

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

        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "x"})

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

        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "x"})

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

        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "x"})

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

        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "x"})

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

        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "x"})

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

        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "x"})

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
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "x"})
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

        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "x"})

        assert resp.status_code == 200


class TestDbPortalCrossSearchPBT:
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

        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "x"})

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

        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "x"})

        assert resp.status_code == 200
        body = resp.json()
        assert [e["db"] for e in body["databases"]] == list(_DB_ORDER)


# === Field-clause query dispatch (ES / ARSA / TXSearch) ===


class TestDbPortalAdvValidDispatch:
    """valid q (field clauses) dispatch routes to ES / ARSA / TXSearch.

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
            "/db-portal/cross-search",
            params={"q": "title:cancer"},
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
            "/db-portal/cross-search",
            params={"q": "date:[2020-01-01 TO 2024-12-31]"},
        )
        assert resp.status_code == 200
        by_db = {e["db"]: e for e in resp.json()["databases"]}
        # ES 6DB は date alias (3 日付 OR) を持つ
        assert by_db["bioproject"]["count"] == 5
        # Solr 2DB は date alias 非対応 → per-arm 簡約で対象外 (Solr を叩かず count=null)
        assert by_db["trad"]["count"] is None
        assert by_db["trad"]["error"] == "field_not_applicable"
        assert by_db["taxonomy"]["count"] is None
        assert by_db["taxonomy"]["error"] == "field_not_applicable"

    def test_adv_cross_db_field_not_applicable_lists_unavailable_fields(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        # publication は biosample / taxonomy で非対応、他 DB では検索可。
        mock_es_search_db_portal.return_value = make_es_search_response(total=5)
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=3)
        mock_txsearch_search_db_portal.return_value = make_solr_txsearch_response(num_found=0)
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": "publication:cancer"},
        )
        assert resp.status_code == 200
        by_db = {e["db"]: e for e in resp.json()["databases"]}
        # 非対応 arm は原因 field を camelCase unavailableFields で名指しする
        for db in ("biosample", "taxonomy"):
            assert by_db[db]["count"] is None
            assert by_db[db]["error"] == "field_not_applicable"
            assert by_db[db]["unavailableFields"] == ["publication"]
        # 対応 arm では null (キーは全 arm で常に出る)
        assert "unavailableFields" in by_db["bioproject"]
        assert by_db["bioproject"]["unavailableFields"] is None
        assert by_db["trad"]["unavailableFields"] is None

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
            params={"q": "title:cancer", "db": "bioproject"},
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
            params={"q": 'title:"human"', "db": "trad"},
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
            params={"q": "title:human", "db": "taxonomy"},
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
            params={"q": "title:cancer", "db": "bioproject"},
        )
        # 最後の call args の body に compile_to_es 結果が入っていることを確認。
        assert mock_es_search_db_portal.await_count >= 1
        body = get_es_search_body(mock_es_search_db_portal)
        assert body["query"] == {
            "bool": {
                "should": [
                    {"match_phrase": {"title": "cancer"}},
                    {"match_phrase_prefix": {"title": "cancer"}},
                ],
                "minimum_should_match": 1,
                "filter": [{"term": {"status": "public"}}],
            },
        }

    def test_adv_arsa_q_contains_compiled_solr(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=0)
        app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "title:cancer", "db": "trad"},
        )
        call_args = mock_arsa_search_db_portal.await_args
        assert call_args is not None
        params = call_args.kwargs.get("params")
        assert params is not None
        assert params["q"] == '(Definition:"cancer" OR Definition:cancer*)'
        assert params["defType"] == "edismax"
        # uf パラメータで allowlist 制御 (defense-in-depth)
        assert "uf" in params

    def test_adv_cursor_with_solr_db_returns_cursor_not_supported(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "title:cancer", "db": "trad", "cursor": "abc.def"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.cursor_not_supported.value

    def test_cursor_with_q_on_es_db_returns_cursor_exclusivity(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        """cursor + q (どんな q でも) は ES DB でも about:blank (cursor exclusivity) で 400."""
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "title:cancer", "db": "bioproject", "cursor": "abc.def"},
        )
        assert resp.status_code == 400
        body = resp.json()
        # _validate_cursor_exclusivity が plain HTTPException で about:blank を返す。
        assert "cursor" in body["detail"].lower()
        assert "q" in body["detail"].lower()

    def test_adv_nest_depth_exceeded_returns_400(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        dsl = "title:a"
        for i in range(6):
            dsl = f"({dsl} AND title:v{i})"
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": dsl})
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.nest_depth_exceeded.value

    def test_adv_missing_value_returns_400(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": 'title:""'},
        )
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.missing_value.value

    def test_adv_invalid_date_returns_400(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": "date_published:2024-99-99"},
        )
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.invalid_date_format.value

    def test_adv_over_max_length_returns_unexpected_token(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        dsl = "title:" + ("x" * 5000)
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": dsl})
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.unexpected_token.value


# === Tier 2 / Tier 3 end-to-end ===


class TestDbPortalAdvTier2Tier3:
    """Tier 2 (submitter / publication) と Tier 3 (DB 別 28 per-DB) の
    cross-mode 拒否と single-mode 成功、nested query 発行を検証。
    """

    def test_tier3_in_cross_mode_returns_400(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        """Tier 3 x cross mode は field-not-available-in-cross-db で 400."""
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": "library_strategy:WGS"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.field_not_available_in_cross_db.value

    def test_tier3_cross_mode_detail_includes_single_db_hint(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        """detail 文字列に候補 DB 列挙 (use db=sra)。"""
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": "library_strategy:WGS"},
        )
        body = resp.json()
        assert "use db=sra" in body["detail"]

    def test_tier3_cross_mode_detail_lists_multiple_candidate_dbs(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        """grant_agency は BioProject + JGA 共通 → 両方列挙."""
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": 'grant_agency:"NIH"'},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "use db=bioproject or db=jga" in body["detail"]

    def test_tier3_taxonomy_cross_mode_rejected(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": "rank:species"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.field_not_available_in_cross_db.value
        assert "use db=taxonomy" in body["detail"]

    def test_tier3_sra_single_mode_compiles_term_query(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        """`library_strategy:WGS AND platform:ILLUMINA` + db=sra → ES term queries."""
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={
                "q": "library_strategy:WGS AND platform:ILLUMINA",
                "db": "sra",
            },
        )
        assert resp.status_code == 200
        body = get_es_search_body(mock_es_search_db_portal)
        # compiled query が term + term の AND になっている
        must = body["query"]["bool"]["must"]
        assert {"term": {"libraryStrategy.keyword": "WGS"}} in must
        assert {"term": {"platform.keyword": "ILLUMINA"}} in must

    def test_tier3_bioproject_grant_agency_nested2(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        """`grant_agency:JSPS` + db=bioproject → 2 段 nested query."""
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "grant_agency:JSPS", "db": "bioproject"},
        )
        assert resp.status_code == 200
        body = get_es_search_body(mock_es_search_db_portal)
        # 期待形: bool.must の先頭 = nested(grant) → nested(grant.agency) →
        # grant.agency.name に対する前方一致対応の bool.should wrapper。
        outer_bool = body["query"]["bool"]
        assert outer_bool["filter"] == [{"term": {"status": "public"}}]
        outer = outer_bool["must"][0]
        assert outer["nested"]["path"] == "grant"
        inner = outer["nested"]["query"]
        assert inner["nested"]["path"] == "grant.agency"
        assert inner["nested"]["query"] == {
            "bool": {
                "should": [
                    {"match_phrase": {"grant.agency.name": "JSPS"}},
                    {"match_phrase_prefix": {"grant.agency.name": "JSPS"}},
                ],
                "minimum_should_match": 1,
            },
        }

    def test_tier2_submitter_nested_query_to_es(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        """Tier 2 submitter は cross mode で ES 単一 DB search にも nested で届く."""
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": 'submitter:"Tokyo University"', "db": "bioproject"},
        )
        assert resp.status_code == 200
        body = get_es_search_body(mock_es_search_db_portal)
        assert body["query"] == {
            "bool": {
                "must": [
                    {
                        "nested": {
                            "path": "organization",
                            "query": {"match_phrase": {"organization.name": "Tokyo University"}},
                            "ignore_unmapped": True,
                        },
                    },
                ],
                "filter": [{"term": {"status": "public"}}],
            },
        }

    def test_tier2_cross_mode_fan_out_to_all_dbs(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        """Tier 2 submitter は cross mode で 8 DB fan-out される。"""
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=0)
        mock_txsearch_search_db_portal.return_value = make_solr_txsearch_response(num_found=0)
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": 'submitter:"DDBJ"'},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert [e["db"] for e in body["databases"]] == list(_DB_ORDER)

    def test_tier3_taxonomy_rank_to_txsearch(
        self,
        app_with_db_portal: TestClient,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        """`rank:species` + db=taxonomy → TXSearch に q=rank:"species" が届く."""
        mock_txsearch_search_db_portal.return_value = make_solr_txsearch_response(
            docs=[
                {"tax_id": "9606", "scientific_name": "Homo sapiens", "rank": "species"},
            ],
            num_found=1,
        )
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "rank:species", "db": "taxonomy"},
        )
        assert resp.status_code == 200
        params = mock_txsearch_search_db_portal.call_args.kwargs["params"]
        assert 'rank:"species"' in params["q"]

    def test_tier3_trad_division_to_arsa(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        """`division:BCT` + db=trad → ARSA に q=Division:"BCT" が届く."""
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "division:BCT", "db": "trad"},
        )
        assert resp.status_code == 200
        params = mock_arsa_search_db_portal.call_args.kwargs["params"]
        assert 'Division:"BCT"' in params["q"]

    def test_tier3_trad_sequence_length_range_to_arsa(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        """number range query が Solr range 構文で届く."""
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "sequence_length:[100 TO 5000]", "db": "trad"},
        )
        assert resp.status_code == 200
        params = mock_arsa_search_db_portal.call_args.kwargs["params"]
        assert "SequenceLength:[100 TO 5000]" in params["q"]

    def test_tier3_not_equals_compiled_to_must_not(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        """GUI の not_equals は DSL の NOT FieldClause → ES must_not."""
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "NOT platform:ILLUMINA", "db": "sra"},
        )
        assert resp.status_code == 200
        body = get_es_search_body(mock_es_search_db_portal)
        body_bool = body["query"]["bool"]
        assert body_bool["must_not"] == [{"term": {"platform.keyword": "ILLUMINA"}}]
        assert body_bool["filter"] == [{"term": {"status": "public"}}]

    def test_tier3_unknown_field_still_rejected(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        """allowlist 外 field は ``unknown-field`` で 400.

        実在しない synthetic な field 名を使う。実在 field を例に取ると
        allowlist 拡張で test が drift する (L33 grammar は ``[a-z_]+`` を
        受理するので parse は通る、validator 段階で reject されるはず)。
        """
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "synthetic_unknown_field:Japan", "db": "biosample"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.unknown_field.value

    def test_sra_number_non_digit_rejected_as_invalid_operator(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        """number 型の非 digit 値は invalid_operator_for_field (new slug 不設けの方針)."""
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"q": "sequence_length:abc", "db": "trad"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.invalid_operator_for_field.value


# === Status filter ===


_PUBLIC_ONLY_CLAUSE = {"term": {"status": "public"}}
_INCLUDE_SUPPRESSED_CLAUSE = {"terms": {"status": ["public", "suppressed"]}}


def _has_multi_match_phrase_prefix(node: Any) -> bool:
    """ツリー内に FreeText 前方一致 (``multi_match`` で ``type=phrase_prefix``) があるか。

    field-scoped contains の ``match_phrase_prefix`` とは別物 (こちらは keyword box の
    前方一致で、suppressed 解禁時に抑止される対象)。
    """
    if isinstance(node, dict):
        mm = node.get("multi_match")
        if isinstance(mm, dict) and mm.get("type") == "phrase_prefix":
            return True
        return any(_has_multi_match_phrase_prefix(v) for v in node.values())
    if isinstance(node, list):
        return any(_has_multi_match_phrase_prefix(v) for v in node)
    return False


def _extract_status_clause(es_query: dict[str, Any]) -> dict[str, Any] | None:
    """ES query body から status filter clause を抽出する。

    ``bool.filter`` 配列の中から ``term`` / ``terms`` 形式で
    ``status`` を絞っている句を 1 つ返す (見つからなければ ``None``)。
    """
    bool_body = es_query.get("bool")
    if not isinstance(bool_body, dict):
        return None
    filters = bool_body.get("filter", [])
    if not isinstance(filters, list):
        return None
    for raw_f in filters:
        if not isinstance(raw_f, dict):
            continue
        f: dict[str, Any] = raw_f
        if "term" in f and "status" in f.get("term", {}):
            return f
        if "terms" in f and "status" in f.get("terms", {}):
            return f
    return None


class TestDbPortalCrossSearchSimpleStatusFilter:
    """`/db-portal/cross-search` simple ``q`` 経路の status filter 適用。

    ``/entries/*`` と同じ ``detect_accession_exact_match`` を使う:
    通常 ``public_only``、``q`` が単一 accession ID 完全一致のときのみ
    ``include_suppressed``。判定は ``q`` から 1 回行い 6 ES DB 全部に共通の
    query body を流す (詳細は docs/db-portal-api-spec.md § データ可視性)。
    """

    def test_no_q_uses_public_only(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get("/db-portal/cross-search")
        assert resp.status_code == 200
        for call in mock_es_search_db_portal.call_args_list:
            assert _extract_status_clause(call.args[2]["query"]) == _PUBLIC_ONLY_CLAUSE

    def test_free_text_uses_public_only(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "cancer"})
        assert resp.status_code == 200
        for call in mock_es_search_db_portal.call_args_list:
            assert _extract_status_clause(call.args[2]["query"]) == _PUBLIC_ONLY_CLAUSE

    @pytest.mark.parametrize(
        "q",
        ["PRJDB1234", "DRA000001", "JGAS000001", "SAMD00000001"],
    )
    def test_accession_q_allows_suppressed(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        q: str,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": q})
        assert resp.status_code == 200
        for call in mock_es_search_db_portal.call_args_list:
            assert _extract_status_clause(call.args[2]["query"]) == _INCLUDE_SUPPRESSED_CLAUSE

    def test_accession_with_quotes_allows_suppressed(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": '"PRJDB1234"'},
        )
        assert resp.status_code == 200
        for call in mock_es_search_db_portal.call_args_list:
            assert _extract_status_clause(call.args[2]["query"]) == _INCLUDE_SUPPRESSED_CLAUSE

    def test_accession_q_disables_freetext_prefix(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        # accession 完全一致で suppressed を解禁したクエリは FreeText の前方一致
        # (multi_match{type:phrase_prefix}) を出してはならない。出すと PRJDB1234* が
        # 別 accession PRJDB12345 の suppressed に当たり漏洩する (docs § データ可視性)。
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "PRJDB1234"})
        assert resp.status_code == 200
        assert mock_es_search_db_portal.call_args_list
        for call in mock_es_search_db_portal.call_args_list:
            # 解禁されていること (前提) と、前方一致が無いこと (本題) を両方 pin。
            assert _extract_status_clause(call.args[2]["query"]) == _INCLUDE_SUPPRESSED_CLAUSE
            assert not _has_multi_match_phrase_prefix(call.args[2]["query"])

    def test_free_text_keeps_prefix(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        # 解禁していない通常 free-text は前方一致を出す (ゲートが条件付きで、常時 OFF で
        # 通ってしまう mutation を排除する positive control)。
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "cancer"})
        assert resp.status_code == 200
        assert mock_es_search_db_portal.call_args_list
        for call in mock_es_search_db_portal.call_args_list:
            assert _extract_status_clause(call.args[2]["query"]) == _PUBLIC_ONLY_CLAUSE
            assert _has_multi_match_phrase_prefix(call.args[2]["query"])

    def test_multi_token_q_uses_public_only(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        """カンマ区切りの multi-token は accession 解放対象外。"""
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": "PRJDB1234,DRA000001"},
        )
        assert resp.status_code == 200
        for call in mock_es_search_db_portal.call_args_list:
            assert _extract_status_clause(call.args[2]["query"]) == _PUBLIC_ONLY_CLAUSE

    def test_wildcard_field_clause_uses_public_only(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        """field clause 形式のワイルドカード ``identifier:PRJDB*`` は accession 解放対象外."""
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "identifier:PRJDB*"})
        assert resp.status_code == 200
        for call in mock_es_search_db_portal.call_args_list:
            assert _extract_status_clause(call.args[2]["query"]) == _PUBLIC_ONLY_CLAUSE

    def test_non_dbtype_accession_uses_public_only(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        """DbType に含まれない accession (GSE は geo) は解放対象外。"""
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "GSE12345"})
        assert resp.status_code == 200
        for call in mock_es_search_db_portal.call_args_list:
            assert _extract_status_clause(call.args[2]["query"]) == _PUBLIC_ONLY_CLAUSE

    def test_all_six_es_dbs_share_status_mode(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        """accession q で 6 ES DB 全部に同一 status filter (1 回判定 → fan-out)。"""
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "PRJDB1234"})
        assert resp.status_code == 200
        # 6 ES DB すべてに同じ include_suppressed が流れる
        assert len(mock_es_search_db_portal.call_args_list) == 6
        for call in mock_es_search_db_portal.call_args_list:
            assert _extract_status_clause(call.args[2]["query"]) == _INCLUDE_SUPPRESSED_CLAUSE


class TestDbPortalCrossSearchAdvStatusFilter:
    """`/db-portal/cross-search?q=...` の status filter 適用 (AST 解析)。

    AST が単一 ``identifier`` field の eq + accession-shape value のときのみ
    ``include_suppressed``。AND/OR/NOT ラップ・wildcard・identifier 以外は
    ``public_only`` 固定 (詳細は docs/db-portal-api-spec.md § データ可視性)。
    """

    def test_adv_other_field_uses_public_only(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": "title:cancer"},
        )
        assert resp.status_code == 200
        for call in mock_es_search_db_portal.call_args_list:
            assert _extract_status_clause(call.args[2]["query"]) == _PUBLIC_ONLY_CLAUSE

    @pytest.mark.parametrize(
        "accession",
        ["PRJDB1234", "DRA000001", "JGAS000001"],
    )
    def test_adv_identifier_eq_accession_allows_suppressed(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        accession: str,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": f"identifier:{accession}"},
        )
        assert resp.status_code == 200
        for call in mock_es_search_db_portal.call_args_list:
            assert _extract_status_clause(call.args[2]["query"]) == _INCLUDE_SUPPRESSED_CLAUSE

    def test_adv_identifier_wildcard_uses_public_only(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        """``identifier:PRJDB*`` は wildcard なので accession 解放対象外。"""
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": "identifier:PRJDB*"},
        )
        assert resp.status_code == 200
        for call in mock_es_search_db_portal.call_args_list:
            assert _extract_status_clause(call.args[2]["query"]) == _PUBLIC_ONLY_CLAUSE

    def test_adv_identifier_non_accession_uses_public_only(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        """``identifier:cancer`` (DbType pattern に matchしない) は解放対象外。"""
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": "identifier:cancer"},
        )
        assert resp.status_code == 200
        for call in mock_es_search_db_portal.call_args_list:
            assert _extract_status_clause(call.args[2]["query"]) == _PUBLIC_ONLY_CLAUSE

    def test_adv_and_with_identifier_allows_suppressed(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        """AND **直下** の child に identifier accession があれば解禁

        (docs/db-portal-api-spec.md § データ可視性 (status 制御) の AST 走査ルール)。
        AST top の FieldClause だけでなく AND 直下の子も走査対象。
        """
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": "identifier:PRJDB1234 AND title:cancer"},
        )
        assert resp.status_code == 200
        for call in mock_es_search_db_portal.call_args_list:
            assert _extract_status_clause(call.args[2]["query"]) == _INCLUDE_SUPPRESSED_CLAUSE

    def test_adv_or_with_identifier_uses_public_only(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        """OR ラップは AST top が BoolOp なので解放対象外。"""
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": "identifier:PRJDB1234 OR identifier:DRA000001"},
        )
        assert resp.status_code == 200
        for call in mock_es_search_db_portal.call_args_list:
            assert _extract_status_clause(call.args[2]["query"]) == _PUBLIC_ONLY_CLAUSE

    def test_adv_publication_field_uses_public_only(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        """``publication`` は identifier 型だが field 名が違うので対象外。"""
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": "publication:PRJDB1234"},
        )
        assert resp.status_code == 200
        for call in mock_es_search_db_portal.call_args_list:
            assert _extract_status_clause(call.args[2]["query"]) == _PUBLIC_ONLY_CLAUSE


class TestDbPortalSearchStatusFilter:
    """`/db-portal/search?db=<es_db>` の status filter 適用 (simple + cursor + field clause)."""

    @pytest.mark.parametrize("db", _ES_DBS)
    def test_simple_q_uses_public_only(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        db: str,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": db, "q": "cancer"},
        )
        assert resp.status_code == 200
        body = get_es_search_body(mock_es_search_db_portal)
        assert _extract_status_clause(body["query"]) == _PUBLIC_ONLY_CLAUSE

    @pytest.mark.parametrize("db", _ES_DBS)
    def test_simple_accession_q_allows_suppressed(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        db: str,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": db, "q": "PRJDB1234"},
        )
        assert resp.status_code == 200
        body = get_es_search_body(mock_es_search_db_portal)
        assert _extract_status_clause(body["query"]) == _INCLUDE_SUPPRESSED_CLAUSE

    @pytest.mark.parametrize("db", _ES_DBS)
    def test_adv_other_field_uses_public_only(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        db: str,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": db, "q": "title:cancer"},
        )
        assert resp.status_code == 200
        body = get_es_search_body(mock_es_search_db_portal)
        assert _extract_status_clause(body["query"]) == _PUBLIC_ONLY_CLAUSE

    @pytest.mark.parametrize("db", _ES_DBS)
    def test_adv_identifier_accession_allows_suppressed(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        db: str,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": db, "q": "identifier:PRJDB1234"},
        )
        assert resp.status_code == 200
        body = get_es_search_body(mock_es_search_db_portal)
        assert _extract_status_clause(body["query"]) == _INCLUDE_SUPPRESSED_CLAUSE

    def test_adv_wildcard_identifier_uses_public_only(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "identifier:PRJDB*"},
        )
        assert resp.status_code == 200
        body = get_es_search_body(mock_es_search_db_portal)
        assert _extract_status_clause(body["query"]) == _PUBLIC_ONLY_CLAUSE

    def test_cursor_inherits_status_filter_from_offset_request(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        """offset 1 ページ目で生成された status filter 込み query が
        CursorPayload.query に焼き込まれ、cursor token を decode すると
        同じ filter が継承されていることを確認する。

        cursor 経路 (``_db_specific_search_cursor``) は ``CursorPayload.query``
        を ES body の ``query`` にそのまま流すため、ここで token に
        ``include_suppressed`` 込みの query が含まれていれば 2 ページ目以降も
        同じ status_mode が確実に適用される (実際の PIT 経路は
        integration test で網羅)。
        """
        per_page = 20
        hits_p1: list[dict[str, Any]] = [
            {
                "_id": f"PRJDB{i}",
                "_source": {"identifier": f"PRJDB{i}", "type": "bioproject"},
                "sort": [f"2024-01-{i + 1:02d}", f"PRJDB{i}"],
            }
            for i in range(per_page)
        ]
        mock_es_search_db_portal.return_value = make_es_search_response(
            hits=hits_p1,
            total=per_page * 3,
        )
        r1 = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "PRJDB1234", "perPage": per_page},
        )
        assert r1.status_code == 200
        body_p1 = get_es_search_body(mock_es_search_db_portal)
        assert _extract_status_clause(body_p1["query"]) == _INCLUDE_SUPPRESSED_CLAUSE
        next_cursor = r1.json()["nextCursor"]
        assert next_cursor is not None
        # cursor token を decode して、焼き込まれた query が
        # include_suppressed 込みであることを直接検証する。
        cursor_payload = decode_cursor(next_cursor)
        assert _extract_status_clause(cursor_payload.query) == _INCLUDE_SUPPRESSED_CLAUSE


def _solr_params_have_no_status(params: dict[str, Any]) -> bool:
    """Solr params の key / value に ``status`` 関連の文字列が一切含まれないか確認する。

    Solr 2 DB (ARSA / TXSearch) は status filter 非適用 (no-op)。
    ``fq`` / ``q`` / その他 key にも ``status`` が混入しないことを担保する。
    """
    for key, value in params.items():
        if "status" in str(key).lower():
            return False
        if isinstance(value, str) and "status" in value.lower():
            return False
        if isinstance(value, list):
            for v in value:
                if isinstance(v, str) and "status" in v.lower():
                    return False
    return True


class TestDbPortalSolrNoStatusFilter:
    """Solr 2 DB (ARSA / TXSearch) は status filter 非適用 (no-op) を保証する。

    Solr proxy は外部 NIG cluster 側で public 固定が SSOT のため、
    `/db-portal/*` から status 関連の filter を一切送らない (詳細は
    docs/db-portal-api-spec.md § データ可視性 (status 制御))。
    """

    def test_arsa_simple_no_status_param(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "trad", "q": "cancer"},
        )
        assert resp.status_code == 200
        params = mock_arsa_search_db_portal.call_args.kwargs["params"]
        assert _solr_params_have_no_status(params)

    def test_arsa_simple_accession_q_no_status_param(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        """ES 6 DB なら解放対象になる accession q でも、Solr 側は影響を受けない。"""
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "trad", "q": "PRJDB1234"},
        )
        assert resp.status_code == 200
        params = mock_arsa_search_db_portal.call_args.kwargs["params"]
        assert _solr_params_have_no_status(params)

    def test_arsa_adv_no_status_param(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "trad", "q": "title:cancer"},
        )
        assert resp.status_code == 200
        params = mock_arsa_search_db_portal.call_args.kwargs["params"]
        assert _solr_params_have_no_status(params)

    def test_txsearch_simple_no_status_param(
        self,
        app_with_db_portal: TestClient,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        mock_txsearch_search_db_portal.return_value = make_solr_txsearch_response(num_found=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "taxonomy", "q": "human"},
        )
        assert resp.status_code == 200
        params = mock_txsearch_search_db_portal.call_args.kwargs["params"]
        assert _solr_params_have_no_status(params)

    def test_txsearch_adv_no_status_param(
        self,
        app_with_db_portal: TestClient,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        mock_txsearch_search_db_portal.return_value = make_solr_txsearch_response(num_found=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "taxonomy", "q": "rank:species"},
        )
        assert resp.status_code == 200
        params = mock_txsearch_search_db_portal.call_args.kwargs["params"]
        assert _solr_params_have_no_status(params)

    def test_cross_search_arsa_no_status_param(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        """`/db-portal/cross-search` の ARSA 経路 (trad) も同じく no-op。"""
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=0)
        mock_txsearch_search_db_portal.return_value = make_solr_txsearch_response(num_found=0)
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": "PRJDB1234"},
        )
        assert resp.status_code == 200
        params = mock_arsa_search_db_portal.call_args.kwargs["params"]
        assert _solr_params_have_no_status(params)

    def test_cross_search_txsearch_no_status_param(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        """`/db-portal/cross-search` の TXSearch 経路 (taxonomy) も同じく no-op。"""
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=0)
        mock_txsearch_search_db_portal.return_value = make_solr_txsearch_response(num_found=0)
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": "PRJDB1234"},
        )
        assert resp.status_code == 200
        params = mock_txsearch_search_db_portal.call_args.kwargs["params"]
        assert _solr_params_have_no_status(params)

    def test_cross_search_adv_arsa_no_status_param(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
        mock_arsa_search_db_portal: AsyncMock,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        """q with field clauses の cross-search でも Solr は影響を受けない。"""
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=0)
        mock_txsearch_search_db_portal.return_value = make_solr_txsearch_response(num_found=0)
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": "identifier:PRJDB1234"},
        )
        assert resp.status_code == 200
        arsa_params = mock_arsa_search_db_portal.call_args.kwargs["params"]
        txsearch_params = mock_txsearch_search_db_portal.call_args.kwargs["params"]
        assert _solr_params_have_no_status(arsa_params)
        assert _solr_params_have_no_status(txsearch_params)


# === q AND-join semantics ===


def _bioproject_es_body(mock: AsyncMock) -> dict[str, Any]:
    """Pick the ES request body for the bioproject index."""
    for call in mock.call_args_list:
        index = call.args[1]
        if index == "bioproject":
            body: dict[str, Any] = call.args[2]
            return body
    raise AssertionError("bioproject ES call was not captured")


def _flatten_must(clause: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the leaves under a bool.must (or wrap a single leaf)."""
    if "bool" in clause:
        return list(clause["bool"].get("must", []))
    return [clause]


def _free_text_token_query(wrapper: dict[str, Any]) -> str:
    """Return the single token query carried by a bare-word FreeText wrapper.

    Each bare-word keyword token compiles to a prefix-aware
    ``bool.should`` of exactly two ``multi_match`` leaves: one
    ``operator=and`` (whole-word, all fields) and one
    ``type=phrase_prefix`` (前方一致, text fields only — the keyword-typed
    ``identifier`` is dropped because ES rejects phrase prefix on keyword
    fields).  This unwraps that shape and asserts the invariant, returning
    the shared ``query`` string.
    """
    inner = wrapper["bool"]
    assert inner["minimum_should_match"] == 1
    should = inner["should"]
    assert len(should) == 2
    word = should[0]["multi_match"]
    prefix = should[1]["multi_match"]
    assert word["operator"] == "and"
    assert "type" not in word
    assert prefix["type"] == "phrase_prefix"
    assert "operator" not in prefix
    assert word["query"] == prefix["query"]
    # 前方一致側は keyword 型 identifier を除いた text field のみ。
    assert "identifier" not in prefix["fields"]
    assert prefix["fields"] == [f for f in word["fields"] if f != "identifier"]
    query = word["query"]
    assert isinstance(query, str)
    return query


def _field_contains_token(wrapper: dict[str, Any], es_field: str) -> str:
    """Return the value carried by a text field contains wrapper.

    A text-type ``field:word`` contains compiles to a prefix-aware
    ``bool.should`` of exactly two leaves on the same field: one
    ``match_phrase`` (whole-word) and one ``match_phrase_prefix``
    (前方一致).  This unwraps that shape and asserts the invariant,
    returning the shared value.
    """
    inner = wrapper["bool"]
    assert inner["minimum_should_match"] == 1
    should = inner["should"]
    assert len(should) == 2
    value = should[0]["match_phrase"][es_field]
    assert should[1]["match_phrase_prefix"][es_field] == value
    assert isinstance(value, str)
    return value


def _is_free_text_wrapper(clause: dict[str, Any]) -> bool:
    """True when ``clause`` is a bare-word FreeText prefix-aware wrapper."""
    should = clause.get("bool", {}).get("should")
    return isinstance(should, list) and len(should) == 2 and "multi_match" in should[0]


def _is_field_contains_wrapper(clause: dict[str, Any], es_field: str) -> bool:
    """True when ``clause`` is a text field contains prefix-aware wrapper."""
    should = clause.get("bool", {}).get("should")
    return isinstance(should, list) and len(should) == 2 and es_field in should[0].get("match_phrase", {})


class TestKeywordOperatorOnDbPortal:
    """``keywordOperator`` パラメータが FreeText の token 連結演算子を切り替えるか.

    - ``OR`` (デフォルト、entries 系と統一): bool.should + minimum_should_match=1 で multi_match を並べる
    - ``AND``: bool.must で multi_match を flat に並べる
    - 明示 BoolOp (``q`` 中の ``AND`` / ``OR``) は影響を受けない
    """

    def test_search_default_or_uses_bool_should(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        # ``keywordOperator`` 未指定で default = OR (wire-level、entries と統一).
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "cancer,tumor"},
        )
        assert resp.status_code == 200
        body = _bioproject_es_body(mock_es_search_db_portal)
        bool_clause = body["query"]["bool"]
        # FreeText 単独 AST + OR → bool.should + minimum_should_match=1。
        # 各 bare word token は前方一致対応の bool.should wrapper になる。
        should = bool_clause.get("should")
        assert isinstance(should, list)
        queries = {_free_text_token_query(c) for c in should}
        assert queries == {"cancer", "tumor"}
        assert bool_clause.get("minimum_should_match") == 1
        assert "must" not in bool_clause

    def test_search_explicit_and_uses_bool_must(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        # 明示 AND で旧 default 挙動 (bool.must) を取り戻せる.
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "cancer,tumor", "keywordOperator": "AND"},
        )
        assert resp.status_code == 200
        body = _bioproject_es_body(mock_es_search_db_portal)
        bool_clause = body["query"]["bool"]
        must = bool_clause.get("must")
        assert isinstance(must, list)
        # 各 bare word token は前方一致対応の bool.should wrapper になる。
        queries = {_free_text_token_query(c) for c in must}
        assert queries == {"cancer", "tumor"}
        assert "should" not in bool_clause

    def test_search_or_uses_bool_should(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "cancer,tumor", "keywordOperator": "OR"},
        )
        assert resp.status_code == 200
        body = _bioproject_es_body(mock_es_search_db_portal)
        bool_clause = body["query"]["bool"]
        # FreeText 単独 AST + OR → bool.should + minimum_should_match=1。
        # 各 bare word token は前方一致対応の bool.should wrapper になる。
        should = bool_clause.get("should")
        assert isinstance(should, list)
        queries = {_free_text_token_query(c) for c in should}
        assert queries == {"cancer", "tumor"}
        assert bool_clause.get("minimum_should_match") == 1

    def test_search_explicit_or_in_dsl_unaffected_by_default_operator(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        """DSL 内の明示 ``title:a OR title:b`` は keywordOperator=AND でも OR semantics を保つ.

        compile_to_es の戻り値は ``bool.should + filter(status)`` (must は無い)
        になっており、明示 OR のセマンティクスがそのまま反映される。
        """
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "title:cancer OR title:tumor"},
        )
        assert resp.status_code == 200
        body = _bioproject_es_body(mock_es_search_db_portal)
        bool_clause = body["query"]["bool"]
        # 明示 OR は AST 上 BoolOp(OR, ...) → bool.should がトップ bool に直接出る。
        # text 型 field の contains は前方一致対応の bool.should wrapper
        # (match_phrase + match_phrase_prefix) になる。
        should = bool_clause["should"]
        titles = {_field_contains_token(c, "title") for c in should}
        assert titles == {"cancer", "tumor"}
        assert bool_clause.get("minimum_should_match") == 1
        # must は無い (filter は status のみ)
        assert "must" not in bool_clause

    def test_cross_search_or_uses_bool_should(
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
            "/db-portal/cross-search",
            params={"q": "cancer,tumor", "topHits": 0, "keywordOperator": "OR"},
        )
        assert resp.status_code == 200
        # ES 側 (bioproject 等のいずれか) body を取って bool.should 構造を確認
        body = get_es_search_body(mock_es_search_db_portal, call_index=0)
        bool_clause = body["query"]["bool"]
        # 各 bare word token は前方一致対応の bool.should wrapper になる。
        should = bool_clause.get("should")
        assert isinstance(should, list)
        queries = {_free_text_token_query(c) for c in should}
        assert queries == {"cancer", "tumor"}

    def test_invalid_keyword_operator_returns_422(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "bioproject", "keywordOperator": "XOR"},
        )
        assert resp.status_code == 422

    def test_cross_search_invalid_keyword_operator_returns_422(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"keywordOperator": "XOR"},
        )
        assert resp.status_code == 422

    def test_arsa_or_uses_or_join(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        """ARSA も keywordOperator=OR で token 間が OR で連結される."""
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "trad", "q": "cancer,tumor", "keywordOperator": "OR"},
        )
        assert resp.status_code == 200
        # ARSA 呼び出し時の q parameter を取り出す
        call = mock_arsa_search_db_portal.call_args
        params = call.kwargs.get("params") or call.args[-1]
        q_value = params.get("q") if isinstance(params, dict) else None
        assert q_value == '(("cancer" OR cancer*) OR ("tumor" OR tumor*))', f"unexpected ARSA q: {q_value!r}"


class TestQueryAndJoin:
    """``q`` 内に bare word + ``field:value`` を AND で並べた場合のコンパイル契約.

    例: ``q='human AND title:cancer'`` → AST ``BoolOp(AND, [FreeText('human'),
    FieldClause(title, cancer)])`` → ES bool.must / Solr edismax AND-join.

    flatten 挙動は ``free_text_operator=AND`` の時のみ働く (compiler_es.py の
    ``_compile_and_children``)。``keywordOperator`` の wire default は OR に
    なったので、flatten を検証する場合は明示的に ``keywordOperator=AND`` を
    渡す必要がある.
    """

    def test_search_es_bool_must_has_free_text_and_field_clauses(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            # default OR では FreeText の bool.should が must 配下に bool wrapper
            # として残り flatten されない. flatten 挙動を見るために AND 明示.
            params={"db": "bioproject", "q": "human AND title:cancer", "keywordOperator": "AND"},
        )
        assert resp.status_code == 200
        body = _bioproject_es_body(mock_es_search_db_portal)
        bool_clause = body["query"]["bool"]
        must = bool_clause["must"]
        # bare word free-text と text field contains はどちらも前方一致対応の
        # bool.should wrapper として AND-flatten 後の must に並ぶ。
        has_free_text = any(
            isinstance(c, dict) and "should" in c.get("bool", {}) and _free_text_token_query(c) == "human"
            for c in must
            if _is_free_text_wrapper(c)
        )
        has_field_clause = any(
            isinstance(c, dict)
            and _is_field_contains_wrapper(c, "title")
            and _field_contains_token(c, "title") == "cancer"
            for c in must
        )
        assert has_free_text, f"free-text wrapper missing from bool.must: {must}"
        assert has_field_clause, f"field-clause wrapper missing from bool.must: {must}"
        assert bool_clause["filter"] == [{"term": {"status": "public"}}]

    def test_cross_search_es_bool_must_has_free_text_and_field_clauses(
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
            "/db-portal/cross-search",
            # default OR では FreeText が flatten されないため、AND 明示で flatten 挙動を見る.
            params={"q": "human AND title:cancer", "topHits": 0, "keywordOperator": "AND"},
        )
        assert resp.status_code == 200
        body = get_es_search_body(mock_es_search_db_portal, call_index=0)
        must = body["query"]["bool"]["must"]
        # bare word free-text と text field contains はどちらも前方一致対応の
        # bool.should wrapper として AND-flatten 後の must に並ぶ。
        has_free_text = any(_is_free_text_wrapper(c) and _free_text_token_query(c) == "human" for c in must)
        has_field_clause = any(
            _is_field_contains_wrapper(c, "title") and _field_contains_token(c, "title") == "cancer" for c in must
        )
        assert has_free_text
        assert has_field_clause

    def test_status_mode_with_free_text_accession_match(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        """FreeText 側が accession exact match → include_suppressed."""
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "PRJDB1234 AND title:cancer"},
        )
        assert resp.status_code == 200
        body = _bioproject_es_body(mock_es_search_db_portal)
        assert body["query"]["bool"]["filter"] == [{"terms": {"status": ["public", "suppressed"]}}]

    def test_status_mode_with_field_clause_identifier_match(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        """トップレベル AND 直下の identifier:<accession> → include_suppressed."""
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "cancer AND identifier:PRJDB1234"},
        )
        assert resp.status_code == 200
        body = _bioproject_es_body(mock_es_search_db_portal)
        assert body["query"]["bool"]["filter"] == [{"terms": {"status": ["public", "suppressed"]}}]

    def test_status_mode_neither_accession_match(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        """両方とも非 accession → public_only."""
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "cancer AND title:cancer"},
        )
        assert resp.status_code == 200
        body = _bioproject_es_body(mock_es_search_db_portal)
        assert body["query"]["bool"]["filter"] == [{"term": {"status": "public"}}]

    def test_arsa_q_is_and_joined(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        """ARSA 側で AND-join された edismax ``q`` 文字列 (単一外側括弧)."""
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "trad", "q": "human AND title:cancer"},
        )
        assert resp.status_code == 200
        params = mock_arsa_search_db_portal.call_args.kwargs["params"]
        q = params["q"]
        assert q.startswith("(")
        assert q.endswith(")")
        assert " AND " in q
        assert 'Definition:"cancer"' in q  # field clause compiled (ARSA dialect)
        assert '"human"' in q  # free-text quoted token
        # uf は FieldClause を含む AST で付与される。
        assert "uf" in params

    def test_txsearch_q_is_and_joined(
        self,
        app_with_db_portal: TestClient,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        """TXSearch 側で AND-join された edismax ``q`` 文字列."""
        mock_txsearch_search_db_portal.return_value = make_solr_txsearch_response(num_found=0)
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "taxonomy", "q": "human AND title:cancer"},
        )
        assert resp.status_code == 200
        params = mock_txsearch_search_db_portal.call_args.kwargs["params"]
        q = params["q"]
        assert q.startswith("(")
        assert q.endswith(")")
        assert " AND " in q
        assert 'scientific_name:"cancer"' in q  # field clause compiled (TXSearch dialect)
        assert '"human"' in q
        assert "uf" in params

    def test_cursor_with_q_returns_400(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        """cursor + q (どんな q でも) は cursor exclusivity で 400 (about:blank)."""
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "cursor": "any-token",
                "q": "human AND title:cancer",
            },
        )
        assert resp.status_code == 400
        # _validate_cursor_exclusivity が plain HTTPException で 400 を返す (about:blank)。
        body = resp.json()
        assert "cursor" in body["detail"].lower()
