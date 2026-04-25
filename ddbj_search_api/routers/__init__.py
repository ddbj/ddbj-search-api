"""Aggregate all API routers.

Route registration order matters: both ``bulk`` and ``umbrella_tree``
must come before ``entry_detail`` to avoid path conflicts
(``/entries/{type}/bulk`` and ``/entries/bioproject/{accession}/umbrella-tree``
vs. the generic ``/entries/{type}/{id}``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ddbj_search_api.routers import bulk, db_portal, dblink, entries, entry_detail, facets, service_info, umbrella_tree
from ddbj_search_api.schemas.common import ProblemDetails

# Only 500 is shared by every endpoint; status-specific errors (400 / 404 /
# 422) are declared per route so the OpenAPI document precisely reflects
# what each endpoint actually returns.  Each error body is application/
# problem+json (rewritten by ``main.custom_openapi``).
PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    500: {
        "description": "Internal Server Error.",
        "model": ProblemDetails,
    },
}

# Per-status response stanzas reused across routers (see PROBLEM_RESPONSES
# rationale).  Importing modules pick whichever subset applies.
PROBLEM_400: dict[int | str, dict[str, Any]] = {
    400: {
        "description": "Bad Request (e.g. deep paging limit exceeded, mutually exclusive query params).",
        "model": ProblemDetails,
    },
}
PROBLEM_404: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Not Found (entry does not exist or invalid {type}).",
        "model": ProblemDetails,
    },
}
PROBLEM_422: dict[int | str, dict[str, Any]] = {
    422: {
        "description": "Unprocessable Entity (parameter validation error).",
        "model": ProblemDetails,
    },
}

router = APIRouter(responses=PROBLEM_RESPONSES)
router.include_router(entries.router)
router.include_router(bulk.router)
router.include_router(umbrella_tree.router)
router.include_router(entry_detail.router)
router.include_router(facets.router)
router.include_router(dblink.router)
router.include_router(db_portal.router)
router.include_router(service_info.router)
