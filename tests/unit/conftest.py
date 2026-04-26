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
from ddbj_search_api.routers.db_portal import _get_config_dep
from ddbj_search_api.solr import get_solr_client


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
def _mock_entries_duckdb() -> collections.abc.Iterator[None]:
    """Mock DuckDB functions used by entries router."""
    with (
        patch(
            "ddbj_search_api.routers.entries.get_linked_ids_limited_bulk",
            return_value={},
        ),
        patch(
            "ddbj_search_api.routers.entries.count_linked_ids_bulk",
            return_value={},
        ),
        patch(
            "ddbj_search_api.routers.entries.DBLINK_DB_PATH",
            MagicMock(exists=MagicMock(return_value=True)),
        ),
    ):
        yield


@pytest.fixture
def app_with_es(config: AppConfig, mock_es_search: AsyncMock, _mock_entries_duckdb: None) -> TestClient:
    """TestClient with es_search mocked (no real ES required).

    Overrides ``get_es_client`` dependency so that ``app.state.es_client``
    is not needed.
    """
    fake_client = AsyncMock(spec=httpx.AsyncClient)
    application = create_app(config)
    application.dependency_overrides[get_es_client] = lambda: fake_client

    return TestClient(application, raise_server_exceptions=False)


@pytest.fixture
def mock_es_open_pit() -> collections.abc.Iterator[AsyncMock]:
    """Patch es_open_pit in the entries router."""
    with patch(
        "ddbj_search_api.routers.entries.es_open_pit",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = "mock_pit_id_123"
        yield mock


@pytest.fixture
def mock_es_search_with_pit() -> collections.abc.Iterator[AsyncMock]:
    """Patch es_search_with_pit in the entries router."""
    with patch(
        "ddbj_search_api.routers.entries.es_search_with_pit",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = make_es_search_response()
        yield mock


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


# --- Entry Detail fixtures ---


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
def mock_es_resolve_same_as() -> collections.abc.Iterator[AsyncMock]:
    """Patch es_resolve_same_as in the entry_detail router."""
    with patch(
        "ddbj_search_api.routers.entry_detail.es_resolve_same_as",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = None
        yield mock


@pytest.fixture
def mock_es_get_source_entry_detail() -> collections.abc.Iterator[AsyncMock]:
    """Patch es_get_source in the entry_detail router.

    Default: ``{"identifier": <requested id>, "status": "public"}``
    so the visibility check (docs/api-spec.md § データ可視性) passes
    for the majority of tests. Override ``.return_value`` or
    ``.side_effect`` to emulate alias docs, hidden statuses, or missing
    documents.
    """
    with patch(
        "ddbj_search_api.routers.entry_detail.es_get_source",
        new_callable=AsyncMock,
    ) as mock:

        async def _default(_client: object, _index: str, id_: str, **_kwargs: object) -> dict[str, str]:
            return {"identifier": id_, "status": "public"}

        mock.side_effect = _default
        yield mock


@pytest.fixture
def _mock_entry_detail_duckdb() -> collections.abc.Iterator[None]:
    """Mock DuckDB functions used by entry_detail router."""
    with (
        patch(
            "ddbj_search_api.routers.entry_detail.iter_linked_ids",
            side_effect=lambda *_args, **_kwargs: iter([]),
        ),
        patch(
            "ddbj_search_api.routers.entry_detail.get_linked_ids_limited",
            return_value=[],
        ),
        patch(
            "ddbj_search_api.routers.entry_detail.count_linked_ids",
            return_value={},
        ),
    ):
        yield


@pytest.fixture
def app_with_entry_detail(
    config: AppConfig,
    mock_es_get_source_stream: AsyncMock,
    mock_es_resolve_same_as: AsyncMock,
    mock_es_get_source_entry_detail: AsyncMock,
    _mock_entry_detail_duckdb: None,
) -> TestClient:
    """TestClient with entry_detail ES and DuckDB functions mocked."""
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


@pytest.fixture(autouse=True)
def mock_es_ping() -> collections.abc.Iterator[AsyncMock]:
    """Autouse-patch es_ping in the service_info router.

    Default: ``return_value=True`` so /service-info reports
    ``elasticsearch=ok``. Tests that need to simulate ES down should take
    ``mock_es_ping`` as an argument and override
    ``mock_es_ping.return_value = False`` (mirroring the
    ``mock_es_mget_source_bulk`` pattern below).
    """
    with patch(
        "ddbj_search_api.routers.service_info.es_ping",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = True
        yield mock


@pytest.fixture(autouse=True)
def mock_es_mget_source_bulk() -> collections.abc.Iterator[AsyncMock]:
    """Autouse-patch es_mget_source in the bulk router.

    Default: every requested ID maps to ``status=public`` so the
    visibility check (docs/api-spec.md § データ可視性) passes and the
    existing per-ID streaming flow is exercised. Override ``.side_effect``
    or ``.return_value`` to emulate withdrawn/private/missing entries.

    Autouse is used so that tests which construct a ``TestClient`` by
    hand (without the ``app_with_bulk`` fixture) also get the mock.
    """
    with patch(
        "ddbj_search_api.routers.bulk.es_mget_source",
        new_callable=AsyncMock,
    ) as mock:

        async def _default(
            _client: object,
            _index: str,
            ids: list[str],
            **_kwargs: object,
        ) -> dict[str, dict[str, str] | None]:
            return {id_: {"status": "public"} for id_ in ids}

        mock.side_effect = _default
        yield mock


@pytest.fixture
def app_with_bulk(
    config: AppConfig,
    mock_es_get_source_stream_bulk: AsyncMock,
    mock_es_mget_source_bulk: AsyncMock,
) -> TestClient:
    """TestClient with es_get_source_stream and es_mget_source mocked for bulk."""
    fake_client = AsyncMock(spec=httpx.AsyncClient)
    application = create_app(config)
    application.dependency_overrides[get_es_client] = lambda: fake_client

    return TestClient(application, raise_server_exceptions=False)


# --- Facets API fixtures ---


def make_facets_aggregations(
    organism: list[dict[str, Any]] | None = None,
    accessibility: list[dict[str, Any]] | None = None,
    type_buckets: list[dict[str, Any]] | None = None,
    object_type: list[dict[str, Any]] | None = None,
    library_strategy: list[dict[str, Any]] | None = None,
    library_source: list[dict[str, Any]] | None = None,
    library_selection: list[dict[str, Any]] | None = None,
    platform: list[dict[str, Any]] | None = None,
    instrument_model: list[dict[str, Any]] | None = None,
    experiment_type: list[dict[str, Any]] | None = None,
    study_type: list[dict[str, Any]] | None = None,
    submission_type: list[dict[str, Any]] | None = None,
    include_common: bool = True,
) -> dict[str, Any]:
    """Build aggregation data for facets tests.

    ``status`` aggregation はビルドしない (docs/api-spec.md § データ可視性)。

    ``include_common=False`` を渡すと organism/accessibility も除外する
    (facet pick で空文字 / 明示指定時の ES レスポンスを再現するために使う)。
    """
    aggs: dict[str, Any] = {}
    if include_common:
        aggs["organism"] = {"buckets": organism or []}
        aggs["accessibility"] = {"buckets": accessibility or []}
    if type_buckets is not None:
        aggs["type"] = {"buckets": type_buckets}
    if object_type is not None:
        aggs["objectType"] = {"buckets": object_type}
    if library_strategy is not None:
        aggs["libraryStrategy"] = {"buckets": library_strategy}
    if library_source is not None:
        aggs["librarySource"] = {"buckets": library_source}
    if library_selection is not None:
        aggs["librarySelection"] = {"buckets": library_selection}
    if platform is not None:
        aggs["platform"] = {"buckets": platform}
    if instrument_model is not None:
        aggs["instrumentModel"] = {"buckets": instrument_model}
    if experiment_type is not None:
        aggs["experimentType"] = {"buckets": experiment_type}
    if study_type is not None:
        aggs["studyType"] = {"buckets": study_type}
    if submission_type is not None:
        aggs["submissionType"] = {"buckets": submission_type}

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


# --- DB Portal API fixtures ---


def make_solr_arsa_response(
    docs: list[dict[str, Any]] | None = None,
    num_found: int = 0,
) -> dict[str, Any]:
    """Build a minimal ARSA ``/select`` JSON response for tests."""
    return {
        "responseHeader": {"status": 0, "QTime": 5},
        "response": {
            "numFound": num_found,
            "start": 0,
            "docs": docs or [],
        },
    }


def make_solr_txsearch_response(
    docs: list[dict[str, Any]] | None = None,
    num_found: int = 0,
) -> dict[str, Any]:
    """Build a minimal TXSearch ``/select`` JSON response for tests."""
    return {
        "responseHeader": {"status": 0, "QTime": 5},
        "response": {
            "numFound": num_found,
            "start": 0,
            "docs": docs or [],
        },
    }


@pytest.fixture
def mock_es_search_db_portal() -> collections.abc.Iterator[AsyncMock]:
    """Patch es_search in the db_portal router.

    Default return value is an empty search response.
    Override via ``mock_es_search_db_portal.return_value = ...`` or
    ``.side_effect = [...]`` to set per-call results.
    """
    with patch(
        "ddbj_search_api.routers.db_portal.es_search",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = make_es_search_response()
        yield mock


@pytest.fixture
def mock_arsa_search_db_portal() -> collections.abc.Iterator[AsyncMock]:
    """Patch arsa_search in the db_portal router.

    Default: empty response. Override via ``return_value`` / ``side_effect``.
    """
    with patch(
        "ddbj_search_api.routers.db_portal.arsa_search",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = make_solr_arsa_response()
        yield mock


@pytest.fixture
def mock_txsearch_search_db_portal() -> collections.abc.Iterator[AsyncMock]:
    """Patch txsearch_search in the db_portal router."""
    with patch(
        "ddbj_search_api.routers.db_portal.txsearch_search",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = make_solr_txsearch_response()
        yield mock


@pytest.fixture
def mock_es_open_pit_db_portal() -> collections.abc.Iterator[AsyncMock]:
    """Patch es_open_pit in the db_portal router."""
    with patch(
        "ddbj_search_api.routers.db_portal.es_open_pit",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = "mock_pit_id_db_portal"
        yield mock


@pytest.fixture
def mock_es_search_with_pit_db_portal() -> collections.abc.Iterator[AsyncMock]:
    """Patch es_search_with_pit in the db_portal router."""
    with patch(
        "ddbj_search_api.routers.db_portal.es_search_with_pit",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = make_es_search_response()
        yield mock


@pytest.fixture
def app_with_db_portal(
    config: AppConfig,
    mock_es_search_db_portal: AsyncMock,
    mock_arsa_search_db_portal: AsyncMock,
    mock_txsearch_search_db_portal: AsyncMock,
) -> TestClient:
    """TestClient with db_portal ES + Solr client calls mocked.

    ARSA / TXSearch config default URLs are set so the router emits
    ``error=unknown`` only when the caller explicitly clears them.
    The ``_get_config_dep`` dependency is overridden so the router
    sees this fixture's ``AppConfig`` instead of the module-level
    singleton from ``get_config``.
    """
    object.__setattr__(config, "solr_arsa_base_url", "http://mock-arsa:51981/solr")
    object.__setattr__(config, "solr_arsa_shards", "mock-arsa:51981/solr/collection1")
    object.__setattr__(config, "solr_arsa_core", "collection1")
    object.__setattr__(config, "solr_txsearch_url", "http://mock-txsearch/solr-rgm/ncbi_taxonomy/select")

    fake_es_client = AsyncMock(spec=httpx.AsyncClient)
    fake_solr_client = AsyncMock(spec=httpx.AsyncClient)
    application = create_app(config)
    application.dependency_overrides[get_es_client] = lambda: fake_es_client
    application.dependency_overrides[get_solr_client] = lambda: fake_solr_client
    application.dependency_overrides[_get_config_dep] = lambda: config

    return TestClient(application, raise_server_exceptions=False)


# --- Umbrella Tree API fixtures ---


@pytest.fixture
def mock_es_get_source() -> collections.abc.Iterator[AsyncMock]:
    """Patch es_get_source in the umbrella_tree router.

    Default return value is None (not found). Override via
    ``return_value`` or ``side_effect`` to control seed fetch results.
    """
    with patch(
        "ddbj_search_api.routers.umbrella_tree.es_get_source",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = None
        yield mock


@pytest.fixture
def mock_es_mget_source() -> collections.abc.Iterator[AsyncMock]:
    """Patch es_mget_source in the umbrella_tree router.

    Default return value is an empty dict. Override via
    ``return_value`` or ``side_effect`` to provide hop-by-hop responses.
    """
    with patch(
        "ddbj_search_api.routers.umbrella_tree.es_mget_source",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = {}
        yield mock


@pytest.fixture
def mock_es_resolve_same_as_umbrella() -> collections.abc.Iterator[AsyncMock]:
    """Patch es_resolve_same_as in the umbrella_tree router."""
    with patch(
        "ddbj_search_api.routers.umbrella_tree.es_resolve_same_as",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = None
        yield mock


@pytest.fixture
def app_with_umbrella_tree(
    config: AppConfig,
    mock_es_get_source: AsyncMock,
    mock_es_mget_source: AsyncMock,
    mock_es_resolve_same_as_umbrella: AsyncMock,
) -> TestClient:
    """TestClient with umbrella_tree ES functions mocked."""
    fake_client = AsyncMock(spec=httpx.AsyncClient)
    application = create_app(config)
    application.dependency_overrides[get_es_client] = lambda: fake_client

    return TestClient(application, raise_server_exceptions=False)
