"""Tests for ddbj_search_api.schemas.entries."""
import pytest
from pydantic import ValidationError

from ddbj_search_api.schemas.common import (EntryListItem, Facets,
                                            FacetBucket, Pagination)
from ddbj_search_api.schemas.entries import (
    DB_TYPE_TO_ENTRY_MODEL,
    BioProjectDetailResponse,
    BioProjectEntryJsonLdResponse,
    BioProjectEntryResponse,
    BioSampleDetailResponse,
    BioSampleEntryJsonLdResponse,
    BioSampleEntryResponse,
    EntryListResponse,
    JgaDetailResponse,
    JgaEntryJsonLdResponse,
    JgaEntryResponse,
    SraDetailResponse,
    SraEntryJsonLdResponse,
    SraEntryResponse,
)
from ddbj_search_converter.schema import JGA, SRA, BioProject, BioSample


# === EntryListResponse ===


class TestEntryListResponse:
    """EntryListResponse: pagination + items + optional facets."""

    def test_basic_construction(self) -> None:
        resp = EntryListResponse(
            pagination=Pagination(page=1, perPage=10, total=1),
            items=[EntryListItem(identifier="PRJDB1", type="bioproject")],
        )
        assert resp.pagination.page == 1
        assert len(resp.items) == 1

    def test_facets_default_to_none(self) -> None:
        resp = EntryListResponse(
            pagination=Pagination(page=1, perPage=10, total=0),
            items=[],
        )
        assert resp.facets is None

    def test_with_facets(self) -> None:
        facets = Facets(
            organism=[FacetBucket(value="human", count=10)],
            status=[FacetBucket(value="live", count=5)],
            accessibility=[FacetBucket(value="public-access", count=8)],
        )
        resp = EntryListResponse(
            pagination=Pagination(page=1, perPage=10, total=10),
            items=[],
            facets=facets,
        )
        assert resp.facets is not None
        assert len(resp.facets.organism) == 1

    def test_empty_items_accepted(self) -> None:
        resp = EntryListResponse(
            pagination=Pagination(page=1, perPage=10, total=0),
            items=[],
        )
        assert resp.items == []

    def test_missing_pagination_raises_error(self) -> None:
        with pytest.raises(ValidationError):
            EntryListResponse(items=[])  # type: ignore[call-arg]

    def test_missing_items_raises_error(self) -> None:
        with pytest.raises(ValidationError):
            EntryListResponse(  # type: ignore[call-arg]
                pagination=Pagination(page=1, perPage=10, total=0),
            )


# === DetailResponse ===


class TestDetailResponse:
    """Detail responses: truncated dbXrefs + dbXrefsCount."""

    DETAIL_CLASSES = [
        BioProjectDetailResponse,
        BioSampleDetailResponse,
        SraDetailResponse,
        JgaDetailResponse,
    ]

    @pytest.mark.parametrize(
        "cls",
        DETAIL_CLASSES,
        ids=["BioProject", "BioSample", "SRA", "JGA"],
    )
    def test_basic_construction(self, cls: type) -> None:
        obj = cls(
            identifier="TEST001",
            type="bioproject",
            dbXrefs=[{"identifier": "BS1", "type": "biosample", "url": "http://x"}],
            dbXrefsCount={"biosample": 10},
        )
        assert obj.identifier == "TEST001"
        assert obj.db_xrefs_count == {"biosample": 10}

    @pytest.mark.parametrize(
        "cls",
        DETAIL_CLASSES,
        ids=["BioProject", "BioSample", "SRA", "JGA"],
    )
    def test_extra_fields_allowed(self, cls: type) -> None:
        obj = cls(
            identifier="TEST001",
            type="bioproject",
            dbXrefs=[],
            dbXrefsCount={},
            title="Extra field test",
            organism={"identifier": "9606", "name": "Homo sapiens"},
        )
        assert obj.model_extra is not None
        assert "title" in obj.model_extra

    @pytest.mark.parametrize(
        "cls",
        DETAIL_CLASSES,
        ids=["BioProject", "BioSample", "SRA", "JGA"],
    )
    def test_alias_serialization(self, cls: type) -> None:
        obj = cls(
            identifier="TEST001",
            type="bioproject",
            dbXrefs=[],
            dbXrefsCount={"biosample": 5},
        )
        data = obj.model_dump(by_alias=True)
        assert "dbXrefs" in data
        assert "dbXrefsCount" in data
        assert "db_xrefs" not in data
        assert "db_xrefs_count" not in data

    @pytest.mark.parametrize(
        "cls",
        DETAIL_CLASSES,
        ids=["BioProject", "BioSample", "SRA", "JGA"],
    )
    def test_missing_required_field_raises_error(self, cls: type) -> None:
        with pytest.raises(ValidationError):
            cls(identifier="TEST001", type="bioproject")  # type: ignore[call-arg]

    def test_empty_db_xrefs_with_count(self) -> None:
        obj = BioProjectDetailResponse(
            identifier="PRJDB1",
            type="bioproject",
            dbXrefs=[],
            dbXrefsCount={"biosample": 1000},
        )
        assert obj.db_xrefs == []
        assert obj.db_xrefs_count == {"biosample": 1000}


