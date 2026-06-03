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
_INCLUDE_SUPPRESSED = {"terms": {"status": ["public", "suppressed"]}}


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

    def test_single_es_type_allowed_for_sra_jga(self) -> None:
        # type facet は複数 subtype を跨ぐ sra / jga で subtype 別集計として許可される。
        assert resolve_db_portal_facets("type", db=DbPortalDb.sra) == ["type"]
        assert resolve_db_portal_facets("type", db=DbPortalDb.jga) == ["type"]

    def test_single_es_type_rejected_for_single_subtype_db(self) -> None:
        # 単一 subtype の DB (bioproject / biosample / gea / metabobank) は subtype 分解の
        # 意味が無いので type facet を要求すると 400 facet-not-applicable のまま。
        for db in (DbPortalDb.bioproject, DbPortalDb.biosample, DbPortalDb.gea, DbPortalDb.metabobank):
            with pytest.raises(DbPortalHTTPException) as exc:
                resolve_db_portal_facets("type", db=db)
            assert exc.value.status_code == 400
            assert exc.value.type_uri == _FACET_NOT_APPLICABLE

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

    def test_type_facet_on_sra_returns_200_with_buckets(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        # type facet は複数 subtype を跨ぐ sra で subtype 別集計として許可され、
        # 集計は hits リクエスト body に相乗りする。
        mock_es_search_db_portal.return_value = make_es_search_response(
            total=4,
            aggregations={"type": _terms_agg("sra-experiment", 4)},
        )
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "sra", "facets": "type"},
        )
        assert resp.status_code == 200
        facets = resp.json()["facets"]
        assert facets is not None
        assert facets["type"][0] == {"value": "sra-experiment", "count": 4}
        assert set(get_es_search_body(mock_es_search_db_portal)["aggs"]) == {"type"}

    def test_type_facet_on_single_subtype_db_returns_400(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        # 単一 subtype の DB (bioproject) は subtype 分解の意味が無いので type facet 要求は 400。
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "bioproject", "facets": "type"},
        )
        assert resp.status_code == 400
        assert resp.json()["type"] == _FACET_NOT_APPLICABLE

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


# === Self-exclusion wiring (facetSelfExclude) ===


def _term_fields(node: Any) -> list[str]:
    """Collect every ``term`` clause field name anywhere in an ES query dict."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "term" and isinstance(value, dict):
                found.extend(value.keys())
            else:
                found.extend(_term_fields(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_term_fields(item))
    return found


def _clause_fields(node: Any, clause: str) -> list[str]:
    """Collect every ``<clause>`` (e.g. match_phrase / match_phrase_prefix) field name."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == clause and isinstance(value, dict):
                found.extend(value.keys())
            else:
                found.extend(_clause_fields(value, clause))
    elif isinstance(node, list):
        for item in node:
            found.extend(_clause_fields(item, clause))
    return found


def _has_freetext_prefix(node: Any) -> bool:
    """ツリー内に FreeText 前方一致 (``multi_match`` type=phrase_prefix) があるか."""
    if isinstance(node, dict):
        mm = node.get("multi_match")
        if isinstance(mm, dict) and mm.get("type") == "phrase_prefix":
            return True
        return any(_has_freetext_prefix(v) for v in node.values())
    if isinstance(node, list):
        return any(_has_freetext_prefix(v) for v in node)
    return False


def _filter_wrapped(name: str, inner: dict[str, Any], doc_count: int) -> dict[str, Any]:
    """Shape an ES ``filter`` aggregation response (self-exclusion wrap)."""
    return {"doc_count": doc_count, name: inner}


