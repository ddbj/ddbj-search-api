"""Solr integration for ARSA (Trad) and TXSearch (NCBI Taxonomy) proxy.

Lightweight httpx-based proxy layer so ``/db-portal/search?db=trad`` and
``/db-portal/search?db=taxonomy`` can serve hits, and ``/db-portal/cross-search``
can include their counts in the 8-DB fan-out.  The unified hits envelope
matches the ES-backed DBs; DB-specific fields (``division`` for trad,
``rank`` / ``commonName`` / ``japaneseName`` for taxonomy) are mapped to
the corresponding ``DbPortalHit`` variant.
"""

from __future__ import annotations

import httpx
from fastapi import Request


async def get_solr_client(request: Request) -> httpx.AsyncClient:
    """FastAPI dependency: retrieve the shared Solr client from app state."""

    client: httpx.AsyncClient = request.app.state.solr_client

    return client
