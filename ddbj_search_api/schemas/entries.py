"""Entry-related response schemas.

Covers search results (EntryListResponse) and entry detail responses
(*DetailResponse, *EntryResponse, *EntryJsonLdResponse).
"""
from typing import Any, Dict, List, Optional, Union

from ddbj_search_converter.schema import JGA, SRA, BioProject, BioSample
from pydantic import BaseModel, ConfigDict, Field

from ddbj_search_api.schemas.common import (DbXrefsCount, EntryListItem,
                                            Facets, Pagination)

# === Search result response ===


class EntryListResponse(BaseModel):
    """Search result list with pagination and optional facets."""

    pagination: Pagination = Field(
        description="Pagination metadata.",
    )
    items: List[EntryListItem] = Field(
        description="Matching entries (summary representation).",
    )
    facets: Optional[Facets] = Field(
        default=None,
        description="Facet aggregation (present when includeFacets=true).",
    )


# === Detail responses (frontend-oriented: truncated dbXrefs + dbXrefsCount) ===


class BioProjectDetailResponse(BaseModel):
    """BioProject entry detail with truncated dbXrefs and dbXrefsCount.

    All BioProject fields plus ``dbXrefsCount`` for frontend display.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    identifier: str
    type: str
    db_xrefs: List[Any] = Field(alias="dbXrefs")
    db_xrefs_count: DbXrefsCount = Field(alias="dbXrefsCount")


class BioSampleDetailResponse(BaseModel):
    """BioSample entry detail with truncated dbXrefs and dbXrefsCount."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    identifier: str
    type: str
    db_xrefs: List[Any] = Field(alias="dbXrefs")
    db_xrefs_count: DbXrefsCount = Field(alias="dbXrefsCount")


class SraDetailResponse(BaseModel):
    """SRA entry detail with truncated dbXrefs and dbXrefsCount."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    identifier: str
    type: str
    db_xrefs: List[Any] = Field(alias="dbXrefs")
    db_xrefs_count: DbXrefsCount = Field(alias="dbXrefsCount")


class JgaDetailResponse(BaseModel):
    """JGA entry detail with truncated dbXrefs and dbXrefsCount."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    identifier: str
    type: str
    db_xrefs: List[Any] = Field(alias="dbXrefs")
    db_xrefs_count: DbXrefsCount = Field(alias="dbXrefsCount")


DetailResponse = Union[
    BioProjectDetailResponse,
    BioSampleDetailResponse,
    SraDetailResponse,
    JgaDetailResponse,
]

# === Raw entry responses (data-access: ES document as-is) ===

BioProjectEntryResponse = BioProject
BioSampleEntryResponse = BioSample
SraEntryResponse = SRA
JgaEntryResponse = JGA

EntryResponse = Union[BioProject, BioSample, SRA, JGA]

# === JSON-LD responses ===


class BioProjectEntryJsonLdResponse(BaseModel):
    """BioProject entry in JSON-LD format."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    at_context: str = Field(alias="@context")
    at_id: str = Field(alias="@id")
    identifier: str
    type: str


class BioSampleEntryJsonLdResponse(BaseModel):
    """BioSample entry in JSON-LD format."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    at_context: str = Field(alias="@context")
    at_id: str = Field(alias="@id")
    identifier: str
    type: str


class SraEntryJsonLdResponse(BaseModel):
    """SRA entry in JSON-LD format."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    at_context: str = Field(alias="@context")
    at_id: str = Field(alias="@id")
    identifier: str
    type: str


class JgaEntryJsonLdResponse(BaseModel):
    """JGA entry in JSON-LD format."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    at_context: str = Field(alias="@context")
    at_id: str = Field(alias="@id")
    identifier: str
    type: str


EntryJsonLdResponse = Union[
    BioProjectEntryJsonLdResponse,
    BioSampleEntryJsonLdResponse,
    SraEntryJsonLdResponse,
    JgaEntryJsonLdResponse,
]

# === Mapping from DbType to converter model ===

DB_TYPE_TO_ENTRY_MODEL: Dict[str, type] = {
    "bioproject": BioProject,
    "biosample": BioSample,
    "sra-submission": SRA,
    "sra-study": SRA,
    "sra-experiment": SRA,
    "sra-run": SRA,
    "sra-sample": SRA,
    "sra-analysis": SRA,
    "jga-study": JGA,
    "jga-dataset": JGA,
    "jga-dac": JGA,
    "jga-policy": JGA,
}
