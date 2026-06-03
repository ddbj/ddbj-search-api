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
import re
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.exceptions import HTTPException as StarletteHTTPException

from ddbj_search_api.config import AppConfig, get_config
from ddbj_search_api.cursor import compute_next_cursor, decode_cursor
from ddbj_search_api.es import get_es_client
from ddbj_search_api.es.client import es_open_pit, es_search, es_search_with_pit
from ddbj_search_api.es.query import (
    DEFAULT_FACET_SIZE,
    StatusMode,
    build_facet_aggs,
    build_facet_base_query,
    build_search_query,
    build_self_excluding_facet_aggs,
    build_sort_with_tiebreaker,
    db_portal_es_facet_allowlist,
    inject_status_filter,
    pagination_to_from_size,
    resolve_facets_size,
)
from ddbj_search_api.schemas.common import ProblemDetails
from ddbj_search_api.schemas.db_portal import (
    DbPortalCount,
    DbPortalCountError,
    DbPortalCrossSearchQuery,
    DbPortalCrossSearchResponse,
    DbPortalDb,
    DbPortalErrorType,
    DbPortalFacets,
    DbPortalHit,
    DbPortalHitsResponse,
    DbPortalLightweightHit,
    DbPortalParseResponse,
    DbPortalSearchQuery,
    DbPortalSerializeRequest,
    DbPortalSerializeResponse,
    _DbPortalHitAdapter,
    _DbPortalLightweightHitAdapter,
)
from ddbj_search_api.search.dsl import (
    DslError,
    ast_to_dsl,
    ast_to_json,
    compile_to_es,
    compile_to_solr,
    json_to_ast,
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
    arsa_facet_field_map,
    build_arsa_request_params,
    build_solr_facet_plan,
    build_txsearch_request_params,
    txsearch_facet_field_map,
)
from ddbj_search_api.utils import parse_db_portal_es_facets, parse_solr_facets

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

# Solr-backed db-portal facet scope: trad / taxonomy each own their facets,
# derived from the solr.query field maps so the scope stays in sync with the
# request/parse code (docs/db-portal-api-spec.md § facet 集計).
_DB_PORTAL_SOLR_FACET_SCOPE: dict[DbPortalDb, frozenset[str]] = {
    DbPortalDb.trad: frozenset(arsa_facet_field_map()),
    DbPortalDb.taxonomy: frozenset(txsearch_facet_field_map()),
}

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
    if query.keyword_operator.value != "OR":
        conflicting.append("keywordOperator")
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


_CROSS_SEARCH_ALLOWED_PARAMS: frozenset[str] = frozenset(
    {"q", "topHits", "keywordOperator", "facets", "facetsSize", "facetSelfExclude"}
)


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


def _db_portal_facet_allowlist(db: DbPortalDb | None) -> frozenset[str]:
    """Facet names accepted for a db-portal scope (cross or one of the 8 DBs).

    ES scopes (cross + the 6 ES DBs) derive from
    :func:`db_portal_es_facet_allowlist`; Solr DBs (trad / taxonomy) use
    their own facet field maps.
    """
    if db in _DB_PORTAL_SOLR_FACET_SCOPE:
        return _DB_PORTAL_SOLR_FACET_SCOPE[db]
    return db_portal_es_facet_allowlist(None if db is None else db.value)


def resolve_db_portal_facets(facets_param: str | None, *, db: DbPortalDb | None) -> list[str] | None:
    """Resolve the wire ``facets`` value into the explicit facet list for a scope.

    Returns ``None`` (parameter omitted) or ``[]`` (empty string) — both
    mean "no aggregation" for db-portal — or the parsed list of facet
    names.  Names have already passed the wire allowlist (422) in
    ``DbPortalSearchQuery`` / ``DbPortalCrossSearchQuery``; here a valid
    name that is out of scope for the target ``db`` raises 400
    ``facet-not-applicable`` (docs/db-portal-api-spec.md § facet 集計).
    """
    if facets_param is None:
        return None
    if facets_param == "":
        return []
    requested = [f.strip() for f in facets_param.split(",")]
    requested = [f for f in requested if f]
    allowed = _db_portal_facet_allowlist(db)
    invalid = [f for f in requested if f not in allowed]
    if invalid:
        scope = "cross-search" if db is None else f"db={db.value}"
        raise DbPortalHTTPException(
            status_code=400,
            type_uri=DbPortalErrorType.facet_not_applicable,
            detail=(
                f"Facet(s) not available for {scope}: {', '.join(invalid)}. Allowed here: {', '.join(sorted(allowed))}."
            ),
        )
    return requested


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
            db=db.value if db is not None else None,
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


