"""Shared fixtures for integration tests.

Integration tests run against a real Elasticsearch instance.
The ES URL is controlled by DDBJ_SEARCH_INTEGRATION_ES_URL
(default: http://localhost:9200).
"""

from __future__ import annotations

import collections.abc
import os

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
def ensure_es() -> None:
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
def app(config: AppConfig) -> collections.abc.Iterator[TestClient]:
    """Create a TestClient connected to real ES (no mocks).

    Uses context manager to ensure lifespan (es_client setup) runs.
    """
    application = create_app(config)
    with TestClient(application, raise_server_exceptions=False) as client:
        yield client


# Representative accession constants (option A: measure on staging, register here).
# Values are filled in once measured against the staging ES (see tests/integration-note.md
# § fixture 戦略). Leaving them as placeholders here keeps lint/type checks green and
# makes IT-* expectations grep-able.

# BioProject
PUBLIC_BIOPROJECT_ID: str = "..."
SUPPRESSED_BIOPROJECT_ID: str = "..."
WITHDRAWN_BIOPROJECT_ID: str = "..."
PRIVATE_BIOPROJECT_ID: str = "..."
UMBRELLA_BIOPROJECT_ID: str = "..."
ORPHAN_BIOPROJECT_ID: str = "..."
DEEP_CHAIN_BIOPROJECT_ID: str = "..."
MULTI_PARENT_BIOPROJECT_ID: str = "..."
DANGLING_CHILD_BIOPROJECT_ID: str = "..."
SECONDARY_BIOPROJECT_ID: str = "..."

# BioSample
PUBLIC_BIOSAMPLE_ID: str = "..."
SUPPRESSED_BIOSAMPLE_ID: str = "..."
WITHDRAWN_BIOSAMPLE_ID: str = "..."
PRIVATE_BIOSAMPLE_ID: str = "..."
SECONDARY_BIOSAMPLE_ID: str = "..."

# SRA
PUBLIC_SRA_ID: str = "..."
SUPPRESSED_SRA_ID: str = "..."
WITHDRAWN_SRA_ID: str = "..."
PRIVATE_SRA_ID: str = "..."
SECONDARY_SRA_ID: str = "..."

# JGA
PUBLIC_JGA_ID: str = "..."
SUPPRESSED_JGA_ID: str = "..."
WITHDRAWN_JGA_ID: str = "..."
PRIVATE_JGA_ID: str = "..."

# GEA
PUBLIC_GEA_ID: str = "..."
SUPPRESSED_GEA_ID: str = "..."

# MetaboBank
PUBLIC_METABOBANK_ID: str = "..."
SUPPRESSED_METABOBANK_ID: str = "..."

# Trad / Taxonomy (Solr backed; @pytest.mark.staging_only suites only)
PUBLIC_TRAD_ACCESSION: str = "..."
PUBLIC_TAXONOMY_ID: str = "..."

# Cross-cutting
NONEXISTENT_ID: str = "PRJDB_DOES_NOT_EXIST_99999"
