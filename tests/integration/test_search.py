"""Integration tests for IT-SEARCH-* scenarios.

GET /entries/ (cross-type) and GET /entries/{type}/ (per-type) — pagination,
sort, fields, types, keywords, type-specific filters, nested filters, and
text-match filters. See ``tests/integration-scenarios.md § IT-SEARCH-*``.
"""

from __future__ import annotations

import itertools

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import (
    BIOPROJECT_OBJECT_TYPE_PRIMARY,
    BIOPROJECT_OBJECT_TYPE_UMBRELLA,
    BIOPROJECT_PROJECT_TYPE,
    BIOSAMPLE_COLLECTION_DATE,
    BIOSAMPLE_GEO_LOC_NAME,
    BIOSAMPLE_HOST,
    BIOSAMPLE_ISOLATE,
    BIOSAMPLE_STRAIN,
    GEA_EXPERIMENT_TYPE,
    JGA_DATASET_TYPE,
    JGA_STUDY_TYPE,
    JGA_STUDY_VENDOR,
    METABOBANK_EXPERIMENT_TYPE,
    METABOBANK_STUDY_TYPE,
    METABOBANK_SUBMISSION_TYPE,
    ORGANIZATION_NAME,
    SRA_ANALYSIS_TYPE,
    SRA_EXPERIMENT_LIBRARY_NAME,
    SRA_EXPERIMENT_LIBRARY_PROTOCOL,
    SRA_INSTRUMENT_MODEL,
    SRA_LIBRARY_LAYOUT,
    SRA_LIBRARY_SELECTION,
    SRA_LIBRARY_SOURCE,
    SRA_LIBRARY_STRATEGY,
    SRA_PLATFORM,
)

# Every per-type endpoint documented in api-spec.md / db-portal-api-spec.md.
_ALL_TYPES = (
    "bioproject",
    "biosample",
    "sra-submission",
    "sra-study",
    "sra-experiment",
    "sra-run",
    "sra-sample",
    "sra-analysis",
    "jga-study",
    "jga-dataset",
    "jga-dac",
    "jga-policy",
    "gea",
    "metabobank",
)

# IT-SEARCH-19 matrix: (type endpoint, parameter, representative bucket).
_TYPE_SPECIFIC_TERM_FILTERS: list[tuple[str, str, str]] = [
    ("sra-experiment", "libraryStrategy", SRA_LIBRARY_STRATEGY),
    ("sra-experiment", "librarySource", SRA_LIBRARY_SOURCE),
    ("sra-experiment", "librarySelection", SRA_LIBRARY_SELECTION),
    ("sra-experiment", "libraryLayout", SRA_LIBRARY_LAYOUT),
    ("sra-experiment", "platform", SRA_PLATFORM),
    ("sra-experiment", "instrumentModel", SRA_INSTRUMENT_MODEL),
    ("sra-analysis", "analysisType", SRA_ANALYSIS_TYPE),
    ("gea", "experimentType", GEA_EXPERIMENT_TYPE),
    ("metabobank", "studyType", METABOBANK_STUDY_TYPE),
    ("metabobank", "experimentType", METABOBANK_EXPERIMENT_TYPE),
    ("metabobank", "submissionType", METABOBANK_SUBMISSION_TYPE),
    ("jga-study", "studyType", JGA_STUDY_TYPE),
    ("jga-dataset", "datasetType", JGA_DATASET_TYPE),
]

# IT-SEARCH-17 matrix: (type endpoint, text-match parameter, representative
# token) per api-spec.md § text match フィールド検索. Coverage spans the
# nine documented fields; representative tokens come from conftest.py
# (top-bucket / top-match probes against the connected ES dataset).
_TEXT_MATCH_FIELDS: list[tuple[str, str, str]] = [
    ("bioproject", "projectType", BIOPROJECT_PROJECT_TYPE),
    ("biosample", "host", BIOSAMPLE_HOST),
    ("biosample", "strain", BIOSAMPLE_STRAIN),
    ("biosample", "isolate", BIOSAMPLE_ISOLATE),
    ("biosample", "geoLocName", BIOSAMPLE_GEO_LOC_NAME),
    ("biosample", "collectionDate", BIOSAMPLE_COLLECTION_DATE),
    ("sra-experiment", "libraryName", SRA_EXPERIMENT_LIBRARY_NAME),
    ("sra-experiment", "libraryConstructionProtocol", SRA_EXPERIMENT_LIBRARY_PROTOCOL),
    ("jga-study", "vendor", JGA_STUDY_VENDOR),
]