def _build_es_query_for_ast(
    ast: DslNode | None,
    status_mode: StatusMode,
    *,
    free_text_operator: Literal["AND", "OR"] = "AND",
) -> dict[str, Any]:
    """AST から ES query body を生成し、status filter を注入する.

    ``ast=None`` (``q`` 未指定) は ``build_search_query(keywords=None, ...)``
    と同形式の ``{"bool": {"filter": [<status>]}}`` を返す (keyword 無し + filter のみ)。
    ``ast`` が空の ``match_all`` ラップで誤って ``must`` に ``match_all`` を残さないよう、
    build_search_query 経由の形式に合わせる。

    ``free_text_operator`` は AST 中の FreeText ノードのトークン連結演算子を制御する
    (``AND`` / ``OR``)。``q`` 未指定パスでは AST 不在のため何も伝播しない。
    """
    if ast is None:
        return build_search_query(keywords=None, keyword_operator="AND", status_mode=status_mode)
    # suppressed 解禁時 (accession 完全一致) は FreeText の前方一致を抑止する
    # (docs/api-spec.md § データ可視性)。
    return inject_status_filter(
        compile_to_es(
            ast,
            free_text_operator=free_text_operator,
            enable_prefix=status_mode != "include_suppressed",
        ),
        status_mode,
    )


def _build_solr_q_for_ast(
    ast: DslNode | None,
    *,
    dialect: str,
    free_text_operator: Literal["AND", "OR"] = "AND",
) -> str:
    """AST から Solr edismax ``q`` 文字列を生成する.

    ``ast=None`` は ``*:*`` (all-docs)。dialect は ``"arsa"`` / ``"txsearch"``。
    ``free_text_operator`` は AST 中の FreeText ノードのトークン連結演算子を制御する。
    """
    if ast is None:
        return "*:*"
    return compile_to_solr(ast, dialect=dialect, free_text_operator=free_text_operator)  # type: ignore[arg-type]


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


async def _cross_facets_agg(
    es_client: httpx.AsyncClient,
    config: AppConfig,
    es_query_body: dict[str, Any],
    requested_facets: list[str],
    facets_size: int,
    *,
    ast: DslNode | None,
    status_mode: StatusMode,
    free_text_operator: Literal["AND", "OR"] = "AND",
    facet_self_exclude: bool = False,
) -> DbPortalFacets | None:
    """Aggregate cross-search facets against the ``entries`` alias (size=0).

    Without ``facet_self_exclude`` this reuses the same compiled ES query
    (status filter included) as the count fan-out for the top-level ``query``,
    so the facet population matches the ES 6-DB union.

    Under ``facet_self_exclude`` the top-level ``query`` is the **base** query
    (every requested facet's own clause removed, :func:`build_facet_base_query`)
    and each facet's terms agg is wrapped in a ``filter`` aggregation that
    re-adds the *other* facets' clauses — ES filter aggs can only narrow the
    top-level query, so the facet selections must live below it, not in it
    (docs/db-portal-api-spec.md § 集計母集団と self-exclusion).  This is a
    size=0 request so no hit population is involved.

    Returns ``None`` on failure / timeout so cross-search stays 200 on the
    count fan-out result (docs/db-portal-api-spec.md § facet 集計).
    """
    if facet_self_exclude:
        query = build_facet_base_query(
            ast,
            status_mode,
            requested_facets=requested_facets,
            free_text_operator=free_text_operator,
        )
        aggs = build_self_excluding_facet_aggs(
            ast=ast,
            status_mode=status_mode,
            is_cross_type=True,
            requested_facets=requested_facets,
            size=facets_size,
            free_text_operator=free_text_operator,
        )
    else:
        query = es_query_body
        aggs = build_facet_aggs(is_cross_type=True, requested_facets=requested_facets, size=facets_size)
    body: dict[str, Any] = {"query": query, "size": 0, "aggs": aggs}
    # The response parse is inside the try so a 200-with-malformed-aggregation
    # (unexpected bucket shape, non-int doc_count, mapping drift) also degrades
    # to ``facets=null`` rather than 500-ing the whole cross-search — matching
    # the count fan-out's own response-shape guards and the docstring's
    # "returns None on failure" promise.
    try:
        resp = await asyncio.wait_for(
            es_search(es_client, "entries", body),
            timeout=config.es_search_timeout,
        )
        return parse_db_portal_es_facets(resp.get("aggregations", {}))
    except asyncio.TimeoutError:
        logger.warning(
            "db-portal cross-search facet aggregation timed out (es_search_timeout=%.2fs)",
            config.es_search_timeout,
        )
        return None
    except Exception as exc:
        logger.warning(
            "db-portal cross-search facet aggregation failed: %s",
            type(exc).__name__,
        )
        return None