class TestDbPortalEsSelfExclusion:
    """facetSelfExclude=true moves the facet selections out of the top-level
    ``query`` (ES filter aggs can only narrow it) into a ``base`` query, wraps
    each facet in a ``filter`` aggregation that re-adds the *other* facets'
    clauses, and restores the hit population via ``post_filter``
    (docs § 集計母集団と self-exclusion)."""

    def test_self_exclude_moves_selections_below_query(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(
            total=3,
            aggregations={
                "organism": _filter_wrapped("organism", _organism_agg("9606", 3, "Homo sapiens"), 3),
                "package": _filter_wrapped("package", _terms_agg("SAMPLE", 3), 3),
            },
        )
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={
                "db": "biosample",
                "q": "organism_id:9606 AND package:SAMPLE",
                "facets": "organism,package",
                "facetSelfExclude": "true",
            },
        )
        assert resp.status_code == 200
        body = get_es_search_body(mock_es_search_db_portal)
        # Top-level query is the base: BOTH facet selections are removed (ES
        # filter aggs cannot widen past it), but status survives.
        top_fields = _term_fields(body["query"])
        assert "organism.identifier" not in top_fields
        assert "package.name" not in top_fields
        assert "status" in top_fields
        # post_filter restores the full q for the hits (both clauses back).
        post_fields = _term_fields(body["post_filter"])
        assert "organism.identifier" in post_fields
        assert "package.name" in post_fields
        # Each facet is a filter aggregation with an inner same-named terms agg,
        # re-adding the OTHER facet's clause only.
        for name in ("organism", "package"):
            assert "filter" in body["aggs"][name]
            assert "terms" in body["aggs"][name]["aggs"][name]
        organism_filter = body["aggs"]["organism"]["filter"]
        assert "organism.identifier" not in _term_fields(organism_filter)
        assert "package.name" in _term_fields(organism_filter)
        package_filter = body["aggs"]["package"]["filter"]
        assert "package.name" not in _term_fields(package_filter)
        assert "organism.identifier" in _term_fields(package_filter)
        # Response is parsed through the filter-wrap shape.
        assert resp.json()["facets"]["organism"][0]["value"] == "9606"

    def test_text_facet_self_exclude_nests_prefix_wrapper(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        """text 型 facet (host) が self-exclusion で再注入されるとき、その clause は
        前方一致 should-wrapper (match_phrase + match_phrase_prefix) として正しく入れ子に
        なる。enum facet (package) の term clause と混在しても base / post_filter /
        各 filter agg の母集団が崩れないことを確認する (前方一致導入の回帰ガード)。"""
        mock_es_search_db_portal.return_value = make_es_search_response(
            total=2,
            aggregations={
                "host": _filter_wrapped("host", _terms_agg("Homo sapiens", 2), 2),
                "package": _filter_wrapped("package", _terms_agg("SAMPLE", 2), 2),
            },
        )
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={
                "db": "biosample",
                "q": "host:Homo AND package:SAMPLE",
                "facets": "host,package",
                "facetSelfExclude": "true",
            },
        )
        assert resp.status_code == 200
        body = get_es_search_body(mock_es_search_db_portal)
        # base (top-level query): 両 facet 句を除外 (status のみ残る)。
        assert "host" not in _clause_fields(body["query"], "match_phrase_prefix")
        assert "package.name" not in _term_fields(body["query"])
        assert "status" in _term_fields(body["query"])
        # post_filter: 全 q を復元 — host は前方一致 should-wrapper、package は term。
        assert "host" in _clause_fields(body["post_filter"], "match_phrase")
        assert "host" in _clause_fields(body["post_filter"], "match_phrase_prefix")
        assert "package.name" in _term_fields(body["post_filter"])
        # host facet の母集団は host 句を除外 (前方一致も消える) し package 句のみ足し戻す。
        host_filter = body["aggs"]["host"]["filter"]
        assert "host" not in _clause_fields(host_filter, "match_phrase_prefix")
        assert "package.name" in _term_fields(host_filter)
        # package facet の母集団は host の前方一致 should-wrapper を保持し package 句を除外。
        package_filter = body["aggs"]["package"]["filter"]
        assert "host" in _clause_fields(package_filter, "match_phrase_prefix")
        assert "package.name" not in _term_fields(package_filter)

    def test_post_filter_suppresses_freetext_prefix_on_accession_unlock(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        """self-exclusion の post_filter も、accession 完全一致で suppressed を解禁した
        クエリでは FreeText 前方一致を出さない (base query と同じゲート)。post_filter は
        hit 母集団を復元するので、ここで prefix が漏れると別 accession の suppressed に
        当たる (docs § データ可視性。db_portal.py post_filter レーンの回帰ガード)。"""
        mock_es_search_db_portal.return_value = make_es_search_response(
            total=1,
            aggregations={"relevance": _filter_wrapped("relevance", _terms_agg("reference", 1), 1)},
        )
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "q": "PRJDB1234 AND relevance:reference",
                "facets": "relevance",
                "facetSelfExclude": "true",
            },
        )
        assert resp.status_code == 200
        body = get_es_search_body(mock_es_search_db_portal)
        assert "post_filter" in body
        # 解禁 (status terms) されていること、かつ post_filter / base に FreeText 前方一致が
        # 無いことを pin (常時 prefix ON の mutation を検出)。
        assert _INCLUDE_SUPPRESSED in _status_filter_clauses(body["query"])
        assert not _has_freetext_prefix(body["post_filter"])
        assert not _has_freetext_prefix(body["query"])

    def test_post_filter_keeps_freetext_prefix_without_accession(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        # positive control: accession でない free-text + self-exclusion では post_filter に
        # FreeText 前方一致が残る (ゲートが条件付きで常時 OFF の mutation を排除)。
        mock_es_search_db_portal.return_value = make_es_search_response(
            total=1,
            aggregations={"relevance": _filter_wrapped("relevance", _terms_agg("reference", 1), 1)},
        )
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "q": "cancer AND relevance:reference",
                "facets": "relevance",
                "facetSelfExclude": "true",
            },
        )
        assert resp.status_code == 200
        body = get_es_search_body(mock_es_search_db_portal)
        assert _has_freetext_prefix(body["post_filter"])

    def test_self_exclude_restores_hits_via_post_filter(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        """Bug guard: hits stay filtered by the full ``q`` (via post_filter) even
        though the self-excluded facet's clause is gone from the top-level query.

        Earlier the facet was wrapped in a filter agg while the top-level query
        kept the facet clause — but ES filter aggs only narrow the top-level
        population, so the self-exclusion was a no-op.  The fix moves the
        selection below the query and restores hits with post_filter.
        """
        mock_es_search_db_portal.return_value = make_es_search_response(
            total=1,
            aggregations={"organism": _filter_wrapped("organism", _organism_agg("9606", 1, "H"), 1)},
        )
        app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "biosample", "q": "organism_id:9606", "facets": "organism", "facetSelfExclude": "true"},
        )
        body_excl = get_es_search_body(mock_es_search_db_portal)

        mock_es_search_db_portal.return_value = make_es_search_response(
            total=1,
            aggregations={"organism": _organism_agg("9606", 1, "H")},
        )
        app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "biosample", "q": "organism_id:9606", "facets": "organism"},
        )
        body_plain = get_es_search_body(mock_es_search_db_portal)

        # Self-exclude: top-level query no longer carries the facet clause...
        assert "organism.identifier" not in _term_fields(body_excl["query"])
        # ...but post_filter does, so hits/total stay on the full q.
        assert "organism.identifier" in _term_fields(body_excl["post_filter"])
        # Non-self-exclude: the facet clause is in the top-level query, no post_filter.
        assert "organism.identifier" in _term_fields(body_plain["query"])
        assert "post_filter" not in body_plain
        assert "filter" in body_excl["aggs"]["organism"]
        assert "filter" not in body_plain["aggs"]["organism"]

    def test_default_is_plain_terms_agg(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        mock_es_search_db_portal.return_value = make_es_search_response(
            total=1,
            aggregations={"organism": _organism_agg("9606", 1, "H")},
        )
        app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "biosample", "q": "organism_id:9606", "facets": "organism"},
        )
        body = get_es_search_body(mock_es_search_db_portal)
        assert "filter" not in body["aggs"]["organism"]
        assert "terms" in body["aggs"]["organism"]
        assert "post_filter" not in body

    def test_cross_self_exclude_uses_base_query(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        async def _es(_client: Any, index: str, _body: dict[str, Any]) -> dict[str, Any]:
            if index == "entries":
                return make_es_search_response(
                    aggregations={"organism": _filter_wrapped("organism", _organism_agg("9606", 5, "H"), 5)},
                )
            return make_es_search_response(total=5)

        mock_es_search_db_portal.side_effect = _es
        resp = app_with_db_portal.get(
            "/db-portal/cross-search",
            params={"q": "organism_id:9606", "facets": "organism", "topHits": "0", "facetSelfExclude": "true"},
        )
        assert resp.status_code == 200
        entries_body = _find_es_body_by_index(mock_es_search_db_portal, "entries")
        assert entries_body is not None
        assert entries_body["size"] == 0
        assert "filter" in entries_body["aggs"]["organism"]
        # size=0 facet request: top-level query is the base (no facet clause), no
        # post_filter (no hits to restore).
        assert "organism.identifier" not in _term_fields(entries_body["query"])
        assert "post_filter" not in entries_body
        assert "organism.identifier" not in _term_fields(entries_body["aggs"]["organism"]["filter"])
        # The per-DB count population still carries the full q (counts are not
        # self-excluded).
        count_body = _find_es_body_by_index(mock_es_search_db_portal, "bioproject")
        assert count_body is not None
        assert "organism.identifier" in _term_fields(count_body["query"])

    def test_cursor_ignores_self_exclude(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_with_pit_db_portal: AsyncMock,
    ) -> None:
        """cursor token carries no AST, so facetSelfExclude is a no-op on the
        cursor path (plain terms agg, docs § 集計母集団と self-exclusion)."""
        baked_query = {"bool": {"filter": [_PUBLIC_ONLY_CLAUSE]}}
        token = encode_cursor(
            CursorPayload(
                pit_id="pit-1",
                search_after=["2024-01-15", "PRJDB1"],
                sort=[{"datePublished": {"order": "desc"}}, {"identifier": {"order": "asc"}}],
                query=baked_query,
            )
        )
        mock_es_search_with_pit_db_portal.return_value = make_es_search_response(
            hits=[],
            total=2,
            aggregations={"organism": _organism_agg("9606", 2, "H")},
        )
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "bioproject", "cursor": token, "facets": "organism", "facetSelfExclude": "true"},
        )
        assert resp.status_code == 200
        pit_body = mock_es_search_with_pit_db_portal.call_args.args[1]
        assert "filter" not in pit_body["aggs"]["organism"]
        assert "terms" in pit_body["aggs"]["organism"]


