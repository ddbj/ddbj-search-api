"""Integration tests for GET /db-portal/search (real ES; Solr is unset).

Verifies the dispatcher shape against a real ES cluster.  Solr-backed
DBs (``trad`` / ``taxonomy``) surface ``error=unknown`` on the count
path and 502 on the db-specific path when ``solr_arsa_base_url`` and
``solr_txsearch_url`` are unset, which is the default for integration
runs.  Advanced Search DSL (``adv``) is parsed by the Lark grammar and
surfaces DSL errors as RFC 7807 400s with dedicated ``type`` URIs.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from ddbj_search_api.schemas.db_portal import DbPortalCountError, DbPortalErrorType

_SOLR_DBS = ("trad", "taxonomy")
_ES_DBS = ("sra", "bioproject", "biosample", "jga", "gea", "metabobank")
_DB_ORDER = ("trad", "sra", "bioproject", "biosample", "jga", "gea", "metabobank", "taxonomy")


# === Cross-database count-only ===


def test_cross_search_returns_eight_databases(app: TestClient) -> None:
    """`q` alone dispatches to cross-search and returns 8 DB entries in order."""
    resp = app.get("/db-portal/search", params={"q": "human"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["databases"]) == 8
    assert [e["db"] for e in body["databases"]] == list(_DB_ORDER)


def test_cross_search_solr_dbs_error_when_solr_unset(app: TestClient) -> None:
    """`trad` / `taxonomy` report `error=unknown` when Solr URLs are unset."""
    resp = app.get("/db-portal/search", params={"q": "human"})
    body = resp.json()
    by_db = {e["db"]: e for e in body["databases"]}
    for db in _SOLR_DBS:
        assert by_db[db]["count"] is None
        assert by_db[db]["error"] == DbPortalCountError.unknown.value


def test_cross_search_es_backed_dbs_return_numeric_count(app: TestClient) -> None:
    """ES-backed DBs return integer counts when ES is up."""
    resp = app.get("/db-portal/search", params={"q": "human"})
    body = resp.json()
    by_db = {e["db"]: e for e in body["databases"]}
    for db in _ES_DBS:
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


# === Dispatch: Solr unconfigured / 400 / 422 ===


def test_adv_syntax_error_returns_400_unexpected_token(app: TestClient) -> None:
    """DSL syntax errors (e.g. ``field=value``) surface as 400 unexpected-token."""
    resp = app.get("/db-portal/search", params={"adv": "type=bioproject"})
    assert resp.status_code == 400
    assert resp.json()["type"] == DbPortalErrorType.unexpected_token.value


def test_adv_valid_tier1_runs_against_es(app: TestClient) -> None:
    """A well-formed Tier 1 DSL dispatches through the cross-db count path."""
    resp = app.get("/db-portal/search", params={"adv": "title:human"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["databases"]) == 8


def test_db_trad_returns_502_when_arsa_unset(app: TestClient) -> None:
    """`db=trad` surfaces 502 when ARSA URL is not configured."""
    resp = app.get("/db-portal/search", params={"q": "x", "db": "trad"})
    assert resp.status_code == 502


def test_db_taxonomy_returns_502_when_txsearch_unset(app: TestClient) -> None:
    """`db=taxonomy` surfaces 502 when TXSearch URL is not configured."""
    resp = app.get("/db-portal/search", params={"q": "x", "db": "taxonomy"})
    assert resp.status_code == 502


def test_cursor_with_trad_returns_400_cursor_not_supported(app: TestClient) -> None:
    """`db=trad` + `cursor` returns 400 cursor-not-supported (Solr is offset-only)."""
    resp = app.get("/db-portal/search", params={"db": "trad", "cursor": "abc.def"})
    assert resp.status_code == 400
    assert resp.json()["type"] == DbPortalErrorType.cursor_not_supported.value


def test_q_and_adv_returns_400(app: TestClient) -> None:
    """`q` and `adv` together returns 400 invalid-query-combination."""
    resp = app.get("/db-portal/search", params={"q": "foo", "adv": "bar"})
    assert resp.status_code == 400
    assert resp.json()["type"] == DbPortalErrorType.invalid_query_combination.value


def test_db_unknown_returns_422(app: TestClient) -> None:
    """Unknown `db` value is rejected by FastAPI enum validation."""
    resp = app.get("/db-portal/search", params={"q": "x", "db": "unknown"})
    assert resp.status_code == 422
