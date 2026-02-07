"""Tests for ddbj_search_api.schemas.common."""
import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from ddbj_search_api.schemas.common import (DbType, EntryListItem,
                                            FacetBucket, Facets, Pagination,
                                            ProblemDetails)
from tests.unit.strategies import (valid_facet_count, valid_facet_value,
                                   valid_page, valid_per_page, valid_total)

# === DbType ===


class TestDbType:
    """DbType enum: 12 database types."""

    EXPECTED_VALUES = [
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
    ]

    def test_enum_has_exactly_12_members(self) -> None:
        assert len(DbType) == 12

    @pytest.mark.parametrize("value", EXPECTED_VALUES)
    def test_enum_contains_expected_value(self, value: str) -> None:
        assert DbType(value) == value

    def test_invalid_value_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            DbType("invalid-type")

    def test_all_expected_values_are_present(self) -> None:
        actual = {e.value for e in DbType}
        expected = set(self.EXPECTED_VALUES)
        assert actual == expected


# === Pagination ===


class TestPagination:
    """Pagination model: page, perPage, total."""

    def test_basic_construction(self) -> None:
        p = Pagination(page=1, perPage=10, total=100)
        assert p.page == 1
        assert p.per_page == 10
        assert p.total == 100

    def test_alias_serialization(self) -> None:
        p = Pagination(page=1, perPage=25, total=50)
        data = p.model_dump(by_alias=True)
        assert data["perPage"] == 25
        assert "per_page" not in data

    def test_populate_by_name(self) -> None:
        p = Pagination(page=1, per_page=10, total=100)
        assert p.per_page == 10


class TestPaginationPBT:
    """Property-based tests for Pagination."""

    @given(page=valid_page, per_page=valid_per_page, total=valid_total)
    def test_valid_values_accepted(
        self, page: int, per_page: int, total: int
    ) -> None:
        p = Pagination(page=page, perPage=per_page, total=total)
        assert p.page == page
        assert p.per_page == per_page
        assert p.total == total

    @given(page=valid_page, per_page=valid_per_page, total=valid_total)
    def test_roundtrip_serialization(
        self, page: int, per_page: int, total: int
    ) -> None:
        p = Pagination(page=page, perPage=per_page, total=total)
        data = p.model_dump(by_alias=True)
        restored = Pagination(**data)
        assert restored == p


# === FacetBucket ===


class TestFacetBucket:
    """FacetBucket: a single facet aggregation bucket."""

    def test_basic_construction(self) -> None:
        bucket = FacetBucket(value="Homo sapiens", count=42)
        assert bucket.value == "Homo sapiens"
        assert bucket.count == 42


class TestFacetBucketPBT:
    """Property-based tests for FacetBucket."""

    @given(value=valid_facet_value, count=valid_facet_count)
    def test_valid_values_accepted(self, value: str, count: int) -> None:
        bucket = FacetBucket(value=value, count=count)
        assert bucket.value == value
        assert bucket.count == count


# === Facets ===


class TestFacets:
    """Facets model: common + optional facet fields."""

    def test_common_facets_required(self) -> None:
        facets = Facets(
            organism=[FacetBucket(value="human", count=10)],
            status=[FacetBucket(value="live", count=5)],
            accessibility=[FacetBucket(value="public-access", count=8)],
        )
        assert len(facets.organism) == 1
        assert len(facets.status) == 1
        assert len(facets.accessibility) == 1

    def test_optional_type_defaults_to_none(self) -> None:
        facets = Facets(
            organism=[],
            status=[],
            accessibility=[],
        )
        assert facets.type is None

    def test_optional_object_type_defaults_to_none(self) -> None:
        facets = Facets(
            organism=[],
            status=[],
            accessibility=[],
        )
        assert facets.object_type is None

    def test_cross_type_search_includes_type_facet(self) -> None:
        facets = Facets(
            type=[
                FacetBucket(value="bioproject", count=100),
                FacetBucket(value="biosample", count=200),
            ],
            organism=[],
            status=[],
            accessibility=[],
        )
        assert facets.type is not None
        assert len(facets.type) == 2

    def test_bioproject_search_includes_object_type_facet(self) -> None:
        facets = Facets(
            organism=[],
            status=[],
            accessibility=[],
            objectType=[
                FacetBucket(value="UmbrellaBioProject", count=5),
                FacetBucket(value="BioProject", count=95),
            ],
        )
        assert facets.object_type is not None
        assert len(facets.object_type) == 2

    def test_alias_serialization(self) -> None:
        facets = Facets(
            organism=[],
            status=[],
            accessibility=[],
            objectType=[FacetBucket(value="BioProject", count=1)],
        )
        data = facets.model_dump(by_alias=True, exclude_none=True)
        assert "objectType" in data
        assert "object_type" not in data

    def test_missing_required_field_raises_error(self) -> None:
        with pytest.raises(ValidationError):
            Facets(organism=[], status=[])  # type: ignore[call-arg]


