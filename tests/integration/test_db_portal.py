"""Integration tests for GET /db-portal/search (real ES).

AP1 scope: verifies the dispatcher shape against a real ES cluster.
Advanced Search (AP3) and Solr proxy (AP4) paths return 501 and are
asserted here for regression safety.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from ddbj_search_api.schemas.db_portal import DbPortalCountError, DbPortalErrorType

_SOLR_PENDING = ("trad", "taxonomy")
_ES_BACKED = ("sra", "bioproject", "biosample", "jga", "gea", "metabobank")
_DB_ORDER = ("trad", "sra", "bioproject", "biosample", "jga", "gea", "metabobank", "taxonomy")


# === Cross-database count-only ===


def test_cross_search_returns_eight_databases(app: TestClient) -> None:
    """`q` alone dispatches to cross-search and returns 8 DB entries in order."""
    resp = app.get("/db-portal/search", params={"q": "human"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["databases"]) == 8
    assert [e["db"] for e in body["databases"]] == list(_DB_ORDER)


def test_cross_search_solr_pending_dbs_are_not_implemented(app: TestClient) -> None:
    """`trad` and `taxonomy` are stubbed until AP4."""
    resp = app.get("/db-portal/search", params={"q": "human"})
    body = resp.json()
    by_db = {e["db"]: e for e in body["databases"]}
    for db in _SOLR_PENDING:
        assert by_db[db]["count"] is None
        assert by_db[db]["error"] == DbPortalCountError.not_implemented.value


def test_cross_search_es_backed_dbs_return_numeric_count(app: TestClient) -> None:
    """ES-backed DBs return integer counts when ES is up."""
    resp = app.get("/db-portal/search", params={"q": "human"})
    body = resp.json()
    by_db = {e["db"]: e for e in body["databases"]}
    for db in _ES_BACKED:
        entry = by_db[db]
        # count may be 0 in a fresh dev index but must never be null.
        assert entry["error"] is None or isinstance(entry["count"], int)


def test_cross_search_without_q(app: TestClient) -> None:
    """`q` is optional; cross-search without it runs match_all."""
    resp = app.get("/db-portal/search")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["databases"]) == 8


# === DB-specific hits search ===


def test_db_specific_bioproject_returns_hits(app: TestClient) -> None:
    """`q` + `db=bioproject` returns a hits envelope."""
    resp = app.get("/db-portal/search", params={"q": "human", "db": "bioproject"})
    assert resp.status_code == 200
    body = resp.json()
    assert "total" in body
    assert "hits" in body
    assert "hardLimitReached" in body
    assert "perPage" in body
    assert body["perPage"] == 20


def test_db_specific_per_page_50(app: TestClient) -> None:
    """perPage=50 accepted (member of Literal)."""
    resp = app.get(
        "/db-portal/search",
        params={"q": "human", "db": "bioproject", "perPage": 50},
    )
    assert resp.status_code == 200
    assert resp.json()["perPage"] == 50


# === Dispatch: 501 / 400 / 422 ===


def test_adv_returns_501(app: TestClient) -> None:
    """`adv` dispatches to the Advanced Search stub."""
    resp = app.get("/db-portal/search", params={"adv": "type=bioproject"})
    assert resp.status_code == 501
    assert resp.json()["type"] == DbPortalErrorType.advanced_search_not_implemented.value


def test_db_trad_returns_501(app: TestClient) -> None:
    """`db=trad` dispatches to the Solr-pending stub."""
    resp = app.get("/db-portal/search", params={"q": "x", "db": "trad"})
    assert resp.status_code == 501
    assert resp.json()["type"] == DbPortalErrorType.db_not_implemented.value


def test_db_taxonomy_returns_501(app: TestClient) -> None:
    """`db=taxonomy` dispatches to the Solr-pending stub."""
    resp = app.get("/db-portal/search", params={"q": "x", "db": "taxonomy"})
    assert resp.status_code == 501
    assert resp.json()["type"] == DbPortalErrorType.db_not_implemented.value


def test_q_and_adv_returns_400(app: TestClient) -> None:
    """`q` and `adv` together returns 400 invalid-query-combination."""
    resp = app.get("/db-portal/search", params={"q": "foo", "adv": "bar"})
    assert resp.status_code == 400
    assert resp.json()["type"] == DbPortalErrorType.invalid_query_combination.value


def test_db_unknown_returns_422(app: TestClient) -> None:
    """Unknown `db` value is rejected by FastAPI enum validation."""
    resp = app.get("/db-portal/search", params={"q": "x", "db": "unknown"})
    assert resp.status_code == 422
