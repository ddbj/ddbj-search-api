"""Bulk API endpoint: POST /entries/{type}/bulk.

Retrieve multiple entries by IDs in JSON Array or NDJSON format.

Design: streaming responses using ``es_get_source_stream`` per ID.
Each document is streamed directly from ES without loading the full
body into API memory, keeping peak memory at one streaming chunk.
"""
import json
from typing import Any, AsyncIterator, List

import httpx
from fastapi import APIRouter, Depends, Path
from fastapi.responses import StreamingResponse

from ddbj_search_api.es import get_es_client
from ddbj_search_api.es.client import es_get_source_stream
from ddbj_search_api.schemas.bulk import BulkRequest, BulkResponse
from ddbj_search_api.schemas.common import DbType
from ddbj_search_api.schemas.queries import BulkFormat, BulkQuery

router = APIRouter(tags=["Bulk"])


# --- Helpers ---


async def _read_source_bytes(response: httpx.Response) -> bytes:
    """Read all bytes from an ES _source stream and strip trailing whitespace.

    ES ``_source`` responses end with ``\\n``; stripping it prevents
    malformed JSON in array mode and empty lines in NDJSON mode.
    """
    chunks: List[bytes] = []
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)
    await response.aclose()

    return b"".join(chunks).rstrip()


# --- Streaming generators ---


async def _generate_bulk_json(
    client: httpx.AsyncClient,
    index: str,
    ids: List[str],
) -> AsyncIterator[bytes]:
    """Stream ``{"entries":[...],"notFound":[...]}`` without loading all docs."""
    yield b'{"entries":['
    not_found: List[str] = []
    first = True

    for id_ in ids:
        response = await es_get_source_stream(client, index, id_)
        if response is None:
            not_found.append(id_)
            continue
        if not first:
            yield b","
        first = False
        yield await _read_source_bytes(response)

    yield b'],"notFound":'
    yield json.dumps(not_found).encode()
    yield b"}"


async def _generate_bulk_ndjson(
    client: httpx.AsyncClient,
    index: str,
    ids: List[str],
) -> AsyncIterator[bytes]:
    """Stream one entry per line. notFound IDs are silently skipped."""
    for id_ in ids:
        response = await es_get_source_stream(client, index, id_)
        if response is None:
            continue
        yield await _read_source_bytes(response)
        yield b"\n"


# --- Endpoint ---


@router.post(
    "/entries/{type}/bulk",
    response_model=BulkResponse,
    summary="Bulk entry retrieval",
    description=(
        "Retrieve multiple entries by their IDs.  Up to 1000 IDs can be "
        "specified per request.\n\n"
        "**format=json** (default): returns ``{entries: [...], "
        "notFound: [...]}``.\n\n"
        "**format=ndjson**: returns one entry per line in NDJSON format "
        "(Content-Type: application/x-ndjson).  IDs not found are not "
        "included in the NDJSON output."
    ),
)
async def bulk_entries(
    body: BulkRequest,
    type: DbType = Path(description="Database type."),
    query: BulkQuery = Depends(),
    client: httpx.AsyncClient = Depends(get_es_client),
) -> Any:
    """Bulk retrieve entries by IDs."""
    index = type.value

    if query.format == BulkFormat.ndjson:
        return StreamingResponse(
            _generate_bulk_ndjson(client, index, body.ids),
            media_type="application/x-ndjson",
        )

    return StreamingResponse(
        _generate_bulk_json(client, index, body.ids),
        media_type="application/json",
    )
