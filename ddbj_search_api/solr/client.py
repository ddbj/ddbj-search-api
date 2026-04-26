"""Solr HTTP client for ARSA and TXSearch.

Thin async wrapper around ``httpx.AsyncClient.get``.  The ARSA wrapper
assembles ``{base_url}/{core}/select`` so callers pass the core name via
config rather than URL-concat themselves; TXSearch takes a fully formed
URL because its endpoint includes a sub-path (``/solr-rgm/...``).

Error classification is intentionally left to the router layer
(``_map_httpx_error``) so the ES path's semantics stay the single source
of truth.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

import httpx


async def arsa_search(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    core: str,
    params: dict[str, str],
) -> dict[str, Any]:
    """Execute ``GET {base_url}/{core}/select`` against ARSA.

    ``base_url`` is expected to omit a trailing slash
    (e.g. ``http://a012:51981/solr``). ``core`` is URL-encoded as a defence
    in depth: AppConfig already validates the env value, but encoding here
    means a stray ``?`` / ``/`` in a runtime override cannot escape the
    intended path segment. Raises ``httpx.HTTPStatusError`` on non-2xx so
    callers can map failures uniformly with other backends.
    """
    encoded_core = urllib.parse.quote(core, safe="")
    response = await client.get(f"{base_url}/{encoded_core}/select", params=params)
    response.raise_for_status()
    result: dict[str, Any] = response.json()
    return result


async def txsearch_search(
    client: httpx.AsyncClient,
    *,
    url: str,
    params: dict[str, str],
) -> dict[str, Any]:
    """Execute ``GET {url}`` against TXSearch (URL is the full /select path)."""
    response = await client.get(url, params=params)
    response.raise_for_status()
    result: dict[str, Any] = response.json()
    return result