# === EntryResponse (aliases for converter types) ===


class TestEntryResponse:
    """Raw entry responses: direct aliases for converter types."""

    def test_bioproject_entry_response_is_bioproject(self) -> None:
        assert BioProjectEntryResponse is BioProject

    def test_biosample_entry_response_is_biosample(self) -> None:
        assert BioSampleEntryResponse is BioSample

    def test_sra_entry_response_is_sra(self) -> None:
        assert SraEntryResponse is SRA

    def test_jga_entry_response_is_jga(self) -> None:
        assert JgaEntryResponse is JGA


# === JSON-LD Response ===


class TestJsonLdResponse:
    """JSON-LD responses: ES document + @context, @id."""

    JSON_LD_CLASSES = [
        BioProjectEntryJsonLdResponse,
        BioSampleEntryJsonLdResponse,
        SraEntryJsonLdResponse,
        JgaEntryJsonLdResponse,
    ]

    @pytest.mark.parametrize(
        "cls",
        JSON_LD_CLASSES,
        ids=["BioProject", "BioSample", "SRA", "JGA"],
    )
    def test_basic_construction(self, cls: type) -> None:
        obj = cls(
            **{
                "@context": "https://schema.org",
                "@id": "https://example.com/PRJDB1",
                "identifier": "PRJDB1",
                "type": "bioproject",
            }
        )
        assert obj.at_context == "https://schema.org"
        assert obj.at_id == "https://example.com/PRJDB1"

    @pytest.mark.parametrize(
        "cls",
        JSON_LD_CLASSES,
        ids=["BioProject", "BioSample", "SRA", "JGA"],
    )
    def test_alias_serialization(self, cls: type) -> None:
        obj = cls(
            **{
                "@context": "https://schema.org",
                "@id": "https://example.com/ID1",
                "identifier": "ID1",
                "type": "bioproject",
            }
        )
        data = obj.model_dump(by_alias=True)
        assert "@context" in data
        assert "@id" in data
        assert "at_context" not in data
        assert "at_id" not in data

    @pytest.mark.parametrize(
        "cls",
        JSON_LD_CLASSES,
        ids=["BioProject", "BioSample", "SRA", "JGA"],
    )
    def test_extra_fields_allowed(self, cls: type) -> None:
        obj = cls(
            **{
                "@context": "https://schema.org",
                "@id": "https://example.com/ID1",
                "identifier": "ID1",
                "type": "bioproject",
                "title": "Test entry",
            }
        )
        assert obj.model_extra is not None
        assert "title" in obj.model_extra

    @pytest.mark.parametrize(
        "cls",
        JSON_LD_CLASSES,
        ids=["BioProject", "BioSample", "SRA", "JGA"],
    )
    def test_missing_context_raises_error(self, cls: type) -> None:
        with pytest.raises(ValidationError):
            cls(
                **{
                    "@id": "https://example.com/ID1",
                    "identifier": "ID1",
                    "type": "bioproject",
                }
            )


# === DB_TYPE_TO_ENTRY_MODEL mapping ===


class TestDbTypeToEntryModel:
    """DB_TYPE_TO_ENTRY_MODEL mapping covers all 12 types."""

    def test_has_12_entries(self) -> None:
        assert len(DB_TYPE_TO_ENTRY_MODEL) == 12

    @pytest.mark.parametrize(
        "db_type,expected_model",
        [
            ("bioproject", BioProject),
            ("biosample", BioSample),
            ("sra-submission", SRA),
            ("sra-study", SRA),
            ("sra-experiment", SRA),
            ("sra-run", SRA),
            ("sra-sample", SRA),
            ("sra-analysis", SRA),
            ("jga-study", JGA),
            ("jga-dataset", JGA),
            ("jga-dac", JGA),
            ("jga-policy", JGA),
        ],
    )
    def test_type_maps_to_correct_model(
        self, db_type: str, expected_model: type
    ) -> None:
        assert DB_TYPE_TO_ENTRY_MODEL[db_type] is expected_model
