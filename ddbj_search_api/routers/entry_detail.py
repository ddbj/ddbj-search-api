"""Entry detail endpoints.

- GET /entries/{type}/{id}.json         — raw ES document (streaming)
- GET /entries/{type}/{id}.jsonld       — JSON-LD format (streaming)
- GET /entries/{type}/{id}/dbxrefs.json — full dbXrefs (streaming)
- GET /entries/{type}/{id}              — frontend-oriented (truncated dbXrefs)

Routes with file extensions (.json, .jsonld) and sub-paths (/dbxrefs.json)
are registered BEFORE the bare ``/{id}`` route so that FastAPI matches
the more specific patterns first.
"""
import json
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from ddbj_search_api.config import JSONLD_CONTEXT_URLS, get_config
from ddbj_search_api.es import get_es_client
from ddbj_search_api.es.client import (es_get_source_stream,
                                       es_search_with_script_fields)
from ddbj_search_api.schemas.common import DbType
from ddbj_search_api.schemas.dbxrefs import DbXrefsFullResponse
from ddbj_search_api.schemas.entries import (DetailResponse,
                                             EntryJsonLdResponse,
                                             EntryResponse)
from ddbj_search_api.schemas.queries import EntryDetailQuery

router = APIRouter(tags=["Entry Detail"])


class JsonLdResponse(JSONResponse):
    """JSONResponse subclass with application/ld+json media type."""

    media_type = "application/ld+json"


# --- Helper: JSON-LD prefix injection ---

async def _inject_jsonld_prefix(
    stream: AsyncIterator[bytes],
    context_url: str,
    at_id: str,
) -> AsyncIterator[bytes]:
    """Inject ``@context`` and ``@id`` into the first ``{`` of a JSON stream.

    Replaces the leading ``{`` with
    ``{"@context":"...","@id":"...",`` and passes through the rest.
    """
    prefix = (
        '{"@context":' + json.dumps(context_url)
        + ',"@id":' + json.dumps(at_id) + ","
    )
    injected = False

    async for chunk in stream:
        if not injected:
            text = chunk.decode("utf-8")
            brace_pos = text.find("{")
            if brace_pos != -1:
                text = text[:brace_pos] + prefix + text[brace_pos + 1:]
                injected = True
            yield text.encode("utf-8")
        else:
            yield chunk


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
    """Get raw ES document (streaming)."""
    response = await es_get_source_stream(client, type.value, id)
    if response is None:
        raise HTTPException(status_code=404, detail="Not Found")

    return StreamingResponse(
        response.aiter_bytes(),
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
    """Get entry as JSON-LD (streaming with @context/@id injection)."""
    response = await es_get_source_stream(client, type.value, id)
    if response is None:
        raise HTTPException(status_code=404, detail="Not Found")

    config = get_config()
    context_url = JSONLD_CONTEXT_URLS[type.value]
    at_id = f"{config.base_url}/entries/{type.value}/{id}"

    body = _inject_jsonld_prefix(
        response.aiter_bytes(), context_url, at_id,
    )

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
    """Get all dbXrefs (streaming)."""
    response = await es_get_source_stream(
        client, type.value, id, source_includes="dbXrefs",
    )
    if response is None:
        raise HTTPException(status_code=404, detail="Not Found")

    return StreamingResponse(
        response.aiter_bytes(),
        media_type="application/json",
        background=BackgroundTask(response.aclose),
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
    """Get entry detail (truncated dbXrefs + dbXrefsCount)."""
    source = await es_search_with_script_fields(
        client, type.value, id, query.db_xrefs_limit,
    )
    if source is None:
        raise HTTPException(status_code=404, detail="Not Found")

    return JSONResponse(content=source)
