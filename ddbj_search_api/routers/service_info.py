from importlib.metadata import version

from fastapi import APIRouter

from ddbj_search_api.schemas import ServiceInfo

router = APIRouter()


@router.get(
    "/service-info",
    response_model=ServiceInfo,
    summary="Service info",
    description="Returns application version and service metadata.",
    tags=["service-info"],
)
async def get_service_info() -> ServiceInfo:
    app_version = version("ddbj-search-api")
    return ServiceInfo(**{"app-version": app_version})
