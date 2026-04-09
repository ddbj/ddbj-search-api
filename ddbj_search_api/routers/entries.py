"""Search endpoints: GET /entries/ and GET /entries/{type}/.

Cross-type and type-specific search with pagination, filtering,
sorting, and optional facet aggregation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

import httpx
from ddbj_search_converter.jsonl.utils import to_xref
from ddbj_search_converter.schema import XrefType
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from ddbj_search_api.config import DBLINK_DB_PATH
from ddbj_search_api.cursor import CursorPayload, decode_cursor, encode_cursor
from ddbj_search_api.dblink.client import count_linked_ids_bulk, get_linked_ids_limited_bulk
from ddbj_search_api.es import get_es_client
from ddbj_search_api.es.client import es_open_pit, es_search, es_search_with_pit
from ddbj_search_api.es.query import (
    build_facet_aggs,
    build_search_query,
    build_sort_with_tiebreaker,
    build_source_filter,
    pagination_to_from_size,
    validate_keyword_fields,
)
from ddbj_search_api.schemas.common import DbType, EntryListItem, Pagination
from ddbj_search_api.schemas.entries import EntryListResponse
from ddbj_search_api.schemas.queries import (
    BioProjectExtraQuery,
    DbXrefsLimitQuery,
    PaginationQuery,
    ResponseControlQuery,
    SearchFilterQuery,
    TypesFilterQuery,
)
from ddbj_search_api.utils import parse_facets

logger = logging.getLogger(__name__)

router = APIRouter()

_DEEP_PAGING_LIMIT = 10000


# === Shared logic ===


def _validate_deep_paging(page: int, per_page: int) -> None:
    """Raise 400 if page * perPage exceeds the deep paging limit."""
    if page * per_page > _DEEP_PAGING_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Deep paging limit exceeded: page ({page}) * "
                f"perPage ({per_page}) = {page * per_page} > "
                f"{_DEEP_PAGING_LIMIT}. Use Bulk API for large result sets."
            ),
        )


def _validate_cursor_exclusivity(
    pagination: PaginationQuery,
    search_filter: SearchFilterQuery,
    response_control: ResponseControlQuery,
    types: str | None = None,
    organization: str | None = None,
    publication: str | None = None,
    grant: str | None = None,
    umbrella: str | None = None,
) -> None:
    """Raise 400 if cursor is used alongside page or search params."""
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

    if types is not None:
        conflicting.append("types")
    if organization is not None:
        conflicting.append("organization")
    if publication is not None:
        conflicting.append("publication")
    if grant is not None:
        conflicting.append("grant")
    if umbrella is not None:
        conflicting.append("umbrella")

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


def _compute_next_cursor(
    raw_hits: list[dict[str, Any]],
    size: int,
    total: int,
    offset: int,
    sort_with_tiebreaker: list[dict[str, Any]],
    query: dict[str, Any],
    pit_id: str | None,
) -> tuple[str | None, bool]:
    """Compute nextCursor and hasNext from search results.

    Returns (next_cursor_token, has_next).
    """
    if not raw_hits or len(raw_hits) < size:
        return (None, False)

    if pit_id is None and offset + size >= total:
        return (None, False)

    last_sort = raw_hits[-1].get("sort")
    if last_sort is None:
        return (None, False)

    payload = CursorPayload(
        pit_id=pit_id,
        search_after=last_sort,
        sort=sort_with_tiebreaker,
        query=query,
    )

    return (encode_cursor(payload), True)


async def _do_search(
    client: httpx.AsyncClient,
    index: str,
    pagination: PaginationQuery,
    search_filter: SearchFilterQuery,
    response_control: ResponseControlQuery,
    db_xrefs_limit: int,
    is_cross_type: bool = False,
    db_type: str | None = None,
    types: str | None = None,
    organization: str | None = None,
    publication: str | None = None,
    grant: str | None = None,
    umbrella: str | None = None,
    include_db_xrefs: bool = True,
) -> Any:
    """Execute search against ES and build the response."""
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
        is_cross_type=is_cross_type,
        db_type=db_type,
        types=types,
        organization=organization,
        publication=publication,
        grant=grant,
        umbrella=umbrella,
        include_db_xrefs=include_db_xrefs,
    )


async def _do_search_offset(
    client: httpx.AsyncClient,
    index: str,
    pagination: PaginationQuery,
    search_filter: SearchFilterQuery,
    response_control: ResponseControlQuery,
    db_xrefs_limit: int,
    is_cross_type: bool = False,
    db_type: str | None = None,
    types: str | None = None,
    organization: str | None = None,
    publication: str | None = None,
    grant: str | None = None,
    umbrella: str | None = None,
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

    # 4. Build ES query
    query = build_search_query(
        keywords=search_filter.keywords,
        keyword_fields=fields,
        keyword_operator=search_filter.keyword_operator.value,
        organism=search_filter.organism,
        date_published_from=search_filter.date_published_from,
        date_published_to=search_filter.date_published_to,
        date_modified_from=search_filter.date_modified_from,
        date_modified_to=search_filter.date_modified_to,
        types=types,
        organization=organization,
        publication=publication,
        grant=grant,
        umbrella=umbrella,
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
        body["aggs"] = build_facet_aggs(
            is_cross_type=is_cross_type,
            db_type=db_type,
        )

    # 9. Execute
    es_resp = await es_search(client, index, body)

    # 10. Parse response and enrich with DuckDB dbXrefs
    raw_hits = es_resp["hits"]["hits"]
    total = es_resp["hits"]["total"]["value"]

    items = await _enrich_hits(raw_hits, db_xrefs_limit, include_db_xrefs=include_db_xrefs)

    facets = None
    if response_control.include_facets and "aggregations" in es_resp:
        facets = parse_facets(
            es_resp["aggregations"],
            is_cross_type=is_cross_type,
            db_type=db_type,
        )

    # 11. Compute nextCursor
    next_cursor, has_next = _compute_next_cursor(
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
    next_cursor, has_next = _compute_next_cursor(
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


# === GET /entries/ (cross-type search) ===


async def _list_all_entries(
    pagination: PaginationQuery = Depends(),
    search_filter: SearchFilterQuery = Depends(),
    response_control: ResponseControlQuery = Depends(),
    types_filter: TypesFilterQuery = Depends(),
    db_xrefs: DbXrefsLimitQuery = Depends(),
    client: httpx.AsyncClient = Depends(get_es_client),
) -> Any:
    """Search entries across all database types.

    Supports keyword search, organism/date filtering, pagination,
    sorting, field selection, and facet aggregation.  Use the ``types``
    parameter to narrow the search to specific database types.
    """
    if pagination.cursor is not None:
        _validate_cursor_exclusivity(
            pagination,
            search_filter,
            response_control,
            types=types_filter.types,
        )

    return await _do_search(
        client=client,
        index="entries",
        pagination=pagination,
        search_filter=search_filter,
        response_control=response_control,
        db_xrefs_limit=db_xrefs.db_xrefs_limit,
        is_cross_type=True,
        types=types_filter.types,
        include_db_xrefs=db_xrefs.include_db_xrefs,
    )


router.add_api_route(
    "/entries/",
    _list_all_entries,
    methods=["GET"],
    response_model=EntryListResponse,
    summary="Cross-type entry search",
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


def _make_type_search_handler(db_type: DbType) -> Any:
    """Factory: create a type-specific search handler.

    BioProject gets extra filter parameters (organization, publication,
    grant, umbrella); other types use the common filter set.
    """
    if db_type == DbType.bioproject:

        async def _handler(
            pagination: PaginationQuery = Depends(),
            search_filter: SearchFilterQuery = Depends(),
            response_control: ResponseControlQuery = Depends(),
            bioproject_extra: BioProjectExtraQuery = Depends(),
            db_xrefs: DbXrefsLimitQuery = Depends(),
            client: httpx.AsyncClient = Depends(get_es_client),
        ) -> Any:
            if pagination.cursor is not None:
                _validate_cursor_exclusivity(
                    pagination,
                    search_filter,
                    response_control,
                    organization=bioproject_extra.organization,
                    publication=bioproject_extra.publication,
                    grant=bioproject_extra.grant,
                    umbrella=bioproject_extra.umbrella,
                )

            return await _do_search(
                client=client,
                index=db_type.value,
                pagination=pagination,
                search_filter=search_filter,
                response_control=response_control,
                db_xrefs_limit=db_xrefs.db_xrefs_limit,
                is_cross_type=False,
                db_type=db_type.value,
                organization=bioproject_extra.organization,
                publication=bioproject_extra.publication,
                grant=bioproject_extra.grant,
                umbrella=bioproject_extra.umbrella,
                include_db_xrefs=db_xrefs.include_db_xrefs,
            )

        _handler.__doc__ = (
            f"Search {db_type.value} entries.\n\n"
            "Supports BioProject-specific filters: organization, "
            "publication, grant, umbrella."
        )
    else:

        async def _handler(  # type: ignore[misc]
            pagination: PaginationQuery = Depends(),
            search_filter: SearchFilterQuery = Depends(),
            response_control: ResponseControlQuery = Depends(),
            db_xrefs: DbXrefsLimitQuery = Depends(),
            client: httpx.AsyncClient = Depends(get_es_client),
        ) -> Any:
            if pagination.cursor is not None:
                _validate_cursor_exclusivity(
                    pagination,
                    search_filter,
                    response_control,
                )

            return await _do_search(
                client=client,
                index=db_type.value,
                pagination=pagination,
                search_filter=search_filter,
                response_control=response_control,
                db_xrefs_limit=db_xrefs.db_xrefs_limit,
                is_cross_type=False,
                db_type=db_type.value,
                include_db_xrefs=db_xrefs.include_db_xrefs,
            )

        _handler.__doc__ = f"Search {db_type.value} entries."

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