class TestCrossTypeSearchSuccess:
    """IT-SEARCH-01: GET /entries/ returns paginated results."""

    def test_returns_200_with_required_keys(self, app: TestClient) -> None:
        """IT-SEARCH-01: pagination.total + items present, perPage capped."""
        resp = app.get("/entries/", params={"perPage": 5})
        assert resp.status_code == 200
        body = resp.json()
        assert "pagination" in body
        assert isinstance(body["pagination"]["total"], int)
        assert "items" in body
        assert isinstance(body["items"], list)
        assert len(body["items"]) <= 5


class TestPerTypeSearchSuccess:
    """IT-SEARCH-02: per-type endpoints succeed for every documented DbType."""

    def test_each_type_returns_200(self, app: TestClient) -> None:
        """IT-SEARCH-02: every documented type endpoint is reachable."""
        for type_ in _ALL_TYPES:
            resp = app.get(f"/entries/{type_}/", params={"perPage": 1})
            assert resp.status_code == 200, f"type={type_} failed with {resp.status_code}"

    def test_each_type_response_filtered_to_path_type(self, app: TestClient) -> None:
        """IT-SEARCH-02: per-type response items only carry ``type==path``."""
        for type_ in _ALL_TYPES:
            resp = app.get(f"/entries/{type_}/", params={"perPage": 5})
            assert resp.status_code == 200, type_
            for item in resp.json()["items"]:
                # ``type`` field on each item should match the path filter.
                assert item.get("type") == type_, f"{type_}: item carries type={item.get('type')}"


class TestPagination:
    """IT-SEARCH-03 / 04: page * perPage limits."""

    def test_within_deep_paging_limit_succeeds(self, app: TestClient) -> None:
        """IT-SEARCH-03: page * perPage == 10000 (boundary) succeeds."""
        resp = app.get("/entries/", params={"page": 100, "perPage": 100})
        assert resp.status_code == 200

    def test_repeated_call_returns_same_result_set(self, app: TestClient) -> None:
        """IT-SEARCH-03: same params produce a deterministic result set."""
        resp_a = app.get("/entries/", params={"page": 1, "perPage": 5, "keywords": "cancer"})
        resp_b = app.get("/entries/", params={"page": 1, "perPage": 5, "keywords": "cancer"})
        assert resp_a.status_code == resp_b.status_code == 200
        ids_a = [item["identifier"] for item in resp_a.json()["items"]]
        ids_b = [item["identifier"] for item in resp_b.json()["items"]]
        assert ids_a == ids_b, f"non-deterministic ordering: {ids_a} vs {ids_b}"

    def test_exceeding_deep_paging_limit_returns_400(self, app: TestClient) -> None:
        """IT-SEARCH-04: page * perPage > 10000 → 400 ProblemDetails with cursor hint."""
        resp = app.get("/entries/", params={"page": 101, "perPage": 100})
        assert resp.status_code == 400
        assert "application/problem+json" in resp.headers["content-type"]
        body = resp.json()
        assert body["status"] == 400
        # The detail must direct callers to ``cursor`` so they can recover.
        assert "cursor" in body["detail"].lower(), body["detail"]


class TestCursorPagination:
    """IT-SEARCH-05/06/07: cursor pagination basics, tampering, expiry."""

    def test_first_page_includes_pagination_block(self, app: TestClient) -> None:
        """IT-SEARCH-05: response carries pagination metadata (nextCursor / hasNext)."""
        resp = app.get("/entries/", params={"perPage": 5})
        assert resp.status_code == 200
        body = resp.json()
        # Pagination is exposed either at the top level or under a "pagination" key.
        has_pagination = "nextCursor" in body or "pagination" in body
        assert has_pagination

    def test_invalid_cursor_token_returns_400(self, app: TestClient) -> None:
        """IT-SEARCH-06: a tampered cursor token yields 400 (not 5xx)."""
        resp = app.get("/entries/", params={"cursor": "not-a-valid-token"})
        assert resp.status_code == 400

    def test_cursor_with_page_returns_400(self, app: TestClient) -> None:
        """IT-SEARCH-06: cursor + page is mutually exclusive → 400."""
        resp = app.get("/entries/", params={"cursor": "anything", "page": 2})
        assert resp.status_code == 400


