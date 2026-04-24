"""Search endpoint for db-portal frontend: GET /db-portal/search.

Five request patterns dispatched by a single handler:

1. ``q`` only            → cross-database count-only (8 entries, ES + Solr)
2. ``q`` + ``db``        → db-specific hits envelope
                           (ES for 6 DBs, Solr for ``trad`` / ``taxonomy``)
3. ``adv`` only          → cross-database count-only via the DSL compiler
4. ``adv`` + ``db``      → db-specific hits envelope via the DSL compiler
5. ``cursor`` + ``db=trad/taxonomy`` → 400 ``cursor-not-supported``
                           (Solr is offset-only; no PIT equivalent in 4.4.0)

Mutually exclusive ``q`` + ``adv`` returns 400.  Cross-search count-only
fans out in parallel with per-backend ``asyncio.wait_for`` bounds and an
overall ``asyncio.wait`` deadline.  DSL errors (unknown-field,
invalid-date-format, etc.) surface as 400 + RFC 7807 + dedicated type URI.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.exceptions import HTTPException as StarletteHTTPException

from ddbj_search_api.config import AppConfig, get_config
from ddbj_search_api.cursor import compute_next_cursor, decode_cursor
from ddbj_search_api.es import get_es_client
from ddbj_search_api.es.client import es_open_pit, es_search, es_search_with_pit
from ddbj_search_api.es.query import (
    build_search_query,
    build_sort_with_tiebreaker,
    pagination_to_from_size,
)
from ddbj_search_api.schemas.common import ProblemDetails
from ddbj_search_api.schemas.db_portal import (
    DbPortalCount,
    DbPortalCountError,
    DbPortalCrossSearchResponse,
    DbPortalDb,
    DbPortalErrorType,
    DbPortalHit,
    DbPortalHitsResponse,
    DbPortalParseResponse,
    DbPortalQuery,
    _DbPortalHitAdapter,
)
from ddbj_search_api.search.dsl import (
    DslError,
    ast_to_json,
    compile_to_es,
    compile_to_solr,
    parse,
    validate,
)
from ddbj_search_api.search.dsl.ast import Node as DslNode
from ddbj_search_api.solr import get_solr_client
from ddbj_search_api.solr.client import arsa_search, txsearch_search
from ddbj_search_api.solr.mappers import (
    arsa_response_to_envelope,
    txsearch_response_to_envelope,
)
from ddbj_search_api.solr.query import (
    build_arsa_adv_params,
    build_arsa_params,
    build_txsearch_adv_params,
    build_txsearch_params,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Deep paging limit aligned with /entries/* (see routers.entries._DEEP_PAGING_LIMIT).
_DEEP_PAGING_LIMIT = 10000

# Cross-search `databases[]` order (fixed, exposed in OpenAPI spec).
_DB_ORDER: tuple[DbPortalDb, ...] = (
    DbPortalDb.trad,
    DbPortalDb.sra,
    DbPortalDb.bioproject,
    DbPortalDb.biosample,
    DbPortalDb.jga,
    DbPortalDb.gea,
    DbPortalDb.metabobank,
    DbPortalDb.taxonomy,
)

# DbPortalDb → ES index/alias name.  converter ALIASES: "sra" (6 indices),
# "jga" (4 indices); other DBs map 1:1 to their index name.  Solr-backed
# DBs (trad / taxonomy) are not in this map.
_DB_TO_INDEX: dict[DbPortalDb, str] = {
    DbPortalDb.sra: "sra",
    DbPortalDb.jga: "jga",
    DbPortalDb.bioproject: "bioproject",
    DbPortalDb.biosample: "biosample",
    DbPortalDb.gea: "gea",
    DbPortalDb.metabobank: "metabobank",
}

_SOLR_DBS: frozenset[DbPortalDb] = frozenset({DbPortalDb.trad, DbPortalDb.taxonomy})


# === Exception ===


class DbPortalHTTPException(StarletteHTTPException):
    """HTTPException carrying an RFC 7807 ``type`` URI.

    Caught by the dedicated handler registered in
    ``ddbj_search_api.main.setup_error_handlers``; the handler forwards
    ``type_uri`` to ``_problem_json(problem_type=...)`` so the response
    body carries the correct ``type`` URI.
    """

    def __init__(
        self,
        status_code: int,
        type_uri: DbPortalErrorType,
        detail: str,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.type_uri: str = type_uri.value


# === Helpers ===


def _db_to_index(db: DbPortalDb) -> str:
    return _DB_TO_INDEX[db]


def _validate_deep_paging(page: int, per_page: int) -> None:
    if page * per_page > _DEEP_PAGING_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Deep paging limit exceeded: page ({page}) * "
                f"perPage ({per_page}) = {page * per_page} > {_DEEP_PAGING_LIMIT}. "
                "Use cursor-based pagination for deep results."
            ),
        )


def _validate_cursor_exclusivity(query: DbPortalQuery) -> None:
    """Raise 400 when cursor is used with incompatible params.

    Cursor mode encodes search state (query, sort, PIT) in the token;
    only ``db`` (required to pick the target index) and ``perPage``
    may accompany it.
    """
    conflicting: list[str] = []
    if query.page != 1:
        conflicting.append("page")
    if query.q is not None:
        conflicting.append("q")
    if query.adv is not None:
        conflicting.append("adv")
    if query.sort is not None:
        conflicting.append("sort")
    if conflicting:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot use 'cursor' with: {', '.join(conflicting)}. "
                "When using cursor-based pagination, only 'db' and 'perPage' are allowed."
            ),
        )


def _hit_from_source(hit: dict[str, Any]) -> DbPortalHit:
    """Dispatch ES ``_source`` dict into one of the 8 DbPortalHit variants.

    ``DbPortalHit`` is a Pydantic v2 discriminated union keyed on ``type``.
    Unknown / missing ``type`` raises ``ValidationError`` and is surfaced as
    a 500 by the caller (no silent fallback variant).
    """
    source = dict(hit.get("_source", {}))
    return _DbPortalHitAdapter.validate_python(source)  # type: ignore[no-any-return]


def _map_httpx_error(exc: Exception) -> DbPortalCountError:
    if isinstance(exc, httpx.TimeoutException):
        return DbPortalCountError.timeout
    if isinstance(exc, httpx.ConnectError):
        return DbPortalCountError.connection_refused
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if 500 <= status < 600:
            return DbPortalCountError.upstream_5xx
    return DbPortalCountError.unknown


# === Cross-database count-only ===


async def _count_one_db_es(
    client: httpx.AsyncClient,
    config: AppConfig,
    db: DbPortalDb,
    query_body: dict[str, Any],
) -> DbPortalCount:
    try:
        resp = await asyncio.wait_for(
            es_search(
                client,
                _db_to_index(db),
                {"query": query_body, "size": 0},
            ),
            timeout=config.es_search_timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "db-portal cross-search timed out for db=%s (es_search_timeout=%.2fs)",
            db.value,
            config.es_search_timeout,
        )
        return DbPortalCount(db=db, count=None, error=DbPortalCountError.timeout)
    except Exception as exc:
        error = _map_httpx_error(exc)
        logger.warning(
            "db-portal cross-search failed for db=%s: %s (error=%s)",
            db.value,
            type(exc).__name__,
            error.value,
        )
        return DbPortalCount(db=db, count=None, error=error)
    try:
        count = int(resp["hits"]["total"]["value"])
    except (KeyError, TypeError, ValueError):
        logger.warning(
            "db-portal cross-search: unexpected ES response shape for db=%s",
            db.value,
        )
        return DbPortalCount(db=db, count=None, error=DbPortalCountError.unknown)
    return DbPortalCount(db=db, count=count, error=None)


async def _count_arsa(
    client: httpx.AsyncClient,
    config: AppConfig,
    q: str | None,
) -> DbPortalCount:
    if not config.solr_arsa_base_url:
        return DbPortalCount(
            db=DbPortalDb.trad,
            count=None,
            error=DbPortalCountError.unknown,
        )
    params = build_arsa_params(
        keywords=q,
        page=1,
        per_page=0,
        sort=None,
        shards=config.solr_arsa_shards,
    )
    try:
        resp = await asyncio.wait_for(
            arsa_search(
                client,
                base_url=config.solr_arsa_base_url,
                core=config.solr_arsa_core,
                params=params,
            ),
            timeout=config.arsa_timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "db-portal cross-search timed out for db=trad (ARSA, arsa_timeout=%.2fs)",
            config.arsa_timeout,
        )
        return DbPortalCount(db=DbPortalDb.trad, count=None, error=DbPortalCountError.timeout)
    except Exception as exc:
        error = _map_httpx_error(exc)
        logger.warning(
            "db-portal cross-search failed for db=trad (ARSA): %s (error=%s)",
            type(exc).__name__,
            error.value,
        )
        return DbPortalCount(db=DbPortalDb.trad, count=None, error=error)
    try:
        count = int(resp["response"]["numFound"])
    except (KeyError, TypeError, ValueError):
        logger.warning("db-portal cross-search: unexpected ARSA response shape")
        return DbPortalCount(db=DbPortalDb.trad, count=None, error=DbPortalCountError.unknown)
    return DbPortalCount(db=DbPortalDb.trad, count=count, error=None)


async def _count_txsearch(
    client: httpx.AsyncClient,
    config: AppConfig,
    q: str | None,
) -> DbPortalCount:
    if not config.solr_txsearch_url:
        return DbPortalCount(
            db=DbPortalDb.taxonomy,
            count=None,
            error=DbPortalCountError.unknown,
        )
    params = build_txsearch_params(
        keywords=q,
        page=1,
        per_page=0,
        sort=None,
    )
    try:
        resp = await asyncio.wait_for(
            txsearch_search(
                client,
                url=config.solr_txsearch_url,
                params=params,
            ),
            timeout=config.txsearch_timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "db-portal cross-search timed out for db=taxonomy (TXSearch, txsearch_timeout=%.2fs)",
            config.txsearch_timeout,
        )
        return DbPortalCount(db=DbPortalDb.taxonomy, count=None, error=DbPortalCountError.timeout)
    except Exception as exc:
        error = _map_httpx_error(exc)
        logger.warning(
            "db-portal cross-search failed for db=taxonomy (TXSearch): %s (error=%s)",
            type(exc).__name__,
            error.value,
        )
        return DbPortalCount(db=DbPortalDb.taxonomy, count=None, error=error)
    try:
        count = int(resp["response"]["numFound"])
    except (KeyError, TypeError, ValueError):
        logger.warning("db-portal cross-search: unexpected TXSearch response shape")
        return DbPortalCount(db=DbPortalDb.taxonomy, count=None, error=DbPortalCountError.unknown)
    return DbPortalCount(db=DbPortalDb.taxonomy, count=count, error=None)


async def _count_one_db(
    es_client: httpx.AsyncClient,
    solr_client: httpx.AsyncClient,
    config: AppConfig,
    db: DbPortalDb,
    es_query_body: dict[str, Any],
    q: str | None,
) -> DbPortalCount:
    """Run one count-only search and map errors to a DbPortalCount."""
    if db == DbPortalDb.trad:
        return await _count_arsa(solr_client, config, q)
    if db == DbPortalDb.taxonomy:
        return await _count_txsearch(solr_client, config, q)
    return await _count_one_db_es(es_client, config, db, es_query_body)


async def _cross_search_count_only(
    es_client: httpx.AsyncClient,
    solr_client: httpx.AsyncClient,
    config: AppConfig,
    q: str | None,
) -> DbPortalCrossSearchResponse:
    """Parallel cross-database count-only search.

    All 8 DBs fan out via ``asyncio.create_task``; ``asyncio.wait`` with
    ``ALL_COMPLETED`` + ``cross_search_total_timeout`` collects them.
    Per-backend timeouts are applied inside each ``_count_one_db_*``
    via ``asyncio.wait_for``.  Tasks still pending at the total deadline
    are cancelled and surfaced as ``error=timeout`` in the response,
    preserving the partial-success policy (200 as long as any DB
    returned a count; 502 only when every DB failed).
    """
    query_body = build_search_query(keywords=q, keyword_operator="AND")
    task_map: dict[asyncio.Task[DbPortalCount], DbPortalDb] = {}
    for db in _DB_ORDER:
        task = asyncio.create_task(
            _count_one_db(es_client, solr_client, config, db, query_body, q),
        )
        task_map[task] = db
    done, pending = await asyncio.wait(
        task_map.keys(),
        timeout=config.cross_search_total_timeout,
        return_when=asyncio.ALL_COMPLETED,
    )
    results: dict[DbPortalDb, DbPortalCount] = {}
    for task in done:
        results[task_map[task]] = task.result()
    for task in pending:
        task.cancel()
        db = task_map[task]
        logger.warning(
            "db-portal cross-search hit total timeout for db=%s (cancelled, total_timeout=%.2fs)",
            db.value,
            config.cross_search_total_timeout,
        )
        results[db] = DbPortalCount(
            db=db,
            count=None,
            error=DbPortalCountError.timeout,
        )
    databases = [results[db] for db in _DB_ORDER]
    if all(item.error is not None for item in databases):
        raise HTTPException(
            status_code=502,
            detail="All databases failed to respond.",
        )
    return DbPortalCrossSearchResponse(databases=databases)


# === Advanced Search DSL cross-db count ===


async def _count_arsa_adv(
    client: httpx.AsyncClient,
    config: AppConfig,
    q_string: str,
) -> DbPortalCount:
    if not config.solr_arsa_base_url:
        return DbPortalCount(
            db=DbPortalDb.trad,
            count=None,
            error=DbPortalCountError.unknown,
        )
    params = build_arsa_adv_params(
        q=q_string,
        page=1,
        per_page=0,
        sort=None,
        shards=config.solr_arsa_shards,
    )
    try:
        resp = await asyncio.wait_for(
            arsa_search(
                client,
                base_url=config.solr_arsa_base_url,
                core=config.solr_arsa_core,
                params=params,
            ),
            timeout=config.arsa_timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "db-portal adv cross-search timed out for db=trad (ARSA, arsa_timeout=%.2fs)",
            config.arsa_timeout,
        )
        return DbPortalCount(db=DbPortalDb.trad, count=None, error=DbPortalCountError.timeout)
    except Exception as exc:
        error = _map_httpx_error(exc)
        logger.warning(
            "db-portal adv cross-search failed for db=trad (ARSA): %s (error=%s)",
            type(exc).__name__,
            error.value,
        )
        return DbPortalCount(db=DbPortalDb.trad, count=None, error=error)
    try:
        count = int(resp["response"]["numFound"])
    except (KeyError, TypeError, ValueError):
        logger.warning("db-portal adv cross-search: unexpected ARSA response shape")
        return DbPortalCount(db=DbPortalDb.trad, count=None, error=DbPortalCountError.unknown)
    return DbPortalCount(db=DbPortalDb.trad, count=count, error=None)


async def _count_txsearch_adv(
    client: httpx.AsyncClient,
    config: AppConfig,
    q_string: str,
) -> DbPortalCount:
    if not config.solr_txsearch_url:
        return DbPortalCount(
            db=DbPortalDb.taxonomy,
            count=None,
            error=DbPortalCountError.unknown,
        )
    params = build_txsearch_adv_params(
        q=q_string,
        page=1,
        per_page=0,
        sort=None,
    )
    try:
        resp = await asyncio.wait_for(
            txsearch_search(
                client,
                url=config.solr_txsearch_url,
                params=params,
            ),
            timeout=config.txsearch_timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "db-portal adv cross-search timed out for db=taxonomy (TXSearch, txsearch_timeout=%.2fs)",
            config.txsearch_timeout,
        )
        return DbPortalCount(db=DbPortalDb.taxonomy, count=None, error=DbPortalCountError.timeout)
    except Exception as exc:
        error = _map_httpx_error(exc)
        logger.warning(
            "db-portal adv cross-search failed for db=taxonomy (TXSearch): %s (error=%s)",
            type(exc).__name__,
            error.value,
        )
        return DbPortalCount(db=DbPortalDb.taxonomy, count=None, error=error)
    try:
        count = int(resp["response"]["numFound"])
    except (KeyError, TypeError, ValueError):
        logger.warning("db-portal adv cross-search: unexpected TXSearch response shape")
        return DbPortalCount(db=DbPortalDb.taxonomy, count=None, error=DbPortalCountError.unknown)
    return DbPortalCount(db=DbPortalDb.taxonomy, count=count, error=None)


async def _count_one_db_adv(
    es_client: httpx.AsyncClient,
    solr_client: httpx.AsyncClient,
    config: AppConfig,
    db: DbPortalDb,
    es_query_body: dict[str, Any],
    arsa_q: str,
    txsearch_q: str,
) -> DbPortalCount:
    if db == DbPortalDb.trad:
        return await _count_arsa_adv(solr_client, config, arsa_q)
    if db == DbPortalDb.taxonomy:
        return await _count_txsearch_adv(solr_client, config, txsearch_q)
    return await _count_one_db_es(es_client, config, db, es_query_body)


async def _adv_cross_search_count_only(
    es_client: httpx.AsyncClient,
    solr_client: httpx.AsyncClient,
    config: AppConfig,
    ast: DslNode,
) -> DbPortalCrossSearchResponse:
    """Parallel cross-database adv count-only.

    Compiles the AST once per backend dialect, then fans out 8 DBs via
    ``asyncio.create_task`` with ``ALL_COMPLETED`` + ``cross_search_total_timeout``.
    Partial-success policy (200 unless every DB failed) matches the simple-search flow.
    """
    es_query_body = compile_to_es(ast)
    arsa_q = compile_to_solr(ast, dialect="arsa")
    txsearch_q = compile_to_solr(ast, dialect="txsearch")
    task_map: dict[asyncio.Task[DbPortalCount], DbPortalDb] = {}
    for db in _DB_ORDER:
        task = asyncio.create_task(
            _count_one_db_adv(
                es_client,
                solr_client,
                config,
                db,
                es_query_body,
                arsa_q,
                txsearch_q,
            ),
        )
        task_map[task] = db
    done, pending = await asyncio.wait(
        task_map.keys(),
        timeout=config.cross_search_total_timeout,
        return_when=asyncio.ALL_COMPLETED,
    )
    results: dict[DbPortalDb, DbPortalCount] = {}
    for task in done:
        results[task_map[task]] = task.result()
    for task in pending:
        task.cancel()
        db = task_map[task]
        logger.warning(
            "db-portal adv cross-search hit total timeout for db=%s (cancelled, total_timeout=%.2fs)",
            db.value,
            config.cross_search_total_timeout,
        )
        results[db] = DbPortalCount(db=db, count=None, error=DbPortalCountError.timeout)
    databases = [results[db] for db in _DB_ORDER]
    if all(item.error is not None for item in databases):
        raise HTTPException(
            status_code=502,
            detail="All databases failed to respond.",
        )
    return DbPortalCrossSearchResponse(databases=databases)


# === Advanced Search DSL db-specific hits ===


async def _search_arsa_adv(
    client: httpx.AsyncClient,
    config: AppConfig,
    query: DbPortalQuery,
    q_string: str,
) -> DbPortalHitsResponse:
    if not config.solr_arsa_base_url:
        raise HTTPException(
            status_code=502,
            detail="ARSA backend is not configured.",
        )
    params = build_arsa_adv_params(
        q=q_string,
        page=query.page,
        per_page=query.per_page,
        sort=query.sort,
        shards=config.solr_arsa_shards,
    )
    try:
        resp = await arsa_search(
            client,
            base_url=config.solr_arsa_base_url,
            core=config.solr_arsa_core,
            params=params,
        )
    except Exception as exc:
        error = _map_httpx_error(exc)
        logger.warning(
            "db-portal adv db-specific search failed for db=trad (ARSA): %s (error=%s)",
            type(exc).__name__,
            error.value,
        )
        raise HTTPException(
            status_code=502,
            detail=f"ARSA upstream failure: {error.value}",
        ) from exc
    return arsa_response_to_envelope(
        resp,
        page=query.page,
        per_page=query.per_page,
        sort=query.sort,
    )


async def _search_txsearch_adv(
    client: httpx.AsyncClient,
    config: AppConfig,
    query: DbPortalQuery,
    q_string: str,
) -> DbPortalHitsResponse:
    if not config.solr_txsearch_url:
        raise HTTPException(
            status_code=502,
            detail="TXSearch backend is not configured.",
        )
    params = build_txsearch_adv_params(
        q=q_string,
        page=query.page,
        per_page=query.per_page,
        sort=query.sort,
    )
    try:
        resp = await txsearch_search(
            client,
            url=config.solr_txsearch_url,
            params=params,
        )
    except Exception as exc:
        error = _map_httpx_error(exc)
        logger.warning(
            "db-portal adv db-specific search failed for db=taxonomy (TXSearch): %s (error=%s)",
            type(exc).__name__,
            error.value,
        )
        raise HTTPException(
            status_code=502,
            detail=f"TXSearch upstream failure: {error.value}",
        ) from exc
    return txsearch_response_to_envelope(
        resp,
        page=query.page,
        per_page=query.per_page,
        sort=query.sort,
    )


async def _db_specific_search_es_adv(
    client: httpx.AsyncClient,
    query: DbPortalQuery,
    es_query_body: dict[str, Any],
) -> DbPortalHitsResponse:
    """ES hits envelope for adv + db (offset-only; cursor + adv is blocked upstream)."""
    assert query.db is not None
    _validate_deep_paging(query.page, query.per_page)
    sort_body = build_sort_with_tiebreaker(query.sort)
    from_, size = pagination_to_from_size(query.page, query.per_page)
    body: dict[str, Any] = {
        "query": es_query_body,
        "from": from_,
        "size": size,
        "sort": sort_body,
    }
    es_resp = await es_search(client, _db_to_index(query.db), body)
    raw_hits = es_resp["hits"]["hits"]
    total = int(es_resp["hits"]["total"]["value"])
    hits = [_hit_from_source(h) for h in raw_hits]
    next_cursor, has_next = compute_next_cursor(
        raw_hits=raw_hits,
        size=size,
        total=total,
        offset=from_,
        sort_with_tiebreaker=sort_body,
        query=es_query_body,
        pit_id=None,
    )
    return DbPortalHitsResponse(  # type: ignore[call-arg]
        total=total,
        hits=hits,
        hard_limit_reached=(total >= _DEEP_PAGING_LIMIT),
        page=query.page,
        per_page=query.per_page,
        next_cursor=next_cursor,
        has_next=has_next,
    )


async def _adv_db_specific_search(
    es_client: httpx.AsyncClient,
    solr_client: httpx.AsyncClient,
    config: AppConfig,
    query: DbPortalQuery,
    ast: DslNode,
) -> DbPortalHitsResponse:
    assert query.db is not None
    if query.db in _SOLR_DBS:
        if query.cursor is not None:
            raise DbPortalHTTPException(
                status_code=400,
                type_uri=DbPortalErrorType.cursor_not_supported,
                detail=(
                    f"Cursor-based pagination is not supported for db='{query.db.value}'. "
                    "Use 'page' + 'perPage' (offset-only) instead."
                ),
            )
        _validate_deep_paging(query.page, query.per_page)
        if query.db == DbPortalDb.trad:
            return await _search_arsa_adv(
                solr_client,
                config,
                query,
                compile_to_solr(ast, dialect="arsa"),
            )
        return await _search_txsearch_adv(
            solr_client,
            config,
            query,
            compile_to_solr(ast, dialect="txsearch"),
        )
    # ES DB: cursor + adv is blocked by _validate_cursor_exclusivity (adv in conflict list).
    return await _db_specific_search_es_adv(es_client, query, compile_to_es(ast))


# === DB-specific hits search ===


async def _db_specific_search(
    es_client: httpx.AsyncClient,
    solr_client: httpx.AsyncClient,
    config: AppConfig,
    query: DbPortalQuery,
) -> DbPortalHitsResponse:
    if query.db in _SOLR_DBS:
        if query.cursor is not None:
            assert query.db is not None
            raise DbPortalHTTPException(
                status_code=400,
                type_uri=DbPortalErrorType.cursor_not_supported,
                detail=(
                    f"Cursor-based pagination is not supported for db='{query.db.value}'. "
                    "Use 'page' + 'perPage' (offset-only) instead."
                ),
            )
        _validate_deep_paging(query.page, query.per_page)
        if query.db == DbPortalDb.trad:
            return await _search_arsa(solr_client, config, query)
        return await _search_txsearch(solr_client, config, query)
    if query.cursor is not None:
        return await _db_specific_search_cursor(es_client, query)
    return await _db_specific_search_offset(es_client, query)


async def _search_arsa(
    client: httpx.AsyncClient,
    config: AppConfig,
    query: DbPortalQuery,
) -> DbPortalHitsResponse:
    if not config.solr_arsa_base_url:
        raise HTTPException(
            status_code=502,
            detail="ARSA backend is not configured.",
        )
    params = build_arsa_params(
        keywords=query.q,
        page=query.page,
        per_page=query.per_page,
        sort=query.sort,
        shards=config.solr_arsa_shards,
    )
    try:
        resp = await arsa_search(
            client,
            base_url=config.solr_arsa_base_url,
            core=config.solr_arsa_core,
            params=params,
        )
    except Exception as exc:
        error = _map_httpx_error(exc)
        logger.warning(
            "db-portal db-specific search failed for db=trad (ARSA): %s (error=%s)",
            type(exc).__name__,
            error.value,
        )
        raise HTTPException(
            status_code=502,
            detail=f"ARSA upstream failure: {error.value}",
        ) from exc
    return arsa_response_to_envelope(
        resp,
        page=query.page,
        per_page=query.per_page,
        sort=query.sort,
    )


async def _search_txsearch(
    client: httpx.AsyncClient,
    config: AppConfig,
    query: DbPortalQuery,
) -> DbPortalHitsResponse:
    if not config.solr_txsearch_url:
        raise HTTPException(
            status_code=502,
            detail="TXSearch backend is not configured.",
        )
    params = build_txsearch_params(
        keywords=query.q,
        page=query.page,
        per_page=query.per_page,
        sort=query.sort,
    )
    try:
        resp = await txsearch_search(
            client,
            url=config.solr_txsearch_url,
            params=params,
        )
    except Exception as exc:
        error = _map_httpx_error(exc)
        logger.warning(
            "db-portal db-specific search failed for db=taxonomy (TXSearch): %s (error=%s)",
            type(exc).__name__,
            error.value,
        )
        raise HTTPException(
            status_code=502,
            detail=f"TXSearch upstream failure: {error.value}",
        ) from exc
    return txsearch_response_to_envelope(
        resp,
        page=query.page,
        per_page=query.per_page,
        sort=query.sort,
    )


async def _db_specific_search_offset(
    client: httpx.AsyncClient,
    query: DbPortalQuery,
) -> DbPortalHitsResponse:
    assert query.db is not None
    _validate_deep_paging(query.page, query.per_page)
    es_query = build_search_query(keywords=query.q, keyword_operator="AND")
    sort_body = build_sort_with_tiebreaker(query.sort)
    from_, size = pagination_to_from_size(query.page, query.per_page)
    body: dict[str, Any] = {
        "query": es_query,
        "from": from_,
        "size": size,
        "sort": sort_body,
    }
    es_resp = await es_search(client, _db_to_index(query.db), body)
    raw_hits = es_resp["hits"]["hits"]
    total = int(es_resp["hits"]["total"]["value"])
    hits = [_hit_from_source(h) for h in raw_hits]
    next_cursor, has_next = compute_next_cursor(
        raw_hits=raw_hits,
        size=size,
        total=total,
        offset=from_,
        sort_with_tiebreaker=sort_body,
        query=es_query,
        pit_id=None,
    )
    return DbPortalHitsResponse(  # type: ignore[call-arg]
        total=total,
        hits=hits,
        hard_limit_reached=(total >= _DEEP_PAGING_LIMIT),
        page=query.page,
        per_page=query.per_page,
        next_cursor=next_cursor,
        has_next=has_next,
    )


async def _db_specific_search_cursor(
    client: httpx.AsyncClient,
    query: DbPortalQuery,
) -> DbPortalHitsResponse:
    assert query.db is not None
    assert query.cursor is not None
    _validate_cursor_exclusivity(query)
    try:
        cursor = decode_cursor(query.cursor)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid cursor token: {exc}",
        ) from exc
    pit_id = cursor.pit_id
    if pit_id is None:
        pit_id = await es_open_pit(client, _db_to_index(query.db))
    body: dict[str, Any] = {
        "query": cursor.query,
        "sort": cursor.sort,
        "size": query.per_page,
        "pit": {"id": pit_id, "keep_alive": "5m"},
        "search_after": cursor.search_after,
    }
    try:
        es_resp = await es_search_with_pit(client, body)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(
                status_code=400,
                detail=("Cursor expired (PIT no longer available). Please restart your search."),
            ) from exc
        raise
    raw_hits = es_resp["hits"]["hits"]
    total = int(es_resp["hits"]["total"]["value"])
    updated_pit_id: str = es_resp.get("pit_id", pit_id)
    hits = [_hit_from_source(h) for h in raw_hits]
    next_cursor, has_next = compute_next_cursor(
        raw_hits=raw_hits,
        size=query.per_page,
        total=total,
        offset=0,
        sort_with_tiebreaker=cursor.sort,
        query=cursor.query,
        pit_id=updated_pit_id,
    )
    return DbPortalHitsResponse(  # type: ignore[call-arg]
        total=total,
        hits=hits,
        hard_limit_reached=(total >= _DEEP_PAGING_LIMIT),
        page=None,
        per_page=query.per_page,
        next_cursor=next_cursor,
        has_next=has_next,
    )


# === Dispatcher ===


def _get_config_dep() -> AppConfig:
    """FastAPI-friendly wrapper around ``get_config``.

    ``get_config`` accepts optional CLI overrides; FastAPI would try to
    interpret those as query params.  This wrapper takes no args.
    """
    return get_config()


async def _search_db_portal(
    query: DbPortalQuery = Depends(),
    es_client: httpx.AsyncClient = Depends(get_es_client),
    solr_client: httpx.AsyncClient = Depends(get_solr_client),
    config: AppConfig = Depends(_get_config_dep),
) -> DbPortalCrossSearchResponse | DbPortalHitsResponse:
    """Unified db-portal search: dispatch by (q/adv) x (db)."""
    if query.q is not None and query.adv is not None:
        raise DbPortalHTTPException(
            status_code=400,
            type_uri=DbPortalErrorType.invalid_query_combination,
            detail="'q' and 'adv' are mutually exclusive; specify exactly one.",
        )
    if query.adv is not None:
        if query.cursor is not None:
            if query.db in _SOLR_DBS:
                assert query.db is not None
                raise DbPortalHTTPException(
                    status_code=400,
                    type_uri=DbPortalErrorType.cursor_not_supported,
                    detail=(
                        f"Cursor-based pagination is not supported for db='{query.db.value}'. "
                        "Use 'page' + 'perPage' (offset-only) instead."
                    ),
                )
            raise DbPortalHTTPException(
                status_code=400,
                type_uri=DbPortalErrorType.cursor_not_supported,
                detail=(
                    "Cursor-based pagination is not supported with 'adv'. "
                    "Advanced Search uses offset pagination; omit 'cursor' to paginate."
                ),
            )
        try:
            ast = parse(query.adv, max_length=config.dsl_max_length)
            validate(
                ast,
                mode="cross" if query.db is None else "single",
                db=query.db,
                max_depth=config.dsl_max_depth,
            )
        except DslError as exc:
            raise DbPortalHTTPException(
                status_code=400,
                type_uri=DbPortalErrorType[exc.type.name],
                detail=exc.detail,
            ) from exc
        if query.db is None:
            return await _adv_cross_search_count_only(es_client, solr_client, config, ast)
        return await _adv_db_specific_search(es_client, solr_client, config, query, ast)
    if query.cursor is not None and query.db is None:
        raise HTTPException(
            status_code=400,
            detail="Cursor-based pagination requires a 'db' parameter.",
        )
    if query.db is None:
        return await _cross_search_count_only(es_client, solr_client, config, query.q)
    return await _db_specific_search(es_client, solr_client, config, query)


router.add_api_route(
    "/db-portal/search",
    _search_db_portal,
    methods=["GET"],
    response_model=DbPortalCrossSearchResponse | DbPortalHitsResponse,
    responses={
        400: {
            "description": (
                "Bad Request (q/adv exclusivity, cursor exclusivity, DSL parse/validate error, deep paging limit)."
            ),
            "model": ProblemDetails,
        },
        502: {
            "description": "Bad Gateway (all databases failed, or Solr upstream error)",
            "model": ProblemDetails,
        },
    },
    summary="DB Portal unified search (cross-db count / db-specific hits / advanced DSL)",
    tags=["db-portal"],
)


# === GET /db-portal/parse — DSL → GUI 逆パーサ ===


async def _parse_db_portal(
    adv: str = Query(
        ...,
        description=(
            "Advanced Search DSL to parse into AST.  Same grammar as "
            "``GET /db-portal/search?adv=...``.  Returned JSON tree "
            "follows SSOT search-backends.md §L363-381 and is intended for "
            "GUI state restoration from shared URLs."
        ),
    ),
    db: DbPortalDb | None = Query(
        default=None,
        description=(
            "Validator mode target.  Omit for cross-db mode (Tier 1 only); "
            "specify a DB for single-db mode (Tier 1 + Tier 2/3 allowlist)."
        ),
    ),
    config: AppConfig = Depends(_get_config_dep),
) -> DbPortalParseResponse:
    """Parse ``adv`` DSL and return the SSOT query-tree JSON for GUI restoration."""
    try:
        ast = parse(adv, max_length=config.dsl_max_length)
        validate(
            ast,
            mode="cross" if db is None else "single",
            db=db,
            max_depth=config.dsl_max_depth,
        )
    except DslError as exc:
        raise DbPortalHTTPException(
            status_code=400,
            type_uri=DbPortalErrorType[exc.type.name],
            detail=exc.detail,
        ) from exc
    return DbPortalParseResponse.model_validate({"ast": ast_to_json(ast)})


router.add_api_route(
    "/db-portal/parse",
    _parse_db_portal,
    methods=["GET"],
    response_model=DbPortalParseResponse,
    responses={
        400: {
            "description": "Bad Request (DSL parse/validate error).",
            "model": ProblemDetails,
        },
    },
    summary="Parse Advanced Search DSL into the SSOT query-tree JSON for GUI state restoration.",
    tags=["db-portal"],
)
