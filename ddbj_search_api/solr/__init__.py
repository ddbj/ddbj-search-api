"""Solr integration for ARSA (Trad) and TXSearch (NCBI Taxonomy) proxy.

Lightweight httpx-based proxy layer so ``/db-portal/search`` can serve
``db=trad`` and ``db=taxonomy``.  The unified hits envelope matches the
ES-backed DBs; DB-specific fields (``division`` for trad, ``rank`` /
``commonName`` / ``japaneseName`` for taxonomy) are mapped to the
corresponding ``DbPortalHit`` variant.
"""

from __future__ import annotations

import httpx
from fastapi import Request


async def get_solr_client(request: Request) -> httpx.AsyncClient:
    """FastAPI dependency: retrieve the shared Solr client from app state."""

    client: httpx.AsyncClient = request.app.state.solr_client

    return client
