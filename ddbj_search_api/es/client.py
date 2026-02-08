"""Elasticsearch HTTP client.

Thin async wrapper around httpx for ES REST API calls.
Each function accepts an ``httpx.AsyncClient`` as the first argument,
allowing dependency injection and easy mocking in tests.
"""
from typing import Any, Dict, List, Optional

import httpx


async def es_ping(client: httpx.AsyncClient) -> bool:
    """Check if Elasticsearch is reachable.

    Returns ``True`` if ES responds to ``GET /``, ``False`` otherwise.
    """
    try:
        response = await client.get("/")
        response.raise_for_status()

        return True
    except (httpx.HTTPError, Exception):

        return False


async def es_search(
    client: httpx.AsyncClient,
    index: str,
    body: Dict[str, Any],
) -> Dict[str, Any]:
    """Execute a search query against Elasticsearch.

    ``track_total_hits`` is always set to ``True`` so the total count
    is accurate for pagination.

    Returns the raw ES search response dict.
    """
    request_body = {**body, "track_total_hits": True}
    response = await client.post(f"/{index}/_search", json=request_body)
    response.raise_for_status()

    return response.json()  # type: ignore[no-any-return]


async def es_get_doc(
    client: httpx.AsyncClient,
    index: str,
    id_: str,
) -> Optional[Dict[str, Any]]:
    """Retrieve a single document by ID.

    Returns the document ``_source`` dict, or ``None`` if not found.
    """
    response = await client.get(f"/{index}/_doc/{id_}")
    if response.status_code == 404:
        return None
    response.raise_for_status()

    return response.json()["_source"]  # type: ignore[no-any-return]


async def es_mget(
    client: httpx.AsyncClient,
    index: str,
    ids: List[str],
) -> Dict[str, Any]:
    """Retrieve multiple documents by IDs.

    Returns the raw ES mget response dict.
    """
    response = await client.post(f"/{index}/_mget", json={"ids": ids})
    response.raise_for_status()

    return response.json()  # type: ignore[no-any-return]


_TRUNCATE_SCRIPT = (
    "def xrefs = params._source.containsKey('dbXrefs')"
    " ? params._source.dbXrefs : [];"
    " if (xrefs == null) { return []; }"
    " int limit = params.limit;"
    " if (limit >= xrefs.size()) { return xrefs; }"
    " List result = new ArrayList();"
    " for (int i = 0; i < limit; i++)"
    " { result.add(xrefs.get(i)); }"
    " return result;"
)

_COUNT_SCRIPT = (
    "def xrefs = params._source.containsKey('dbXrefs')"
    " ? params._source.dbXrefs : [];"
    " if (xrefs == null) { return [:]; }"
    " Map counts = new HashMap();"
    " for (def x : xrefs) {"
    "   String t = x.containsKey('type')"
    "     ? x['type'] : 'unknown';"
    "   counts.put(t,"
    "     counts.containsKey(t)"
    "       ? counts.get(t) + 1 : 1);"
    " }"
    " return counts;"
)


def build_db_xrefs_script_fields(limit: int) -> Dict[str, Any]:
    """Build ES script_fields for dbXrefs truncation and counting."""

    return {
        "dbXrefsTruncated": {
            "script": {
                "lang": "painless",
                "source": _TRUNCATE_SCRIPT,
                "params": {"limit": limit},
            },
        },
        "dbXrefsCountByType": {
            "script": {
                "lang": "painless",
                "source": _COUNT_SCRIPT,
            },
        },
    }


def parse_script_fields_hit(hit: Dict[str, Any]) -> Dict[str, Any]:
    """Merge script_fields results into a hit's _source.

    Extracts ``dbXrefsTruncated`` → ``dbXrefs`` and
    ``dbXrefsCountByType`` → ``dbXrefsCount`` from ES ``fields``.
    """
    source: Dict[str, Any] = dict(hit["_source"])
    fields = hit.get("fields", {})
    source["dbXrefs"] = fields.get("dbXrefsTruncated", [[]])[0]
    source["dbXrefsCount"] = fields.get("dbXrefsCountByType", [{}])[0]

    return source


async def es_search_with_script_fields(
    client: httpx.AsyncClient,
    index: str,
    id_: str,
    db_xrefs_limit: int,
) -> Optional[Dict[str, Any]]:
    """Search for a single document with script_fields for dbXrefs truncation.

    Uses Painless scripting to truncate ``dbXrefs`` and compute per-type
    counts on the ES side, avoiding loading the full array into API memory.

    Returns a dict with ``_source`` (without dbXrefs) merged with
    ``dbXrefsTruncated`` and ``dbXrefsCountByType`` from script_fields,
    or ``None`` if not found.
    """
    body: Dict[str, Any] = {
        "query": {"term": {"_id": id_}},
        "size": 1,
        "_source": {"excludes": ["dbXrefs"]},
        "script_fields": build_db_xrefs_script_fields(db_xrefs_limit),
    }
    response = await client.post(f"/{index}/_search", json=body)
    response.raise_for_status()

    data = response.json()
    hits = data["hits"]["hits"]
    if not hits:
        return None

    return parse_script_fields_hit(hits[0])


async def es_get_source_stream(
    client: httpx.AsyncClient,
    index: str,
    id_: str,
    source_includes: Optional[str] = None,
) -> Optional[httpx.Response]:
    """Open a streaming connection to ES ``_source`` endpoint.

    Returns an ``httpx.Response`` with the body stream open.
    The caller is responsible for closing the response via ``aclose()``.
    Returns ``None`` if the document is not found (404).
    """
    params: Dict[str, str] = {}
    if source_includes is not None:
        params["_source_includes"] = source_includes

    url = f"/{index}/_source/{id_}"
    request = client.build_request("GET", url, params=params)
    response = await client.send(request, stream=True)

    if response.status_code == 404:
        await response.aclose()

        return None

    response.raise_for_status()

    return response


async def es_count(
    client: httpx.AsyncClient,
    index: str,
    query: Optional[Dict[str, Any]] = None,
) -> int:
    """Count documents matching a query.

    Returns the total count.
    """
    body: Dict[str, Any] = {}
    if query is not None:
        body["query"] = query
    response = await client.post(f"/{index}/_count", json=body)
    response.raise_for_status()

    return response.json()["count"]  # type: ignore[no-any-return]
