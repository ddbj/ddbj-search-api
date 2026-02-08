"""Service info endpoint: GET /service-info."""
import importlib.metadata

import httpx
from fastapi import APIRouter, Depends

from ddbj_search_api.es import get_es_client
from ddbj_search_api.es.client import es_ping
from ddbj_search_api.schemas.service_info import ServiceInfoResponse

router = APIRouter(tags=["Service Info"])


@router.get(
    "/service-info",
    response_model=ServiceInfoResponse,
    summary="Get service information",
    description=(
        "Returns service metadata including name, version, "
        "and Elasticsearch connectivity status."
    ),
)
async def get_service_info(
    client: httpx.AsyncClient = Depends(get_es_client),
) -> ServiceInfoResponse:
    """Return service metadata with ES health status."""
    version = importlib.metadata.version("ddbj-search-api")
    is_healthy = await es_ping(client)

    return ServiceInfoResponse(
        name="DDBJ Search API",
        version=version,
        description=(
            "RESTful API for searching and retrieving BioProject, "
            "BioSample, SRA, and JGA entries."
        ),
        elasticsearch="ok" if is_healthy else "unavailable",
    )
