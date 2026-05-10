"""Integration tests for IT-FACETS-* scenarios.

GET /facets, GET /facets/{type}, and the includeFacets toggle on
/entries/. See ``tests/integration-scenarios.md § IT-FACETS-*`` for the
SSOT.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

# Defaults documented in api-spec.md § ファセット集計対象の選択.
_CROSS_TYPE_DEFAULTS = {"organism", "accessibility", "type"}

# Strings unlikely to collide with any real facet name (used for 422/400 probes).
_UNKNOWN_FACET = "__definitely_not_a_facet__"

# IT-FACETS-08 matrix: (type endpoint, type-specific facet field).
_TYPE_SPECIFIC_FACETS: list[tuple[str, str]] = [
    ("sra-experiment", "libraryStrategy"),
    ("sra-experiment", "librarySource"),
    ("sra-experiment", "librarySelection"),
    ("sra-experiment", "platform"),
    ("sra-experiment", "instrumentModel"),
    ("gea", "experimentType"),
    ("metabobank", "studyType"),
    ("metabobank", "experimentType"),
    ("metabobank", "submissionType"),
    ("jga-study", "studyType"),
]


class TestCrossTypeFacets:
    """IT-FACETS-01: GET /facets returns cross-type aggregations."""

    def test_returns_200(self, app: TestClient) -> None:
        """IT-FACETS-01: endpoint reachable."""
        resp = app.get("/facets")
        assert resp.status_code == 200

    def test_response_shape(self, app: TestClient) -> None:
        """IT-FACETS-01: response has a facets dict."""
        body = app.get("/facets").json()
        assert "facets" in body
        assert isinstance(body["facets"], dict)

    def test_default_aggregations_present(self, app: TestClient) -> None:
        """IT-FACETS-01: cross-type defaults populated as lists (not None)."""
        facets = app.get("/facets").json()["facets"]
        for name in _CROSS_TYPE_DEFAULTS:
            assert facets.get(name) is not None, f"missing default facet: {name}"
            assert isinstance(facets[name], list)

    def test_type_specific_facets_omitted_by_default(self, app: TestClient) -> None:
        """IT-FACETS-01: cross-type defaults do NOT include type-specific facets.

        Per api-spec.md § ファセット集計対象の選択, the default selection is
        ``organism`` / ``accessibility`` / ``type`` only; ``objectType`` is
        bioproject-specific and must be opt-in via ``facets=``.
        """
        facets = app.get("/facets").json()["facets"]
        # Schema may expose the field as ``None`` (Facets pydantic model
        # has it optional); the explicit assertion is "not aggregated".
        for type_specific in (
            "objectType",
            "libraryStrategy",
            "experimentType",
            "studyType",
            "submissionType",
        ):
            assert facets.get(type_specific) is None, f"{type_specific} should not be returned by default"


class TestTypeSpecificFacets:
    """IT-FACETS-02: GET /facets/{type} surfaces type-specific aggregations."""

    def test_bioproject_returns_200(self, app: TestClient) -> None:
        """IT-FACETS-02: /facets/bioproject is reachable."""
        resp = app.get("/facets/bioproject")
        assert resp.status_code == 200

    def test_bioproject_object_type_via_facets_param(self, app: TestClient) -> None:
        """IT-FACETS-02: ``objectType`` is bioproject-specific and opt-in."""
        body = app.get("/facets/bioproject", params={"facets": "objectType"}).json()
        bucket = body["facets"].get("objectType")
        assert bucket is not None
        assert isinstance(bucket, list)


class TestCrossTypeWithTypeSpecificFacet:
    """IT-FACETS-03: cross-type endpoint accepts allowlisted type-specific names.

    Per api-spec.md § ファセット集計対象の選択: cross-type ``/facets``
    accepts any allowlisted facet name. Indices that don't carry the field
    simply yield empty buckets, rather than 422.
    """

    def test_object_type_accepted_in_cross_type(self, app: TestClient) -> None:
        """IT-FACETS-03: ``objectType`` is allowed on cross-type ``/facets``."""
        resp = app.get("/facets", params={"facets": "objectType"})
        assert resp.status_code == 200
        bucket = resp.json()["facets"].get("objectType")
        assert bucket is not None
        assert isinstance(bucket, list)


class TestEntriesIncludeFacets:
    """IT-FACETS-04: /entries/?includeFacets=true bundles items and facets."""

    def test_with_include_facets(self, app: TestClient) -> None:
        """IT-FACETS-04: includeFacets=true populates the facets dict."""
        resp = app.get("/entries/", params={"perPage": 5, "includeFacets": "true"})
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        facets = body.get("facets")
        assert facets is not None
        assert isinstance(facets, dict)
        # At least one cross-type default appears as a list.
        assert any(facets.get(name) is not None for name in _CROSS_TYPE_DEFAULTS)


class TestFacetsStatusFilter:
    """IT-FACETS-05: facet aggregation runs over status:public only.

    We cannot probe individual hidden statuses without staging-data
    coverage of withdrawn/private — see conftest.py constants. The
    invariant we *can* assert is structural: bucket counts are
    non-negative integers, and aggregations succeed (not 5xx).
    """

    def test_buckets_are_non_negative_integers(self, app: TestClient) -> None:
        """IT-FACETS-05: every bucket count is a non-negative int."""
        body = app.get("/facets").json()
        for name in _CROSS_TYPE_DEFAULTS:
            for bucket in body["facets"].get(name) or []:
                assert isinstance(bucket["count"], int)
                assert bucket["count"] >= 0


class TestOpenAPIFacetsSchema:
    """IT-FACETS-06: the published OpenAPI Facets schema dropped ``status``."""

    def test_facets_schema_has_no_status(self, app: TestClient) -> None:
        """IT-FACETS-06: ``status`` is no longer a Facets property (commit 40196f7)."""
        spec = app.get("/openapi.json").json()
        schemas = spec.get("components", {}).get("schemas", {})
        facets_schema = schemas.get("Facets")
        assert facets_schema is not None, "Facets schema missing from OpenAPI"
        assert "status" not in facets_schema.get("properties", {})

    def test_facets_schema_lists_organism_and_accessibility(self, app: TestClient) -> None:
        """IT-FACETS-06: defaults are still part of the schema."""
        spec = app.get("/openapi.json").json()
        schemas = spec.get("components", {}).get("schemas", {})
        facets_schema = schemas.get("Facets")
        assert facets_schema is not None
        properties = facets_schema.get("properties", {})
        assert "organism" in properties
        assert "accessibility" in properties


class TestFacetsAllowlistRejection:
    """IT-FACETS-07: unknown facet names are rejected with 422 (Pydantic typo class)."""

    def test_unknown_facet_on_entries(self, app: TestClient) -> None:
        """IT-FACETS-07: /entries/ rejects an unknown facet name with 422."""
        resp = app.get("/entries/", params={"facets": _UNKNOWN_FACET})
        assert resp.status_code == 422

    def test_unknown_facet_on_facets_endpoint(self, app: TestClient) -> None:
        """IT-FACETS-07: /facets rejects an unknown facet name with 422."""
        resp = app.get("/facets", params={"facets": _UNKNOWN_FACET})
        assert resp.status_code == 422


class TestTypeSpecificFacetBuckets:
    """IT-FACETS-08: per-type endpoint exposes its type-specific aggregations."""

    @pytest.mark.parametrize(("type_", "facet"), _TYPE_SPECIFIC_FACETS)
    def test_type_specific_bucket_returned(
        self,
        app: TestClient,
        type_: str,
        facet: str,
    ) -> None:
        """IT-FACETS-08: facets={facet} on the right endpoint surfaces non-empty buckets."""
        resp = app.get(f"/facets/{type_}", params={"facets": facet})
        assert resp.status_code == 200, f"{type_}/{facet}: {resp.status_code}"
        bucket = resp.json()["facets"].get(facet)
        assert bucket is not None, f"{type_}/{facet}: bucket missing"
        assert isinstance(bucket, list)
        assert bucket, f"{type_}/{facet}: bucket empty"
        for entry in bucket:
            # Buckets surface as ``{value, count}`` (api-spec.md § ファセット).
            assert "value" in entry
            assert isinstance(entry.get("count"), int)
            assert entry["count"] >= 0


class TestFacetsTypeMismatchOnPerTypeEndpoint:
    """IT-FACETS-09: valid facet name on the wrong type endpoint returns 400."""

    def test_library_strategy_on_bioproject_returns_400(self, app: TestClient) -> None:
        """IT-FACETS-09: ``libraryStrategy`` (sra-experiment-only) on bioproject → 400."""
        resp = app.get("/facets/bioproject", params={"facets": "libraryStrategy"})
        assert resp.status_code == 400

    def test_object_type_on_biosample_returns_400(self, app: TestClient) -> None:
        """IT-FACETS-09: ``objectType`` (bioproject-only) on biosample → 400."""
        resp = app.get("/facets/biosample", params={"facets": "objectType"})
        assert resp.status_code == 400

    def test_unknown_facet_still_returns_422(self, app: TestClient) -> None:
        """IT-FACETS-09: typo (allowlist outside) is 422, distinct from type-mismatch 400."""
        resp = app.get("/facets/bioproject", params={"facets": _UNKNOWN_FACET})
        assert resp.status_code == 422


class TestOrganismFacetBucketShape:
    """IT-FACETS-10: organism bucket は value=TaxID / label=name で、
    bucket の value をそのまま検索 API に再注入できる。

    docs/api-spec.md § ファセット § bucket 形式
    """

    _TAX_ID_PATTERN = re.compile(r"^\d+$")

    def _organism_buckets(self, app: TestClient) -> list[dict[str, Any]]:
        resp = app.get("/facets", params={"facets": "organism"})
        assert resp.status_code == 200, resp.text
        buckets: list[dict[str, Any]] | None = resp.json()["facets"].get("organism")
        assert buckets is not None, "organism aggregation should be present when explicitly requested"
        if not buckets:
            pytest.skip("staging data has no organism buckets to validate against")
        return buckets

    def test_value_is_tax_id_string(self, app: TestClient) -> None:
        """IT-FACETS-10: organism bucket value は ``^\\d+$`` (NCBI TaxID, string)。"""
        for bucket in self._organism_buckets(app):
            value = bucket["value"]
            assert isinstance(value, str)
            assert self._TAX_ID_PATTERN.fullmatch(value), bucket

    def test_label_is_non_empty_string(self, app: TestClient) -> None:
        """IT-FACETS-10: bucket には label が必ず存在し non-empty。"""
        for bucket in self._organism_buckets(app):
            label = bucket["label"]
            assert isinstance(label, str)
            assert label, bucket

    def test_value_is_re_injectable_into_search_api(self, app: TestClient) -> None:
        """IT-FACETS-10: bucket の value をそのまま ``?organism=<value>`` に
        渡したリクエストが 200 を返し、ヒット件数が bucket の count 以上 (status:public
        フィルタは facet 集計と /entries 検索で共通、entries は accession-exact 例外で
        suppressed が混じりうるため `==` ではなく `>=` で確認)。
        """
        buckets = self._organism_buckets(app)
        # 上位 bucket だけサンプリング (staging で全 bucket を回すとレスポンス時間がかさむ)。
        for bucket in buckets[:3]:
            value: str = bucket["value"]
            count: int = bucket["count"]
            resp = app.get(
                "/entries/",
                params={"organism": value, "perPage": 1},
            )
            assert resp.status_code == 200, (bucket, resp.text)
            total = resp.json()["pagination"]["total"]
            assert total >= count, (bucket, total)

    def test_organism_name_still_rejected_with_422(self, app: TestClient) -> None:
        """IT-FACETS-10: 旧仕様で動いていた ``?organism=<scientific name>`` が
        引き続き 422 で蹴られること (`_ORGANISM_PATTERN = ^\\d+$`)。"""
        resp = app.get("/entries/", params={"organism": "Homo sapiens"})
        assert resp.status_code == 422
