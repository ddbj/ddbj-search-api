"""Search endpoints: GET /entries/ and GET /entries/{type}/.

Cross-type and type-specific search with pagination, filtering,
sorting, and optional facet aggregation.
"""
import logging
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from ddbj_search_api.es import get_es_client
from ddbj_search_api.es.client import es_search
from ddbj_search_api.es.query import (build_facet_aggs, build_search_query,
                                      build_sort, build_source_filter,
                                      pagination_to_from_size,
                                      validate_keyword_fields)
from ddbj_search_api.schemas.common import DbType, Pagination
from ddbj_search_api.schemas.entries import EntryListResponse
from ddbj_search_api.schemas.queries import (BioProjectExtraQuery,
                                             PaginationQuery,
                                             ResponseControlQuery,
                                             SearchFilterQuery)
from ddbj_search_api.utils import parse_es_hits, parse_facets

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


def _validate_params(
    sort_param: Optional[str],
    keyword_fields: Optional[str],
) -> None:
    """Validate sort and keywordFields; raise 422 on invalid input."""
    try:
        build_sort(sort_param)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        validate_keyword_fields(keyword_fields)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _do_search(
    client: httpx.AsyncClient,
    index: str,
    pagination: PaginationQuery,
    search_filter: SearchFilterQuery,
    response_control: ResponseControlQuery,
    db_xrefs_limit: int,
    is_cross_type: bool = False,
    db_type: Optional[str] = None,
    types: Optional[str] = None,
    organization: Optional[str] = None,
    publication: Optional[str] = None,
    grant: Optional[str] = None,
    umbrella: Optional[str] = None,
) -> EntryListResponse:
    """Execute search against ES and build the response."""
    # 1. Deep paging check
    _validate_deep_paging(pagination.page, pagination.per_page)

    # 2. Validate sort and keywordFields
    _validate_params(response_control.sort, search_filter.keyword_fields)

    # 3. Build ES query
    query = build_search_query(
        keywords=search_filter.keywords,
        keyword_fields=search_filter.keyword_fields,
        keyword_operator=search_filter.keyword_operator.value,
        organism=search_filter.organism,
        date_published_from=search_filter.date_published_from,
        date_published_to=search_filter.date_published_to,
        date_updated_from=search_filter.date_updated_from,
        date_updated_to=search_filter.date_updated_to,
        types=types,
        organization=organization,
        publication=publication,
        grant=grant,
        umbrella=umbrella,
    )

    # 4. Pagination → from/size
    from_, size = pagination_to_from_size(pagination.page, pagination.per_page)

    # 5. Sort
    sort = build_sort(response_control.sort)

    # 6. Source filter
    source = build_source_filter(
        response_control.fields,
        response_control.include_properties,
    )

    # 7. Build request body
    body: Dict[str, Any] = {
        "query": query,
        "from": from_,
        "size": size,
    }
    if sort is not None:
        body["sort"] = sort
    if source is not None:
        body["_source"] = source

    # 8. Facet aggregations
    if response_control.include_facets:
        body["aggs"] = build_facet_aggs(
            is_cross_type=is_cross_type,
            db_type=db_type,
        )

    # 9. Execute
    es_resp = await es_search(client, index, body)

    # 10. Parse response
    raw_hits = es_resp["hits"]["hits"]
    total = es_resp["hits"]["total"]["value"]

    items = parse_es_hits(raw_hits, db_xrefs_limit)

    facets = None
    if response_control.include_facets and "aggregations" in es_resp:
        facets = parse_facets(
            es_resp["aggregations"],
            is_cross_type=is_cross_type,
            db_type=db_type,
        )

    return EntryListResponse(
        pagination=Pagination(
            page=pagination.page,
            per_page=pagination.per_page,  # type: ignore[call-arg]
            total=total,
        ),
        items=items,
        facets=facets,
    )


# === GET /entries/ (cross-type search) ===


async def _list_all_entries(
    pagination: PaginationQuery = Depends(),
    search_filter: SearchFilterQuery = Depends(),
    response_control: ResponseControlQuery = Depends(),
    types: Optional[str] = Query(
        default=None,
        description="Filter by database types (comma-separated).",
    ),
    db_xrefs_limit: int = Query(
        default=100,
        ge=0,
        le=1000,
        alias="dbXrefsLimit",
        description=(
            "Maximum number of dbXrefs to return (0-1000). "
            "Use 0 to omit dbXrefs but still get dbXrefsCount."
        ),
    ),
    client: httpx.AsyncClient = Depends(get_es_client),
) -> EntryListResponse:
    """Search entries across all database types.

    Supports keyword search, organism/date filtering, pagination,
    sorting, field selection, and facet aggregation.  Use the ``types``
    parameter to narrow the search to specific database types.
    """

    return await _do_search(
        client=client,
        index="entries",
        pagination=pagination,
        search_filter=search_filter,
        response_control=response_control,
        db_xrefs_limit=db_xrefs_limit,
        is_cross_type=True,
        types=types,
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


def _make_type_search_handler(db_type: DbType):  # type: ignore[no-untyped-def]
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
            db_xrefs_limit: int = Query(
                default=100,
                ge=0,
                le=1000,
                alias="dbXrefsLimit",
                description=(
                    "Maximum number of dbXrefs to return (0-1000). "
                    "Use 0 to omit dbXrefs but still get dbXrefsCount."
                ),
            ),
            client: httpx.AsyncClient = Depends(get_es_client),
        ) -> EntryListResponse:

            return await _do_search(
                client=client,
                index=db_type.value,
                pagination=pagination,
                search_filter=search_filter,
                response_control=response_control,
                db_xrefs_limit=db_xrefs_limit,
                is_cross_type=False,
                db_type=db_type.value,
                organization=bioproject_extra.organization,
                publication=bioproject_extra.publication,
                grant=bioproject_extra.grant,
                umbrella=bioproject_extra.umbrella,
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
            db_xrefs_limit: int = Query(
                default=100,
                ge=0,
                le=1000,
                alias="dbXrefsLimit",
                description=(
                    "Maximum number of dbXrefs to return (0-1000). "
                    "Use 0 to omit dbXrefs but still get dbXrefsCount."
                ),
            ),
            client: httpx.AsyncClient = Depends(get_es_client),
        ) -> EntryListResponse:

            return await _do_search(
                client=client,
                index=db_type.value,
                pagination=pagination,
                search_filter=search_filter,
                response_control=response_control,
                db_xrefs_limit=db_xrefs_limit,
                is_cross_type=False,
                db_type=db_type.value,
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
