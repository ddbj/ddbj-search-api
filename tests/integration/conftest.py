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
# Representative accession constants (option A: measure on staging, register
# here). Values were measured on staging Elasticsearch (index ``*-20260423``)
# via ``_msearch`` with ``term: {status: <X>}`` queries.
#
# Empty string ("") means "this status combination is not present in the
# staging data" (e.g. no ``withdrawn`` entries currently exist anywhere; no
# ``private`` entries in BioProject / BioSample / JGA / GEA / MetaboBank;
# no ``suppressed`` entries in JGA / GEA / MetaboBank). Tests that depend on
# such accessions should call ``require_accession`` so they are skipped —
# not failed — when the constant is empty.
#
# Special-purpose accessions (umbrella / orphan / multi-parent / sameAs /
# dangling-child) are filled in by the IT-UMBRELLA and IT-DETAIL suites as
# they are implemented.
# ---------------------------------------------------------------------------

# BioProject (public + suppressed only on staging)
PUBLIC_BIOPROJECT_ID: str = "PRJDB42131"
SUPPRESSED_BIOPROJECT_ID: str = "PRJDB5611"
WITHDRAWN_BIOPROJECT_ID: str = ""
PRIVATE_BIOPROJECT_ID: str = ""
UMBRELLA_BIOPROJECT_ID: str = ""
ORPHAN_BIOPROJECT_ID: str = ""
DEEP_CHAIN_BIOPROJECT_ID: str = ""
MULTI_PARENT_BIOPROJECT_ID: str = ""
DANGLING_CHILD_BIOPROJECT_ID: str = ""
SECONDARY_BIOPROJECT_ID: str = ""

# BioSample (public + suppressed only on staging)
PUBLIC_BIOSAMPLE_ID: str = "SAMN24542748"
SUPPRESSED_BIOSAMPLE_ID: str = "SAMN00249953"
WITHDRAWN_BIOSAMPLE_ID: str = ""
PRIVATE_BIOSAMPLE_ID: str = ""
SECONDARY_BIOSAMPLE_ID: str = ""

# SRA-submission / SRA-study / SRA-experiment have public + suppressed + private.
# SRA-run / SRA-sample / SRA-analysis sampled here only as public; suppressed
# and private representatives are added when the corresponding IT-STATUS
# variants need them.
PUBLIC_SRA_SUBMISSION_ID: str = "DRA000208"
SUPPRESSED_SRA_SUBMISSION_ID: str = "DRA014954"
PRIVATE_SRA_SUBMISSION_ID: str = "SRA2372146"

PUBLIC_SRA_STUDY_ID: str = "DRP000209"
SUPPRESSED_SRA_STUDY_ID: str = "DRP015196"
PRIVATE_SRA_STUDY_ID: str = "SRP579550"

PUBLIC_SRA_EXPERIMENT_ID: str = "SRX10405429"
SUPPRESSED_SRA_EXPERIMENT_ID: str = "DRX397277"
PRIVATE_SRA_EXPERIMENT_ID: str = "SRX32982793"

PUBLIC_SRA_RUN_ID: str = "SRR9653884"
PUBLIC_SRA_SAMPLE_ID: str = "SRS6222312"
PUBLIC_SRA_ANALYSIS_ID: str = "DRZ105276"

# JGA (public only on staging)
PUBLIC_JGA_STUDY_ID: str = "JGAS000001"
PUBLIC_JGA_DATASET_ID: str = "JGAD000001"
PUBLIC_JGA_DAC_ID: str = "JGAC000001"
PUBLIC_JGA_POLICY_ID: str = "JGAP000001"

# GEA / MetaboBank (public only on staging)
PUBLIC_GEA_ID: str = "E-GEAD-282"
PUBLIC_METABOBANK_ID: str = "MTBKS102"

# Solr-backed (ARSA / TXSearch); populated when ``staging_only`` Solr suites
# are implemented (IT-DBPORTAL-*).
PUBLIC_TRAD_ACCESSION: str = ""
PUBLIC_TAXONOMY_ID: str = ""

# Cross-cutting (chosen so it never collides with a real accession; reused
# by the "missing entry" assertions in IT-DETAIL-05, IT-STATUS-04, IT-UMBRELLA-07)
NONEXISTENT_ID: str = "PRJDB_DOES_NOT_EXIST_99999"


def require_accession(name: str, value: str) -> str:
    """Skip the calling test if the accession constant is not populated.

    Empty values mean "this status combination is not present in the staging
    data" (e.g. ``WITHDRAWN_BIOPROJECT_ID`` is empty because the staging ES
    holds no withdrawn entries). Tests that need such accessions should
    funnel through this helper so they ``skip`` instead of failing on
    misleading 404s.
    """
    if not value:
        pytest.skip(f"{name} is not populated (staging data unavailable)")

    return value
