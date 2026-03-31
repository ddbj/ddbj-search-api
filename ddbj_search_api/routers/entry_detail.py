"""Entry detail endpoints.

- GET /entries/{type}/{id}.json         — raw ES document (streaming, dbXrefs from DuckDB)
- GET /entries/{type}/{id}.jsonld       — JSON-LD format (streaming, dbXrefs from DuckDB)
- GET /entries/{type}/{id}/dbxrefs.json — full dbXrefs (DuckDB streaming)
- GET /entries/{type}/{id}              — frontend-oriented (truncated dbXrefs from DuckDB)

Routes with file extensions (.json, .jsonld) and sub-paths (/dbxrefs.json)
are registered BEFORE the bare ``/{id}`` route so that FastAPI matches
the more specific patterns first.
"""

from __future__ import annotations

import asyncio
import collections.abc
import json
import queue
import threading
from typing import Any, cast

import httpx
from ddbj_search_converter.jsonl.utils import to_xref
from ddbj_search_converter.schema import XrefType
from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from ddbj_search_api.config import DBLINK_DB_PATH, JSONLD_CONTEXT_URLS, get_config
from ddbj_search_api.dblink.client import count_linked_ids, get_linked_ids_limited, iter_linked_ids
from ddbj_search_api.es import get_es_client
from ddbj_search_api.es.client import es_get_identifier, es_get_source_stream, es_head_exists, es_resolve_same_as
from ddbj_search_api.schemas.common import DbType
from ddbj_search_api.schemas.dbxrefs import DbXrefsFullResponse
from ddbj_search_api.schemas.entries import DetailResponse, EntryJsonLdResponse, EntryResponse
from ddbj_search_api.schemas.queries import EntryDetailQuery
from ddbj_search_api.utils import format_xref

router = APIRouter(tags=["Entry Detail"])


# --- Helper: sameAs ID resolution ---


async def _get_source_with_fallback(
    client: httpx.AsyncClient,
    db_type: str,
    id_: str,
    source_excludes: str | None = None,
) -> tuple[httpx.Response, str]:
    """Get ES source stream, falling back to sameAs resolution.

    Returns ``(response, entry_id)`` where *entry_id* is the resolved
    primary identifier (same as *id_* when the direct lookup succeeds).

    Raises :class:`HTTPException` 404 if neither lookup finds a match.
    """
    response = await es_get_source_stream(client, db_type, id_, source_excludes=source_excludes)
    if response is not None:
        entry_id = await es_get_identifier(client, db_type, id_)
        return response, entry_id

    resolved_id = await es_resolve_same_as(client, db_type, id_)
    if resolved_id is None:
        raise HTTPException(
            status_code=404,
            detail=f"The requested {db_type} '{id_}' was not found.",
        )

    response = await es_get_source_stream(client, db_type, resolved_id, source_excludes=source_excludes)
    if response is None:
        raise HTTPException(
            status_code=404,
            detail=f"The requested {db_type} '{id_}' was not found.",
        )

    return response, resolved_id


async def _check_exists_with_fallback(
    client: httpx.AsyncClient,
    db_type: str,
    id_: str,
) -> str:
    """Check entry existence, falling back to sameAs resolution.

    Returns the resolved primary identifier.

    Raises :class:`HTTPException` 404 if neither lookup finds a match.
    """
    if await es_head_exists(client, db_type, id_):
        return await es_get_identifier(client, db_type, id_)

    resolved_id = await es_resolve_same_as(client, db_type, id_)
    if resolved_id is None:
        raise HTTPException(
            status_code=404,
            detail=f"The requested {db_type} '{id_}' was not found.",
        )

    return resolved_id


class JsonLdResponse(JSONResponse):
    """JSONResponse subclass with application/ld+json media type."""

    media_type = "application/ld+json"


# --- Helper: JSON-LD prefix injection ---


async def _inject_jsonld_prefix(
    stream: collections.abc.AsyncIterator[bytes],
    context_url: str,
    at_id: str,
) -> collections.abc.AsyncIterator[bytes]:
    """Inject ``@context`` and ``@id`` into the first ``{`` of a JSON stream.

    Replaces the leading ``{`` with
    ``{"@context":"...","@id":"...",`` and passes through the rest.
    """
    prefix = '{"@context":' + json.dumps(context_url) + ',"@id":' + json.dumps(at_id) + ","
    injected = False

    async for chunk in stream:
        if not injected:
            text = chunk.decode("utf-8")
            brace_pos = text.find("{")
            if brace_pos != -1:
                text = text[:brace_pos] + prefix + text[brace_pos + 1 :]
                injected = True
            yield text.encode("utf-8")
        else:
            yield chunk


