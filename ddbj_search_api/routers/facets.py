"""Facets endpoints: GET /facets and GET /facets/{type}.

Retrieve facet aggregation counts without full search results.
Uses ``es_search`` with ``size=0`` to get only aggregation buckets.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from ddbj_search_api.es import get_es_client
from ddbj_search_api.es.client import es_search
from ddbj_search_api.es.query import (
    build_facet_aggs,
    build_search_query,
    resolve_requested_facets,
    validate_keyword_fields,
)
from ddbj_search_api.routers._query_validation import (
    TYPE_GROUP_FILTERS_DESC,
    extra_to_filters,
    facets_allowed_query_params,
    reject_unknown_query_params,
)
from ddbj_search_api.schemas.common import DB_TYPE_DISPLAY, DbType, ProblemDetails
from ddbj_search_api.schemas.facets import FacetsResponse
from ddbj_search_api.schemas.queries import (
    BioProjectExtraQuery,
    BioSampleExtraQuery,
    FacetsParamQuery,
    GeaExtraQuery,
    JgaExtraQuery,
    MetaboBankExtraQuery,
    SearchFilterQuery,
    SraExtraQuery,
    TypesFilterQuery,
    TypeSpecificFilters,
)
from ddbj_search_api.utils import parse_facets

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Facets"])

_FACETS_ERRORS: dict[int | str, dict[str, Any]] = {
    400: {
        "description": (
            "Bad Request (facet selected but not applicable to the endpoint, "
            "e.g. ``facets=libraryStrategy`` on ``/facets/bioproject``)."
        ),
        "model": ProblemDetails,
    },
    422: {
        "description": "Unprocessable Entity (parameter validation error).",
        "model": ProblemDetails,
    },
}


# --- Shared logic ---


async def _do_facets(
    client: httpx.AsyncClient,
    index: str,
    search_filter: SearchFilterQuery,
    facets_param: FacetsParamQuery,
    filters: TypeSpecificFilters,
    is_cross_type: bool = False,
    db_type: str | None = None,
) -> FacetsResponse:
    """Execute facet aggregation against ES and build the response.

    ``filters`` carries every type-specific value (term / nested / text
    plus the cross-type ``types`` filter). Cross-type endpoints pass
    ``TypeSpecificFilters(types=...)`` with all other fields ``None``;
    type-specific endpoints fill in the kwargs that match their type
    group via :func:`ddbj_search_api.routers._query_validation.extra_to_filters`.
    """
    try:
        fields = validate_keyword_fields(search_filter.keyword_fields)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        requested_facets = resolve_requested_facets(
            facets_param.facets,
            is_cross_type=is_cross_type,
            db_type=db_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Facet 集計は常に status:public に絞り込む
    # (docs/api-spec.md § データ可視性)。
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
        status_mode="public_only",
        **dataclasses.asdict(filters),
    )

    aggs = build_facet_aggs(
        is_cross_type=is_cross_type,
        requested_facets=requested_facets,
    )

    body: dict[str, Any] = {
        "query": query,
        "size": 0,
    }
    if aggs:
        body["aggs"] = aggs

    es_resp = await es_search(client, index, body)
    facets = parse_facets(es_resp.get("aggregations", {}))
    return FacetsResponse(facets=facets)


# --- GET /facets (cross-type) ---


async def _get_facets(
    request: Request,
    search_filter: SearchFilterQuery = Depends(),
    types_filter: TypesFilterQuery = Depends(),
    facets_param: FacetsParamQuery = Depends(),
    client: httpx.AsyncClient = Depends(get_es_client),
) -> FacetsResponse:
    """Get facet counts across all database types.

    Returns aggregated counts for organism, accessibility, and type by
    default. Use the ``facets`` parameter to opt in to type-specific
    aggregations; the cross-type endpoint accepts any allowlisted facet
    name and runs aggregations against the underlying alias, so indices
    that lack the field simply produce empty buckets.
    """
    reject_unknown_query_params(request, allowed=facets_allowed_query_params(None))
    filters = extra_to_filters(None, types=types_filter.types)
    return await _do_facets(
        client=client,
        index="entries",
        search_filter=search_filter,
        facets_param=facets_param,
        filters=filters,
        is_cross_type=True,
    )


router.add_api_route(
    "/facets",
    _get_facets,
    methods=["GET"],
    response_model=FacetsResponse,
    summary="Cross-type facet aggregation",
    operation_id="getFacets",
    responses=_FACETS_ERRORS,
)


# --- GET /facets/{type} (type-specific) ---


def _is_sra(db_type: DbType) -> bool:
    return db_type.value.startswith("sra-")


def _is_jga(db_type: DbType) -> bool:
    return db_type.value.startswith("jga-")


def _make_type_facets_handler(db_type: DbType) -> Any:
    """Factory: create a type-specific facets handler.

    Each handler injects exactly the ``*ExtraQuery`` dependency that
    matches the type group (BioProject / BioSample / SRA-* / JGA-* /
    GEA / MetaboBank). Parameters from another type group surface as
    422 through FastAPI's unknown-query handling without an explicit
    guard.
    """
    if db_type == DbType.bioproject:

        async def _handler(
            request: Request,
            search_filter: SearchFilterQuery = Depends(),
            extra: BioProjectExtraQuery = Depends(),
            facets_param: FacetsParamQuery = Depends(),
            client: httpx.AsyncClient = Depends(get_es_client),
        ) -> FacetsResponse:
            reject_unknown_query_params(request, allowed=facets_allowed_query_params(db_type))
            filters = extra_to_filters(extra)
            return await _do_facets(
                client=client,
                index=db_type.value,
                search_filter=search_filter,
                facets_param=facets_param,
                filters=filters,
                is_cross_type=False,
                db_type=db_type.value,
            )

    elif db_type == DbType.biosample:

        async def _handler(  # type: ignore[misc]
            request: Request,
            search_filter: SearchFilterQuery = Depends(),
            extra: BioSampleExtraQuery = Depends(),
            facets_param: FacetsParamQuery = Depends(),
            client: httpx.AsyncClient = Depends(get_es_client),
        ) -> FacetsResponse:
            reject_unknown_query_params(request, allowed=facets_allowed_query_params(db_type))
            filters = extra_to_filters(extra)
            return await _do_facets(
                client=client,
                index=db_type.value,
                search_filter=search_filter,
                facets_param=facets_param,
                filters=filters,
                is_cross_type=False,
                db_type=db_type.value,
            )

    elif _is_sra(db_type):

        async def _handler(  # type: ignore[misc]
            request: Request,
            search_filter: SearchFilterQuery = Depends(),
            extra: SraExtraQuery = Depends(),
            facets_param: FacetsParamQuery = Depends(),
            client: httpx.AsyncClient = Depends(get_es_client),
        ) -> FacetsResponse:
            reject_unknown_query_params(request, allowed=facets_allowed_query_params(db_type))
            filters = extra_to_filters(extra)
            return await _do_facets(
                client=client,
                index=db_type.value,
                search_filter=search_filter,
                facets_param=facets_param,
                filters=filters,
                is_cross_type=False,
                db_type=db_type.value,
            )

    elif _is_jga(db_type):

        async def _handler(  # type: ignore[misc]
            request: Request,
            search_filter: SearchFilterQuery = Depends(),
            extra: JgaExtraQuery = Depends(),
            facets_param: FacetsParamQuery = Depends(),
            client: httpx.AsyncClient = Depends(get_es_client),
        ) -> FacetsResponse:
            reject_unknown_query_params(request, allowed=facets_allowed_query_params(db_type))
            filters = extra_to_filters(extra)
            return await _do_facets(
                client=client,
                index=db_type.value,
                search_filter=search_filter,
                facets_param=facets_param,
                filters=filters,
                is_cross_type=False,
                db_type=db_type.value,
            )

    elif db_type == DbType.gea:

        async def _handler(  # type: ignore[misc]
            request: Request,
            search_filter: SearchFilterQuery = Depends(),
            extra: GeaExtraQuery = Depends(),
            facets_param: FacetsParamQuery = Depends(),
            client: httpx.AsyncClient = Depends(get_es_client),
        ) -> FacetsResponse:
            reject_unknown_query_params(request, allowed=facets_allowed_query_params(db_type))
            filters = extra_to_filters(extra)
            return await _do_facets(
                client=client,
                index=db_type.value,
                search_filter=search_filter,
                facets_param=facets_param,
                filters=filters,
                is_cross_type=False,
                db_type=db_type.value,
            )

    elif db_type == DbType.metabobank:

        async def _handler(  # type: ignore[misc]
            request: Request,
            search_filter: SearchFilterQuery = Depends(),
            extra: MetaboBankExtraQuery = Depends(),
            facets_param: FacetsParamQuery = Depends(),
            client: httpx.AsyncClient = Depends(get_es_client),
        ) -> FacetsResponse:
            reject_unknown_query_params(request, allowed=facets_allowed_query_params(db_type))
            filters = extra_to_filters(extra)
            return await _do_facets(
                client=client,
                index=db_type.value,
                search_filter=search_filter,
                facets_param=facets_param,
                filters=filters,
                is_cross_type=False,
                db_type=db_type.value,
            )

    else:  # pragma: no cover — every DbType should hit a branch above
        raise RuntimeError(f"Unhandled DbType in facets handler factory: {db_type}")

    _handler.__doc__ = f"Get facet counts for {db_type.value} entries. {TYPE_GROUP_FILTERS_DESC[db_type]}"
    _handler.__name__ = f"get_{db_type.value.replace('-', '_')}_facets"

    return _handler


for _db_type in DbType:
    _handler = _make_type_facets_handler(_db_type)
    router.add_api_route(
        f"/facets/{_db_type.value}",
        _handler,
        methods=["GET"],
        response_model=FacetsResponse,
        summary=f"Facet aggregation for {_db_type.value}",
        operation_id=f"get{DB_TYPE_DISPLAY[_db_type]}Facets",
        responses=_FACETS_ERRORS,
    )