async def _cross_search_dispatch(
    es_client: httpx.AsyncClient,
    solr_client: httpx.AsyncClient,
    config: AppConfig,
    ast: DslNode | None,
    top_hits: int,
    *,
    free_text_operator: Literal["AND", "OR"] = "AND",
    requested_facets: list[str] | None = None,
    facets_size: int = DEFAULT_FACET_SIZE,
    facet_self_exclude: bool = False,
) -> DbPortalCrossSearchResponse:
    """cross-search 単一 dispatch (AST → 8 DB fan-out).

    ``ast=None`` (``q`` 未指定) は ``match_all`` (ES) / ``*:*`` (Solr) でカウントだけ取る。
    ``free_text_operator`` は AST 中の FreeText ノードのトークン連結演算子を制御する。

    ``requested_facets`` が非空のとき、count fan-out と並行して entries alias へ
    size=0 の facet 集計を 1 本発行する (organism / accessibility / type のみ)。
    集計が失敗 / timeout しても fan-out 結果で応答を返し ``facets=None`` にする。
    ``facet_self_exclude`` が True のとき各 facet 自身の clause を母集団から外して
    集計する (hit population は不変)。
    """
    status_mode = _resolve_status_mode(ast)
    es_query_body = _build_es_query_for_ast(ast, status_mode, free_text_operator=free_text_operator)
    arsa_q = _build_solr_q_for_ast(ast, dialect="arsa", free_text_operator=free_text_operator)
    txsearch_q = _build_solr_q_for_ast(ast, dialect="txsearch", free_text_operator=free_text_operator)
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
    # The facet aggregation rides the same total-timeout window as the
    # count fan-out; it carries its own per-request timeout and never
    # raises (returns None on failure), so it is awaited alongside the
    # count tasks but kept out of ``task_map`` (it is not a per-DB count).
    facet_task: asyncio.Task[DbPortalFacets | None] | None = None
    wait_set: set[asyncio.Task[Any]] = set(task_map.keys())
    if requested_facets:
        facet_task = asyncio.create_task(
            _cross_facets_agg(
                es_client,
                config,
                es_query_body,
                requested_facets,
                facets_size,
                ast=ast,
                status_mode=status_mode,
                free_text_operator=free_text_operator,
                facet_self_exclude=facet_self_exclude,
            ),
        )
        wait_set.add(facet_task)
    done, _pending = await asyncio.wait(
        wait_set,
        timeout=config.cross_search_total_timeout,
        return_when=asyncio.ALL_COMPLETED,
    )
    # Resolve per-DB counts via ``task_map`` membership (rather than iterating
    # ``done``/``pending`` and identity-checking the facet task) so the facet
    # task stays cleanly separated from the per-DB count tasks.
    results: dict[DbPortalDb, DbPortalCount] = {}
    for task, db in task_map.items():
        if task in done:
            results[db] = task.result()
            continue
        task.cancel()
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
    # Resolve (and, if still pending at the total timeout, cancel) the facet
    # task BEFORE the all-failed 502 check so a pending facet request is never
    # orphaned when every count task failed.
    facets: DbPortalFacets | None = None
    if facet_task is not None:
        if facet_task in done and not facet_task.cancelled():
            facets = facet_task.result()
        else:
            facet_task.cancel()
            logger.warning(
                "db-portal cross-search facet aggregation cancelled at total timeout (%.2fs)",
                config.cross_search_total_timeout,
            )
    databases = [results[db] for db in _DB_ORDER]
    if all(item.error is not None for item in databases):
        raise HTTPException(
            status_code=502,
            detail="All databases failed to respond.",
        )
    return DbPortalCrossSearchResponse(databases=databases, facets=facets)


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
    ast: DslNode | None,
    *,
    with_uf: bool,
    requested_facets: list[str] | None,
    facets_size: int,
    facet_self_exclude: bool = False,
    free_text_operator: Literal["AND", "OR"] = "AND",
) -> DbPortalHitsResponse:
    """ARSA db-specific search の hits 検索発行.

    ``requested_facets`` が非空のとき、hits 検索と同一リクエストに terms
    faceting を相乗りさせ、``facet_counts`` を ``DbPortalFacets`` にパースして
    envelope に詰める。既定では母集団 = hits と同一。``facet_self_exclude`` が True の
    とき、:func:`build_solr_facet_plan` がトップレベル AND 直下の facet 自身の clause を
    ``q`` から ``{!tag}`` 付き ``fq`` に分離し、その facet の ``facet.field`` を
    ``{!ex}`` で外す (hits 母集団 = ``q`` ∧ ``fq`` は不変)。
    """
    if not config.solr_arsa_base_url:
        raise HTTPException(
            status_code=502,
            detail="ARSA backend is not configured.",
        )
    plan = build_solr_facet_plan(
        ast,
        requested_facets,
        dialect="arsa",
        free_text_operator=free_text_operator,
        self_exclude=facet_self_exclude,
    )
    params = build_arsa_request_params(
        q=plan.q,
        page=query.page,
        per_page=query.per_page,
        sort=query.sort,
        shards=config.solr_arsa_shards,
        with_uf=with_uf,
        facet_fields=plan.facet_fields,
        facet_limit=facets_size,
        fq=plan.fq,
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
    envelope = arsa_response_to_envelope(
        resp,
        page=query.page,
        per_page=query.per_page,
        sort=query.sort,
    )
    if plan.name_to_field:
        envelope.facets = parse_solr_facets(resp.get("facet_counts", {}), plan.name_to_field)
    return envelope


async def _search_txsearch_unified(
    client: httpx.AsyncClient,
    config: AppConfig,
    query: DbPortalSearchQuery,
    ast: DslNode | None,
    *,
    with_uf: bool,
    requested_facets: list[str] | None,
    facets_size: int,
    facet_self_exclude: bool = False,
    free_text_operator: Literal["AND", "OR"] = "AND",
) -> DbPortalHitsResponse:
    """TXSearch db-specific search の hits 検索発行.

    ``requested_facets`` 指定時の facet 相乗り (self-exclusion 含む) は
    :func:`_search_arsa_unified` と同じ (TXSearch は rank / kingdom のみ)。
    """
    if not config.solr_txsearch_url:
        raise HTTPException(
            status_code=502,
            detail="TXSearch backend is not configured.",
        )
    plan = build_solr_facet_plan(
        ast,
        requested_facets,
        dialect="txsearch",
        free_text_operator=free_text_operator,
        self_exclude=facet_self_exclude,
    )
    params = build_txsearch_request_params(
        q=plan.q,
        page=query.page,
        per_page=query.per_page,
        sort=query.sort,
        with_uf=with_uf,
        facet_fields=plan.facet_fields,
        facet_limit=facets_size,
        fq=plan.fq,
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
    envelope = txsearch_response_to_envelope(
        resp,
        page=query.page,
        per_page=query.per_page,
        sort=query.sort,
    )
    if plan.name_to_field:
        envelope.facets = parse_solr_facets(resp.get("facet_counts", {}), plan.name_to_field)
    return envelope


async def _db_specific_search_es_unified(
    client: httpx.AsyncClient,
    query: DbPortalSearchQuery,
    es_query_body: dict[str, Any],
    *,
    ast: DslNode | None,
    status_mode: StatusMode,
    free_text_operator: Literal["AND", "OR"] = "AND",
    requested_facets: list[str] | None,
    facets_size: int,
    facet_self_exclude: bool = False,
) -> DbPortalHitsResponse:
    """ES hits envelope (offset mode).

    cursor 経路は ``_db_specific_search_cursor`` (cursor token に焼き込んだ
    query を decode して使う) に分離されているため、こちらは offset のみ扱う。

    ``requested_facets`` が非空のとき、hits 検索と同一 body に aggs を相乗りさせ
    ``DbPortalFacets`` にパースして詰める。既定では top-level ``query`` = ``es_query_body``
    (= hits と同一 query) で集計する。

    ``facet_self_exclude`` が True のときは top-level ``query`` を base
    (全 requested facet を除外した query, :func:`build_facet_base_query`) に差し替え、
    ``post_filter`` で hits だけ ``q`` 全フィルタに絞り直す。ES の ``filter`` aggregation は
    top-level query を超えて母集団を広げられないため、facet 選択を top-level query から
    抜き、各 facet の filter agg で他 facet 句を足し戻すことで「``q`` から自 facet の句だけを
    外した母集団」を実現する。``post_filter`` は aggregation に影響しないので、hits / total /
    cursor は ``q`` 全フィルタ適用のまま (docs/db-portal-api-spec.md § 集計母集団と
    self-exclusion)。
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
    if requested_facets:
        if facet_self_exclude:
            body["query"] = build_facet_base_query(
                ast,
                status_mode,
                requested_facets=requested_facets,
                free_text_operator=free_text_operator,
            )
            if ast is not None:
                # Restore the hit population to the full ``q`` for hits / total
                # only; ``post_filter`` does not touch the aggregations, which
                # keep seeing the base query.  Gate the FreeText prefix the same
                # way as the base query so suppressed-unlock (accession exact)
                # stays prefix-free on both lanes (docs § データ可視性).
                body["post_filter"] = compile_to_es(
                    ast,
                    free_text_operator=free_text_operator,
                    enable_prefix=status_mode != "include_suppressed",
                )
            body["aggs"] = build_self_excluding_facet_aggs(
                ast=ast,
                status_mode=status_mode,
                requested_facets=requested_facets,
                size=facets_size,
                free_text_operator=free_text_operator,
            )
        else:
            body["aggs"] = build_facet_aggs(requested_facets=requested_facets, size=facets_size)
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
    facets = parse_db_portal_es_facets(es_resp.get("aggregations", {})) if requested_facets else None
    return DbPortalHitsResponse(  # type: ignore[call-arg]
        total=total,
        hits=hits,
        hard_limit_reached=(total >= _DEEP_PAGING_LIMIT),
        page=query.page,
        per_page=query.per_page,
        next_cursor=next_cursor,
        has_next=has_next,
        facets=facets,
    )


async def _db_specific_search_dispatch(
    es_client: httpx.AsyncClient,
    solr_client: httpx.AsyncClient,
    config: AppConfig,
    query: DbPortalSearchQuery,
    ast: DslNode | None,
    *,
    requested_facets: list[str] | None,
    facets_size: int,
    facet_self_exclude: bool = False,
) -> DbPortalHitsResponse:
    """db-specific 単一 dispatch (AST → 単一 backend).

    ``ast`` が ``FieldClause`` を含むかで Solr ``uf`` 適用を決め (handler が
    AST 全体を見る)、ES 側は inject_status_filter で accession 解禁を決める。
    cursor 経路は ``_db_specific_search_cursor`` (cursor token に焼き込んだ
    ES query を decode して使う) に分離されているため、本 dispatch には
    含めない。

    ``requested_facets`` / ``facets_size`` は backend 別の facet 相乗りに
    そのまま渡す (scope 検証は handler の ``resolve_db_portal_facets`` 済み)。
    """
    assert query.db is not None
    with_uf = ast is not None and ast_has_field_clause(ast)
    free_text_op: Literal["AND", "OR"] = query.keyword_operator.value
    if query.db in _SOLR_DBS:
        _validate_deep_paging(query.page, query.per_page)
        if query.db == DbPortalDb.trad:
            return await _search_arsa_unified(
                solr_client,
                config,
                query,
                ast,
                with_uf=with_uf,
                requested_facets=requested_facets,
                facets_size=facets_size,
                facet_self_exclude=facet_self_exclude,
                free_text_operator=free_text_op,
            )
        return await _search_txsearch_unified(
            solr_client,
            config,
            query,
            ast,
            with_uf=with_uf,
            requested_facets=requested_facets,
            facets_size=facets_size,
            facet_self_exclude=facet_self_exclude,
            free_text_operator=free_text_op,
        )
    status_mode = _resolve_status_mode(ast)
    es_query_body = _build_es_query_for_ast(ast, status_mode, free_text_operator=free_text_op)
    return await _db_specific_search_es_unified(
        es_client,
        query,
        es_query_body,
        ast=ast,
        status_mode=status_mode,
        free_text_operator=free_text_op,
        requested_facets=requested_facets,
        facets_size=facets_size,
        facet_self_exclude=facet_self_exclude,
    )


async def _db_specific_search_cursor(
    client: httpx.AsyncClient,
    query: DbPortalSearchQuery,
    *,
    requested_facets: list[str] | None,
    facets_size: int,
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
    # Facet aggregation rides the PIT search body; the population is the
    # cursor's baked-in query (status filter included), so it matches the
    # original offset request's facets across cursor continuation.
    if requested_facets:
        body["aggs"] = build_facet_aggs(requested_facets=requested_facets, size=facets_size)
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
    facets = parse_db_portal_es_facets(es_resp.get("aggregations", {})) if requested_facets else None
    return DbPortalHitsResponse(  # type: ignore[call-arg]
        total=total,
        hits=hits,
        hard_limit_reached=(total >= _DEEP_PAGING_LIMIT),
        page=None,
        per_page=query.per_page,
        next_cursor=next_cursor,
        has_next=has_next,
        facets=facets,
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
    requested_facets = resolve_db_portal_facets(query.facets, db=None)
    return await _cross_search_dispatch(
        es_client,
        solr_client,
        config,
        ast,
        query.top_hits,
        free_text_operator=query.keyword_operator.value,
        requested_facets=requested_facets,
        facets_size=resolve_facets_size(query.facets_size),
        facet_self_exclude=query.facet_self_exclude,
    )


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
        requested_facets = resolve_db_portal_facets(query.facets, db=query.db)
        return await _db_specific_search_cursor(
            es_client,
            query,
            requested_facets=requested_facets,
            facets_size=resolve_facets_size(query.facets_size),
        )
    ast = _parse_and_validate_query(query.q, db=query.db, config=config) if query.q else None
    requested_facets = resolve_db_portal_facets(query.facets, db=query.db)
    return await _db_specific_search_dispatch(
        es_client,
        solr_client,
        config,
        query,
        ast,
        requested_facets=requested_facets,
        facets_size=resolve_facets_size(query.facets_size),
        facet_self_exclude=query.facet_self_exclude,
    )


_CROSS_SEARCH_EXAMPLE: dict[str, Any] = {
    "databases": [
        {
            "db": "trad",
            "count": None,
            "error": "timeout",
            "hits": [],
        },
        {
            "db": "sra",
            "count": 1234,
            "error": None,
            "hits": [
                {
                    "identifier": "DRR123456",
                    "type": "sra-run",
                    "url": "https://ddbj.nig.ac.jp/search/entry/sra-run/DRR123456",
                    "title": "Whole-genome sequencing of Homo sapiens",
                    "description": None,
                    "organism": {"identifier": "9606", "name": "Homo sapiens"},
                    "status": "public",
                    "accessibility": "public-access",
                    "dateCreated": "2024-01-01",
                    "dateModified": "2024-06-01",
                    "datePublished": "2024-01-15",
                    "isPartOf": "sra",
                },
            ],
        },
        {"db": "bioproject", "count": 567, "error": None, "hits": []},
        {"db": "biosample", "count": 890, "error": None, "hits": []},
        {"db": "jga", "count": 12, "error": None, "hits": []},
        {"db": "gea", "count": 34, "error": None, "hits": []},
        {"db": "metabobank", "count": 5, "error": None, "hits": []},
        {"db": "taxonomy", "count": 12, "error": None, "hits": []},
    ],
}


router.add_api_route(
    "/db-portal/cross-search",
    _cross_search_handler,
    methods=["GET"],
    response_model=DbPortalCrossSearchResponse,
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": _CROSS_SEARCH_EXAMPLE,
                },
            },
        },
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


_SEARCH_HIT_EXAMPLE: dict[str, Any] = {
    "identifier": "PRJDB1234",
    "type": "bioproject",
    "title": "Whole-genome sequencing of Homo sapiens",
    "description": "Reference genome assembly with deep coverage.",
    "organism": {"identifier": "9606", "name": "Homo sapiens"},
    "datePublished": "2024-01-15",
    "dateModified": "2024-06-01",
    "dateCreated": "2024-01-01",
    "url": "https://ddbj.nig.ac.jp/search/entry/bioproject/PRJDB1234",
    "sameAs": [],
    "dbXrefs": None,
    "status": "public",
    "accessibility": "public-access",
    "projectType": "BioProject",
    "organization": [],
    "publication": [],
    "grant": [],
    "externalLink": [],
    "relevance": ["Medical"],
}


router.add_api_route(
    "/db-portal/search",
    _db_search_handler,
    methods=["GET"],
    response_model=DbPortalHitsResponse,
    responses={
        200: {
            "content": {
                "application/json": {
                    "examples": {
                        "offset_mode": {
                            "summary": "Offset mode (first page, more results pending)",
                            "value": {
                                "total": 1234,
                                "hits": [_SEARCH_HIT_EXAMPLE],
                                "hardLimitReached": False,
                                "page": 1,
                                "perPage": 20,
                                "nextCursor": "eyJwaXRfaWQiOiJhYmMxMjMifQ.def456",
                                "hasNext": True,
                            },
                        },
                        "cursor_mode": {
                            "summary": "Cursor mode (continued, last page)",
                            "value": {
                                "total": 1234,
                                "hits": [_SEARCH_HIT_EXAMPLE],
                                "hardLimitReached": False,
                                "page": None,
                                "perPage": 20,
                                "nextCursor": None,
                                "hasNext": False,
                            },
                        },
                    },
                },
            },
        },
        400: {
            "description": (
                "Bad Request (missing-db, cursor exclusivity, query parse/validate error, deep paging limit)."
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
        "are offset-only (400 `cursor-not-supported` if cursor is supplied). "
        "On ES DBs, `cursor` cannot be combined with `q` / `sort` / `page>1` (400 `about:blank`, cursor exclusivity). "
        "Cross-database counts go through /db-portal/cross-search instead."
    ),
    operation_id="searchDbPortal",
    tags=["db-portal"],
)


# === GET /db-portal/parse — query → GUI 逆パーサ ===


async def _parse_db_portal(
    q: str = Query(
        ...,
        examples=["cancer AND organism_id:9606"],
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


# === POST /db-portal/serialize — AST JSON tree → DSL 文字列 (parse の逆経路) ===

# validator の error detail は parser 由来の Position (1-based column / length) を
# 文字列に埋め込む.  serialize endpoint は元 DSL 文字列を持たず ``json_to_ast`` で
# dummy Position(column=1, length=0) を割り当てるため、その表記は client にとって
# 誤誘導になる.  validator メッセージ末尾の ``at column N`` / ``at column N (length M)``
# 表記を一括 strip して詳細から取り除く.
_DUMMY_COLUMN_INFO_RE = re.compile(r"\s+at column \d+(?:\s*\(length \d+\))?")


def _strip_dummy_column_info(detail: str) -> str:
    """Remove ``at column N`` markers from validator errors raised via /db-portal/serialize."""
    return _DUMMY_COLUMN_INFO_RE.sub("", detail) if detail else detail


async def _serialize_db_portal(
    body: DbPortalSerializeRequest,
    db: DbPortalDb | None = Query(
        default=None,
        examples=["bioproject"],
        description=(
            "Validator mode target.  Omit for cross-db mode (Tier 1/2 only); "
            "specify a DB for single-db mode (Tier 1/2/3 allowlist).  "
            "Same semantics as ``GET /db-portal/parse``."
        ),
    ),
    config: AppConfig = Depends(_get_config_dep),
) -> DbPortalSerializeResponse:
    """Serialize an AST JSON tree back into the normalized DSL string."""
    payload = body.model_dump(by_alias=True)["ast"]
    ast = json_to_ast(payload)
    try:
        validate(
            ast,
            mode="cross" if db is None else "single",
            db=db.value if db is not None else None,
            max_depth=config.dsl_max_depth,
            max_nodes=config.dsl_max_nodes,
        )
    except DslError as exc:
        raise DbPortalHTTPException(
            status_code=400,
            type_uri=DbPortalErrorType[exc.type.name],
            detail=_strip_dummy_column_info(exc.detail),
        ) from exc
    return DbPortalSerializeResponse(dsl=ast_to_dsl(ast))


router.add_api_route(
    "/db-portal/serialize",
    _serialize_db_portal,
    methods=["POST"],
    response_model=DbPortalSerializeResponse,
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {"dsl": 'cancer AND organism_name:"Homo sapiens"'},
                },
            },
        },
        400: {
            "description": (
                "Bad Request — request body schema violation (``invalid-ast``) or "
                "validator error (``unknown-field`` / ``invalid-operator-for-field`` etc.)."
            ),
            "model": ProblemDetails,
        },
        422: {
            "description": "Unprocessable Entity (invalid ``db`` enum value in query string).",
            "model": ProblemDetails,
        },
    },
    summary="Serialize an AST JSON tree (parse-response shape) into a normalized DSL string.",
    operation_id="serializeDbPortal",
    tags=["db-portal"],
)
