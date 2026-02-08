"""Facets endpoints: GET /facets and GET /facets/{type}.

Retrieve facet aggregation counts without full search results.
Uses ``es_search`` with ``size=0`` to get only aggregation buckets.
"""
import logging
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException

from ddbj_search_api.es import get_es_client
from ddbj_search_api.es.client import es_search
from ddbj_search_api.es.query import (build_facet_aggs, build_search_query,
                                      validate_keyword_fields)
from ddbj_search_api.schemas.common import DbType
from ddbj_search_api.schemas.facets import FacetsResponse
from ddbj_search_api.schemas.queries import (BioProjectExtraQuery,
                                             SearchFilterQuery,
                                             TypesFilterQuery)
from ddbj_search_api.utils import parse_facets

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Facets"])


# --- Shared logic ---


async def _do_facets(
    client: httpx.AsyncClient,
    index: str,
    search_filter: SearchFilterQuery,
    is_cross_type: bool = False,
    db_type: Optional[str] = None,
    types: Optional[str] = None,
    organization: Optional[str] = None,
    publication: Optional[str] = None,
    grant: Optional[str] = None,
    umbrella: Optional[str] = None,
) -> FacetsResponse:
    """Execute facet aggregation against ES and build the response."""
    try:
        fields = validate_keyword_fields(search_filter.keyword_fields)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

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

    aggs = build_facet_aggs(
        is_cross_type=is_cross_type,
        db_type=db_type,
    )

    body: Dict[str, Any] = {
        "query": query,
        "size": 0,
        "aggs": aggs,
    }

    es_resp = await es_search(client, index, body)

    facets = parse_facets(
        es_resp.get("aggregations", {}),
        is_cross_type=is_cross_type,
        db_type=db_type,
    )

    return FacetsResponse(facets=facets)


# --- GET /facets (cross-type) ---


async def _get_facets(
    search_filter: SearchFilterQuery = Depends(),
    types_filter: TypesFilterQuery = Depends(),
    client: httpx.AsyncClient = Depends(get_es_client),
) -> FacetsResponse:
    """Get facet counts across all database types.

    Returns aggregated counts for organism, status, accessibility, and
    type.  Search filter parameters narrow the set of entries that
    facets are computed over.
    """

    return await _do_facets(
        client=client,
        index="entries",
        search_filter=search_filter,
        is_cross_type=True,
        types=types_filter.types,
    )


router.add_api_route(
    "/facets",
    _get_facets,
    methods=["GET"],
    response_model=FacetsResponse,
    summary="Cross-type facet aggregation",
)


# --- GET /facets/{type} (type-specific) ---


def _make_type_facets_handler(db_type: DbType):  # type: ignore[no-untyped-def]
    """Factory: create a type-specific facets handler."""
    if db_type == DbType.bioproject:

        async def _handler(
            search_filter: SearchFilterQuery = Depends(),
            bioproject_extra: BioProjectExtraQuery = Depends(),
            client: httpx.AsyncClient = Depends(get_es_client),
        ) -> FacetsResponse:

            return await _do_facets(
                client=client,
                index=db_type.value,
                search_filter=search_filter,
                is_cross_type=False,
                db_type=db_type.value,
                organization=bioproject_extra.organization,
                publication=bioproject_extra.publication,
                grant=bioproject_extra.grant,
                umbrella=bioproject_extra.umbrella,
            )

        _handler.__doc__ = (
            f"Get facet counts for {db_type.value} entries.\n\n"
            "Includes the bioproject-specific ``objectType`` facet "
            "and supports additional BioProject filter parameters."
        )
    else:

        async def _handler(  # type: ignore[misc]
            search_filter: SearchFilterQuery = Depends(),
            client: httpx.AsyncClient = Depends(get_es_client),
        ) -> FacetsResponse:

            return await _do_facets(
                client=client,
                index=db_type.value,
                search_filter=search_filter,
                is_cross_type=False,
                db_type=db_type.value,
            )

        _handler.__doc__ = f"Get facet counts for {db_type.value} entries."

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
    )
