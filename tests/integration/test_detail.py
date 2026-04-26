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
    """IT-DETAIL-04: converter-side alias document hit.

    ddbj-search-converter ingests JGA Secondary IDs as alias ES documents
    whose ``_id`` is the long form but whose ``_source.identifier`` is
    the short Primary. Hitting the long-form path must therefore resolve
    via the direct ``_doc/{long}`` lookup (step 1 of the resolution
    pipeline in api-spec.md § sameAs), without falling through to the
    nested-query fallback.
    """

    def test_alias_resolves_to_canonical(self, app: TestClient) -> None:
        """IT-DETAIL-04: long-form alias returns 200 with the Primary identifier."""
        long_form = require_accession(
            "SECONDARY_JGA_STUDY_ID",
            SECONDARY_JGA_STUDY_ID,
        )
        resp = app.get(f"/entries/jga-study/{long_form}")
        assert resp.status_code == 200
        body = resp.json()
        # ``identifier`` must be the short-form Primary (alias ``_source``
        # is identical to the Primary document's ``_source``).
        assert body["identifier"] != long_form
        # Smoke: the canonical short form looks like JGAS\\d+ and is
        # shorter than the long-form alias.
        assert len(body["identifier"]) < len(long_form)


class TestNotFound:
    """IT-DETAIL-05: missing accessions return 404 across all variants.

    All four variants must yield the same detail string regardless of
    accession value — leaking the accession through ``detail`` would
    break the visibility-hiding contract (api-spec.md § データ可視性,
    cross-checked by IT-STATUS-04).
    """

    @pytest.mark.parametrize("suffix", ["", ".json", ".jsonld", "/dbxrefs.json"])
    def test_variant_returns_404_with_problem_details(
        self,
        app: TestClient,
        suffix: str,
    ) -> None:
        """IT-DETAIL-05: every variant yields 404 + RFC 7807 detail."""
        resp = app.get(f"/entries/bioproject/{NONEXISTENT_ID}{suffix}")
        assert resp.status_code == 404, suffix
        body = resp.json()
        assert body["status"] == 404
        # Accession must not leak into the detail (visibility hiding).
        assert NONEXISTENT_ID not in body["detail"]

    def test_detail_string_consistent_across_variants(self, app: TestClient) -> None:
        """IT-DETAIL-05: all four variants share the same detail string."""
        details = {
            suffix: app.get(f"/entries/bioproject/{NONEXISTENT_ID}{suffix}").json()["detail"]
            for suffix in ("", ".json", ".jsonld", "/dbxrefs.json")
        }
        # Every variant must produce an identical detail string.
        unique = set(details.values())
        assert len(unique) == 1, f"variants disagree: {details}"


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
    here we drive real ES through every variant for every DbType so that
    Pydantic-bypassing streaming paths (.json / .jsonld) do not lose the
    converter required-list-field contract on real data.
    """

    @pytest.mark.parametrize(("type_", "accession"), _REPS)
    @pytest.mark.parametrize("suffix", ["", ".json", ".jsonld"])
    def test_required_list_fields_present(
        self,
        app: TestClient,
        type_: str,
        accession: str,
        suffix: str,
    ) -> None:
        """IT-DETAIL-08: ``dbXrefs`` is always a key in the response."""
        resp = app.get(f"/entries/{type_}/{accession}{suffix}")
        assert resp.status_code == 200, f"{type_}/{accession}{suffix}: {resp.status_code}"
        body = resp.json()
        # ``dbXrefs`` is required across all six DbType groups (truncated
        # on the short variant, full on the streaming variants); the key
        # must be present even when the value is an empty list.
        assert "dbXrefs" in body, f"{type_}/{accession}{suffix}: dbXrefs missing"


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

    def test_full_count_matches_short_variant_total(self, app: TestClient) -> None:
        """IT-DETAIL-09: full dbXrefs length equals short variant ``dbXrefsCount`` total.

        ``dbXrefsCount`` is the per-type aggregate that the short variant
        exposes alongside the truncated dbXrefs; summing it must equal
        the full streaming variant's array length (api-spec.md § dbXrefs).
        """
        full = app.get(f"/entries/bioproject/{PUBLIC_BIOPROJECT_ID}/dbxrefs.json").json()
        short = app.get(f"/entries/bioproject/{PUBLIC_BIOPROJECT_ID}").json()
        full_len = len(full["dbXrefs"])
        # short variant exposes ``dbXrefsCount`` as ``{type: int}``.
        counts = short.get("dbXrefsCount") or {}
        if not isinstance(counts, dict):
            pytest.skip("dbXrefsCount missing on the short variant")
        assert full_len == sum(counts.values()), (
            f"full={full_len} short_total={sum(counts.values())} short_counts={counts}"
        )


class TestSameAsFallthrough:
    """IT-DETAIL-10: malformed Secondary IDs return 404 / 422 (no 5xx)."""

    def test_malformed_secondary_returns_non_5xx(self, app: TestClient) -> None:
        """IT-DETAIL-10: ES sameAs failures collapse to a clean 404 / 422."""
        resp = app.get("/entries/bioproject/SOMETHING_THAT_LOOKS_BROKEN_X@Y")
        assert resp.status_code in {404, 422}


class TestCaseSensitivity:
    """IT-DETAIL-11: case-folding is not applied; lowercase = 404."""

    @pytest.mark.parametrize("suffix", ["", ".json", ".jsonld", "/dbxrefs.json"])
    def test_lowercase_returns_404(self, app: TestClient, suffix: str) -> None:
        """IT-DETAIL-11: lowercase accession resolves to 404 across variants."""
        resp = app.get(f"/entries/bioproject/{PUBLIC_BIOPROJECT_ID.lower()}{suffix}")
        assert resp.status_code == 404, suffix

    def test_lowercase_detail_matches_missing(self, app: TestClient) -> None:
        """IT-DETAIL-11: lowercase 404 detail equals missing-ID 404 detail.

        Both must collapse to the visibility-hiding fixed string used for
        ``private`` / non-existent accessions (IT-STATUS-04 SSOT).
        """
        miss = app.get(f"/entries/bioproject/{NONEXISTENT_ID}")
        low = app.get(f"/entries/bioproject/{PUBLIC_BIOPROJECT_ID.lower()}")
        assert miss.status_code == low.status_code == 404
        assert miss.json()["detail"] == low.json()["detail"]

    def test_canonical_uppercase_still_resolves(self, app: TestClient) -> None:
        """IT-DETAIL-11: uppercase canonical accession returns 200 (anchor)."""
        resp = app.get(f"/entries/bioproject/{PUBLIC_BIOPROJECT_ID}")
        assert resp.status_code == 200


class TestIncludeDbXrefs:
    """IT-DETAIL-12: ``includeDbXrefs=false`` skips DuckDB and drops both keys."""

    def test_include_db_xrefs_false_omits_keys(self, app: TestClient) -> None:
        """IT-DETAIL-12: ``includeDbXrefs=false`` drops dbXrefs / dbXrefsCount."""
        resp = app.get(
            f"/entries/bioproject/{PUBLIC_BIOPROJECT_ID}",
            params={"includeDbXrefs": "false"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "dbXrefs" not in body
        assert "dbXrefsCount" not in body

    def test_db_xrefs_limit_zero_keeps_count(self, app: TestClient) -> None:
        """IT-DETAIL-12: ``dbXrefsLimit=0`` keeps both keys (empty list, real count)."""
        resp = app.get(
            f"/entries/bioproject/{PUBLIC_BIOPROJECT_ID}",
            params={"dbXrefsLimit": 0},
        )
        assert resp.status_code == 200
        body = resp.json()
        # ``dbXrefs`` is preserved as an empty list and ``dbXrefsCount`` as
        # the real (non-truncated) per-type aggregate.
        assert body.get("dbXrefs") == []
        assert "dbXrefsCount" in body

    def test_include_false_overrides_limit(self, app: TestClient) -> None:
        """IT-DETAIL-12: ``includeDbXrefs=false`` wins over ``dbXrefsLimit``."""
        resp = app.get(
            f"/entries/bioproject/{PUBLIC_BIOPROJECT_ID}",
            params={"includeDbXrefs": "false", "dbXrefsLimit": 100},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "dbXrefs" not in body
        assert "dbXrefsCount" not in body