# === EntryListItem ===


class TestEntryListItem:
    """EntryListItem: summary entry in search results."""

    def test_minimal_construction(self) -> None:
        item = EntryListItem(identifier="PRJDB1", type="bioproject")
        assert item.identifier == "PRJDB1"
        assert item.type == "bioproject"

    def test_optional_fields_default_to_none(self) -> None:
        item = EntryListItem(identifier="PRJDB1", type="bioproject")
        assert item.url is None
        assert item.title is None
        assert item.description is None
        assert item.organism is None
        assert item.status is None
        assert item.accessibility is None
        assert item.date_published is None
        assert item.date_modified is None
        assert item.date_created is None
        assert item.db_xrefs is None
        assert item.db_xrefs_count is None
        assert item.properties is None

    def test_extra_fields_allowed(self) -> None:
        item = EntryListItem(
            identifier="PRJDB1",
            type="bioproject",
            customField="extra_value",
        )
        assert item.model_extra is not None
        assert item.model_extra["customField"] == "extra_value"

    def test_alias_serialization(self) -> None:
        item = EntryListItem(
            identifier="PRJDB1",
            type="bioproject",
            datePublished="2024-01-01",
        )
        data = item.model_dump(by_alias=True, exclude_none=True)
        assert "datePublished" in data
        assert "date_published" not in data

    def test_db_xrefs_count_as_dict(self) -> None:
        item = EntryListItem(
            identifier="PRJDB1",
            type="bioproject",
            dbXrefsCount={"biosample": 10, "sra-run": 5},
        )
        assert item.db_xrefs_count == {"biosample": 10, "sra-run": 5}


# === ProblemDetails ===


class TestProblemDetails:
    """ProblemDetails: RFC 7807 error response."""

    def test_basic_construction(self) -> None:
        problem = ProblemDetails(
            title="Not Found",
            status=404,
            detail="Entry not found.",
        )
        assert problem.title == "Not Found"
        assert problem.status == 404
        assert problem.detail == "Entry not found."

    def test_type_defaults_to_about_blank(self) -> None:
        problem = ProblemDetails(
            title="Error",
            status=500,
            detail="Something went wrong.",
        )
        assert problem.type == "about:blank"

    def test_optional_fields_default_to_none(self) -> None:
        problem = ProblemDetails(
            title="Error",
            status=500,
            detail="Error.",
        )
        assert problem.instance is None
        assert problem.timestamp is None
        assert problem.request_id is None

    def test_full_construction(self) -> None:
        problem = ProblemDetails(
            type="about:blank",
            title="Bad Request",
            status=400,
            detail="Deep paging limit exceeded.",
            instance="/entries/bioproject/",
            timestamp="2024-01-15T10:30:00Z",
            requestId="req-abc123",
        )
        assert problem.instance == "/entries/bioproject/"
        assert problem.timestamp == "2024-01-15T10:30:00Z"
        assert problem.request_id == "req-abc123"

    def test_alias_serialization(self) -> None:
        problem = ProblemDetails(
            title="Error",
            status=500,
            detail="Error.",
            requestId="req-123",
        )
        data = problem.model_dump(by_alias=True, exclude_none=True)
        assert "requestId" in data
        assert "request_id" not in data

    def test_missing_required_field_raises_error(self) -> None:
        with pytest.raises(ValidationError):
            ProblemDetails(title="Error", status=404)  # type: ignore[call-arg]
