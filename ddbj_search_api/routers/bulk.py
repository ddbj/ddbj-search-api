"""Bulk API endpoint: POST /entries/{type}/bulk.

Retrieve multiple entries by IDs in JSON Array or NDJSON format.

Design: streaming responses using ``es_get_source_stream`` per ID.
Each document is streamed directly from ES without loading the full
body into API memory, keeping peak memory at one streaming chunk.
DuckDB dbXrefs are injected into each entry's JSON.
"""

from __future__ import annotations

import asyncio
import collections.abc
import json
import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import StreamingResponse

from ddbj_search_api.config import DBLINK_DB_PATH
from ddbj_search_api.dblink.client import iter_linked_ids
from ddbj_search_api.es import get_es_client
from ddbj_search_api.es.client import es_get_source_stream
from ddbj_search_api.schemas.bulk import BulkRequest, BulkResponse
from ddbj_search_api.schemas.common import DbType
from ddbj_search_api.schemas.queries import BulkFormat, BulkQuery
from ddbj_search_api.utils import format_xref

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Bulk"])


# --- Helpers ---


async def _read_source_bytes(response: httpx.Response) -> bytes:
    """Read all bytes from an ES _source stream and strip trailing whitespace.

    ES ``_source`` responses end with ``\\n``; stripping it prevents
    malformed JSON in array mode and empty lines in NDJSON mode.
    """
    chunks: list[bytes] = []
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)
    await response.aclose()

    return b"".join(chunks).rstrip()


async def _inject_dbxrefs_into_bytes(
    source_bytes: bytes,
    acc_type: str,
    entry_id: str,
) -> bytes:
    """Inject dbXrefs from DuckDB into an ES source JSON bytes object."""
    text = source_bytes.decode("utf-8")
    brace_pos = text.rfind("}")
    if brace_pos == -1:
        return source_bytes

    def _fetch_all() -> list[tuple[str, str]]:
        return list(iter_linked_ids(DBLINK_DB_PATH, acc_type, entry_id))

    rows = await asyncio.to_thread(_fetch_all)
    xrefs_parts = [format_xref(t, acc) for t, acc in rows]
    db_xrefs_json = ',"dbXrefs":[' + ",".join(xrefs_parts) + "]"
    text = text[:brace_pos] + db_xrefs_json + text[brace_pos:]

    return text.encode("utf-8")


# --- Streaming generators ---


async def _generate_bulk_json(
    client: httpx.AsyncClient,
    index: str,
    ids: list[str],
    acc_type: str,
    include_db_xrefs: bool = True,
) -> collections.abc.AsyncIterator[bytes]:
    """Stream ``{"entries":[...],"notFound":[...]}`` without loading all docs."""
    yield b'{"entries":['
    not_found: list[str] = []
    first = True

    for id_ in ids:
        response = await es_get_source_stream(
            client,
            index,
            id_,
            source_excludes="dbXrefs",
        )
        if response is None:
            not_found.append(id_)
            continue
        if not first:
            yield b","
        first = False
        source_bytes = await _read_source_bytes(response)
        if include_db_xrefs:
            yield await _inject_dbxrefs_into_bytes(source_bytes, acc_type, id_)
        else:
            yield source_bytes

    yield b'],"notFound":'
    yield json.dumps(not_found).encode()
    yield b"}"


async def _generate_bulk_ndjson(
    client: httpx.AsyncClient,
    index: str,
    ids: list[str],
    acc_type: str,
    include_db_xrefs: bool = True,
) -> collections.abc.AsyncIterator[bytes]:
    """Stream one entry per line. notFound IDs are silently skipped."""
    for id_ in ids:
        response = await es_get_source_stream(
            client,
            index,
            id_,
            source_excludes="dbXrefs",
        )
        if response is None:
            continue
        source_bytes = await _read_source_bytes(response)
        if include_db_xrefs:
            yield await _inject_dbxrefs_into_bytes(source_bytes, acc_type, id_)
        else:
            yield source_bytes
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
    responses={
        200: {
            "content": {
                "application/x-ndjson": {
                    "schema": {
                        "type": "string",
                        "description": ("One JSON object per line (NDJSON). Each line is an entry document."),
                    },
                },
            },
        },
    },
)
async def bulk_entries(
    body: BulkRequest,
    type: DbType = Path(description="Database type."),
    query: BulkQuery = Depends(),
    client: httpx.AsyncClient = Depends(get_es_client),
) -> Any:
    """Bulk retrieve entries by IDs."""
    if query.include_db_xrefs and not DBLINK_DB_PATH.exists():
        logger.error("DuckDB file not found: %s", DBLINK_DB_PATH)
        raise HTTPException(
            status_code=500,
            detail=f"dblink database is not available: {DBLINK_DB_PATH}",
        )

    index = type.value
    acc_type = type.value

    if query.format == BulkFormat.ndjson:
        return StreamingResponse(
            _generate_bulk_ndjson(client, index, body.ids, acc_type, query.include_db_xrefs),
            media_type="application/x-ndjson",
        )

    return StreamingResponse(
        _generate_bulk_json(client, index, body.ids, acc_type, query.include_db_xrefs),
        media_type="application/json",
    )
