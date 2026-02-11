"""Elasticsearch integration: client and query builder."""

from __future__ import annotations

import httpx
from fastapi import Request


async def get_es_client(request: Request) -> httpx.AsyncClient:
    """FastAPI dependency: retrieve the shared ES client from app state."""

    client: httpx.AsyncClient = request.app.state.es_client

    return client
