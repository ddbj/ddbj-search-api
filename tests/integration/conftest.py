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


# ---------------------------------------------------------------------------
# Representative accession constants. Each value is measured against the
# Elasticsearch the suite connects to. ``""`` means "no representative is
# available in the current dataset" — ``require_accession`` turns those
# into pytest skips. Add or update only when a test actually consumes the
# constant; abandoned constants accrue drift without surfacing as failures.
# ---------------------------------------------------------------------------

# BioProject
PUBLIC_BIOPROJECT_ID: str = "PRJDB42131"
SUPPRESSED_BIOPROJECT_ID: str = "PRJDB5611"
ORPHAN_BIOPROJECT_ID: str = "PRJDB39956"
# UmbrellaBioProject whose every child resolves in ES (3 children).
UMBRELLA_BIOPROJECT_ID: str = "PRJNA117"
# Child accession with parentBioProjects of length >= 2.
MULTI_PARENT_BIOPROJECT_ID: str = "PRJNA119"
# Umbrella whose childBioProjects list contains references missing from ES;
# walking it exercises the dangling-edge prune path.
DANGLING_CHILD_BIOPROJECT_ID: str = "PRJNA121"

# BioSample
PUBLIC_BIOSAMPLE_ID: str = "SAMN24542748"

# SRA
PUBLIC_SRA_SUBMISSION_ID: str = "DRA000208"
PUBLIC_SRA_STUDY_ID: str = "DRP000209"
PUBLIC_SRA_EXPERIMENT_ID: str = "SRX10405429"
PRIVATE_SRA_EXPERIMENT_ID: str = "SRX32982793"
PUBLIC_SRA_RUN_ID: str = "SRR9653884"
PUBLIC_SRA_SAMPLE_ID: str = "SRS6222312"
PUBLIC_SRA_ANALYSIS_ID: str = "DRZ105276"

# JGA
PUBLIC_JGA_STUDY_ID: str = "JGAS000001"
PUBLIC_JGA_DATASET_ID: str = "JGAD000001"
PUBLIC_JGA_DAC_ID: str = "JGAC000001"
PUBLIC_JGA_POLICY_ID: str = "JGAP000001"
# Long-form alias document (``_id`` = long form, ``_source.identifier`` =
# short Primary). Drives the alias / sameAs scenarios.
SECONDARY_JGA_STUDY_ID: str = "JGAS00000000001"

# GEA / MetaboBank
PUBLIC_GEA_ID: str = "E-GEAD-282"
PUBLIC_METABOBANK_ID: str = "MTBKS102"

# Crafted to never collide with a real accession.
NONEXISTENT_ID: str = "PRJDB_DOES_NOT_EXIST_99999"


def require_accession(name: str, value: str) -> str:
    """Skip the calling test when the accession constant is empty.

    Empty constants represent shapes the connected dataset does not
    contain. Routing through this helper turns an absent fixture into a
    pytest skip instead of a misleading 404.
    """
    if not value:
        pytest.skip(f"{name} is not populated (dataset does not contain a representative)")

    return value