class TestSortParameter:
    """IT-SEARCH-08: sort parsing (valid + invalid direction / field)."""

    def test_valid_sort_succeeds(self, app: TestClient) -> None:
        """IT-SEARCH-08: documented sort form ``field:direction`` works."""
        resp = app.get(
            "/entries/",
            params={"sort": "datePublished:desc", "perPage": 5},
        )
        assert resp.status_code == 200

    def test_descending_sort_is_actually_descending(self, app: TestClient) -> None:
        """IT-SEARCH-08: ``datePublished:desc`` produces a non-increasing sequence."""
        resp = app.get(
            "/entries/",
            params={"sort": "datePublished:desc", "perPage": 20},
        )
        assert resp.status_code == 200
        dates = [item.get("datePublished") for item in resp.json()["items"] if item.get("datePublished")]
        for left, right in itertools.pairwise(dates):
            assert left >= right, f"sort broken: {left} < {right}"

    def test_invalid_direction_returns_422(self, app: TestClient) -> None:
        """IT-SEARCH-08: unknown direction → 422."""
        resp = app.get("/entries/", params={"sort": "datePublished:foo"})
        assert resp.status_code == 422

    def test_invalid_field_returns_422(self, app: TestClient) -> None:
        """IT-SEARCH-08: unknown sort field → 422."""
        resp = app.get("/entries/", params={"sort": "__not_a_field__:asc"})
        assert resp.status_code == 422


