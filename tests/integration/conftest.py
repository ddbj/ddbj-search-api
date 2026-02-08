"""Shared fixtures for integration tests.

Integration tests run against a real Elasticsearch instance.
The ES URL is controlled by DDBJ_SEARCH_INTEGRATION_ES_URL
(default: http://localhost:9200).
"""
import os
from typing import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from ddbj_search_api.config import AppConfig
from ddbj_search_api.main import create_app

ES_URL = os.environ.get(
    "DDBJ_SEARCH_INTEGRATION_ES_URL",
    "http://localhost:9200",
)


@pytest.fixture(scope="session", autouse=True)
def ensure_es():
    """Skip all integration tests when ES is not reachable."""
    try:
        resp = httpx.get(ES_URL, timeout=5.0)
        resp.raise_for_status()
    except (httpx.HTTPError, httpx.ConnectError):
        pytest.skip(
            f"Elasticsearch is not available at {ES_URL}",
            allow_module_level=True,
        )


@pytest.fixture(scope="session")
def es_url() -> str:
    """Return the ES URL for integration tests."""

    return ES_URL


@pytest.fixture(scope="session")
def config(es_url: str) -> AppConfig:
    """Create an AppConfig pointing to the integration ES."""

    return AppConfig(es_url=es_url)


@pytest.fixture(scope="session")
def app(config: AppConfig) -> Iterator[TestClient]:
    """Create a TestClient connected to real ES (no mocks).

    Uses context manager to ensure lifespan (es_client setup) runs.
    """
    application = create_app(config)
    with TestClient(application, raise_server_exceptions=False) as client:
        yield client