# --- Helper: dbXrefs tail injection ---


async def _inject_dbxrefs_tail_streaming(
    stream: collections.abc.AsyncIterator[bytes],
    db_type: str,
    entry_id: str,
) -> collections.abc.AsyncIterator[bytes]:
    """Inject ``,"dbXrefs":[...]`` before the closing ``}`` of a JSON stream.

    Streams DuckDB rows in chunks to avoid loading all rows into memory.
    Uses a one-chunk-behind buffer for the ES stream.

    Thread safety: DuckDB generator creation and consumption happen
    entirely within a dedicated worker thread via ``threading.Queue``.
    """
    prev: bytes | None = None

    async for chunk in stream:
        if prev is not None:
            yield prev
        prev = chunk

    if prev is None:
        return

    text = prev.decode("utf-8")
    brace_pos = text.rfind("}")
    if brace_pos == -1:
        yield prev

        return

    # Emit everything before the closing brace + start of dbXrefs array
    yield (text[:brace_pos] + ',"dbXrefs":[').encode("utf-8")

    # Stream DuckDB rows via a dedicated worker thread
    q: queue.Queue[list[tuple[str, str]] | None] = queue.Queue(maxsize=2)

    def _worker() -> None:
        try:
            batch: list[tuple[str, str]] = []
            for row in iter_linked_ids(DBLINK_DB_PATH, db_type, entry_id):
                batch.append(row)
                if len(batch) >= 10000:
                    q.put(batch)
                    batch = []
            if batch:
                q.put(batch)
        finally:
            q.put(None)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

    first = True
    while True:
        item = await asyncio.to_thread(q.get)
        if item is None:
            break
        for type_, acc in item:
            if not first:
                yield b","
            first = False
            yield format_xref(type_, acc).encode("utf-8")

    thread.join()

    # Close the array and the object
    yield ("]" + text[brace_pos:]).encode("utf-8")


# --- GET /entries/{type}/{id}.json ---
# Registered before /{id} to prevent {id} from matching "X.json".


@router.get(
    "/entries/{type}/{id}.json",
    response_model=EntryResponse,
    summary="Get raw entry (ES document)",
    description=(
        "Retrieve the raw Elasticsearch document for an entry.  "
        "All fields including the full ``dbXrefs`` are returned as-is.\n\n"
        "The response type varies by database type: "
        "BioProject, BioSample, SRA, or JGA."
    ),
)
async def get_entry_json(
    type: DbType = Path(description="Database type."),
    id: str = Path(description="Entry accession identifier."),
    client: httpx.AsyncClient = Depends(get_es_client),
) -> StreamingResponse:
    """Get raw ES document + DuckDB dbXrefs (streaming)."""
    response, entry_id = await _get_source_with_fallback(
        client,
        type.value,
        id,
        source_excludes="dbXrefs",
    )

    body = _inject_dbxrefs_tail_streaming(
        response.aiter_bytes(),
        type.value,
        entry_id,
    )

    return StreamingResponse(
        body,
        media_type="application/json",
        background=BackgroundTask(response.aclose),
    )


# --- GET /entries/{type}/{id}.jsonld ---


@router.get(
    "/entries/{type}/{id}.jsonld",
    response_model=EntryJsonLdResponse,
    response_class=JsonLdResponse,
    summary="Get entry in JSON-LD format",
    description=(
        "Retrieve an entry in JSON-LD format with ``@context`` and "
        "``@id`` fields added for RDF compatibility.\n\n"
        "Content-Type: application/ld+json."
    ),
)
async def get_entry_jsonld(
    type: DbType = Path(description="Database type."),
    id: str = Path(description="Entry accession identifier."),
    client: httpx.AsyncClient = Depends(get_es_client),
) -> StreamingResponse:
    """Get entry as JSON-LD (streaming with @context/@id + dbXrefs injection)."""
    response, entry_id = await _get_source_with_fallback(
        client,
        type.value,
        id,
        source_excludes="dbXrefs",
    )

    config = get_config()
    context_url = JSONLD_CONTEXT_URLS[type.value]
    at_id = f"{config.base_url}/entries/{type.value}/{entry_id}"

    # Chain: ES stream → dbXrefs tail injection → JSON-LD prefix injection
    with_dbxrefs = _inject_dbxrefs_tail_streaming(
        response.aiter_bytes(),
        type.value,
        entry_id,
    )
    body = _inject_jsonld_prefix(with_dbxrefs, context_url, at_id)

    return StreamingResponse(
        body,
        media_type="application/ld+json",
        background=BackgroundTask(response.aclose),
    )


