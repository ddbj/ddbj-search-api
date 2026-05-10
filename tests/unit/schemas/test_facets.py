"""Tests for ddbj_search_api.schemas.facets."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ddbj_search_api.schemas.common import FacetBucket, Facets, OrganismFacetBucket
from ddbj_search_api.schemas.facets import FacetsResponse


class TestFacetsResponse:
    """FacetsResponse: wraps Facets model."""

    def test_basic_construction(self) -> None:
        resp = FacetsResponse(
            facets=Facets(
                organism=[OrganismFacetBucket(value="9606", count=10, label="Homo sapiens")],
                accessibility=[FacetBucket(value="public-access", count=8)],
            ),
        )
        assert resp.facets.organism is not None
        assert len(resp.facets.organism) == 1

    def test_missing_facets_raises_error(self) -> None:
        with pytest.raises(ValidationError):
            FacetsResponse()  # type: ignore[call-arg]

    def test_with_type_facet(self) -> None:
        resp = FacetsResponse(
            facets=Facets(
                type=[
                    FacetBucket(value="bioproject", count=100),
                    FacetBucket(value="biosample", count=200),
                ],
                organism=[],
                accessibility=[],
            ),
        )
        assert resp.facets.type is not None
        assert len(resp.facets.type) == 2


class TestOrganismFacetBucket:
    """OrganismFacetBucket extends FacetBucket with a required ``label``.

    docs/api-spec.md § ファセット § bucket 形式 で定義された 3 フィールド
    形式 (value=TaxID, count, label=scientific name) に準拠。
    """

    def test_full_construction(self) -> None:
        bucket = OrganismFacetBucket(value="9606", count=10, label="Homo sapiens")
        assert bucket.value == "9606"
        assert bucket.count == 10
        assert bucket.label == "Homo sapiens"

    def test_missing_label_raises_error(self) -> None:
        with pytest.raises(ValidationError):
            OrganismFacetBucket(value="9606", count=10)  # type: ignore[call-arg]

    def test_facet_bucket_label_optional_compat(self) -> None:
        """``FacetBucket`` (parent) は label を持たない。 type-specific facet
        (例: libraryStrategy) は引き続き 2 フィールド形式で validate される。"""
        bucket = FacetBucket(value="WGS", count=5)
        assert not hasattr(bucket, "label")

    def test_organism_facet_bucket_label_must_be_string(self) -> None:
        with pytest.raises(ValidationError):
            OrganismFacetBucket(value="9606", count=10, label=123)  # type: ignore[arg-type]
