"""Shared fixtures for unit tests."""

from __future__ import annotations

import collections.abc
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from ddbj_search_api.config import AppConfig
from ddbj_search_api.es import get_es_client
from ddbj_search_api.main import create_app


def _make_app(config: AppConfig) -> TestClient:
    """Create a TestClient with get_es_client overridden."""
    fake_client = AsyncMock(spec=httpx.AsyncClient)
    # es_ping calls response.raise_for_status() synchronously;
    # ensure the mock response's raise_for_status is a regular MagicMock
    # to avoid "coroutine never awaited" warnings.
    fake_response = AsyncMock()
    fake_response.raise_for_status = MagicMock()
    fake_client.get.return_value = fake_response
    application = create_app(config)
    application.dependency_overrides[get_es_client] = lambda: fake_client

    return TestClient(application)


@pytest.fixture
def config() -> AppConfig:
    """Create a fresh AppConfig with defaults (no lru_cache)."""

    return AppConfig()


@pytest.fixture
def app(config: AppConfig) -> TestClient:
    """Create a TestClient using a fresh AppConfig."""

    return _make_app(config)


def make_es_search_response(
    hits: Any = None,
    total: int = 0,
    aggregations: Any = None,
) -> dict[str, Any]:
    """Build a minimal ES search response dict."""
    if hits is None:
        hits = []
    resp: dict[str, Any] = {
        "hits": {
            "total": {"value": total, "relation": "eq"},
            "hits": hits,
        },
    }
    if aggregations is not None:
        resp["aggregations"] = aggregations

    return resp


@pytest.fixture
def mock_es_search() -> collections.abc.Iterator[AsyncMock]:
    """Patch es_search and yield the AsyncMock.

    Default return value is an empty search response.
    Override via ``mock_es_search.return_value = ...``.
    """
    with patch(
        "ddbj_search_api.routers.entries.es_search",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = make_es_search_response()
        yield mock


@pytest.fixture
def app_with_es(config: AppConfig, mock_es_search: AsyncMock) -> TestClient:
    """TestClient with es_search mocked (no real ES required).

    Overrides ``get_es_client`` dependency so that ``app.state.es_client``
    is not needed.
    """
    fake_client = AsyncMock(spec=httpx.AsyncClient)
    application = create_app(config)
    application.dependency_overrides[get_es_client] = lambda: fake_client

    return TestClient(application, raise_server_exceptions=False)


def make_mock_stream_response(body: bytes) -> httpx.Response:
    """Create a mock httpx.Response that supports async streaming.

    The response has ``aiter_bytes()`` yielding the body and a no-op
    ``aclose()``.  Suitable for patching ``es_get_source_stream``.
    """
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200

    async def _aiter_bytes() -> collections.abc.AsyncIterator[bytes]:
        yield body

    response.aiter_bytes = _aiter_bytes
    response.aclose = AsyncMock()

    return response


@pytest.fixture
def mock_es_search_with_script_fields() -> collections.abc.Iterator[AsyncMock]:
    """Patch es_search_with_script_fields in the entry_detail router."""
    with patch(
        "ddbj_search_api.routers.entry_detail.es_search_with_script_fields",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = None
        yield mock


@pytest.fixture
def mock_es_get_source_stream() -> collections.abc.Iterator[AsyncMock]:
    """Patch es_get_source_stream in the entry_detail router."""
    with patch(
        "ddbj_search_api.routers.entry_detail.es_get_source_stream",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = None
        yield mock


@pytest.fixture
def app_with_entry_detail(
    config: AppConfig,
    mock_es_search_with_script_fields: AsyncMock,
    mock_es_get_source_stream: AsyncMock,
) -> TestClient:
    """TestClient with entry_detail ES functions mocked."""
    fake_client = AsyncMock(spec=httpx.AsyncClient)
    application = create_app(config)
    application.dependency_overrides[get_es_client] = lambda: fake_client

    return TestClient(application, raise_server_exceptions=False)


# --- Bulk API fixtures ---


@pytest.fixture
def mock_es_get_source_stream_bulk() -> collections.abc.Iterator[AsyncMock]:
    """Patch es_get_source_stream in the bulk router.

    Default return value is None (not found).
    Override via ``side_effect`` to control per-ID behaviour.
    """
    with patch(
        "ddbj_search_api.routers.bulk.es_get_source_stream",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = None
        yield mock


@pytest.fixture
def app_with_bulk(
    config: AppConfig,
    mock_es_get_source_stream_bulk: AsyncMock,
) -> TestClient:
    """TestClient with es_get_source_stream mocked for bulk."""
    fake_client = AsyncMock(spec=httpx.AsyncClient)
    application = create_app(config)
    application.dependency_overrides[get_es_client] = lambda: fake_client

    return TestClient(application, raise_server_exceptions=False)


# --- Facets API fixtures ---


def make_facets_aggregations(
    organism: list[dict[str, Any]] | None = None,
    status: list[dict[str, Any]] | None = None,
    accessibility: list[dict[str, Any]] | None = None,
    type_buckets: list[dict[str, Any]] | None = None,
    object_type: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build aggregation data for facets tests."""
    aggs: dict[str, Any] = {
        "organism": {"buckets": organism or []},
        "status": {"buckets": status or []},
        "accessibility": {"buckets": accessibility or []},
    }
    if type_buckets is not None:
        aggs["type"] = {"buckets": type_buckets}
    if object_type is not None:
        aggs["objectType"] = {"buckets": object_type}

    return aggs


@pytest.fixture
def mock_es_search_facets() -> collections.abc.Iterator[AsyncMock]:
    """Patch es_search in the facets router.

    Default return value is an empty search response with empty facets.
    """
    with patch(
        "ddbj_search_api.routers.facets.es_search",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = make_es_search_response(
            aggregations=make_facets_aggregations(
                type_buckets=[],
            ),
        )
        yield mock


@pytest.fixture
def app_with_facets(
    config: AppConfig,
    mock_es_search_facets: AsyncMock,
) -> TestClient:
    """TestClient with es_search mocked for facets."""
    fake_client = AsyncMock(spec=httpx.AsyncClient)
    application = create_app(config)
    application.dependency_overrides[get_es_client] = lambda: fake_client

    return TestClient(application, raise_server_exceptions=False)
