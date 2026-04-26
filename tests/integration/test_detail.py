"""Integration tests for IT-DETAIL-* scenarios.

GET /entries/{type}/{id} 4 variants (`/{id}`, `.json`, `.jsonld`,
`/dbxrefs.json`) — sameAs fallback, alias hit, missing entries, dbXrefs
truncation, JSON-LD @id, array-field contract, case normalisation.
See ``tests/integration-scenarios.md § IT-DETAIL-*``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import (
    NONEXISTENT_ID,
    PUBLIC_BIOPROJECT_ID,
    PUBLIC_BIOSAMPLE_ID,
    PUBLIC_GEA_ID,
    PUBLIC_JGA_DAC_ID,
    PUBLIC_JGA_DATASET_ID,
    PUBLIC_JGA_POLICY_ID,
    PUBLIC_JGA_STUDY_ID,
    PUBLIC_METABOBANK_ID,
    PUBLIC_SRA_ANALYSIS_ID,
    PUBLIC_SRA_EXPERIMENT_ID,
    PUBLIC_SRA_RUN_ID,
    PUBLIC_SRA_SAMPLE_ID,
    PUBLIC_SRA_STUDY_ID,
    PUBLIC_SRA_SUBMISSION_ID,
    SECONDARY_JGA_STUDY_ID,
    require_accession,
)

# Every public-status (type, accession) representative.
_REPS: list[tuple[str, str]] = [
    ("bioproject", PUBLIC_BIOPROJECT_ID),
    ("biosample", PUBLIC_BIOSAMPLE_ID),
    ("sra-submission", PUBLIC_SRA_SUBMISSION_ID),
    ("sra-study", PUBLIC_SRA_STUDY_ID),
    ("sra-experiment", PUBLIC_SRA_EXPERIMENT_ID),
    ("sra-run", PUBLIC_SRA_RUN_ID),
    ("sra-sample", PUBLIC_SRA_SAMPLE_ID),
    ("sra-analysis", PUBLIC_SRA_ANALYSIS_ID),
    ("jga-study", PUBLIC_JGA_STUDY_ID),
    ("jga-dataset", PUBLIC_JGA_DATASET_ID),
    ("jga-dac", PUBLIC_JGA_DAC_ID),
    ("jga-policy", PUBLIC_JGA_POLICY_ID),
    ("gea", PUBLIC_GEA_ID),
    ("metabobank", PUBLIC_METABOBANK_ID),
]


class TestDetailFourVariantsSuccess:
    """IT-DETAIL-01: every variant returns 200 for a public accession."""

    @pytest.mark.parametrize(("type_", "accession"), _REPS)
    def test_default_variant(self, app: TestClient, type_: str, accession: str) -> None:
        """IT-DETAIL-01: /{type}/{id} → 200."""
        resp = app.get(f"/entries/{type_}/{accession}")
        assert resp.status_code == 200, f"{type_}/{accession}: {resp.status_code}"

    @pytest.mark.parametrize(("type_", "accession"), _REPS)
    def test_json_variant(self, app: TestClient, type_: str, accession: str) -> None:
        """IT-DETAIL-01: ``.json`` → 200 application/json."""
        resp = app.get(f"/entries/{type_}/{accession}.json")
        assert resp.status_code == 200, f"{type_}/{accession}: {resp.status_code}"
        assert "application/json" in resp.headers["content-type"]

    @pytest.mark.parametrize(("type_", "accession"), _REPS)
    def test_jsonld_variant(self, app: TestClient, type_: str, accession: str) -> None:
        """IT-DETAIL-01: ``.jsonld`` → 200 application/ld+json."""
        resp = app.get(f"/entries/{type_}/{accession}.jsonld")
        assert resp.status_code == 200, f"{type_}/{accession}: {resp.status_code}"
        assert "application/ld+json" in resp.headers["content-type"]

    @pytest.mark.parametrize(("type_", "accession"), _REPS)
    def test_dbxrefs_variant(self, app: TestClient, type_: str, accession: str) -> None:
        """IT-DETAIL-01: ``/dbxrefs.json`` → 200 application/json."""
        resp = app.get(f"/entries/{type_}/{accession}/dbxrefs.json")
        assert resp.status_code == 200, f"{type_}/{accession}: {resp.status_code}"
        assert "application/json" in resp.headers["content-type"]


class TestDetailVariantContent:
    """IT-DETAIL-02: short variant truncates dbXrefs; ``.json`` is raw."""

    def test_short_variant_has_identifier(self, app: TestClient) -> None:
        """IT-DETAIL-02: short variant exposes the canonical identifier."""
        resp = app.get(f"/entries/bioproject/{PUBLIC_BIOPROJECT_ID}")
        assert resp.status_code == 200
        assert "identifier" in resp.json()

    def test_json_variant_returns_raw_es_doc(self, app: TestClient) -> None:
        """IT-DETAIL-02: ``.json`` exposes the raw ES ``_source`` identifier."""
        resp = app.get(f"/entries/bioproject/{PUBLIC_BIOPROJECT_ID}.json")
        assert resp.status_code == 200
        assert resp.json().get("identifier") == PUBLIC_BIOPROJECT_ID


class TestSameAsFallback:
    """IT-DETAIL-03: Secondary ID resolves via sameAs nested query.

    JGA-study carries a long-form sameAs (e.g. ``JGAS000001`` ↔
    ``JGAS00000000001``); the long form is the documented Secondary
    that should resolve back to the short-form Primary. BioProject
    sameAs entries point to external DBs (GEO etc.) and are not in
    the API fallback path on staging — see the coverage note in
    tests/integration-note.md.
    """

    def test_secondary_id_resolves_to_primary(self, app: TestClient) -> None:
        """IT-DETAIL-03: Secondary returns 200 with the Primary identifier."""
        secondary = require_accession(
            "SECONDARY_JGA_STUDY_ID",
            SECONDARY_JGA_STUDY_ID,
        )
        resp = app.get(f"/entries/jga-study/{secondary}")
        assert resp.status_code == 200
        assert resp.json()["identifier"] != secondary


class TestAliasDocument:
    """IT-DETAIL-04: converter-side alias document hit."""

    def test_alias_resolves_to_canonical(self) -> None:
        """IT-DETAIL-04: alias placeholder — populated during D-4."""
        pytest.skip("alias accession not configured (D-4 deferred)")


class TestNotFound:
    """IT-DETAIL-05: missing accessions return 404 across all variants."""

    def test_default_variant_404(self, app: TestClient) -> None:
        """IT-DETAIL-05: /{id} on missing accession → 404."""
        resp = app.get(f"/entries/bioproject/{NONEXISTENT_ID}")
        assert resp.status_code == 404

    def test_json_variant_404(self, app: TestClient) -> None:
        """IT-DETAIL-05: ``.json`` on missing accession → 404."""
        resp = app.get(f"/entries/bioproject/{NONEXISTENT_ID}.json")
        assert resp.status_code == 404

    def test_jsonld_variant_404(self, app: TestClient) -> None:
        """IT-DETAIL-05: ``.jsonld`` on missing accession → 404."""
        resp = app.get(f"/entries/bioproject/{NONEXISTENT_ID}.jsonld")
        assert resp.status_code == 404

    def test_dbxrefs_variant_404(self, app: TestClient) -> None:
        """IT-DETAIL-05: ``/dbxrefs.json`` on missing accession → 404."""
        resp = app.get(f"/entries/bioproject/{NONEXISTENT_ID}/dbxrefs.json")
        assert resp.status_code == 404


class TestDbXrefsTruncation:
    """IT-DETAIL-06: dbXrefsLimit truncates the inline list."""

    def test_zero_limit_returns_empty_db_xrefs(self, app: TestClient) -> None:
        """IT-DETAIL-06: dbXrefsLimit=0 → dbXrefs == []."""
        resp = app.get(
            f"/entries/bioproject/{PUBLIC_BIOPROJECT_ID}",
            params={"dbXrefsLimit": 0},
        )
        assert resp.status_code == 200
        assert resp.json().get("dbXrefs") == []

    def test_limit_caps_db_xrefs_length(self, app: TestClient) -> None:
        """IT-DETAIL-06: dbXrefsLimit=N caps the array length."""
        resp = app.get(
            f"/entries/bioproject/{PUBLIC_BIOPROJECT_ID}",
            params={"dbXrefsLimit": 5},
        )
        assert resp.status_code == 200
        body = resp.json()
        if "dbXrefs" in body and body["dbXrefs"] is not None:
            assert len(body["dbXrefs"]) <= 5


class TestJsonLdAtId:
    """IT-DETAIL-07: JSON-LD @id and @context contract."""

    def test_at_id_contains_primary_identifier(self, app: TestClient) -> None:
        """IT-DETAIL-07: ``@id`` embeds the Primary accession."""
        resp = app.get(f"/entries/bioproject/{PUBLIC_BIOPROJECT_ID}.jsonld")
        assert resp.status_code == 200
        body = resp.json()
        assert "@id" in body
        assert PUBLIC_BIOPROJECT_ID in body["@id"]

    def test_context_present(self, app: TestClient) -> None:
        """IT-DETAIL-07: ``@context`` required on JSON-LD."""
        resp = app.get(f"/entries/bioproject/{PUBLIC_BIOPROJECT_ID}.jsonld")
        assert resp.status_code == 200
        assert "@context" in resp.json()


class TestArrayFieldContractInDetail:
    """IT-DETAIL-08: required list fields surface as keys (possibly empty).

    Schema-level coverage lives in tests/unit/schemas/test_converter_contract.py;
    here we smoke-check that real public accessions still satisfy the contract.
    """

    def test_default_variant_has_db_xrefs(self, app: TestClient) -> None:
        """IT-DETAIL-08: dbXrefs key present on the short variant."""
        resp = app.get(f"/entries/bioproject/{PUBLIC_BIOPROJECT_ID}")
        assert resp.status_code == 200
        assert "dbXrefs" in resp.json()


class TestDbXrefsFullStream:
    """IT-DETAIL-09: ``/dbxrefs.json`` streams the complete dbXrefs list."""

    def test_dbxrefs_full_returns_array(self, app: TestClient) -> None:
        """IT-DETAIL-09: ``DbXrefsFullResponse`` carries a ``dbXrefs`` list."""
        resp = app.get(f"/entries/bioproject/{PUBLIC_BIOPROJECT_ID}/dbxrefs.json")
        assert resp.status_code == 200
        body = resp.json()
        # ``DbXrefsFullResponse`` schema is just ``{dbXrefs: list[Xref]}``;
        # the canonical identifier check is covered by IT-DETAIL-01.
        assert "dbXrefs" in body
        assert isinstance(body["dbXrefs"], list)

    def test_full_count_ge_short_variant(self, app: TestClient) -> None:
        """IT-DETAIL-09: full dbXrefs is >= short-variant truncated dbXrefs."""
        full = app.get(f"/entries/bioproject/{PUBLIC_BIOPROJECT_ID}/dbxrefs.json").json()
        short = app.get(f"/entries/bioproject/{PUBLIC_BIOPROJECT_ID}").json()
        full_len = len(full.get("dbXrefs", []))
        short_len = len(short.get("dbXrefs", []) or [])
        assert full_len >= short_len


class TestSameAsFallthrough:
    """IT-DETAIL-10: malformed Secondary IDs return 404 / 422 (no 5xx)."""

    def test_malformed_secondary_returns_non_5xx(self, app: TestClient) -> None:
        """IT-DETAIL-10: ES sameAs failures collapse to a clean 404 / 422."""
        resp = app.get("/entries/bioproject/SOMETHING_THAT_LOOKS_BROKEN_X@Y")
        assert resp.status_code in {404, 422}


class TestCaseNormalization:
    """IT-DETAIL-11: case-insensitive accession lookup (or consistent 404)."""

    def test_lowercase_does_not_5xx(self, app: TestClient) -> None:
        """IT-DETAIL-11: lowercase variant resolves to 200 or 404, never 5xx."""
        resp = app.get(f"/entries/bioproject/{PUBLIC_BIOPROJECT_ID.lower()}")
        assert resp.status_code in {200, 404}
