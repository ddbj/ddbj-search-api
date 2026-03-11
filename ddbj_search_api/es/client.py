"""Elasticsearch HTTP client.

Thin async wrapper around httpx for ES REST API calls.
Each function accepts an ``httpx.AsyncClient`` as the first argument,
allowing dependency injection and easy mocking in tests.
"""

from __future__ import annotations

from typing import Any

import httpx


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
