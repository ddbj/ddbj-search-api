"""Entry-related response schemas.

Covers search results (EntryListResponse) and entry detail responses
(*DetailResponse, *EntryResponse, *EntryJsonLdResponse).
"""

from __future__ import annotations

from ddbj_search_converter.schema import JGA, SRA, BioProject, BioSample
from pydantic import BaseModel, ConfigDict, Field

from ddbj_search_api.schemas.common import DbXrefsCount, EntryListItem, Facets, Pagination

# === Search result response ===


class EntryListResponse(BaseModel):
    """Search result list with pagination and optional facets."""

    pagination: Pagination = Field(
        description="Pagination metadata.",
    )
    items: list[EntryListItem] = Field(
        description="Matching entries (summary representation).",
    )
    facets: Facets | None = Field(
        default=None,
        description="Facet aggregation (present when includeFacets=true).",
    )


# === Detail responses (frontend-oriented: truncated dbXrefs + dbXrefsCount) ===


class BioProjectDetailResponse(BioProject):
    """BioProject entry detail with truncated dbXrefs and dbXrefsCount."""

    model_config = ConfigDict(populate_by_name=True)

    db_xrefs_count: DbXrefsCount = Field(alias="dbXrefsCount")


class BioSampleDetailResponse(BioSample):
    """BioSample entry detail with truncated dbXrefs and dbXrefsCount."""

    model_config = ConfigDict(populate_by_name=True)

    db_xrefs_count: DbXrefsCount = Field(alias="dbXrefsCount")


class SraDetailResponse(SRA):
    """SRA entry detail with truncated dbXrefs and dbXrefsCount."""

    model_config = ConfigDict(populate_by_name=True)

    db_xrefs_count: DbXrefsCount = Field(alias="dbXrefsCount")


class JgaDetailResponse(JGA):
    """JGA entry detail with truncated dbXrefs and dbXrefsCount."""

    model_config = ConfigDict(populate_by_name=True)

    db_xrefs_count: DbXrefsCount = Field(alias="dbXrefsCount")


DetailResponse = BioProjectDetailResponse | BioSampleDetailResponse | SraDetailResponse | JgaDetailResponse

# === Raw entry responses (data-access: ES document as-is) ===

BioProjectEntryResponse = BioProject
BioSampleEntryResponse = BioSample
SraEntryResponse = SRA
JgaEntryResponse = JGA

EntryResponse = BioProject | BioSample | SRA | JGA

# === JSON-LD responses ===


class BioProjectEntryJsonLdResponse(BioProject):
    """BioProject entry in JSON-LD format."""

    model_config = ConfigDict(populate_by_name=True)

    at_context: str = Field(alias="@context")
    at_id: str = Field(alias="@id")


class BioSampleEntryJsonLdResponse(BioSample):
    """BioSample entry in JSON-LD format."""

    model_config = ConfigDict(populate_by_name=True)

    at_context: str = Field(alias="@context")
    at_id: str = Field(alias="@id")


class SraEntryJsonLdResponse(SRA):
    """SRA entry in JSON-LD format."""

    model_config = ConfigDict(populate_by_name=True)

    at_context: str = Field(alias="@context")
    at_id: str = Field(alias="@id")


class JgaEntryJsonLdResponse(JGA):
    """JGA entry in JSON-LD format."""

    model_config = ConfigDict(populate_by_name=True)

    at_context: str = Field(alias="@context")
    at_id: str = Field(alias="@id")


EntryJsonLdResponse = (
    BioProjectEntryJsonLdResponse | BioSampleEntryJsonLdResponse | SraEntryJsonLdResponse | JgaEntryJsonLdResponse
)

# === Mapping from DbType to converter model ===

DB_TYPE_TO_ENTRY_MODEL: dict[str, type] = {
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
