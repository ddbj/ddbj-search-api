"""Bulk API endpoint: POST /entries/{type}/bulk.

Retrieve multiple entries by IDs in JSON Array or NDJSON format.

Design: one `_mget` call classifies visibility, one DuckDB bulk query
collects every visible entry's dbXrefs, and ES bodies are fetched in
``_BULK_CHUNK_SIZE``-sized ``_mget`` batches.  Each batch is streamed
to the client as soon as it arrives, so peak memory is bounded by the
chunk's `_source` total plus the (one-shot) dbXrefs map rather than
the entire result set.
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
from ddbj_search_api.dblink.client import get_linked_ids_bulk
from ddbj_search_api.es import get_es_client
from ddbj_search_api.es.client import es_mget_source
from ddbj_search_api.schemas.bulk import BulkRequest, BulkResponse
from ddbj_search_api.schemas.common import DbType, ProblemDetails
from ddbj_search_api.schemas.queries import BulkFormat, BulkQuery
from ddbj_search_api.utils import format_xref_dict

_VISIBLE_STATUSES = ("public", "suppressed")

# Number of IDs per `_mget` batch when fetching bodies.  50 keeps the
# JSON payload per request below a few MB (typical _source is tens of KB)
# while reducing N=1000 round-trips from 1000 to 20.
_BULK_CHUNK_SIZE = 50


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


async def _fetch_all_dbxrefs(
    visible_ids: list[str],
    acc_type: str,
) -> dict[str, list[tuple[str, str]]]:
    """Return ``{id: [(linked_type, accession), ...]}`` for every visible id.

    One DuckDB query for every visible id; replaces the N per-id
    ``iter_linked_ids`` calls that dominated wall-clock time for large
    bulk requests.
    """
    if not visible_ids:
        return {}
    entries = [(acc_type, id_) for id_ in visible_ids]
    bulk_result = await asyncio.to_thread(get_linked_ids_bulk, DBLINK_DB_PATH, entries)
    return {id_: bulk_result.get((acc_type, id_), []) for id_ in visible_ids}


def _serialize_entry(
    source: dict[str, Any],
    entry_id: str,
    include_db_xrefs: bool,
    dbxrefs_map: dict[str, list[tuple[str, str]]],
) -> bytes:
    """Inject dbXrefs into ``source`` and return UTF-8 JSON bytes.

    Mutates ``source`` (already a one-shot dict owned by the caller),
    then ``json.dumps`` once -- no string splice, no double-encode.
    """
    if include_db_xrefs:
        source["dbXrefs"] = [format_xref_dict(t, acc) for t, acc in dbxrefs_map.get(entry_id, [])]
    return json.dumps(source, ensure_ascii=False).encode("utf-8")


# --- Streaming generators ---


async def _generate_bulk_json(
    client: httpx.AsyncClient,
    index: str,
    ids: list[str],
    acc_type: str,
    include_db_xrefs: bool = True,
) -> collections.abc.AsyncIterator[bytes]:
    """Stream ``{"entries":[...],"notFound":[...]}`` from chunked `_mget`.

    Visibility is resolved up front (one `_mget`), dbXrefs are fetched
    in a single DuckDB bulk query, and bodies are pulled in
    ``_BULK_CHUNK_SIZE``-sized `_mget` batches.  ``withdrawn`` / ``private``
    / missing ids and any ids deleted between visibility check and body
    fetch all land in ``notFound`` (docs/api-spec.md § データ可視性).
    """
    visible_ids, hidden_ids = await _resolve_visible_ids(client, index, ids)
    dbxrefs_map = await _fetch_all_dbxrefs(visible_ids, acc_type) if include_db_xrefs else {}

    yield b'{"entries":['
    not_found: list[str] = list(hidden_ids)
    first = True

    for chunk_start in range(0, len(visible_ids), _BULK_CHUNK_SIZE):
        chunk_ids = visible_ids[chunk_start : chunk_start + _BULK_CHUNK_SIZE]
        sources = await es_mget_source(
            client,
            index,
            chunk_ids,
            source_excludes=["dbXrefs"],
        )
        for id_ in chunk_ids:
            src = sources.get(id_)
            if src is None:
                # Race: doc deleted between visibility check and body fetch.
                not_found.append(id_)
                continue
            if not first:
                yield b","
            first = False
            yield _serialize_entry(src, id_, include_db_xrefs, dbxrefs_map)

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
    """Stream one entry per line. Missing / withdrawn / private / race-
    deleted IDs are silently skipped (docs/api-spec.md § データ可視性).
    """
    visible_ids, _hidden_ids = await _resolve_visible_ids(client, index, ids)
    dbxrefs_map = await _fetch_all_dbxrefs(visible_ids, acc_type) if include_db_xrefs else {}

    for chunk_start in range(0, len(visible_ids), _BULK_CHUNK_SIZE):
        chunk_ids = visible_ids[chunk_start : chunk_start + _BULK_CHUNK_SIZE]
        sources = await es_mget_source(
            client,
            index,
            chunk_ids,
            source_excludes=["dbXrefs"],
        )
        for id_ in chunk_ids:
            src = sources.get(id_)
            if src is None:
                continue
            yield _serialize_entry(src, id_, include_db_xrefs, dbxrefs_map)
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
                        "description": (
                            "One JSON object per line (NDJSON). Each line is an entry document. "
                            "Missing or hidden (private / withdrawn) ids are silently skipped in NDJSON mode; "
                            "the `notFound` array is only available in `format=json` mode."
                        ),
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
        500: {
            "description": "Internal Server Error (Elasticsearch unreachable or DuckDB missing).",
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