class TestFieldsFilter:
    """IT-SEARCH-09: fields parameter limits returned keys."""

    def test_fields_filter_includes_requested(self, app: TestClient) -> None:
        """IT-SEARCH-09: requested fields are present in items."""
        resp = app.get(
            "/entries/",
            params={"fields": "identifier,type", "perPage": 3},
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert items, "no hits to verify fields filter"
        for item in items:
            assert "identifier" in item
            assert "type" in item

    def test_unrequested_fields_carry_null_values(self, app: TestClient) -> None:
        """IT-SEARCH-09: fields outside ``fields=`` come back as ``null``.

        ES ``_source_includes`` only fetches the requested fields, but the
        FastAPI / Pydantic response retains every schema-declared key.
        Unrequested fields therefore surface as ``None`` (per
        api-spec.md § 検索パラメータ). If a non-requested field comes
        back with a real value, the ``fields`` allowlist is not being
        applied and the test fails loudly.
        """
        resp = app.get(
            "/entries/",
            params={"fields": "identifier,type", "perPage": 3},
        )
        items = resp.json()["items"]
        assert items
        for item in items:
            # Unrequested top-level fields exist in the schema but must
            # not be populated from ES.
            assert item.get("title") is None, item
            assert item.get("description") is None, item


class TestTypesFilter:
    """IT-SEARCH-10: types comma-separated narrows the search."""

    def test_types_filter_returns_only_specified(self, app: TestClient) -> None:
        """IT-SEARCH-10: types=bioproject,biosample restricts the result set."""
        resp = app.get(
            "/entries/",
            params={"types": "bioproject,biosample", "perPage": 20},
        )
        assert resp.status_code == 200
        for item in resp.json()["items"]:
            assert item["type"] in {"bioproject", "biosample"}

    def test_single_type_total_le_combined(self, app: TestClient) -> None:
        """IT-SEARCH-10: total(bioproject) <= total(bioproject,biosample)."""
        only_bp = app.get("/entries/", params={"types": "bioproject"}).json()["pagination"]["total"]
        combined = app.get(
            "/entries/",
            params={"types": "bioproject,biosample"},
        ).json()["pagination"]["total"]
        assert only_bp <= combined


class TestKeywordOperators:
    """IT-SEARCH-11: keywords semantics — relative invariants between operators.

    ``AND`` / ``OR`` / ``NOT`` switch via the ``keywordOperator`` parameter
    (or are encoded inside the DSL on ``/db-portal/*``). Here we assert
    structural relationships between the resulting totals so that bug-driven
    counter-monotonic regressions surface (e.g. ``OR`` shrinking the result
    set).
    """

    def test_phrase_more_restrictive_than_token(self, app: TestClient) -> None:
        """IT-SEARCH-11: a longer phrase cannot match more docs than a sub-token."""
        single = app.get("/entries/", params={"keywords": "genome"}).json()["pagination"]["total"]
        phrase = app.get("/entries/", params={"keywords": '"genome sequencing"'}).json()["pagination"]["total"]
        # Phrase is strictly more restrictive than its lexical sub-token.
        assert phrase <= single

    def test_or_at_least_as_broad_as_and(self, app: TestClient) -> None:
        """IT-SEARCH-11: ``OR`` total >= ``AND`` total for the same keyword set."""
        and_total = app.get(
            "/entries/",
            params={"keywords": "cancer,brain", "keywordOperator": "AND"},
        ).json()["pagination"]["total"]
        or_total = app.get(
            "/entries/",
            params={"keywords": "cancer,brain", "keywordOperator": "OR"},
        ).json()["pagination"]["total"]
        # OR can only expand the result set relative to AND.
        assert or_total >= and_total
        # Sanity: at least one keyword should match something.
        assert or_total > 0

    def test_symbol_keyword_uses_phrase_match(self, app: TestClient) -> None:
        """IT-SEARCH-11: a symbol-bearing keyword (``HIF-1``) doesn't fall back to ``HIF``.

        Auto-phrasing (api-spec.md § フレーズマッチ) prevents the analyzer
        from splitting on ``-``; otherwise ``HIF-1`` would balloon to
        every doc containing ``HIF``.
        """
        sym = app.get("/entries/", params={"keywords": "HIF-1"}).json()["pagination"]["total"]
        bare = app.get("/entries/", params={"keywords": "HIF"}).json()["pagination"]["total"]
        # Phrase match is at most as broad as a sub-token alone.
        assert sym <= bare


class TestArrayFieldContractInSearch:
    """IT-SEARCH-12: required list fields surface as keys (possibly empty)."""

    def test_default_items_carry_db_xrefs_key(self, app: TestClient) -> None:
        """IT-SEARCH-12: dbXrefs key present on default response items."""
        items = app.get("/entries/", params={"perPage": 3}).json().get("items", [])
        if not items:
            return
        for item in items:
            assert "dbXrefs" in item


class TestTypeSpecificObjectTypesFilter:
    """IT-SEARCH-13: BioProject objectTypes filter."""

    def test_bioproject_object_types_filter_returns_200(self, app: TestClient) -> None:
        """IT-SEARCH-13: objectTypes value is accepted on /entries/bioproject/."""
        # Bucket key per `objectType` aggregation: "BioProject" or "UmbrellaBioProject".
        resp = app.get(
            "/entries/bioproject/",
            params={"objectTypes": "UmbrellaBioProject", "perPage": 5},
        )
        assert resp.status_code == 200

    def test_object_types_on_cross_type_returns_422(self, app: TestClient) -> None:
        """IT-SEARCH-13: objectTypes is invalid on cross-type /entries/."""
        resp = app.get("/entries/", params={"objectTypes": "UmbrellaBioProject"})
        assert resp.status_code == 422


class TestFacetsParamAllowlist:
    """IT-SEARCH-14: facets parameter allowlist control."""

    def test_facets_filter_explicit_pair(self, app: TestClient) -> None:
        """IT-SEARCH-14: explicit facets list populates the requested aggregations."""
        # ``organism`` / ``accessibility`` are part of the facet allowlist
        # (``VALID_FACET_FIELDS``); ``organization`` / ``publication`` are
        # nested *filters*, not facets, so they are rejected at this layer.
        resp = app.get(
            "/entries/",
            params={
                "facets": "organism,accessibility",
                "includeFacets": "true",
                "perPage": 3,
            },
        )
        assert resp.status_code == 200
        facets = resp.json().get("facets") or {}
        for name in ("organism", "accessibility"):
            assert name in facets


class TestNestedFieldFilters:
    """IT-SEARCH-15: organization / publication / grant available cross-type."""

    def test_organization_on_cross_type_returns_200(self, app: TestClient) -> None:
        """IT-SEARCH-15: organization filter works on cross-type."""
        resp = app.get(
            "/entries/",
            params={"organization": ORGANIZATION_NAME, "perPage": 1},
        )
        assert resp.status_code == 200

    def test_organization_on_per_type_returns_200(self, app: TestClient) -> None:
        """IT-SEARCH-15: organization filter works on per-type."""
        resp = app.get(
            "/entries/bioproject/",
            params={"organization": ORGANIZATION_NAME, "perPage": 1},
        )
        assert resp.status_code == 200

    def test_organization_narrows_cross_type(self, app: TestClient) -> None:
        """IT-SEARCH-15: organization filter has total > 0 and <= unfiltered cross-type."""
        all_total = app.get(
            "/entries/",
            params={"perPage": 1},
        ).json()["pagination"]["total"]
        org_total = app.get(
            "/entries/",
            params={"organization": ORGANIZATION_NAME, "perPage": 1},
        ).json()["pagination"]["total"]
        assert 0 < org_total <= all_total


class TestNestedFieldGroupRestriction:
    """IT-SEARCH-16: type-group-restricted nested filters (externalLinkLabel etc.)."""

    def test_external_link_label_on_cross_type_returns_422(self, app: TestClient) -> None:
        """IT-SEARCH-16: externalLinkLabel rejected on /entries/."""
        resp = app.get("/entries/", params={"externalLinkLabel": "github"})
        assert resp.status_code == 422

    def test_external_link_label_on_bioproject_returns_200(self, app: TestClient) -> None:
        """IT-SEARCH-16: externalLinkLabel allowed on bioproject."""
        resp = app.get(
            "/entries/bioproject/",
            params={"externalLinkLabel": "github", "perPage": 1},
        )
        assert resp.status_code == 200

    def test_external_link_label_on_biosample_returns_422(self, app: TestClient) -> None:
        """IT-SEARCH-16: externalLinkLabel rejected on biosample (out of group)."""
        resp = app.get(
            "/entries/biosample/",
            params={"externalLinkLabel": "github", "perPage": 1},
        )
        assert resp.status_code == 422


class TestTextMatchFields:
    """IT-SEARCH-17: every text-match field works on its documented type group."""

    @pytest.mark.parametrize(("type_", "param", "value"), _TEXT_MATCH_FIELDS)
    def test_text_match_on_documented_type(
        self,
        app: TestClient,
        type_: str,
        param: str,
        value: str,
    ) -> None:
        """IT-SEARCH-17: documented (type, field) pair returns 200 with hits."""
        resp = app.get(f"/entries/{type_}/", params={param: value, "perPage": 1})
        assert resp.status_code == 200, f"{type_}/{param}={value}: {resp.status_code}"
        body = resp.json()
        # Representative tokens are pinned in conftest to bucket keys with
        # populated hit counts; ``> 0`` catches analyzer / mapping regressions.
        assert body["pagination"]["total"] > 0, f"{type_}/{param}={value} returned 0"

    def test_symbol_token_auto_phrases(self, app: TestClient) -> None:
        """IT-SEARCH-17: ``host=HIF-1`` is at most as broad as ``host=HIF`` (auto-phrase)."""
        with_symbol = app.get(
            "/entries/biosample/",
            params={"host": "HIF-1", "perPage": 1},
        ).json()["pagination"]["total"]
        bare = app.get(
            "/entries/biosample/",
            params={"host": "HIF", "perPage": 1},
        ).json()["pagination"]["total"]
        assert with_symbol <= bare


class TestTextMatchCrossTypeRejected:
    """IT-SEARCH-18: text-match fields are not allowed on cross-type."""

    def test_host_on_cross_type_returns_422(self, app: TestClient) -> None:
        """IT-SEARCH-18: host rejected on /entries/."""
        resp = app.get("/entries/", params={"host": "Homo sapiens"})
        assert resp.status_code == 422


class TestTypeSpecificTermFilters:
    """IT-SEARCH-19: type-specific term filters across sra / gea / metabobank / jga."""

    @pytest.mark.parametrize(("type_", "param", "value"), _TYPE_SPECIFIC_TERM_FILTERS)
    def test_filter_returns_non_zero_total(
        self,
        app: TestClient,
        type_: str,
        param: str,
        value: str,
    ) -> None:
        """IT-SEARCH-19: representative bucket key narrows the result set."""
        resp = app.get(f"/entries/{type_}/", params={param: value, "perPage": 1})
        assert resp.status_code == 200, f"{type_}/{param}={value}: {resp.status_code}"
        body = resp.json()
        assert body["pagination"]["total"] > 0, f"{type_}/{param}={value} returned 0"

    @pytest.mark.parametrize(("type_", "param", "value"), _TYPE_SPECIFIC_TERM_FILTERS)
    def test_filter_rejected_on_cross_type(
        self,
        app: TestClient,
        type_: str,
        param: str,
        value: str,
    ) -> None:
        """IT-SEARCH-19: cross-type endpoint refuses type-specific term params."""
        resp = app.get("/entries/", params={param: value})
        assert resp.status_code == 422, f"cross-type accepted {param}={value}"

    def test_object_types_or_combination(self, app: TestClient) -> None:
        """IT-SEARCH-19: comma-separated values OR within bioproject objectTypes."""
        primary = app.get(
            "/entries/bioproject/",
            params={"objectTypes": BIOPROJECT_OBJECT_TYPE_PRIMARY, "perPage": 1},
        ).json()["pagination"]["total"]
        umbrella = app.get(
            "/entries/bioproject/",
            params={"objectTypes": BIOPROJECT_OBJECT_TYPE_UMBRELLA, "perPage": 1},
        ).json()["pagination"]["total"]
        combined = app.get(
            "/entries/bioproject/",
            params={
                "objectTypes": (f"{BIOPROJECT_OBJECT_TYPE_PRIMARY},{BIOPROJECT_OBJECT_TYPE_UMBRELLA}"),
                "perPage": 1,
            },
        ).json()["pagination"]["total"]
        assert primary > 0
        assert umbrella > 0
        # OR semantics: combined equals the sum of disjoint buckets.
        assert combined == primary + umbrella


class TestOrganismFilter:
    """IT-SEARCH-20: ``organism`` filter narrows results, AND-combines with keywords."""

    def test_organism_filter_returns_200(self, app: TestClient) -> None:
        """IT-SEARCH-20: organism=9606 succeeds."""
        resp = app.get("/entries/", params={"organism": "9606", "perPage": 1})
        assert resp.status_code == 200
        assert resp.json()["pagination"]["total"] > 0

    def test_organism_combined_with_keywords_is_subset(self, app: TestClient) -> None:
        """IT-SEARCH-20: ``cancer AND organism=9606`` <= ``cancer`` alone."""
        cancer = app.get("/entries/", params={"keywords": "cancer"}).json()["pagination"]["total"]
        cancer_and_human = app.get(
            "/entries/",
            params={"keywords": "cancer", "organism": "9606"},
        ).json()["pagination"]["total"]
        assert cancer_and_human <= cancer


class TestDateRange:
    """IT-SEARCH-21: date range parameters and validation errors."""

    def test_valid_range_succeeds(self, app: TestClient) -> None:
        """IT-SEARCH-21: ISO 8601 ``YYYY-MM-DD`` accepted."""
        resp = app.get(
            "/entries/",
            params={
                "datePublishedFrom": "2020-01-01",
                "datePublishedTo": "2020-12-31",
                "perPage": 1,
            },
        )
        assert resp.status_code == 200

    def test_single_bound_succeeds(self, app: TestClient) -> None:
        """IT-SEARCH-21: ``From`` alone is accepted."""
        resp = app.get(
            "/entries/",
            params={"datePublishedFrom": "2020-01-01", "perPage": 1},
        )
        assert resp.status_code == 200

    @pytest.mark.parametrize("bad", ["2020/01/01", "20200101", "2024-02-30", "2024-13-01"])
    def test_invalid_format_or_date_returns_422(self, app: TestClient, bad: str) -> None:
        """IT-SEARCH-21: format violations and impossible dates yield 422."""
        resp = app.get("/entries/", params={"datePublishedFrom": bad})
        assert resp.status_code == 422, bad

    def test_inverted_range_yields_zero(self, app: TestClient) -> None:
        """IT-SEARCH-21: ``From > To`` is accepted by Pydantic and zeros out at ES."""
        resp = app.get(
            "/entries/",
            params={
                "datePublishedFrom": "2030-01-01",
                "datePublishedTo": "2000-01-01",
                "perPage": 1,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["pagination"]["total"] == 0


class TestKeywordFields:
    """IT-SEARCH-22: ``keywordFields`` allowlist (identifier / title / name / description)."""

    @pytest.mark.parametrize("field", ["identifier", "title", "name", "description"])
    def test_each_field_accepted(self, app: TestClient, field: str) -> None:
        """IT-SEARCH-22: each documented field name is accepted."""
        resp = app.get(
            "/entries/",
            params={"keywords": "cancer", "keywordFields": field, "perPage": 1},
        )
        assert resp.status_code == 200, field

    def test_combined_fields_succeed(self, app: TestClient) -> None:
        """IT-SEARCH-22: comma-separated allowlisted fields succeed."""
        resp = app.get(
            "/entries/",
            params={"keywords": "cancer", "keywordFields": "title,name", "perPage": 1},
        )
        assert resp.status_code == 200

    def test_unknown_field_returns_422(self, app: TestClient) -> None:
        """IT-SEARCH-22: typo-style field name yields 422."""
        resp = app.get(
            "/entries/",
            params={"keywords": "cancer", "keywordFields": "__not_a_field__"},
        )
        assert resp.status_code == 422

    def test_field_filter_narrows_results(self, app: TestClient) -> None:
        """IT-SEARCH-22: a field-restricted search has total <= unrestricted."""
        unrestricted = app.get(
            "/entries/",
            params={"keywords": "cancer"},
        ).json()["pagination"]["total"]
        title_only = app.get(
            "/entries/",
            params={"keywords": "cancer", "keywordFields": "title"},
        ).json()["pagination"]["total"]
        assert title_only <= unrestricted


class TestKeywordOperator:
    """IT-SEARCH-23: ``keywordOperator`` AND vs OR semantics."""

    def test_or_total_at_least_as_large_as_and(self, app: TestClient) -> None:
        """IT-SEARCH-23: OR total >= AND total for the same keywords."""
        and_total = app.get(
            "/entries/",
            params={"keywords": "cancer,brain", "keywordOperator": "AND"},
        ).json()["pagination"]["total"]
        or_total = app.get(
            "/entries/",
            params={"keywords": "cancer,brain", "keywordOperator": "OR"},
        ).json()["pagination"]["total"]
        assert or_total >= and_total

    def test_or_at_least_as_large_as_single(self, app: TestClient) -> None:
        """IT-SEARCH-23: OR(cancer,brain) total >= total(cancer)."""
        single = app.get("/entries/", params={"keywords": "cancer"}).json()["pagination"]["total"]
        or_total = app.get(
            "/entries/",
            params={"keywords": "cancer,brain", "keywordOperator": "OR"},
        ).json()["pagination"]["total"]
        assert or_total >= single

    def test_invalid_operator_returns_422(self, app: TestClient) -> None:
        """IT-SEARCH-23: anything outside {AND, OR} yields 422."""
        resp = app.get(
            "/entries/",
            params={"keywords": "cancer", "keywordOperator": "XOR"},
        )
        assert resp.status_code == 422


class TestDbXrefsLimitOnList:
    """IT-SEARCH-24: ``dbXrefsLimit`` truncates list-endpoint dbXrefs per type."""

    def test_zero_limit_returns_empty_lists(self, app: TestClient) -> None:
        """IT-SEARCH-24: ``dbXrefsLimit=0`` empties dbXrefs but keeps the count."""
        resp = app.get("/entries/", params={"perPage": 5, "dbXrefsLimit": 0})
        assert resp.status_code == 200
        for item in resp.json()["items"]:
            assert item.get("dbXrefs") == []
            assert "dbXrefsCount" in item

    def test_per_type_truncation(self, app: TestClient) -> None:
        """IT-SEARCH-24: each (item, type) bucket length <= dbXrefsLimit."""
        limit = 3
        resp = app.get("/entries/", params={"perPage": 10, "dbXrefsLimit": limit})
        assert resp.status_code == 200
        for item in resp.json()["items"]:
            xrefs = item.get("dbXrefs") or []
            # Group by ``type`` and assert no bucket exceeds the limit.
            counts: dict[str, int] = {}
            for x in xrefs:
                counts[x.get("type", "")] = counts.get(x.get("type", ""), 0) + 1
            for t, c in counts.items():
                assert c <= limit, f"item={item.get('identifier')} type={t} count={c}"

    def test_upper_bound_accepted(self, app: TestClient) -> None:
        """IT-SEARCH-24: ``dbXrefsLimit=1000`` is accepted."""
        resp = app.get("/entries/", params={"perPage": 1, "dbXrefsLimit": 1000})
        assert resp.status_code == 200

    def test_above_upper_bound_returns_422(self, app: TestClient) -> None:
        """IT-SEARCH-24: ``dbXrefsLimit=1001`` triggers Pydantic validation."""
        resp = app.get("/entries/", params={"perPage": 1, "dbXrefsLimit": 1001})
        assert resp.status_code == 422


class TestIncludePropertiesOnList:
    """IT-SEARCH-25: ``includeProperties`` controls the ``properties`` key."""

    def test_default_includes_properties(self, app: TestClient) -> None:
        """IT-SEARCH-25: ``includeProperties`` defaults to true."""
        resp = app.get("/entries/", params={"perPage": 3})
        assert resp.status_code == 200
        for item in resp.json()["items"]:
            assert "properties" in item

    def test_false_omits_properties(self, app: TestClient) -> None:
        """IT-SEARCH-25: ``includeProperties=false`` drops the key from items."""
        resp = app.get(
            "/entries/",
            params={"perPage": 3, "includeProperties": "false"},
        )
        assert resp.status_code == 200
        for item in resp.json()["items"]:
            assert "properties" not in item

    def test_total_invariant_across_modes(self, app: TestClient) -> None:
        """IT-SEARCH-25: ``includeProperties`` does not affect the result set."""
        a = app.get("/entries/", params={"keywords": "cancer", "perPage": 1}).json()
        b = app.get(
            "/entries/",
            params={"keywords": "cancer", "perPage": 1, "includeProperties": "false"},
        ).json()
        assert a["pagination"]["total"] == b["pagination"]["total"]


class TestIncludeDbXrefsOnList:
    """IT-SEARCH-26: ``includeDbXrefs=false`` drops both dbXrefs / dbXrefsCount."""

    def test_false_omits_both_keys(self, app: TestClient) -> None:
        """IT-SEARCH-26: list items lose both keys when DuckDB is bypassed."""
        resp = app.get(
            "/entries/",
            params={"perPage": 5, "includeDbXrefs": "false"},
        )
        assert resp.status_code == 200
        for item in resp.json()["items"]:
            assert "dbXrefs" not in item
            assert "dbXrefsCount" not in item

    def test_db_xrefs_limit_zero_keeps_count(self, app: TestClient) -> None:
        """IT-SEARCH-26: ``dbXrefsLimit=0`` keeps both keys on the list path."""
        resp = app.get("/entries/", params={"perPage": 5, "dbXrefsLimit": 0})
        assert resp.status_code == 200
        for item in resp.json()["items"]:
            assert item.get("dbXrefs") == []
            # ``dbXrefsCount`` is the per-type aggregate dict (possibly empty).
            assert isinstance(item.get("dbXrefsCount"), dict)

    def test_include_false_overrides_limit(self, app: TestClient) -> None:
        """IT-SEARCH-26: ``includeDbXrefs=false`` wins over ``dbXrefsLimit``."""
        resp = app.get(
            "/entries/",
            params={"perPage": 5, "includeDbXrefs": "false", "dbXrefsLimit": 100},
        )
        assert resp.status_code == 200
        for item in resp.json()["items"]:
            assert "dbXrefs" not in item
            assert "dbXrefsCount" not in item
