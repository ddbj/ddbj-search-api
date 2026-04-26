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
from ddbj_search_api.es.client import es_get_source_stream, es_mget_source
from ddbj_search_api.schemas.bulk import BulkRequest, BulkResponse
from ddbj_search_api.schemas.common import DbType, ProblemDetails
from ddbj_search_api.schemas.queries import BulkFormat, BulkQuery
from ddbj_search_api.utils import format_xref

_VISIBLE_STATUSES = ("public", "suppressed")


async def _resolve_visible_ids(
    client: httpx.AsyncClient,
    index: str,
    ids: list[str],
) -> tuple[list[str], list[str]]:
    """Batch-classify bulk IDs by visibility using a single ``_mget`` call.

    Returns ``(visible_ids, hidden_ids)`` where ``visible_ids`` is the
    subset whose status is ``public`` or ``suppressed`` (in the original
    input order), and ``hidden_ids`` contains the rest (missing,
    ``withdrawn``, ``private``, or unknown). See
    ``docs/api-spec.md`` § データ可視性 (status 制御).
    """
    if not ids:
        return [], []
    sources = await es_mget_source(client, index, ids, source_includes=["status"])
    visible: list[str] = []
    hidden: list[str] = []
    for id_ in ids:
        src = sources.get(id_)
        if src is not None and src.get("status") in _VISIBLE_STATUSES:
            visible.append(id_)
        else:
            hidden.append(id_)
    return visible, hidden


logger = logging.getLogger(__name__)

router = APIRouter(tags=["Bulk"])


# --- Helpers ---


async def _read_source_bytes(response: httpx.Response) -> bytes:
    """Read all bytes from an ES _source stream and strip trailing whitespace.

    ES ``_source`` responses end with ``\\n``; stripping it prevents
    malformed JSON in array mode and empty lines in NDJSON mode.
    """
    try:
        chunks: list[bytes] = []
        async for chunk in response.aiter_bytes():
            chunks.append(chunk)
        return b"".join(chunks).rstrip()
    finally:
        await response.aclose()


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
    """Stream ``{"entries":[...],"notFound":[...]}`` without loading all docs.

    Before streaming entries, a single ``_mget`` call classifies IDs by
    visibility (``status``): ``withdrawn`` / ``private`` and missing IDs
    are all reported via ``notFound`` (docs/api-spec.md § データ可視性).
    """
    visible_ids, hidden_ids = await _resolve_visible_ids(client, index, ids)

    yield b'{"entries":['
    not_found: list[str] = list(hidden_ids)
    first = True

    for id_ in visible_ids:
        response = await es_get_source_stream(
            client,
            index,
            id_,
            source_excludes="dbXrefs",
        )
        if response is None:
            # Race condition: doc deleted between visibility check and stream.
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
    """Stream one entry per line. Missing / withdrawn / private IDs are
    silently skipped (docs/api-spec.md § データ可視性).
    """
    visible_ids, _hidden_ids = await _resolve_visible_ids(client, index, ids)

    for id_ in visible_ids:
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
    operation_id="bulkEntries",
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
        404: {
            "description": "Not Found (invalid {type}).",
            "model": ProblemDetails,
        },
        422: {
            "description": "Unprocessable Entity (parameter validation error).",
            "model": ProblemDetails,
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

    # Dedup ids while preserving first-seen order (api-spec.md § Bulk API).
    seen: set[str] = set()
    deduped_ids: list[str] = []
    for i in body.ids:
        if i not in seen:
            seen.add(i)
            deduped_ids.append(i)

    if query.format == BulkFormat.ndjson:
        return StreamingResponse(
            _generate_bulk_ndjson(client, index, deduped_ids, acc_type, query.include_db_xrefs),
            media_type="application/x-ndjson",
        )

    return StreamingResponse(
        _generate_bulk_json(client, index, deduped_ids, acc_type, query.include_db_xrefs),
        media_type="application/json",
    )
