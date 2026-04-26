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
# Representative accession constants. Values are measured against the
# Elasticsearch / Solr the test suite connects to. Empty string ("") means
# the status / shape is not present in the connected dataset (e.g. no
# ``withdrawn`` entries indexed); ``require_accession`` turns those into
# pytest skips rather than failures so the same suite runs against every
# environment.
# ---------------------------------------------------------------------------

# BioProject
PUBLIC_BIOPROJECT_ID: str = "PRJDB42131"
SUPPRESSED_BIOPROJECT_ID: str = "PRJDB5611"
WITHDRAWN_BIOPROJECT_ID: str = ""
PRIVATE_BIOPROJECT_ID: str = ""
ORPHAN_BIOPROJECT_ID: str = "PRJDB39956"
# Umbrella with all children resolvable (3 children, all present in ES).
UMBRELLA_BIOPROJECT_ID: str = "PRJNA117"
# Multi-parent: child carrying parentBioProjects of length >= 2.
MULTI_PARENT_BIOPROJECT_ID: str = "PRJNA119"
# Umbrella whose childBioProjects contains references missing from ES;
# walking it exercises the dangling-edge prune path.
DANGLING_CHILD_BIOPROJECT_ID: str = "PRJNA121"
# Chain longer than ``MAX_DEPTH=10``: not produced by the converter
# (parent depth is bounded), so deep-chain assertions stay skipped here.
DEEP_CHAIN_BIOPROJECT_ID: str = ""
# BioProject ``sameAs`` only carries external cross-refs (GEO etc.), so
# Secondary→Primary fallback is exercised via the JGA constants below.
SECONDARY_BIOPROJECT_ID: str = ""

# BioSample
PUBLIC_BIOSAMPLE_ID: str = "SAMN24542748"
SUPPRESSED_BIOSAMPLE_ID: str = "SAMN00249953"
WITHDRAWN_BIOSAMPLE_ID: str = ""
PRIVATE_BIOSAMPLE_ID: str = ""
SECONDARY_BIOSAMPLE_ID: str = ""

# SRA submission / study / experiment
PUBLIC_SRA_SUBMISSION_ID: str = "DRA000208"
SUPPRESSED_SRA_SUBMISSION_ID: str = "DRA014954"
PRIVATE_SRA_SUBMISSION_ID: str = "SRA2372146"

PUBLIC_SRA_STUDY_ID: str = "DRP000209"
SUPPRESSED_SRA_STUDY_ID: str = "DRP015196"
PRIVATE_SRA_STUDY_ID: str = "SRP579550"

PUBLIC_SRA_EXPERIMENT_ID: str = "SRX10405429"
SUPPRESSED_SRA_EXPERIMENT_ID: str = "DRX397277"
PRIVATE_SRA_EXPERIMENT_ID: str = "SRX32982793"

# SRA run / sample / analysis
PUBLIC_SRA_RUN_ID: str = "SRR9653884"
SUPPRESSED_SRA_RUN_ID: str = "DRR411679"
PRIVATE_SRA_RUN_ID: str = "SRR37528245"

PUBLIC_SRA_SAMPLE_ID: str = "SRS6222312"
SUPPRESSED_SRA_SAMPLE_ID: str = "DRS626629"
PRIVATE_SRA_SAMPLE_ID: str = "SRS24801026"

# sra-analysis on staging only carries public entries.
PUBLIC_SRA_ANALYSIS_ID: str = "DRZ105276"
SUPPRESSED_SRA_ANALYSIS_ID: str = ""
PRIVATE_SRA_ANALYSIS_ID: str = ""

# JGA: only public on staging.
PUBLIC_JGA_STUDY_ID: str = "JGAS000001"
PUBLIC_JGA_DATASET_ID: str = "JGAD000001"
PUBLIC_JGA_DAC_ID: str = "JGAC000001"
PUBLIC_JGA_POLICY_ID: str = "JGAP000001"

# JGA Secondary (long-form sameAs alias documents). The long form exists in
# ES as an alias whose ``identifier`` is the short Primary; Secondary→Primary
# resolution must surface the short form as ``identifier`` / ``query``.
SECONDARY_JGA_STUDY_ID: str = "JGAS00000000001"
SECONDARY_JGA_DATASET_ID: str = "JGAD00000000001"
SECONDARY_JGA_DAC_ID: str = "JGAC00000000001"
# jga-policy has no sameAs entries in the index.
SECONDARY_JGA_POLICY_ID: str = ""

# GEA / MetaboBank: only public on staging.
PUBLIC_GEA_ID: str = "E-GEAD-282"
PUBLIC_METABOBANK_ID: str = "MTBKS102"

# Solr-backed DBs (ARSA / TXSearch). Used by ``staging_only`` IT-DBPORTAL.
PUBLIC_TRAD_ACCESSION: str = "GL589895"
PUBLIC_TAXONOMY_ID: str = "2201696"

# Crafted to never collide with a real accession (used by IT-DETAIL-05,
# IT-STATUS-04, IT-UMBRELLA-07 missing-entry assertions).
NONEXISTENT_ID: str = "PRJDB_DOES_NOT_EXIST_99999"


def require_accession(name: str, value: str) -> str:
    """Skip the calling test when the accession constant is empty.

    Empty constants represent status / shape combinations the connected
    dataset does not contain. Routing through this helper turns an absent
    fixture into a pytest skip instead of a misleading 404.
    """
    if not value:
        pytest.skip(f"{name} is not populated (dataset does not contain a representative)")

    return value
