"""Elasticsearch HTTP client.

Thin async wrapper around httpx for ES REST API calls.
Each function accepts an ``httpx.AsyncClient`` as the first argument,
allowing dependency injection and easy mocking in tests.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


async def es_ping(client: httpx.AsyncClient) -> bool:
    """Check if Elasticsearch is reachable.

    Returns ``True`` if ES responds to ``GET /``, ``False`` otherwise.
    """
    try:
        response = await client.get("/")
        response.raise_for_status()

        return True
    except Exception:
        return False


async def es_search(
    client: httpx.AsyncClient,
    index: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Execute a search query against Elasticsearch.

    ``track_total_hits`` is always set to ``True`` so the total count
    is accurate for pagination.

    Returns the raw ES search response dict.
    """
    request_body = {**body, "track_total_hits": True}
    response = await client.post(f"/{index}/_search", json=request_body)
    response.raise_for_status()

    result: dict[str, Any] = response.json()
    return result


async def es_open_pit(
    client: httpx.AsyncClient,
    index: str,
    keep_alive: str = "5m",
) -> str:
    """Open a Point in Time for cursor-based pagination.

    Returns the PIT ID string.
    """
    response = await client.post(
        f"/{index}/_pit",
        params={"keep_alive": keep_alive},
    )
    response.raise_for_status()
    result: dict[str, Any] = response.json()
    pit_id: str = result["id"]

    return pit_id


async def es_search_with_pit(
    client: httpx.AsyncClient,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Execute a search_after query with PIT (no index in path).

    The caller must include ``pit``, ``search_after``, and ``sort``
    in the body. ``track_total_hits`` is always set to ``True``.

    Returns the raw ES search response dict.
    """
    request_body = {**body, "track_total_hits": True}
    response = await client.post("/_search", json=request_body)
    response.raise_for_status()

    result: dict[str, Any] = response.json()

    return result


async def es_get_source_stream(
    client: httpx.AsyncClient,
    index: str,
    id_: str,
    source_includes: str | None = None,
    source_excludes: str | None = None,
) -> httpx.Response | None:
    """Open a streaming connection to ES ``_source`` endpoint.

    Returns an ``httpx.Response`` with the body stream open.
    The caller is responsible for closing the response via ``aclose()``.
    Returns ``None`` if the document is not found (404).
    """
    params: dict[str, str] = {}
    if source_includes is not None:
        params["_source_includes"] = source_includes
    if source_excludes is not None:
        params["_source_excludes"] = source_excludes

    url = f"/{index}/_source/{id_}"
    request = client.build_request("GET", url, params=params)
    response = await client.send(request, stream=True)

    if response.status_code == 404:
        await response.aclose()

        return None

    response.raise_for_status()

    return response


async def es_resolve_same_as(
    client: httpx.AsyncClient,
    index: str,
    id_: str,
) -> str | None:
    """Resolve an ID via sameAs nested query.

    Searches for an entry whose ``sameAs`` contains a matching
    ``identifier`` with the same ``type`` as the index.

    Returns the primary ``_id`` if found, ``None`` otherwise.
    """
    body: dict[str, Any] = {
        "query": {
            "nested": {
                "path": "sameAs",
                "query": {
                    "bool": {
                        "must": [
                            {"term": {"sameAs.identifier": id_}},
                            {"term": {"sameAs.type": index}},
                        ],
                    },
                },
            },
        },
        "_source": False,
        "size": 1,
    }
    response = await client.post(f"/{index}/_search", json=body)
    if 400 <= response.status_code < 500:
        logger.warning(
            "sameAs resolution query returned HTTP %d for %s/%s",
            response.status_code,
            index,
            id_,
        )
        return None
    response.raise_for_status()
    result: dict[str, Any] = response.json()
    hits = result.get("hits", {}).get("hits", [])
    if not hits:
        return None
    primary_id: str = hits[0]["_id"]
    return primary_id


async def es_get_identifier(
    client: httpx.AsyncClient,
    index: str,
    id_: str,
) -> str:
    """Get the ``identifier`` field from an ES document's ``_source``.

    For alias documents (where ``_id`` != ``identifier``), returns the
    primary identifier.  For normal documents, returns *id_* unchanged.
    Falls back to *id_* if the document is not found.
    """
    response = await client.get(
        f"/{index}/_source/{id_}",
        params={"_source_includes": "identifier"},
    )
    if response.status_code == 404:
        return id_
    response.raise_for_status()
    result: dict[str, Any] = response.json()
    identifier: str = result.get("identifier", id_)
    return identifier


async def es_head_exists(
    client: httpx.AsyncClient,
    index: str,
    id_: str,
) -> bool:
    """Check if a document exists using HEAD request.

    Returns ``True`` if the document exists (200), ``False`` on 404.
    Raises on other HTTP errors.
    """
    response = await client.head(f"/{index}/_source/{id_}")
    if response.status_code == 404:
        return False
    response.raise_for_status()

    return True


async def es_get_source(
    client: httpx.AsyncClient,
    index: str,
    id_: str,
    source_includes: str | None = None,
    source_excludes: str | None = None,
) -> dict[str, Any] | None:
    """Fetch an ES document's ``_source`` as a dict (non-streaming).

    Returns the ``_source`` dict, or ``None`` if the document is not
    found (404).
    """
    params: dict[str, str] = {}
    if source_includes is not None:
        params["_source_includes"] = source_includes
    if source_excludes is not None:
        params["_source_excludes"] = source_excludes

    response = await client.get(f"/{index}/_source/{id_}", params=params)
    if response.status_code == 404:
        return None
    response.raise_for_status()

    result: dict[str, Any] = response.json()
    return result


async def es_mget_source(
    client: httpx.AsyncClient,
    index: str,
    ids: list[str],
    source_includes: list[str] | None = None,
) -> dict[str, dict[str, Any] | None]:
    """Batch fetch ``_source`` for multiple document IDs via ``_mget``.

    Returns a dict mapping each requested id to its ``_source`` dict,
    or ``None`` when the document was not found.  Empty ``ids``
    short-circuits to an empty dict without hitting ES.
    """
    if not ids:
        return {}

    docs: list[dict[str, Any]] = []
    for id_ in ids:
        doc: dict[str, Any] = {"_id": id_}
        if source_includes is not None:
            doc["_source"] = {"includes": source_includes}
        docs.append(doc)

    response = await client.post(f"/{index}/_mget", json={"docs": docs})
    response.raise_for_status()

    result: dict[str, Any] = response.json()
    out: dict[str, dict[str, Any] | None] = {}
    for entry in result.get("docs", []):
        entry_id: str = entry["_id"]
        if entry.get("found"):
            out[entry_id] = entry.get("_source") or {}
        else:
            out[entry_id] = None

    return out
