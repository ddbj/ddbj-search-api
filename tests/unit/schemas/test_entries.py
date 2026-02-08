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

# Minimal required data for each converter type.

_COMMON_OPTIONAL = {
    "name": None,
    "organism": None,
    "title": None,
    "description": None,
    "dateCreated": None,
    "dateModified": None,
    "datePublished": None,
}

_BIOPROJECT_BASE = {
    "identifier": "PRJDB1",
    "properties": {},
    "distribution": [],
    "isPartOf": "BioProject",
    "type": "bioproject",
    "objectType": "BioProject",
    "url": "https://example.com/PRJDB1",
    "organization": [],
    "publication": [],
    "grant": [],
    "externalLink": [],
    "dbXrefs": [],
    "sameAs": [],
    "status": "live",
    "accessibility": "public-access",
    **_COMMON_OPTIONAL,
}

_BIOSAMPLE_BASE = {
    "identifier": "SAMD00000001",
    "properties": {},
    "distribution": [],
    "isPartOf": "BioSample",
    "type": "biosample",
    "url": "https://example.com/SAMD00000001",
    "attributes": [],
    "model": [],
    "package": None,
    "dbXrefs": [],
    "sameAs": [],
    "status": "live",
    "accessibility": "public-access",
    **_COMMON_OPTIONAL,
}

_SRA_BASE = {
    "identifier": "DRR000001",
    "properties": {},
    "distribution": [],
    "isPartOf": "sra",
    "type": "sra-run",
    "url": "https://example.com/DRR000001",
    "dbXrefs": [],
    "sameAs": [],
    "status": "live",
    "accessibility": "public-access",
    **_COMMON_OPTIONAL,
}

_JGA_BASE = {
    "identifier": "JGAS000001",
    "properties": {},
    "distribution": [],
    "isPartOf": "jga",
    "type": "jga-study",
    "url": "https://example.com/JGAS000001",
    "dbXrefs": [],
    "sameAs": [],
    "status": "live",
    "accessibility": "controlled-access",
    **_COMMON_OPTIONAL,
}


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
    """Detail responses: converter type + dbXrefsCount."""

    DETAIL_CASES = [
        (BioProjectDetailResponse, _BIOPROJECT_BASE),
        (BioSampleDetailResponse, _BIOSAMPLE_BASE),
        (SraDetailResponse, _SRA_BASE),
        (JgaDetailResponse, _JGA_BASE),
    ]

    @pytest.mark.parametrize(
        "cls,base",
        DETAIL_CASES,
        ids=["BioProject", "BioSample", "SRA", "JGA"],
    )
    def test_basic_construction(self, cls: type, base: dict) -> None:
        obj = cls(**base, dbXrefsCount={"biosample": 10})
        assert obj.identifier == base["identifier"]
        assert obj.db_xrefs_count == {"biosample": 10}

    @pytest.mark.parametrize(
        "cls,parent",
        [
            (BioProjectDetailResponse, BioProject),
            (BioSampleDetailResponse, BioSample),
            (SraDetailResponse, SRA),
            (JgaDetailResponse, JGA),
        ],
        ids=["BioProject", "BioSample", "SRA", "JGA"],
    )
    def test_inherits_from_converter_type(
        self, cls: type, parent: type,
    ) -> None:
        assert issubclass(cls, parent)

    @pytest.mark.parametrize(
        "cls,base",
        DETAIL_CASES,
        ids=["BioProject", "BioSample", "SRA", "JGA"],
    )
    def test_converter_fields_are_real_fields(
        self, cls: type, base: dict,
    ) -> None:
        data = {**base, "title": "Test Title", "dbXrefsCount": {}}
        obj = cls(**data)
        assert obj.title == "Test Title"
        assert "title" not in (obj.model_extra or {})

    @pytest.mark.parametrize(
        "cls,base",
        DETAIL_CASES,
        ids=["BioProject", "BioSample", "SRA", "JGA"],
    )
    def test_alias_serialization(self, cls: type, base: dict) -> None:
        obj = cls(**base, dbXrefsCount={"biosample": 5})
        data = obj.model_dump(by_alias=True)
        assert "dbXrefs" in data
        assert "dbXrefsCount" in data
        assert "db_xrefs_count" not in data

    @pytest.mark.parametrize(
        "cls,base",
        DETAIL_CASES,
        ids=["BioProject", "BioSample", "SRA", "JGA"],
    )
    def test_missing_db_xrefs_count_raises_error(
        self, cls: type, base: dict,
    ) -> None:
        with pytest.raises(ValidationError):
            cls(**base)  # type: ignore[call-arg]

    def test_empty_db_xrefs_with_count(self) -> None:
        obj = BioProjectDetailResponse(
            **_BIOPROJECT_BASE,
            dbXrefsCount={"biosample": 1000},
        )
        assert obj.dbXrefs == []
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
    """JSON-LD responses: converter type + @context, @id."""

    JSON_LD_CASES = [
        (BioProjectEntryJsonLdResponse, _BIOPROJECT_BASE),
        (BioSampleEntryJsonLdResponse, _BIOSAMPLE_BASE),
        (SraEntryJsonLdResponse, _SRA_BASE),
        (JgaEntryJsonLdResponse, _JGA_BASE),
    ]

    @pytest.mark.parametrize(
        "cls,base",
        JSON_LD_CASES,
        ids=["BioProject", "BioSample", "SRA", "JGA"],
    )
    def test_basic_construction(self, cls: type, base: dict) -> None:
        obj = cls(
            **{
                "@context": "https://schema.org",
                "@id": "https://example.com/entry1",
                **base,
            }
        )
        assert obj.at_context == "https://schema.org"
        assert obj.at_id == "https://example.com/entry1"

    @pytest.mark.parametrize(
        "cls,base",
        JSON_LD_CASES,
        ids=["BioProject", "BioSample", "SRA", "JGA"],
    )
    def test_alias_serialization(self, cls: type, base: dict) -> None:
        obj = cls(
            **{
                "@context": "https://schema.org",
                "@id": "https://example.com/entry1",
                **base,
            }
        )
        data = obj.model_dump(by_alias=True)
        assert "@context" in data
        assert "@id" in data
        assert "at_context" not in data
        assert "at_id" not in data

    @pytest.mark.parametrize(
        "cls,base",
        JSON_LD_CASES,
        ids=["BioProject", "BioSample", "SRA", "JGA"],
    )
    def test_converter_fields_are_real_fields(
        self, cls: type, base: dict,
    ) -> None:
        data = {
            "@context": "https://schema.org",
            "@id": "https://example.com/entry1",
            **base,
            "title": "Test entry",
        }
        obj = cls(**data)
        assert obj.title == "Test entry"
        assert "title" not in (obj.model_extra or {})

    @pytest.mark.parametrize(
        "cls,base",
        JSON_LD_CASES,
        ids=["BioProject", "BioSample", "SRA", "JGA"],
    )
    def test_missing_context_raises_error(
        self, cls: type, base: dict,
    ) -> None:
        with pytest.raises(ValidationError):
            cls(
                **{
                    "@id": "https://example.com/entry1",
                    **base,
                }
            )

    @pytest.mark.parametrize(
        "cls,parent",
        [
            (BioProjectEntryJsonLdResponse, BioProject),
            (BioSampleEntryJsonLdResponse, BioSample),
            (SraEntryJsonLdResponse, SRA),
            (JgaEntryJsonLdResponse, JGA),
        ],
        ids=["BioProject", "BioSample", "SRA", "JGA"],
    )
    def test_inherits_from_converter_type(
        self, cls: type, parent: type,
    ) -> None:
        assert issubclass(cls, parent)


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
