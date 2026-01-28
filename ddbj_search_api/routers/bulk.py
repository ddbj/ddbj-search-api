from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from ddbj_search_api.schemas import BulkRequest, DbType, ProblemDetails

router = APIRouter()


@router.get(
    "/entries/{type}/bulk",
    summary="Bulk get (GET)",
    description="Bulk retrieve entries by IDs (GET). Returns entries in JSON Lines (NDJSON) format. Pass comma-separated IDs as a query parameter.",
    responses={
        200: {
            "content": {"application/x-ndjson": {"schema": {"type": "string"}}},
            "description": "Entries in JSON Lines format",
        },
        400: {"model": ProblemDetails},
        500: {"model": ProblemDetails},
    },
    response_class=StreamingResponse,
    tags=["entries"],
)
async def bulk_get_entries(
    type: DbType,
    ids: str = Query(..., description="Entry IDs (comma-separated)"),
) -> StreamingResponse:
    # TODO: Phase 2 - Implement ES mget

    async def empty_generator():  # type: ignore[return]
        return
        yield  # noqa: unreachable  # make it an async generator

    return StreamingResponse(
        content=empty_generator(),
        media_type="application/x-ndjson",
    )


@router.post(
    "/entries/{type}/bulk",
    summary="Bulk get (POST)",
    description="Bulk retrieve entries by IDs (POST). Returns entries in JSON Lines (NDJSON) format. Pass IDs in the request body.",
    responses={
        200: {
            "content": {"application/x-ndjson": {"schema": {"type": "string"}}},
            "description": "Entries in JSON Lines format",
        },
        400: {"model": ProblemDetails},
        500: {"model": ProblemDetails},
    },
    response_class=StreamingResponse,
    tags=["entries"],
)
async def bulk_post_entries(
    type: DbType,
    body: BulkRequest,
) -> StreamingResponse:
    # TODO: Phase 2 - Implement ES mget

    async def empty_generator():  # type: ignore[return]
        return
        yield  # noqa: unreachable  # make it an async generator

    return StreamingResponse(
        content=empty_generator(),
        media_type="application/x-ndjson",
    )
