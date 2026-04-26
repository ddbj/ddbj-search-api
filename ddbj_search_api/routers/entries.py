"""Search endpoints: GET /entries/ and GET /entries/{type}/.

Cross-type and type-specific search with pagination, filtering,
sorting, and optional facet aggregation.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Any, cast

import httpx
from ddbj_search_converter.jsonl.utils import to_xref
from ddbj_search_converter.schema import XrefType
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ddbj_search_api.config import DBLINK_DB_PATH
from ddbj_search_api.cursor import compute_next_cursor, decode_cursor
from ddbj_search_api.dblink.client import count_linked_ids_bulk, get_linked_ids_limited_bulk
from ddbj_search_api.es import get_es_client
from ddbj_search_api.es.client import es_open_pit, es_search, es_search_with_pit
from ddbj_search_api.es.query import (
    StatusMode,
    build_facet_aggs,
    build_search_query,
    build_sort_with_tiebreaker,
    build_source_filter,
    pagination_to_from_size,
    resolve_requested_facets,
    validate_keyword_fields,
)
from ddbj_search_api.routers._query_validation import (
    TYPE_GROUP_FILTERS_DESC,
    entries_allowed_query_params,
    extra_to_filters,
    reject_unknown_query_params,
)
from ddbj_search_api.schemas.common import DB_TYPE_DISPLAY, DbType, EntryListItem, Pagination, ProblemDetails
from ddbj_search_api.schemas.entries import EntryListResponse
from ddbj_search_api.schemas.queries import (
    BioProjectExtraQuery,
    BioSampleExtraQuery,
    DbXrefsLimitQuery,
    FacetsParamQuery,
    GeaExtraQuery,
    JgaExtraQuery,
    MetaboBankExtraQuery,
    PaginationQuery,
    ResponseControlQuery,
    SearchFilterQuery,
    SraExtraQuery,
    TypesFilterQuery,
    TypeSpecificFilters,
)
from ddbj_search_api.search.accession import detect_accession_exact_match
from ddbj_search_api.utils import parse_facets

_LIST_ENTRIES_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {
        "description": (
            "Bad Request (deep paging limit exceeded, cursor combined with mutually exclusive params, "
            "invalid cursor token, cursor expired, facet not applicable to this endpoint)."
        ),
        "model": ProblemDetails,
    },
    422: {
        "description": "Unprocessable Entity (parameter validation error).",
        "model": ProblemDetails,
    },
}

logger = logging.getLogger(__name__)

router = APIRouter()

_DEEP_PAGING_LIMIT = 10000

# Snake_case attr -> wire-level alias for every type-specific filter
# carried by ``TypeSpecificFilters``. ``_validate_cursor_exclusivity``
# uses this mapping to surface the alias name (camelCase) in the 400
# error detail when a cursor request also carries one of these
# parameters. Adding a new field to ``TypeSpecificFilters`` requires a
# matching entry here so the cursor exclusivity check stays complete.
_CURSOR_EXCLUSIVE_FILTER_FIELDS: dict[str, str] = {
    "types": "types",
    "object_types": "objectTypes",
    "external_link_label": "externalLinkLabel",
    "project_type": "projectType",
    "derived_from_id": "derivedFromId",
    "host": "host",
    "strain": "strain",
    "isolate": "isolate",
    "geo_loc_name": "geoLocName",
    "collection_date": "collectionDate",
    "library_strategy": "libraryStrategy",
    "library_source": "librarySource",
    "library_selection": "librarySelection",
    "platform": "platform",
    "instrument_model": "instrumentModel",
    "library_layout": "libraryLayout",
    "analysis_type": "analysisType",
    "library_name": "libraryName",
    "library_construction_protocol": "libraryConstructionProtocol",
    "study_type": "studyType",
    "dataset_type": "datasetType",
    "vendor": "vendor",
    "experiment_type": "experimentType",
    "submission_type": "submissionType",
}


# === Shared logic ===


def _validate_deep_paging(page: int, per_page: int) -> None:
    """Raise 400 if page * perPage exceeds the deep paging limit.

    The detail string mirrors :mod:`routers.db_portal` and points callers
    at cursor pagination per ``docs/api-spec.md § カーソルベースページネーション``.
    """
    if page * per_page > _DEEP_PAGING_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Deep paging limit exceeded: page ({page}) * "
                f"perPage ({per_page}) = {page * per_page} > "
                f"{_DEEP_PAGING_LIMIT}. Use cursor-based pagination for deep results."
            ),
        )


def _validate_cursor_exclusivity(
    pagination: PaginationQuery,
    search_filter: SearchFilterQuery,
    response_control: ResponseControlQuery,
    facets_param: FacetsParamQuery,
    filters: TypeSpecificFilters,
) -> None:
    """Raise 400 if cursor is used alongside any non-cursor-safe parameter."""
    conflicting: list[str] = []

    if pagination.page != 1:
        conflicting.append("page")

    if search_filter.keywords is not None:
        conflicting.append("keywords")
    if search_filter.keyword_fields is not None:
        conflicting.append("keywordFields")
    if search_filter.keyword_operator.value != "AND":
        conflicting.append("keywordOperator")
    if search_filter.organism is not None:
        conflicting.append("organism")
    if search_filter.organization is not None:
        conflicting.append("organization")
    if search_filter.publication is not None:
        conflicting.append("publication")
    if search_filter.grant is not None:
        conflicting.append("grant")
    if search_filter.date_published_from is not None:
        conflicting.append("datePublishedFrom")
    if search_filter.date_published_to is not None:
        conflicting.append("datePublishedTo")
    if search_filter.date_modified_from is not None:
        conflicting.append("dateModifiedFrom")
    if search_filter.date_modified_to is not None:
        conflicting.append("dateModifiedTo")

    if response_control.sort is not None:
        conflicting.append("sort")
    if response_control.include_facets:
        conflicting.append("includeFacets")
    if not response_control.include_properties:
        conflicting.append("includeProperties")
    if response_control.fields is not None:
        conflicting.append("fields")

    if facets_param.facets is not None:
        conflicting.append("facets")

    for attr, alias in _CURSOR_EXCLUSIVE_FILTER_FIELDS.items():
        if getattr(filters, attr) is not None:
            conflicting.append(alias)

    if conflicting:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot use 'cursor' with: {', '.join(conflicting)}. "
                "When using cursor-based pagination, only 'perPage' is allowed."
            ),
        )


def _parse_hit_source(hit: dict[str, Any]) -> dict[str, Any]:
    """Extract _source from an ES hit (no script_fields processing)."""

    return dict(hit["_source"])


def _check_dblink_db() -> None:
    """Raise HTTPException 500 if DuckDB file is missing."""
    if not DBLINK_DB_PATH.exists():
        logger.error("DuckDB file not found: %s", DBLINK_DB_PATH)
        raise HTTPException(
            status_code=500,
            detail=f"dblink database is not available: {DBLINK_DB_PATH}",
        )


def _build_source_body(source: list[str] | dict[str, Any] | None) -> dict[str, Any]:
    """Build _source portion of ES body, always excluding dbXrefs."""
    if isinstance(source, list):
        filtered = [f for f in source if f != "dbXrefs"]

        return {"_source": filtered}
    if isinstance(source, dict):
        excludes = list(source.get("excludes", []))
        if "dbXrefs" not in excludes:
            excludes.append("dbXrefs")

        return {"_source": {"excludes": excludes}}

    return {"_source": {"excludes": ["dbXrefs"]}}


async def _do_search(
    client: httpx.AsyncClient,
    index: str,
    pagination: PaginationQuery,
    search_filter: SearchFilterQuery,
    response_control: ResponseControlQuery,
    db_xrefs_limit: int,
    filters: TypeSpecificFilters,
    is_cross_type: bool = False,
    db_type: str | None = None,
    requested_facets: list[str] | None = None,
    include_db_xrefs: bool = True,
) -> Any:
    """Execute search against ES and build the response.

    ``filters`` carries every type-specific kwarg consumed by
    :func:`build_search_query` plus the cross-type ``types`` filter.
    Routers convert their endpoint-scoped ``*ExtraQuery`` and
    :class:`TypesFilterQuery` dependencies into the dataclass via
    :func:`extra_to_filters`; non-applicable fields stay ``None`` and
    become inert downstream.
    """
    if include_db_xrefs:
        _check_dblink_db()

    if pagination.cursor is not None:
        return await _do_search_cursor(
            client=client,
            index=index,
            cursor_token=pagination.cursor,
            per_page=pagination.per_page,
            db_xrefs_limit=db_xrefs_limit,
            include_db_xrefs=include_db_xrefs,
        )

    return await _do_search_offset(
        client=client,
        index=index,
        pagination=pagination,
        search_filter=search_filter,
        response_control=response_control,
        db_xrefs_limit=db_xrefs_limit,
        filters=filters,
        is_cross_type=is_cross_type,
        db_type=db_type,
        requested_facets=requested_facets,
        include_db_xrefs=include_db_xrefs,
    )


async def _do_search_offset(
    client: httpx.AsyncClient,
    index: str,
    pagination: PaginationQuery,
    search_filter: SearchFilterQuery,
    response_control: ResponseControlQuery,
    db_xrefs_limit: int,
    filters: TypeSpecificFilters,
    is_cross_type: bool = False,
    db_type: str | None = None,
    requested_facets: list[str] | None = None,
    include_db_xrefs: bool = True,
) -> Any:
    """Offset-based search (existing behaviour + nextCursor generation)."""
    # 1. Deep paging check
    _validate_deep_paging(pagination.page, pagination.per_page)

    # 2. Validate and build sort
    try:
        sort_with_tiebreaker = build_sort_with_tiebreaker(response_control.sort)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # 3. Validate keyword fields
    try:
        fields = validate_keyword_fields(search_filter.keyword_fields)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # 4. Build ES query.
    # keywords が単一 accession ID と完全一致する場合のみ suppressed も許可する
    # (docs/api-spec.md § データ可視性)。それ以外は常に public のみ。
    status_mode: StatusMode = (
        "include_suppressed" if detect_accession_exact_match(search_filter.keywords) is not None else "public_only"
    )
    query = build_search_query(
        keywords=search_filter.keywords,
        keyword_fields=fields,
        keyword_operator=search_filter.keyword_operator.value,
        organism=search_filter.organism,
        date_published_from=search_filter.date_published_from,
        date_published_to=search_filter.date_published_to,
        date_modified_from=search_filter.date_modified_from,
        date_modified_to=search_filter.date_modified_to,
        organization=search_filter.organization,
        publication=search_filter.publication,
        grant=search_filter.grant,
        status_mode=status_mode,
        **dataclasses.asdict(filters),
    )

    # 5. Pagination -> from/size
    from_, size = pagination_to_from_size(pagination.page, pagination.per_page)

    # 6. Source filter
    source = build_source_filter(
        response_control.fields,
        response_control.include_properties,
    )

    # 7. Build request body (always include sort with tiebreaker for nextCursor)
    body: dict[str, Any] = {
        "query": query,
        "from": from_,
        "size": size,
        "sort": sort_with_tiebreaker,
        **_build_source_body(source),
    }

    # 8. Facet aggregations
    if response_control.include_facets:
        aggs = build_facet_aggs(
            is_cross_type=is_cross_type,
            requested_facets=requested_facets,
        )
        if aggs:
            body["aggs"] = aggs

    # 9. Execute
    es_resp = await es_search(client, index, body)

    # 10. Parse response and enrich with DuckDB dbXrefs
    raw_hits = es_resp["hits"]["hits"]
    total = es_resp["hits"]["total"]["value"]

    items = await _enrich_hits(raw_hits, db_xrefs_limit, include_db_xrefs=include_db_xrefs)

    facets = None
    if response_control.include_facets and "aggregations" in es_resp:
        facets = parse_facets(es_resp["aggregations"])

    # 11. Compute nextCursor
    next_cursor, has_next = compute_next_cursor(
        raw_hits=raw_hits,
        size=size,
        total=total,
        offset=from_,
        sort_with_tiebreaker=sort_with_tiebreaker,
        query=query,
        pit_id=None,
    )

    response = EntryListResponse(
        pagination=Pagination(
            page=pagination.page,
            per_page=pagination.per_page,  # type: ignore[call-arg]
            total=total,
            next_cursor=next_cursor,
            has_next=has_next,
        ),
        items=items,
        facets=facets,
    )

    # When includeProperties=false, use exclude_unset to drop Pydantic
    # default fields (like properties=None) that ES correctly excluded.
    if not response_control.include_properties:
        pagination_dict = response.pagination.model_dump(
            by_alias=True,
        )
        items_list = [item.model_dump(by_alias=True, exclude_unset=True) for item in response.items]
        facets_dict = response.facets.model_dump(by_alias=True) if response.facets is not None else None

        return JSONResponse(
            content={
                "pagination": pagination_dict,
                "items": items_list,
                "facets": facets_dict,
            }
        )

    return response


async def _do_search_cursor(
    client: httpx.AsyncClient,
    index: str,
    cursor_token: str,
    per_page: int,
    db_xrefs_limit: int,
    include_db_xrefs: bool = True,
) -> Any:
    """Cursor-based search using search_after + PIT."""
    # 1. Decode cursor
    try:
        cursor = decode_cursor(cursor_token)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid cursor token: {exc}",
        ) from exc

    # 2. Open PIT if needed (first cursor from offset mode has no PIT)
    pit_id = cursor.pit_id
    if pit_id is None:
        pit_id = await es_open_pit(client, index)

    # 3. Build ES body
    body: dict[str, Any] = {
        "query": cursor.query,
        "sort": cursor.sort,
        "size": per_page,
        "pit": {"id": pit_id, "keep_alive": "5m"},
        "search_after": cursor.search_after,
        "_source": {"excludes": ["dbXrefs"]},
    }

    # 4. Execute search_after with PIT
    try:
        es_resp = await es_search_with_pit(client, body)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(
                status_code=400,
                detail="Cursor expired (PIT no longer available). Please restart your search.",
            ) from exc

        raise

    # 5. Parse response
    raw_hits = es_resp["hits"]["hits"]
    total = es_resp["hits"]["total"]["value"]

    # Use updated PIT ID from ES response if available
    updated_pit_id: str = es_resp.get("pit_id", pit_id)

    items = await _enrich_hits(raw_hits, db_xrefs_limit, include_db_xrefs=include_db_xrefs)

    # 6. Compute nextCursor
    next_cursor, has_next = compute_next_cursor(
        raw_hits=raw_hits,
        size=per_page,
        total=total,
        offset=0,
        sort_with_tiebreaker=cursor.sort,
        query=cursor.query,
        pit_id=updated_pit_id,
    )

    return EntryListResponse(
        pagination=Pagination(
            page=None,
            per_page=per_page,  # type: ignore[call-arg]
            total=total,
            next_cursor=next_cursor,
            has_next=has_next,
        ),
        items=items,
        facets=None,
    )


async def _enrich_hits(
    raw_hits: list[dict[str, Any]],
    db_xrefs_limit: int,
    include_db_xrefs: bool = True,
) -> list[EntryListItem]:
    """Parse ES hits and enrich with DuckDB dbXrefs."""
    raw_sources = [_parse_hit_source(hit) for hit in raw_hits]

    if not include_db_xrefs:
        return [EntryListItem(**src) for src in raw_sources]

    entries_keys = [(src.get("type", ""), src.get("identifier", "")) for src in raw_sources]

    bulk_xrefs, bulk_counts = await asyncio.gather(
        asyncio.to_thread(get_linked_ids_limited_bulk, DBLINK_DB_PATH, entries_keys, db_xrefs_limit),
        asyncio.to_thread(count_linked_ids_bulk, DBLINK_DB_PATH, entries_keys),
    )

    enriched = []
    for src in raw_sources:
        key = (src.get("type", ""), src.get("identifier", ""))
        xrefs_rows = bulk_xrefs.get(key, [])
        xrefs = [to_xref(acc, type_hint=cast(XrefType, t)).model_dump(by_alias=True) for t, acc in xrefs_rows]
        src["dbXrefs"] = xrefs
        src["dbXrefsCount"] = bulk_counts.get(key, {})
        enriched.append(src)

    return [EntryListItem(**src) for src in enriched]


def _resolve_requested_facets_or_400(
    facets_param: FacetsParamQuery,
    include_facets: bool,
    *,
    is_cross_type: bool,
    db_type: str | None,
) -> list[str] | None:
    """Resolve ``facets`` -> list, 400 on type-mismatch, ignore when off."""
    if not include_facets:
        return None
    try:
        return resolve_requested_facets(
            facets_param.facets,
            is_cross_type=is_cross_type,
            db_type=db_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# === GET /entries/ (cross-type search) ===


async def _list_all_entries(
    request: Request,
    pagination: PaginationQuery = Depends(),
    search_filter: SearchFilterQuery = Depends(),
    response_control: ResponseControlQuery = Depends(),
    types_filter: TypesFilterQuery = Depends(),
    facets_param: FacetsParamQuery = Depends(),
    db_xrefs: DbXrefsLimitQuery = Depends(),
    client: httpx.AsyncClient = Depends(get_es_client),
) -> Any:
    """Search entries across all database types.

    Supports keyword search, organism/date filtering, pagination,
    sorting, field selection, and facet aggregation. Use the ``types``
    parameter to narrow the search to specific database types.
    """
    reject_unknown_query_params(request, allowed=entries_allowed_query_params(None))

    filters = extra_to_filters(None, types=types_filter.types)

    if pagination.cursor is not None:
        _validate_cursor_exclusivity(
            pagination,
            search_filter,
            response_control,
            facets_param,
            filters,
        )

    requested_facets = _resolve_requested_facets_or_400(
        facets_param,
        include_facets=response_control.include_facets,
        is_cross_type=True,
        db_type=None,
    )

    return await _do_search(
        client=client,
        index="entries",
        pagination=pagination,
        search_filter=search_filter,
        response_control=response_control,
        db_xrefs_limit=db_xrefs.db_xrefs_limit,
        filters=filters,
        is_cross_type=True,
        requested_facets=requested_facets,
        include_db_xrefs=db_xrefs.include_db_xrefs,
    )


router.add_api_route(
    "/entries/",
    _list_all_entries,
    methods=["GET"],
    response_model=EntryListResponse,
    summary="Cross-type entry search",
    operation_id="listEntries",
    responses=_LIST_ENTRIES_RESPONSES,
    tags=["Entries"],
)
router.add_api_route(
    "/entries",
    _list_all_entries,
    methods=["GET"],
    response_model=EntryListResponse,
    include_in_schema=False,
    tags=["Entries"],
)


# === GET /entries/{type}/ (type-specific search) ===


def _is_sra(db_type: DbType) -> bool:
    return db_type.value.startswith("sra-")


def _is_jga(db_type: DbType) -> bool:
    return db_type.value.startswith("jga-")


_TypeExtraQuery = (
    BioProjectExtraQuery | BioSampleExtraQuery | SraExtraQuery | JgaExtraQuery | GeaExtraQuery | MetaboBankExtraQuery
)


async def _run_type_search(
    *,
    request: Request,
    pagination: PaginationQuery,
    search_filter: SearchFilterQuery,
    response_control: ResponseControlQuery,
    extra: _TypeExtraQuery,
    facets_param: FacetsParamQuery,
    db_xrefs: DbXrefsLimitQuery,
    client: httpx.AsyncClient,
    db_type: DbType,
) -> Any:
    """Common type-specific entrypoint shared by every handler factory branch."""
    reject_unknown_query_params(request, allowed=entries_allowed_query_params(db_type))

    filters = extra_to_filters(extra)

    if pagination.cursor is not None:
        _validate_cursor_exclusivity(
            pagination,
            search_filter,
            response_control,
            facets_param,
            filters,
        )

    requested_facets = _resolve_requested_facets_or_400(
        facets_param,
        include_facets=response_control.include_facets,
        is_cross_type=False,
        db_type=db_type.value,
    )

    return await _do_search(
        client=client,
        index=db_type.value,
        pagination=pagination,
        search_filter=search_filter,
        response_control=response_control,
        db_xrefs_limit=db_xrefs.db_xrefs_limit,
        filters=filters,
        is_cross_type=False,
        db_type=db_type.value,
        requested_facets=requested_facets,
        include_db_xrefs=db_xrefs.include_db_xrefs,
    )


def _make_type_search_handler(db_type: DbType) -> Any:
    """Factory: create a type-specific search handler.

    The handler injects exactly the ``*ExtraQuery`` matching the type
    group. Parameters from another type group surface as 422 through
    FastAPI's unknown-query handling.
    """
    if db_type == DbType.bioproject:

        async def _handler(
            request: Request,
            pagination: PaginationQuery = Depends(),
            search_filter: SearchFilterQuery = Depends(),
            response_control: ResponseControlQuery = Depends(),
            extra: BioProjectExtraQuery = Depends(),
            facets_param: FacetsParamQuery = Depends(),
            db_xrefs: DbXrefsLimitQuery = Depends(),
            client: httpx.AsyncClient = Depends(get_es_client),
        ) -> Any:
            return await _run_type_search(
                request=request,
                pagination=pagination,
                search_filter=search_filter,
                response_control=response_control,
                extra=extra,
                facets_param=facets_param,
                db_xrefs=db_xrefs,
                client=client,
                db_type=db_type,
            )

    elif db_type == DbType.biosample:

        async def _handler(  # type: ignore[misc]
            request: Request,
            pagination: PaginationQuery = Depends(),
            search_filter: SearchFilterQuery = Depends(),
            response_control: ResponseControlQuery = Depends(),
            extra: BioSampleExtraQuery = Depends(),
            facets_param: FacetsParamQuery = Depends(),
            db_xrefs: DbXrefsLimitQuery = Depends(),
            client: httpx.AsyncClient = Depends(get_es_client),
        ) -> Any:
            return await _run_type_search(
                request=request,
                pagination=pagination,
                search_filter=search_filter,
                response_control=response_control,
                extra=extra,
                facets_param=facets_param,
                db_xrefs=db_xrefs,
                client=client,
                db_type=db_type,
            )

    elif _is_sra(db_type):

        async def _handler(  # type: ignore[misc]
            request: Request,
            pagination: PaginationQuery = Depends(),
            search_filter: SearchFilterQuery = Depends(),
            response_control: ResponseControlQuery = Depends(),
            extra: SraExtraQuery = Depends(),
            facets_param: FacetsParamQuery = Depends(),
            db_xrefs: DbXrefsLimitQuery = Depends(),
            client: httpx.AsyncClient = Depends(get_es_client),
        ) -> Any:
            return await _run_type_search(
                request=request,
                pagination=pagination,
                search_filter=search_filter,
                response_control=response_control,
                extra=extra,
                facets_param=facets_param,
                db_xrefs=db_xrefs,
                client=client,
                db_type=db_type,
            )

    elif _is_jga(db_type):

        async def _handler(  # type: ignore[misc]
            request: Request,
            pagination: PaginationQuery = Depends(),
            search_filter: SearchFilterQuery = Depends(),
            response_control: ResponseControlQuery = Depends(),
            extra: JgaExtraQuery = Depends(),
            facets_param: FacetsParamQuery = Depends(),
            db_xrefs: DbXrefsLimitQuery = Depends(),
            client: httpx.AsyncClient = Depends(get_es_client),
        ) -> Any:
            return await _run_type_search(
                request=request,
                pagination=pagination,
                search_filter=search_filter,
                response_control=response_control,
                extra=extra,
                facets_param=facets_param,
                db_xrefs=db_xrefs,
                client=client,
                db_type=db_type,
            )

    elif db_type == DbType.gea:

        async def _handler(  # type: ignore[misc]
            request: Request,
            pagination: PaginationQuery = Depends(),
            search_filter: SearchFilterQuery = Depends(),
            response_control: ResponseControlQuery = Depends(),
            extra: GeaExtraQuery = Depends(),
            facets_param: FacetsParamQuery = Depends(),
            db_xrefs: DbXrefsLimitQuery = Depends(),
            client: httpx.AsyncClient = Depends(get_es_client),
        ) -> Any:
            return await _run_type_search(
                request=request,
                pagination=pagination,
                search_filter=search_filter,
                response_control=response_control,
                extra=extra,
                facets_param=facets_param,
                db_xrefs=db_xrefs,
                client=client,
                db_type=db_type,
            )

    elif db_type == DbType.metabobank:

        async def _handler(  # type: ignore[misc]
            request: Request,
            pagination: PaginationQuery = Depends(),
            search_filter: SearchFilterQuery = Depends(),
            response_control: ResponseControlQuery = Depends(),
            extra: MetaboBankExtraQuery = Depends(),
            facets_param: FacetsParamQuery = Depends(),
            db_xrefs: DbXrefsLimitQuery = Depends(),
            client: httpx.AsyncClient = Depends(get_es_client),
        ) -> Any:
            return await _run_type_search(
                request=request,
                pagination=pagination,
                search_filter=search_filter,
                response_control=response_control,
                extra=extra,
                facets_param=facets_param,
                db_xrefs=db_xrefs,
                client=client,
                db_type=db_type,
            )

    else:  # pragma: no cover — every DbType should hit a branch above
        raise RuntimeError(f"Unhandled DbType in entries handler factory: {db_type}")

    _handler.__doc__ = f"Search {db_type.value} entries. {TYPE_GROUP_FILTERS_DESC[db_type]}"
    _handler.__name__ = f"list_{db_type.value.replace('-', '_')}_entries"

    return _handler


for _db_type in DbType:
    _handler = _make_type_search_handler(_db_type)
    router.add_api_route(
        f"/entries/{_db_type.value}/",
        _handler,
        methods=["GET"],
        response_model=EntryListResponse,
        summary=f"Search {_db_type.value} entries",
        operation_id=f"list{DB_TYPE_DISPLAY[_db_type]}Entries",
        responses=_LIST_ENTRIES_RESPONSES,
        tags=["Entries"],
    )
    router.add_api_route(
        f"/entries/{_db_type.value}",
        _handler,
        methods=["GET"],
        response_model=EntryListResponse,
        include_in_schema=False,
        tags=["Entries"],
    )
