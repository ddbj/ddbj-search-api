"""Aggregate all API routers.

Route registration order matters: bulk must come before entry_detail
to avoid path conflicts (``/entries/{type}/bulk`` vs
``/entries/{type}/{id}``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ddbj_search_api.routers import bulk, entries, entry_detail, facets, service_info
from ddbj_search_api.schemas.common import ProblemDetails

# Common error responses (RFC 7807) applied to all endpoints.
PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {
        "description": "Bad Request (e.g. deep paging limit exceeded).",
        "model": ProblemDetails,
    },
    404: {
        "description": "Not Found (entry does not exist or invalid type).",
        "model": ProblemDetails,
    },
    422: {
        "description": "Unprocessable Entity (parameter validation error).",
        "model": ProblemDetails,
    },
    500: {
        "description": "Internal Server Error.",
        "model": ProblemDetails,
    },
}

router = APIRouter(responses=PROBLEM_RESPONSES)
router.include_router(entries.router)
router.include_router(bulk.router)
router.include_router(entry_detail.router)
router.include_router(facets.router)
router.include_router(service_info.router)