# --- GET /entries/{type}/{id}/dbxrefs.json ---


@router.get(
    "/entries/{type}/{id}/dbxrefs.json",
    response_model=DbXrefsFullResponse,
    summary="Get all dbXrefs",
    description="Retrieve all cross-references for an entry in one response.",
)
async def get_dbxrefs_full(
    type: DbType = Path(description="Database type."),
    id: str = Path(description="Entry accession identifier."),
    client: httpx.AsyncClient = Depends(get_es_client),
) -> StreamingResponse:
    """Get all dbXrefs (DuckDB streaming, ES HEAD for existence check)."""
    entry_id = await _check_exists_with_fallback(client, type.value, id)

    async def _stream_dbxrefs() -> collections.abc.AsyncIterator[bytes]:
        yield b'{"dbXrefs":['

        q: queue.Queue[list[tuple[str, str]] | None] = queue.Queue(maxsize=2)

        def _worker() -> None:
            try:
                batch: list[tuple[str, str]] = []
                for row in iter_linked_ids(DBLINK_DB_PATH, type.value, entry_id):
                    batch.append(row)
                    if len(batch) >= 10000:
                        q.put(batch)
                        batch = []
                if batch:
                    q.put(batch)
            finally:
                q.put(None)

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

        first = True
        while True:
            item = await asyncio.to_thread(q.get)
            if item is None:
                break
            for type_, acc in item:
                if not first:
                    yield b","
                first = False
                yield format_xref(type_, acc).encode("utf-8")

        thread.join()

        yield b"]}"

    return StreamingResponse(
        _stream_dbxrefs(),
        media_type="application/json",
    )


# --- GET /entries/{type}/{id} ---
# Registered last: catch-all for bare IDs (no extension).


@router.get(
    "/entries/{type}/{id}",
    response_model=DetailResponse,
    summary="Get entry detail (frontend-oriented)",
    description=(
        "Retrieve a single entry with dbXrefs truncated by "
        "``dbXrefsLimit`` and ``dbXrefsCount`` added.  Use this endpoint "
        "for frontend display.  For the raw ES document, use the "
        "``.json`` variant.\n\n"
        "The response type varies by database type: "
        "BioProjectDetailResponse, BioSampleDetailResponse, "
        "SraDetailResponse, or JgaDetailResponse."
    ),
)
async def get_entry_detail(
    type: DbType = Path(description="Database type."),
    id: str = Path(description="Entry accession identifier."),
    query: EntryDetailQuery = Depends(),
    client: httpx.AsyncClient = Depends(get_es_client),
) -> JSONResponse:
    """Get entry detail (truncated dbXrefs from DuckDB + dbXrefsCount)."""
    response, entry_id = await _get_source_with_fallback(
        client,
        type.value,
        id,
        source_excludes="dbXrefs",
    )

    # Read ES response body (without dbXrefs)
    chunks: list[bytes] = []
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)
    await response.aclose()
    source: dict[str, Any] = json.loads(b"".join(chunks))

    # Get dbXrefs from DuckDB (parallel)
    limit = query.db_xrefs_limit

    xrefs_rows, counts = await asyncio.gather(
        asyncio.to_thread(get_linked_ids_limited, DBLINK_DB_PATH, type.value, entry_id, limit),
        asyncio.to_thread(count_linked_ids, DBLINK_DB_PATH, type.value, entry_id),
    )

    xrefs = [to_xref(acc, type_hint=cast(XrefType, t)).model_dump(by_alias=True) for t, acc in xrefs_rows]
    source["dbXrefs"] = xrefs
    source["dbXrefsCount"] = counts

    return JSONResponse(content=source)
