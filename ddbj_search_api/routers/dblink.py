"""DBLinks API router -- accession cross-reference lookups via DuckDB."""

from __future__ import annotations

import asyncio
import collections.abc
import json
import logging
import queue
import threading

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import StreamingResponse

from ddbj_search_api.config import DBLINK_DB_PATH
from ddbj_search_api.dblink.client import count_linked_ids_bulk, iter_linked_ids
from ddbj_search_api.schemas.common import ProblemDetails
from ddbj_search_api.schemas.dblink import (
    AccessionType,
    DbLinksCountsRequest,
    DbLinksCountsResponse,
    DbLinksCountsResponseItem,
    DbLinksQuery,
    DbLinksResponse,
    DbLinksTypesResponse,
)
from ddbj_search_api.utils import format_xref

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dblink", tags=["dblink"])


@router.get(
    "/",
    response_model=DbLinksTypesResponse,
    summary="List available accession types",
    operation_id="listDbLinkTypes",
)
@router.get(
    "",
    response_model=DbLinksTypesResponse,
    include_in_schema=False,
)
def list_types() -> DbLinksTypesResponse:
    """Return all available AccessionType values (static, no DB required)."""

    return DbLinksTypesResponse(types=sorted(AccessionType, key=lambda t: t.value))


@router.get(
    "/{type}/{id}",
    response_model=DbLinksResponse,
    summary="Get linked accessions",
    operation_id="getDbLinks",
    responses={
        422: {
            "description": "Unprocessable Entity (invalid {type} or target).",
            "model": ProblemDetails,
        },
    },
)
async def get_links(
    type: AccessionType = Path(description="Source accession type."),
    id: str = Path(description="Source accession identifier."),
    query: DbLinksQuery = Depends(),
) -> StreamingResponse:
    """Look up related accessions for the given type/id pair (streaming)."""
    # Pre-check DB existence before streaming
    if not DBLINK_DB_PATH.exists():
        logger.error("DuckDB file not found: %s", DBLINK_DB_PATH)
        raise HTTPException(
            status_code=500,
            detail=f"dblink database is not available: {DBLINK_DB_PATH}",
        )

    target_values: list[str] | None = None
    if query.target is not None:
        target_values = [t.value for t in query.target]

    async def _stream() -> collections.abc.AsyncIterator[bytes]:
        header = '{"identifier":' + json.dumps(id) + ',"type":' + json.dumps(type.value) + ',"dbXrefs":['
        yield header.encode("utf-8")

        q: queue.Queue[list[tuple[str, str]] | None] = queue.Queue(maxsize=2)

        def _worker() -> None:
            try:
                batch: list[tuple[str, str]] = []
                for row in iter_linked_ids(DBLINK_DB_PATH, type.value, id, target=target_values):
                    batch.append(row)
                    if len(batch) >= 10000:
                        q.put(batch)
                        batch = []
                if batch:
                    q.put(batch)
            except FileNotFoundError:
                logger.exception("DuckDB file not found: %s", DBLINK_DB_PATH)
            finally:
                q.put(None)

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

        first = True
        while True:
            item = await asyncio.to_thread(q.get)
            if item is None:
                break
            for t, acc in item:
                if not first:
                    yield b","
                first = False
                yield format_xref(t, acc).encode("utf-8")

        thread.join()

        yield b"]}"

    return StreamingResponse(
        _stream(),
        media_type="application/json",
    )


@router.post(
    "/counts",
    response_model=DbLinksCountsResponse,
    summary="Bulk count linked accessions",
    operation_id="bulkDbLinkCounts",
    responses={
        422: {
            "description": "Unprocessable Entity (empty / oversized items, invalid type).",
            "model": ProblemDetails,
        },
    },
)
async def bulk_counts(
    body: DbLinksCountsRequest,
) -> DbLinksCountsResponse:
    """Return per-type counts for multiple accessions in one request."""
    if not DBLINK_DB_PATH.exists():
        logger.error("DuckDB file not found: %s", DBLINK_DB_PATH)
        raise HTTPException(
            status_code=500,
            detail=f"dblink database is not available: {DBLINK_DB_PATH}",
        )

    entries = [(item.type.value, item.id) for item in body.items]
    counts = await asyncio.to_thread(
        count_linked_ids_bulk,
        DBLINK_DB_PATH,
        entries,
    )

    items = []
    for item in body.items:
        key = (item.type.value, item.id)
        items.append(
            DbLinksCountsResponseItem(
                identifier=item.id,
                type=item.type,
                counts=counts.get(key, {}),
            )
        )

    return DbLinksCountsResponse(items=items)
