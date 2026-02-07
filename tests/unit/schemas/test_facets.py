"""Tests for ddbj_search_api.schemas.facets."""
import pytest
from pydantic import ValidationError

from ddbj_search_api.schemas.common import FacetBucket, Facets
from ddbj_search_api.schemas.facets import FacetsResponse


class TestFacetsResponse:
    """FacetsResponse: wraps Facets model."""

    def test_basic_construction(self) -> None:
        resp = FacetsResponse(
            facets=Facets(
                organism=[FacetBucket(value="human", count=10)],
                status=[FacetBucket(value="live", count=5)],
                accessibility=[FacetBucket(value="public-access", count=8)],
            ),
        )
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
                status=[],
                accessibility=[],
            ),
        )
        assert resp.facets.type is not None
        assert len(resp.facets.type) == 2
