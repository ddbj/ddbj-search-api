"""Tests for db-portal facet aggregation (docs/db-portal-api-spec.md § facet 集計).

Covers:
- ``resolve_db_portal_facets`` scope resolution (cross / ES DB / Solr DB),
  400 ``facet-not-applicable`` for out-of-scope facets.
- single-DB ES facets: aggs ride the hits request, response carries facets,
  population (query body) matches the hits query (same status filter).
- single-DB Solr facets: facet params ride the Solr request, ``facet_counts``
  parsed into the envelope.
- cross-search facets: a separate ``entries`` alias aggregation request, only
  when facets are requested, with graceful ``null`` on failure.
- cursor mode: facets aggregate over the baked-in query.
- wire-level validation: unknown facet name 422, out-of-scope 400.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from ddbj_search_api.cursor import CursorPayload, encode_cursor
from ddbj_search_api.routers.db_portal import (
    DbPortalHTTPException,
    resolve_db_portal_facets,
)
from ddbj_search_api.schemas.db_portal import DbPortalDb, DbPortalErrorType
from tests.unit.conftest import (
    get_es_search_body,
    make_es_search_response,
    make_solr_arsa_response,
    make_solr_txsearch_response,
)

_FACET_NOT_APPLICABLE = DbPortalErrorType.facet_not_applicable.value
_PUBLIC_ONLY_CLAUSE = {"term": {"status": "public"}}


def _organism_agg(tax_id: str, count: int, label: str) -> dict[str, Any]:
    return {
        "buckets": [
            {"key": tax_id, "doc_count": count, "name": {"buckets": [{"key": label, "doc_count": count}]}},
        ],
    }


def _terms_agg(key: str, count: int) -> dict[str, Any]:
    return {"buckets": [{"key": key, "doc_count": count}]}


def _find_es_body_by_index(mock: AsyncMock, index: str) -> dict[str, Any] | None:
    """Return the body of the first recorded es_search call for ``index``."""
    for call in mock.call_args_list:
        idx = call.args[1] if len(call.args) > 1 else call.kwargs.get("index")
        if idx == index:
            return call.args[2] if len(call.args) > 2 else call.kwargs.get("body")
    return None


def _es_indices_called(mock: AsyncMock) -> list[Any]:
    indices: list[Any] = []
    for call in mock.call_args_list:
        idx = call.args[1] if len(call.args) > 1 else call.kwargs.get("index")
        indices.append(idx)
    return indices


def _status_filter_clauses(es_query: dict[str, Any]) -> list[dict[str, Any]]:
    bool_body = es_query.get("bool", {})
    if not isinstance(bool_body, dict):
        return []
    filters = bool_body.get("filter", [])
    return filters if isinstance(filters, list) else []


# === resolve_db_portal_facets (scope resolution) ===


class TestResolveDbPortalFacets:
    """resolve_db_portal_facets: scope 別 allow / reject (400 facet-not-applicable)."""

    def test_none_returns_none(self) -> None:
        assert resolve_db_portal_facets(None, db=None) is None
        assert resolve_db_portal_facets(None, db=DbPortalDb.sra) is None

    def test_empty_string_returns_empty_list(self) -> None:
        assert resolve_db_portal_facets("", db=None) == []
        assert resolve_db_portal_facets("", db=DbPortalDb.bioproject) == []

    def test_cross_valid_facets(self) -> None:
        assert resolve_db_portal_facets("organism,type", db=None) == ["organism", "type"]
        assert resolve_db_portal_facets("accessibility", db=None) == ["accessibility"]

    def test_cross_rejects_type_specific(self) -> None:
        with pytest.raises(DbPortalHTTPException) as exc:
            resolve_db_portal_facets("libraryStrategy", db=None)
        assert exc.value.status_code == 400
        assert exc.value.type_uri == _FACET_NOT_APPLICABLE
        assert "libraryStrategy" in exc.value.detail

    def test_single_es_valid(self) -> None:
        assert resolve_db_portal_facets("organism,libraryStrategy", db=DbPortalDb.sra) == [
            "organism",
            "libraryStrategy",
        ]

    def test_single_es_rejects_other_db_facet(self) -> None:
        # package belongs to biosample, not bioproject.
        with pytest.raises(DbPortalHTTPException) as exc:
            resolve_db_portal_facets("package", db=DbPortalDb.bioproject)
        assert exc.value.status_code == 400
        assert exc.value.type_uri == _FACET_NOT_APPLICABLE

    def test_single_es_rejects_type(self) -> None:
        # type is cross-only.
        with pytest.raises(DbPortalHTTPException):
            resolve_db_portal_facets("type", db=DbPortalDb.sra)

    def test_solr_trad_valid(self) -> None:
        assert resolve_db_portal_facets("division,molecularType", db=DbPortalDb.trad) == [
            "division",
            "molecularType",
        ]

    def test_solr_trad_rejects_organism(self) -> None:
        # organism is degenerate on ARSA; not offered.
        with pytest.raises(DbPortalHTTPException) as exc:
            resolve_db_portal_facets("organism", db=DbPortalDb.trad)
        assert exc.value.type_uri == _FACET_NOT_APPLICABLE

    def test_taxonomy_valid(self) -> None:
        assert resolve_db_portal_facets("rank,kingdom", db=DbPortalDb.taxonomy) == ["rank", "kingdom"]

    def test_taxonomy_rejects_division(self) -> None:
        # division is an ARSA facet, not taxonomy.
        with pytest.raises(DbPortalHTTPException):
            resolve_db_portal_facets("division", db=DbPortalDb.taxonomy)

    def test_taxonomy_rejects_organism(self) -> None:
        with pytest.raises(DbPortalHTTPException):
            resolve_db_portal_facets("organism", db=DbPortalDb.taxonomy)


# === Single-DB ES facets ===


class TestDbPortalSearchEsFacets:
    def test_facets_in_response(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(
            total=5,
            aggregations={
                "organism": _organism_agg("9606", 5, "Homo sapiens"),
                "objectType": _terms_agg("BioProject", 5),
            },
        )
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "bioproject", "facets": "organism,objectType"},
        )
        assert resp.status_code == 200
        facets = resp.json()["facets"]
        assert facets is not None
        assert facets["organism"][0] == {"value": "9606", "count": 5, "label": "Homo sapiens"}
        assert facets["objectType"][0] == {"value": "BioProject", "count": 5}
        # Solr-only facet stays null on an ES DB response.
        assert facets["division"] is None

    def test_aggs_added_to_request_body(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(
            total=1,
            aggregations={"organism": _organism_agg("9606", 1, "Homo sapiens")},
        )
        app_with_db_portal.get("/db-portal/search", params={"db": "bioproject", "facets": "organism"})
        body = get_es_search_body(mock_es_search_db_portal)
        assert "aggs" in body
        assert set(body["aggs"]) == {"organism"}

    def test_facets_size_flows_to_terms_size(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(
            total=1,
            aggregations={"objectType": _terms_agg("BioProject", 1)},
        )
        app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "bioproject", "facets": "objectType", "facetsSize": "7"},
        )
        body = get_es_search_body(mock_es_search_db_portal)
        assert body["aggs"]["objectType"]["terms"]["size"] == 7

    def test_no_facets_param_means_null_and_no_aggs(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=3)
        resp = app_with_db_portal.get("/db-portal/search", params={"db": "bioproject"})
        assert resp.status_code == 200
        assert resp.json()["facets"] is None
        assert "aggs" not in get_es_search_body(mock_es_search_db_portal)

    def test_facets_empty_string_yields_null_no_aggs(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        """``facets=`` (empty string) means "no aggregation" — facets:null and
        no ``aggs`` on the ES body (distinct from the default-common behaviour
        of /facets)."""
        mock_es_search_db_portal.return_value = make_es_search_response(total=2)
        resp = app_with_db_portal.get("/db-portal/search", params={"db": "bioproject", "facets": ""})
        assert resp.status_code == 200
        assert resp.json()["facets"] is None
        assert "aggs" not in get_es_search_body(mock_es_search_db_portal)

    def test_facet_population_matches_hits_query(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        """Facet aggs ride the SAME query as the hits search (status filter included).

        Bug guard: if facets were computed against a different query
        (e.g. /facets-style public_only-only or a re-derived body), the
        sidebar counts would not match the result set.
        """
        mock_es_search_db_portal.return_value = make_es_search_response(
            total=3,
            aggregations={"organism": {"buckets": []}},
        )
        app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "cancer", "facets": "organism"},
        )
        body_with = get_es_search_body(mock_es_search_db_portal)
        assert "aggs" in body_with

        mock_es_search_db_portal.return_value = make_es_search_response(total=3)
        app_with_db_portal.get("/db-portal/search", params={"db": "bioproject", "q": "cancer"})
        body_without = get_es_search_body(mock_es_search_db_portal)
        assert "aggs" not in body_without

        # Same compiled query (status filter included); aggs are purely additive.
        assert body_with["query"] == body_without["query"]
        assert _PUBLIC_ONLY_CLAUSE in _status_filter_clauses(body_with["query"])

    def test_out_of_scope_facet_returns_400(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "bioproject", "facets": "package"},  # package = biosample
        )
        assert resp.status_code == 400
        assert resp.json()["type"] == _FACET_NOT_APPLICABLE

    def test_unknown_facet_name_returns_422(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "bioproject", "facets": "bogusFacet"},
        )
        assert resp.status_code == 422


# === Single-DB Solr facets ===


class TestDbPortalSearchSolrFacets:
    def test_trad_facets_in_response(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        resp_doc = make_solr_arsa_response(docs=[], num_found=10)
        resp_doc["facet_counts"] = {"facet_fields": {"Division": ["BCT", 7, "VRL", 3]}}
        mock_arsa_search_db_portal.return_value = resp_doc
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "trad", "facets": "division"},
        )
        assert resp.status_code == 200
        facets = resp.json()["facets"]
        assert facets["division"] == [{"value": "BCT", "count": 7}, {"value": "VRL", "count": 3}]

    def test_trad_facet_params_in_request(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        resp_doc = make_solr_arsa_response(num_found=1)
        resp_doc["facet_counts"] = {"facet_fields": {"Division": ["BCT", 1], "MolecularType": ["genomic DNA", 1]}}
        mock_arsa_search_db_portal.return_value = resp_doc
        app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "trad", "facets": "division,molecularType", "facetsSize": "20"},
        )
        params = mock_arsa_search_db_portal.call_args.kwargs["params"]
        assert params["facet"] == "true"
        assert params["facet.field"] == ["Division", "MolecularType"]
        assert params["facet.mincount"] == "1"
        assert params["facet.limit"] == "20"

    def test_trad_without_facets_omits_facet_params(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        mock_arsa_search_db_portal.return_value = make_solr_arsa_response(num_found=1)
        resp = app_with_db_portal.get("/db-portal/search", params={"db": "trad"})
        assert resp.status_code == 200
        assert resp.json()["facets"] is None
        params = mock_arsa_search_db_portal.call_args.kwargs["params"]
        assert "facet" not in params
        assert "facet.field" not in params

    def test_taxonomy_facets_in_response(
        self,
        app_with_db_portal: TestClient,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        resp_doc = make_solr_txsearch_response(num_found=4)
        resp_doc["facet_counts"] = {"facet_fields": {"rank": ["species", 3, "genus", 1], "kingdom": ["Bacteria", 4]}}
        mock_txsearch_search_db_portal.return_value = resp_doc
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "taxonomy", "facets": "rank,kingdom"},
        )
        assert resp.status_code == 200
        facets = resp.json()["facets"]
        assert facets["rank"] == [{"value": "species", "count": 3}, {"value": "genus", "count": 1}]
        assert facets["kingdom"] == [{"value": "Bacteria", "count": 4}]

    def test_trad_out_of_scope_facet_400(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "trad", "facets": "organism"},
        )
        assert resp.status_code == 400
        assert resp.json()["type"] == _FACET_NOT_APPLICABLE


# === Cross-search facets ===


class TestDbPortalCrossFacets:
    def test_cross_accepts_facets_param(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        # Bug guard: facets / facetsSize must not trip _reject_unexpected_cross_params.
        async def _es(_client: Any, index: str, _body: dict[str, Any]) -> dict[str, Any]:
            if index == "entries":
                return make_es_search_response(aggregations={"organism": _organism_agg("9606", 1, "H")})
            return make_es_search_response(total=1)

        mock_es_search_db_portal.side_effect = _es
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"facets": "organism", "facetsSize": "10", "topHits": "0"},
        )
        assert resp.status_code == 200

    def test_cross_facets_in_response(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        async def _es(_client: Any, index: str, _body: dict[str, Any]) -> dict[str, Any]:
            if index == "entries":
                return make_es_search_response(
                    aggregations={
                        "organism": _organism_agg("9606", 5, "Homo sapiens"),
                        "type": _terms_agg("bioproject", 5),
                    },
                )
            return make_es_search_response(total=5)

        mock_es_search_db_portal.side_effect = _es
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"facets": "organism,type", "topHits": "0"},
        )
        assert resp.status_code == 200
        facets = resp.json()["facets"]
        assert facets["type"][0] == {"value": "bioproject", "count": 5}
        assert facets["organism"][0]["value"] == "9606"

    def test_cross_facet_request_hits_entries_alias_size_zero(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        async def _es(_client: Any, index: str, _body: dict[str, Any]) -> dict[str, Any]:
            if index == "entries":
                return make_es_search_response(aggregations={"type": _terms_agg("bioproject", 1)})
            return make_es_search_response(total=1)

        mock_es_search_db_portal.side_effect = _es
        app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"facets": "type", "facetsSize": "15", "topHits": "0"},
        )
        entries_body = _find_es_body_by_index(mock_es_search_db_portal, "entries")
        assert entries_body is not None
        assert entries_body["size"] == 0
        # Exactly the requested facet is aggregated (not the default set, not a
        # dropped one) — guards the cross agg-selection plumbing.
        assert set(entries_body["aggs"]) == {"type"}
        # facetsSize flows into the agg terms.size on the cross path.
        assert entries_body["aggs"]["type"]["terms"]["size"] == 15
        assert _PUBLIC_ONLY_CLAUSE in _status_filter_clauses(entries_body["query"])
        # 母集団一致: the facet query body is byte-identical to a per-DB count
        # query body (same compiled query + status filter), not a re-derived one.
        count_body = _find_es_body_by_index(mock_es_search_db_portal, "bioproject")
        assert count_body is not None
        assert entries_body["query"] == count_body["query"]

    def test_cross_facet_malformed_aggregation_yields_null_facets_but_200(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        """A 200-with-malformed-aggregation must degrade to facets=null, not 500.

        Bug guard: the facet parse must be inside the failure-isolation
        boundary so an unexpected bucket shape (here a non-int doc_count)
        does not crash the whole cross-search while the counts are fine.
        """

        async def _es(_client: Any, index: str, _body: dict[str, Any]) -> dict[str, Any]:
            if index == "entries":
                return make_es_search_response(
                    aggregations={"type": {"buckets": [{"key": "bioproject", "doc_count": "not-an-int"}]}},
                )
            return make_es_search_response(total=1)

        mock_es_search_db_portal.side_effect = _es
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"facets": "type", "topHits": "0"},
        )
        assert resp.status_code == 200
        assert resp.json()["facets"] is None

    def test_cross_facets_empty_string_yields_null_no_entries_request(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=1)
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"facets": "", "topHits": "0"})
        assert resp.status_code == 200
        assert resp.json()["facets"] is None
        assert "entries" not in _es_indices_called(mock_es_search_db_portal)

    def test_cross_without_facets_makes_no_entries_request(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(total=1)
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"topHits": "0"})
        assert resp.status_code == 200
        assert resp.json()["facets"] is None
        assert "entries" not in _es_indices_called(mock_es_search_db_portal)

    def test_cross_type_specific_facet_returns_400(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"facets": "libraryStrategy"},
        )
        assert resp.status_code == 400
        assert resp.json()["type"] == _FACET_NOT_APPLICABLE

    def test_cross_facet_failure_yields_null_facets_but_200(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        async def _es(_client: Any, index: str, _body: dict[str, Any]) -> dict[str, Any]:
            if index == "entries":
                raise httpx.ConnectError("boom")
            return make_es_search_response(total=1)

        mock_es_search_db_portal.side_effect = _es
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"facets": "organism", "topHits": "0"},
        )
        # The count fan-out still succeeds, so the response is 200 with facets=null.
        assert resp.status_code == 200
        assert resp.json()["facets"] is None


# === Cursor mode facets ===


class TestDbPortalCursorFacets:
    def test_cursor_with_facets_aggregates_over_baked_query(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_with_pit_db_portal: AsyncMock,
    ) -> None:
        baked_query = {"bool": {"filter": [_PUBLIC_ONLY_CLAUSE]}}
        payload = CursorPayload(
            pit_id="pit-1",
            search_after=["2024-01-15", "PRJDB1"],
            sort=[{"datePublished": {"order": "desc"}}, {"identifier": {"order": "asc"}}],
            query=baked_query,
        )
        token = encode_cursor(payload)
        mock_es_search_with_pit_db_portal.return_value = make_es_search_response(
            hits=[],
            total=2,
            aggregations={"organism": _organism_agg("9606", 2, "Homo sapiens")},
        )
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "bioproject", "cursor": token, "facets": "organism", "facetsSize": "13"},
        )
        assert resp.status_code == 200
        facets = resp.json()["facets"]
        assert facets is not None
        assert facets["organism"][0]["value"] == "9606"
        # aggs ride the PIT search body over the baked-in query.
        pit_body = mock_es_search_with_pit_db_portal.call_args.args[1]
        assert "aggs" in pit_body
        assert pit_body["query"] == baked_query
        # facetsSize flows into the agg terms.size on the cursor path too.
        assert pit_body["aggs"]["organism"]["terms"]["size"] == 13
