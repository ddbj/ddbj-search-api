"""Search endpoints for db-portal frontend: split into two operations.

* ``GET /db-portal/cross-search`` — cross-database fan-out across 8 DBs
  (ES 6 + Solr 2) returning per-DB count and (when ``topHits>=1``) up
  to ``topHits`` lightweight hits.  Accepts only ``q`` / ``topHits``;
  any other query parameter returns 400 ``unexpected-parameter``.
  Tier 1/2 fields only.
* ``GET /db-portal/search`` — db-specific hits envelope.  ``db`` is
  required; omitting it returns 400 ``missing-db``.  Accepts ``q``
  plus pagination (``page`` / ``perPage`` / ``cursor``) and ``sort``.
  ES for 6 DBs, Solr for ``trad`` / ``taxonomy`` (offset-only;
  ``cursor`` returns 400 ``cursor-not-supported``).

Both endpoints share the same pipeline: parse ``q`` → validate → compile
to ES/Solr.  Query errors (unknown-field, invalid-date-format, etc.)
surface as 400 + RFC 7807 + dedicated type URI.  Cross-search fan-out
uses per-backend ``asyncio.wait_for`` bounds and an overall
``asyncio.wait`` deadline.

Handlers never assemble AST nodes themselves; ``_parse_and_validate_query``
is the sole entry point so cross-search and search stay in lock-step.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.exceptions import HTTPException as StarletteHTTPException

from ddbj_search_api.config import AppConfig, get_config
from ddbj_search_api.cursor import compute_next_cursor, decode_cursor
from ddbj_search_api.es import get_es_client
from ddbj_search_api.es.client import es_open_pit, es_search, es_search_with_pit
from ddbj_search_api.es.query import (
    StatusMode,
    build_search_query,
    build_sort_with_tiebreaker,
    inject_status_filter,
    pagination_to_from_size,
)
from ddbj_search_api.schemas.common import ProblemDetails
from ddbj_search_api.schemas.db_portal import (
    DbPortalCount,
    DbPortalCountError,
    DbPortalCrossSearchQuery,
    DbPortalCrossSearchResponse,
    DbPortalDb,
    DbPortalErrorType,
    DbPortalHit,
    DbPortalHitsResponse,
    DbPortalLightweightHit,
    DbPortalParseResponse,
    DbPortalSearchQuery,
    _DbPortalHitAdapter,
    _DbPortalLightweightHitAdapter,
)
from ddbj_search_api.search.dsl import (
    DslError,
    ast_to_json,
    compile_to_es,
    compile_to_solr,
    parse,
    validate,
)
from ddbj_search_api.search.dsl.accession_exact_match import (
    detect_accession_exact_match_in_ast,
)
from ddbj_search_api.search.dsl.ast import Node as DslNode
from ddbj_search_api.search.dsl.inspect import ast_has_field_clause
from ddbj_search_api.solr import get_solr_client
from ddbj_search_api.solr.client import arsa_search, txsearch_search
from ddbj_search_api.solr.mappers import (
    arsa_docs_to_lightweight_hits,
    arsa_response_to_envelope,
    txsearch_docs_to_lightweight_hits,
    txsearch_response_to_envelope,
)
from ddbj_search_api.solr.query import (
    build_arsa_request_params,
    build_txsearch_request_params,
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

# Cross-search lightweight hit `_source` allowlist (12 fields shared with the
# DbPortalHitBase contract).  Db-specific extras (`projectType`, `division`,
# `rank` etc.) are intentionally excluded; the cross-search UI only renders the
# common base fields per DB.
_CROSS_SEARCH_LIGHTWEIGHT_FIELDS: tuple[str, ...] = (
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
)


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


def _validate_cursor_exclusivity(query: DbPortalSearchQuery) -> None:
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


_CROSS_SEARCH_ALLOWED_PARAMS: frozenset[str] = frozenset({"q", "topHits"})


def _reject_unexpected_cross_params(request: Request) -> None:
    """Raise 400 ``unexpected-parameter`` for forbidden params on /db-portal/cross-search.

    cross-search is a fixed 8-DB fan-out without pagination; ``db`` /
    ``cursor`` / ``page`` / ``perPage`` / ``sort`` have no meaning here.
    Silently ignoring would hide user typos, so the first unexpected key
    is reported by name.
    """
    extra = [k for k in request.query_params if k not in _CROSS_SEARCH_ALLOWED_PARAMS]
    if not extra:
        return
    name = extra[0]
    raise DbPortalHTTPException(
        status_code=400,
        type_uri=DbPortalErrorType.unexpected_parameter,
        detail=(
            f"Parameter '{name}' is not allowed on /db-portal/cross-search. "
            "Use /db-portal/search?db=<id> for db-specific paginated hits."
        ),
    )


def _parse_and_validate_query(
    q: str,
    db: DbPortalDb | None,
    config: AppConfig,
) -> DslNode:
    """Parse and validate a search query string.

    ``mode`` is ``"cross"`` when ``db`` is None and ``"single"`` otherwise.
    Query errors are translated to ``DbPortalHTTPException`` (400 + dedicated
    type URI) so the caller can return RFC 7807 problem details.
    """
    try:
        ast = parse(q, max_length=config.dsl_max_length)
        validate(
            ast,
            mode="cross" if db is None else "single",
            max_depth=config.dsl_max_depth,
            max_nodes=config.dsl_max_nodes,
        )
    except DslError as exc:
        raise DbPortalHTTPException(
            status_code=400,
            type_uri=DbPortalErrorType[exc.type.name],
            detail=exc.detail,
        ) from exc
    return ast


def _resolve_status_mode(ast: DslNode | None) -> StatusMode:
    """AST から ``status_mode`` を導出する.

    accession 完全一致 ([§ データ可視性](docs/db-portal-api-spec.md)) を満たす場合のみ
    ``include_suppressed``、それ以外は ``public_only``。``ast=None`` (``q``
    未指定) は accession 一致しないので ``public_only``。
    """
    if ast is None:
        return "public_only"
    return "include_suppressed" if detect_accession_exact_match_in_ast(ast) is not None else "public_only"


def _build_es_query_for_ast(ast: DslNode | None, status_mode: StatusMode) -> dict[str, Any]:
    """AST から ES query body を生成し、status filter を注入する.

    ``ast=None`` (``q`` 未指定) は ``build_search_query(keywords=None, ...)``
    と同形式の ``{"bool": {"filter": [<status>]}}`` を返す (keyword 無し + filter のみ)。
    ``ast`` が空の ``match_all`` ラップで誤って ``must`` に ``match_all`` を残さないよう、
    build_search_query 経由の形式に合わせる。
    """
    if ast is None:
        return build_search_query(keywords=None, keyword_operator="AND", status_mode=status_mode)
    return inject_status_filter(compile_to_es(ast), status_mode)


def _build_solr_q_for_ast(ast: DslNode | None, *, dialect: str) -> str:
    """AST から Solr edismax ``q`` 文字列を生成する.

    ``ast=None`` は ``*:*`` (all-docs)。dialect は ``"arsa"`` / ``"txsearch"``.
    """
    if ast is None:
        return "*:*"
    return compile_to_solr(ast, dialect=dialect)  # type: ignore[arg-type]


# === Cross-database fan-out (count + optional top hits) ===


def _empty_hits_or_none(top_hits: int) -> list[DbPortalLightweightHit] | None:
    """`DbPortalCount.hits` の空値: `top_hits=0` で `None`、`top_hits>=1` で `[]`。"""
    return [] if top_hits > 0 else None


# converter 側の sameAs alias 投入 (同一 _source を別 _id で複数件 ES に格納する
# 設計) により ES raw hits に同 (identifier, type) の重複が混入する。multiplier
# は経験則: alias 1 entity あたりの secondary 数は小さい (prefix 一致 + 同 type
# 条件で限定) ため 3 倍取れば top_hits 件 unique を再構成できる前提。
_CROSS_SEARCH_DEDUP_OVERSHOOT = 3


def _dedup_lightweight_hits(
    hits: list[DbPortalLightweightHit],
    limit: int,
) -> list[DbPortalLightweightHit]:
    """Drop duplicates by ``(identifier, type)`` (insertion-order, first-wins) and truncate to *limit*."""
    if limit <= 0:
        return []
    seen: set[tuple[str, str]] = set()
    result: list[DbPortalLightweightHit] = []
    for hit in hits:
        key = (hit.identifier, hit.type)
        if key in seen:
            continue
        seen.add(key)
        result.append(hit)
        if len(result) >= limit:
            break
    return result


async def _count_one_db_es(
    client: httpx.AsyncClient,
    config: AppConfig,
    db: DbPortalDb,
    query_body: dict[str, Any],
    top_hits: int,
) -> DbPortalCount:
    fetch_size = top_hits * _CROSS_SEARCH_DEDUP_OVERSHOOT if top_hits > 0 else 0
    body: dict[str, Any] = {"query": query_body, "size": fetch_size}
    if top_hits > 0:
        body["_source"] = list(_CROSS_SEARCH_LIGHTWEIGHT_FIELDS)
        body["sort"] = build_sort_with_tiebreaker(None)
        # ES truncates total at 10000 when size>=1 unless ``track_total_hits`` is
        # set; force exact count so per-DB ``count`` stays accurate alongside hits.
        body["track_total_hits"] = True
    try:
        resp = await asyncio.wait_for(
            es_search(client, _db_to_index(db), body),
            timeout=config.es_search_timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "db-portal cross-search timed out for db=%s (es_search_timeout=%.2fs)",
            db.value,
            config.es_search_timeout,
        )
        return DbPortalCount(
            db=db,
            count=None,
            error=DbPortalCountError.timeout,
            hits=_empty_hits_or_none(top_hits),
        )
    except Exception as exc:
        error = _map_httpx_error(exc)
        logger.warning(
            "db-portal cross-search failed for db=%s: %s (error=%s)",
            db.value,
            type(exc).__name__,
            error.value,
        )
        return DbPortalCount(
            db=db,
            count=None,
            error=error,
            hits=_empty_hits_or_none(top_hits),
        )
    try:
        count = int(resp["hits"]["total"]["value"])
    except (KeyError, TypeError, ValueError):
        logger.warning(
            "db-portal cross-search: unexpected ES response shape for db=%s",
            db.value,
        )
        return DbPortalCount(
            db=db,
            count=None,
            error=DbPortalCountError.unknown,
            hits=_empty_hits_or_none(top_hits),
        )
    hits: list[DbPortalLightweightHit] | None = None
    if top_hits > 0:
        raw_hits = resp.get("hits", {}).get("hits", [])
        parsed = [_DbPortalLightweightHitAdapter.validate_python(h.get("_source", {})) for h in raw_hits]
        hits = _dedup_lightweight_hits(parsed, top_hits)
    return DbPortalCount(db=db, count=count, error=None, hits=hits)


# === Cross-database fan-out (AST 経由 dispatch) ===
#
# ``q`` を ``_parse_and_validate_query`` で AST に変換し,
# ``_cross_search_dispatch`` が ES 6 DB + Solr 2 DB に並列発行する。詳細は
# docs/db-portal-api-spec.md § 内部モデル参照。


async def _count_arsa_unified(
    client: httpx.AsyncClient,
    config: AppConfig,
    q_string: str,
    top_hits: int,
    *,
    with_uf: bool,
) -> DbPortalCount:
    """ARSA cross-search 用 ``count + top hits`` クエリ発行."""
    if not config.solr_arsa_base_url:
        return DbPortalCount(
            db=DbPortalDb.trad,
            count=None,
            error=DbPortalCountError.unknown,
            hits=_empty_hits_or_none(top_hits),
        )
    params = build_arsa_request_params(
        q=q_string,
        page=1,
        per_page=top_hits,
        sort=None,
        shards=config.solr_arsa_shards,
        with_uf=with_uf,
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
        return DbPortalCount(
            db=DbPortalDb.trad,
            count=None,
            error=DbPortalCountError.timeout,
            hits=_empty_hits_or_none(top_hits),
        )
    except Exception as exc:
        error = _map_httpx_error(exc)
        logger.warning(
            "db-portal cross-search failed for db=trad (ARSA): %s (error=%s)",
            type(exc).__name__,
            error.value,
        )
        return DbPortalCount(
            db=DbPortalDb.trad,
            count=None,
            error=error,
            hits=_empty_hits_or_none(top_hits),
        )
    try:
        count = int(resp["response"]["numFound"])
    except (KeyError, TypeError, ValueError):
        logger.warning("db-portal cross-search: unexpected ARSA response shape")
        return DbPortalCount(
            db=DbPortalDb.trad,
            count=None,
            error=DbPortalCountError.unknown,
            hits=_empty_hits_or_none(top_hits),
        )
    hits: list[DbPortalLightweightHit] | None = None
    if top_hits > 0:
        docs = (resp.get("response") or {}).get("docs") or []
        hits = arsa_docs_to_lightweight_hits(docs)
    return DbPortalCount(db=DbPortalDb.trad, count=count, error=None, hits=hits)


async def _count_txsearch_unified(
    client: httpx.AsyncClient,
    config: AppConfig,
    q_string: str,
    top_hits: int,
    *,
    with_uf: bool,
) -> DbPortalCount:
    """TXSearch cross-search のカウントとライト hits を取得する."""
    if not config.solr_txsearch_url:
        return DbPortalCount(
            db=DbPortalDb.taxonomy,
            count=None,
            error=DbPortalCountError.unknown,
            hits=_empty_hits_or_none(top_hits),
        )
    params = build_txsearch_request_params(
        q=q_string,
        page=1,
        per_page=top_hits,
        sort=None,
        with_uf=with_uf,
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
        return DbPortalCount(
            db=DbPortalDb.taxonomy,
            count=None,
            error=DbPortalCountError.timeout,
            hits=_empty_hits_or_none(top_hits),
        )
    except Exception as exc:
        error = _map_httpx_error(exc)
        logger.warning(
            "db-portal cross-search failed for db=taxonomy (TXSearch): %s (error=%s)",
            type(exc).__name__,
            error.value,
        )
        return DbPortalCount(
            db=DbPortalDb.taxonomy,
            count=None,
            error=error,
            hits=_empty_hits_or_none(top_hits),
        )
    try:
        count = int(resp["response"]["numFound"])
    except (KeyError, TypeError, ValueError):
        logger.warning("db-portal cross-search: unexpected TXSearch response shape")
        return DbPortalCount(
            db=DbPortalDb.taxonomy,
            count=None,
            error=DbPortalCountError.unknown,
            hits=_empty_hits_or_none(top_hits),
        )
    hits: list[DbPortalLightweightHit] | None = None
    if top_hits > 0:
        docs = (resp.get("response") or {}).get("docs") or []
        hits = txsearch_docs_to_lightweight_hits(docs)
    return DbPortalCount(db=DbPortalDb.taxonomy, count=count, error=None, hits=hits)


async def _count_one_db_unified(
    es_client: httpx.AsyncClient,
    solr_client: httpx.AsyncClient,
    config: AppConfig,
    db: DbPortalDb,
    es_query_body: dict[str, Any],
    arsa_q: str,
    txsearch_q: str,
    top_hits: int,
    *,
    with_uf: bool,
) -> DbPortalCount:
    if db == DbPortalDb.trad:
        return await _count_arsa_unified(solr_client, config, arsa_q, top_hits, with_uf=with_uf)
    if db == DbPortalDb.taxonomy:
        return await _count_txsearch_unified(solr_client, config, txsearch_q, top_hits, with_uf=with_uf)
    return await _count_one_db_es(es_client, config, db, es_query_body, top_hits)


async def _cross_search_dispatch(
    es_client: httpx.AsyncClient,
    solr_client: httpx.AsyncClient,
    config: AppConfig,
    ast: DslNode | None,
    top_hits: int,
) -> DbPortalCrossSearchResponse:
    """cross-search 単一 dispatch (AST → 8 DB fan-out).

    ``ast=None`` (``q`` 未指定) は ``match_all`` (ES) / ``*:*`` (Solr) でカウントだけ取る。
    """
    status_mode = _resolve_status_mode(ast)
    es_query_body = _build_es_query_for_ast(ast, status_mode)
    arsa_q = _build_solr_q_for_ast(ast, dialect="arsa")
    txsearch_q = _build_solr_q_for_ast(ast, dialect="txsearch")
    with_uf = ast is not None and ast_has_field_clause(ast)
    task_map: dict[asyncio.Task[DbPortalCount], DbPortalDb] = {}
    for db in _DB_ORDER:
        task = asyncio.create_task(
            _count_one_db_unified(
                es_client,
                solr_client,
                config,
                db,
                es_query_body,
                arsa_q,
                txsearch_q,
                top_hits,
                with_uf=with_uf,
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
            "db-portal cross-search hit total timeout for db=%s (cancelled, total_timeout=%.2fs)",
            db.value,
            config.cross_search_total_timeout,
        )
        results[db] = DbPortalCount(
            db=db,
            count=None,
            error=DbPortalCountError.timeout,
            hits=_empty_hits_or_none(top_hits),
        )
    databases = [results[db] for db in _DB_ORDER]
    if all(item.error is not None for item in databases):
        raise HTTPException(
            status_code=502,
            detail="All databases failed to respond.",
        )
    return DbPortalCrossSearchResponse(databases=databases)


# === DB-specific hits (AST 経由 dispatch) ===
#
# ``q`` を ``_parse_and_validate_query`` で AST に変換し,
# ``_db_specific_search_dispatch`` が ES (offset / cursor は別 path) と
# Solr 2 DB に振り分ける。cursor 経路は ``_db_specific_search_cursor`` 専用で
# AST dispatch を通らない (cursor token に焼き込んだ ES query を decode して使う)。


async def _search_arsa_unified(
    client: httpx.AsyncClient,
    config: AppConfig,
    query: DbPortalSearchQuery,
    q_string: str,
    *,
    with_uf: bool,
) -> DbPortalHitsResponse:
    """ARSA db-specific search の hits 検索発行."""
    if not config.solr_arsa_base_url:
        raise HTTPException(
            status_code=502,
            detail="ARSA backend is not configured.",
        )
    params = build_arsa_request_params(
        q=q_string,
        page=query.page,
        per_page=query.per_page,
        sort=query.sort,
        shards=config.solr_arsa_shards,
        with_uf=with_uf,
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


async def _search_txsearch_unified(
    client: httpx.AsyncClient,
    config: AppConfig,
    query: DbPortalSearchQuery,
    q_string: str,
    *,
    with_uf: bool,
) -> DbPortalHitsResponse:
    """TXSearch db-specific search の hits 検索発行."""
    if not config.solr_txsearch_url:
        raise HTTPException(
            status_code=502,
            detail="TXSearch backend is not configured.",
        )
    params = build_txsearch_request_params(
        q=q_string,
        page=query.page,
        per_page=query.per_page,
        sort=query.sort,
        with_uf=with_uf,
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


async def _db_specific_search_es_unified(
    client: httpx.AsyncClient,
    query: DbPortalSearchQuery,
    es_query_body: dict[str, Any],
) -> DbPortalHitsResponse:
    """ES hits envelope (offset mode).

    cursor 経路は ``_db_specific_search_cursor`` (cursor token に焼き込んだ
    query を decode して使う) に分離されているため、こちらは offset のみ扱う。
    """
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


async def _db_specific_search_dispatch(
    es_client: httpx.AsyncClient,
    solr_client: httpx.AsyncClient,
    config: AppConfig,
    query: DbPortalSearchQuery,
    ast: DslNode | None,
) -> DbPortalHitsResponse:
    """db-specific 単一 dispatch (AST → 単一 backend).

    ``ast`` が ``FieldClause`` を含むかで Solr ``uf`` 適用を決め (handler が
    AST 全体を見る)、ES 側は inject_status_filter で accession 解禁を決める。
    cursor 経路は ``_db_specific_search_cursor`` (cursor token に焼き込んだ
    ES query を decode して使う) に分離されているため、本 dispatch には
    含めない。
    """
    assert query.db is not None
    with_uf = ast is not None and ast_has_field_clause(ast)
    if query.db in _SOLR_DBS:
        _validate_deep_paging(query.page, query.per_page)
        if query.db == DbPortalDb.trad:
            return await _search_arsa_unified(
                solr_client,
                config,
                query,
                _build_solr_q_for_ast(ast, dialect="arsa"),
                with_uf=with_uf,
            )
        return await _search_txsearch_unified(
            solr_client,
            config,
            query,
            _build_solr_q_for_ast(ast, dialect="txsearch"),
            with_uf=with_uf,
        )
    status_mode = _resolve_status_mode(ast)
    es_query_body = _build_es_query_for_ast(ast, status_mode)
    return await _db_specific_search_es_unified(es_client, query, es_query_body)


async def _db_specific_search_cursor(
    client: httpx.AsyncClient,
    query: DbPortalSearchQuery,
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


async def _cross_search_handler(
    request: Request,
    query: DbPortalCrossSearchQuery = Depends(),
    es_client: httpx.AsyncClient = Depends(get_es_client),
    solr_client: httpx.AsyncClient = Depends(get_solr_client),
    config: AppConfig = Depends(_get_config_dep),
) -> DbPortalCrossSearchResponse:
    """``GET /db-portal/cross-search``: cross-database count + top hits search.

    ``q`` を Lark でパース → validator → ES/Solr compiler の単一 pipeline を通して
    ``_cross_search_dispatch`` に渡す。``q`` 省略時は ``ast=None`` で全件 match_all
    fan-out を行う。
    """
    _reject_unexpected_cross_params(request)
    ast = _parse_and_validate_query(query.q, db=None, config=config) if query.q else None
    return await _cross_search_dispatch(es_client, solr_client, config, ast, query.top_hits)


async def _db_search_handler(
    query: DbPortalSearchQuery = Depends(),
    es_client: httpx.AsyncClient = Depends(get_es_client),
    solr_client: httpx.AsyncClient = Depends(get_solr_client),
    config: AppConfig = Depends(_get_config_dep),
) -> DbPortalHitsResponse:
    """``GET /db-portal/search``: db-specific hits search.

    cursor 経路 (``_db_specific_search_cursor``) は cursor token に焼き込んだ
    ES query を decode して継続する設計のため、本 dispatch には流さない。
    cursor + q は ``_validate_cursor_exclusivity`` (cursor 経路前段) で弾く。
    """
    if query.db is None:
        raise DbPortalHTTPException(
            status_code=400,
            type_uri=DbPortalErrorType.missing_db,
            detail=(
                "Parameter 'db' is required on /db-portal/search. "
                "Allowed: trad, sra, bioproject, biosample, jga, gea, metabobank, taxonomy. "
                "For cross-database count, use /db-portal/cross-search."
            ),
        )
    if query.cursor is not None:
        if query.db in _SOLR_DBS:
            raise DbPortalHTTPException(
                status_code=400,
                type_uri=DbPortalErrorType.cursor_not_supported,
                detail=(
                    f"Cursor-based pagination is not supported for db='{query.db.value}'. "
                    "Use 'page' + 'perPage' (offset-only) instead."
                ),
            )
        _validate_cursor_exclusivity(query)
        return await _db_specific_search_cursor(es_client, query)
    ast = _parse_and_validate_query(query.q, db=query.db, config=config) if query.q else None
    return await _db_specific_search_dispatch(es_client, solr_client, config, query, ast)


router.add_api_route(
    "/db-portal/cross-search",
    _cross_search_handler,
    methods=["GET"],
    response_model=DbPortalCrossSearchResponse,
    responses={
        400: {
            "description": ("Bad Request (unexpected parameter, query parse/validate error)."),
            "model": ProblemDetails,
        },
        422: {
            "description": "Unprocessable Entity (parameter validation error).",
            "model": ProblemDetails,
        },
        502: {
            "description": "Bad Gateway (all databases failed)",
            "model": ProblemDetails,
        },
    },
    summary="DB Portal cross-database fan-out (count + top hits)",
    description=(
        "Fan-out search across 8 databases (6 Elasticsearch + 2 Solr). "
        "Per-backend timeouts and a global timeout enforce partial-failure tolerance: "
        "individual DB errors surface in `databases[i].error` while the response stays 200. "
        "All-DB failure returns 502. Pagination concepts (db / cursor / page / perPage / sort) "
        "are rejected with 400 `unexpected-parameter`; use /db-portal/search for paginated single-DB queries."
    ),
    operation_id="crossSearchDbPortal",
    tags=["db-portal"],
)


router.add_api_route(
    "/db-portal/search",
    _db_search_handler,
    methods=["GET"],
    response_model=DbPortalHitsResponse,
    responses={
        400: {
            "description": (
                "Bad Request (missing-db, cursor exclusivity, "
                "query parse/validate error, deep paging limit)."
            ),
            "model": ProblemDetails,
        },
        422: {
            "description": "Unprocessable Entity (parameter validation error, e.g. invalid db / sort / perPage).",
            "model": ProblemDetails,
        },
        502: {
            "description": "Bad Gateway (Solr upstream error)",
            "model": ProblemDetails,
        },
    },
    summary="DB Portal db-specific hits search",
    description=(
        "Single-database hits search with pagination. `db` is required (400 `missing-db` if omitted). "
        "Elasticsearch-backed DBs support cursor-based pagination; Solr-backed DBs (db=trad / db=taxonomy) "
        "and queries containing field clauses are offset-only (400 `cursor-not-supported` if cursor is supplied). "
        "Cross-database counts go through /db-portal/cross-search instead."
    ),
    operation_id="searchDbPortal",
    tags=["db-portal"],
)


# === GET /db-portal/parse — query → GUI 逆パーサ ===


async def _parse_db_portal(
    q: str = Query(
        ...,
        examples=["cancer AND organism:9606"],
        description=(
            "Search query to parse into AST.  Same grammar as "
            "``GET /db-portal/cross-search?q=...`` / "
            "``GET /db-portal/search?q=...&db=<id>``.  Returned JSON tree is "
            "intended for GUI state restoration from shared URLs."
        ),
    ),
    db: DbPortalDb | None = Query(
        default=None,
        examples=["bioproject"],
        description=(
            "Validator mode target.  Omit for cross-db mode (Tier 1/2 only); "
            "specify a DB for single-db mode (Tier 1/2/3 allowlist)."
        ),
    ),
    config: AppConfig = Depends(_get_config_dep),
) -> DbPortalParseResponse:
    """Parse the query and return the SSOT query-tree JSON for GUI restoration."""
    ast = _parse_and_validate_query(q, db=db, config=config)
    return DbPortalParseResponse.model_validate({"ast": ast_to_json(ast)})


router.add_api_route(
    "/db-portal/parse",
    _parse_db_portal,
    methods=["GET"],
    response_model=DbPortalParseResponse,
    responses={
        400: {
            "description": "Bad Request (query parse/validate error).",
            "model": ProblemDetails,
        },
        422: {
            "description": "Unprocessable Entity (missing q, invalid db value).",
            "model": ProblemDetails,
        },
    },
    summary="Parse a search query into the SSOT query-tree JSON for GUI state restoration.",
    operation_id="parseDbPortal",
    tags=["db-portal"],
)