class TestDbPortalSolrSelfExclusion:
    """facetSelfExclude=true splits a Solr facet's top-level clause into a tagged
    ``fq`` and excludes it on that facet via ``{!ex}`` (docs § 集計母集団と
    self-exclusion)."""

    def test_trad_self_exclude_adds_ex_and_fq(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        resp_doc = make_solr_arsa_response(num_found=1)
        resp_doc["facet_counts"] = {"facet_fields": {"Division": ["BCT", 1], "MolecularType": ["genomic DNA", 1]}}
        mock_arsa_search_db_portal.return_value = resp_doc
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={
                "db": "trad",
                "q": 'division:BCT AND molecular_type:"genomic DNA"',
                "facets": "division,molecularType",
                "facetSelfExclude": "true",
            },
        )
        assert resp.status_code == 200
        params = mock_arsa_search_db_portal.call_args.kwargs["params"]
        assert params["facet.field"] == [
            "{!ex=selfex_division key=Division}Division",
            "{!ex=selfex_molecular_type key=MolecularType}MolecularType",
        ]
        assert params["fq"] == [
            '{!tag=selfex_division}Division:"BCT"',
            '{!tag=selfex_molecular_type}MolecularType:"genomic DNA"',
        ]
        # Both clauses were split out, so q collapses to all-docs; fq re-applies
        # them to the hits so the population is unchanged.
        assert params["q"] == "*:*"
        # Response still parses under the bare field keys (key=<field> preserved).
        assert resp.json()["facets"]["division"] == [{"value": "BCT", "count": 1}]

    def test_trad_default_has_no_ex_or_fq(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        resp_doc = make_solr_arsa_response(num_found=1)
        resp_doc["facet_counts"] = {"facet_fields": {"Division": ["BCT", 1]}}
        mock_arsa_search_db_portal.return_value = resp_doc
        app_with_db_portal.get(
            "/db-portal/search",
            params={"db": "trad", "q": "division:BCT", "facets": "division"},
        )
        params = mock_arsa_search_db_portal.call_args.kwargs["params"]
        assert params["facet.field"] == ["Division"]
        assert "fq" not in params

    def test_trad_multiselect_or_degrades(
        self,
        app_with_db_portal: TestClient,
        mock_arsa_search_db_portal: AsyncMock,
    ) -> None:
        """OR multi-select は分離できず {!ex} 無し (degrade)。他 facet は self-exclude される."""
        resp_doc = make_solr_arsa_response(num_found=1)
        resp_doc["facet_counts"] = {"facet_fields": {"Division": ["BCT", 1], "MolecularType": ["genomic DNA", 1]}}
        mock_arsa_search_db_portal.return_value = resp_doc
        app_with_db_portal.get(
            "/db-portal/search",
            params={
                "db": "trad",
                "q": '(division:BCT OR division:GSS) AND molecular_type:"genomic DNA"',
                "facets": "division,molecularType",
                "facetSelfExclude": "true",
            },
        )
        params = mock_arsa_search_db_portal.call_args.kwargs["params"]
        # division stays plain (OR group not split); molecularType is self-excluded.
        assert params["facet.field"] == ["Division", "{!ex=selfex_molecular_type key=MolecularType}MolecularType"]
        assert params["fq"] == ['{!tag=selfex_molecular_type}MolecularType:"genomic DNA"']
        assert "Division" in params["q"]

    def test_taxonomy_self_exclude(
        self,
        app_with_db_portal: TestClient,
        mock_txsearch_search_db_portal: AsyncMock,
    ) -> None:
        resp_doc = make_solr_txsearch_response(num_found=2)
        resp_doc["facet_counts"] = {"facet_fields": {"rank": ["species", 2], "kingdom": ["Bacteria", 2]}}
        mock_txsearch_search_db_portal.return_value = resp_doc
        resp = app_with_db_portal.get(
            "/db-portal/search",
            params={
                "db": "taxonomy",
                "q": "rank:species AND kingdom:Bacteria",
                "facets": "rank,kingdom",
                "facetSelfExclude": "true",
            },
        )
        assert resp.status_code == 200
        params = mock_txsearch_search_db_portal.call_args.kwargs["params"]
        assert params["facet.field"] == [
            "{!ex=selfex_rank key=rank}rank",
            "{!ex=selfex_kingdom key=kingdom}kingdom",
        ]
        # rank は enum (eq) なので完全一致のまま。kingdom は text contains の simple word
        # なので前方一致を相乗りした (kingdom:"Bacteria" OR kingdom:Bacteria*) になる。
        assert params["fq"] == [
            '{!tag=selfex_rank}rank:"species"',
            '{!tag=selfex_kingdom}(kingdom:"Bacteria" OR kingdom:Bacteria*)',
        ]
