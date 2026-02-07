"""Service info endpoint: GET /service-info."""
import importlib.metadata

from fastapi import APIRouter

from ddbj_search_api.schemas.service_info import ServiceInfoResponse

router = APIRouter(tags=["Service Info"])


@router.get(
    "/service-info",
    response_model=ServiceInfoResponse,
    summary="Get service information",
    description="Returns service metadata including name and version.",
)
async def get_service_info() -> ServiceInfoResponse:
    """Return service metadata."""
    version = importlib.metadata.version("ddbj-search-api")

    return ServiceInfoResponse(
        name="DDBJ Search API",
        version=version,
        description=(
            "RESTful API for searching and retrieving BioProject, "
            "BioSample, SRA, and JGA entries."
        ),
    )
