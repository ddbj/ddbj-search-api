"""Common schema types shared across the API."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DbType(str, Enum):
    """Database types supported by the API (12 types)."""

    bioproject = "bioproject"
    biosample = "biosample"
    sra_submission = "sra-submission"
    sra_study = "sra-study"
    sra_experiment = "sra-experiment"
    sra_run = "sra-run"
    sra_sample = "sra-sample"
    sra_analysis = "sra-analysis"
    jga_study = "jga-study"
    jga_dataset = "jga-dataset"
    jga_dac = "jga-dac"
    jga_policy = "jga-policy"


class Pagination(BaseModel):
    """Offset-based pagination metadata."""

    model_config = ConfigDict(populate_by_name=True)

    page: int = Field(description="Current page number (1-based).")
    per_page: int = Field(alias="perPage", description="Items per page.")
    total: int = Field(description="Total number of matching items.")


class FacetBucket(BaseModel):
    """A single bucket in a facet aggregation."""

    value: str = Field(description="Facet value (e.g. organism name, status).")
    count: int = Field(description="Number of entries matching this value.")


class Facets(BaseModel):
    """Facet aggregation results.

    Common facets (organism, status, accessibility) are always present.
    ``type`` is included only for cross-type searches.
    ``objectType`` is included only for bioproject-type searches.
    """

    model_config = ConfigDict(populate_by_name=True)

    type: list[FacetBucket] | None = Field(
        default=None,
        description="Entry count per database type (cross-type search only).",
    )
    organism: list[FacetBucket] = Field(
        description="Entry count per organism.",
    )
    status: list[FacetBucket] = Field(
        description="Entry count per status.",
    )
    accessibility: list[FacetBucket] = Field(
        description="Entry count per accessibility level.",
    )
    object_type: list[FacetBucket] | None = Field(
        default=None,
        alias="objectType",
        description="Umbrella / non-umbrella count (bioproject only).",
    )


# DbXrefsCount: mapping from XrefType to count
DbXrefsCount = dict[str, int]


class EntryListItem(BaseModel):
    """Summary representation of an entry in search result lists.

    The actual fields vary by database type.  Common fields shared by all
    types are declared here; type-specific fields are captured by
    ``extra="allow"``.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    identifier: str = Field(description="Entry accession identifier.")
    type: str = Field(description="Database type (e.g. 'bioproject').")
    url: str | None = Field(default=None, description="Canonical URL.")
    title: str | None = Field(default=None, description="Entry title.")
    description: str | None = Field(
        default=None,
        description="Entry description.",
    )
    organism: Any | None = Field(
        default=None,
        description="Organism information.",
    )
    status: str | None = Field(default=None, description="INSDC status.")
    accessibility: str | None = Field(
        default=None,
        description="Access level.",
    )
    date_published: str | None = Field(
        default=None,
        alias="datePublished",
        description="Publication date (ISO 8601).",
    )
    date_modified: str | None = Field(
        default=None,
        alias="dateModified",
        description="Last modification date (ISO 8601).",
    )
    date_created: str | None = Field(
        default=None,
        alias="dateCreated",
        description="Creation date (ISO 8601).",
    )
    db_xrefs: list[Any] | None = Field(
        default=None,
        alias="dbXrefs",
        description="Cross-references (truncated by dbXrefsLimit).",
    )
    db_xrefs_count: DbXrefsCount | None = Field(
        default=None,
        alias="dbXrefsCount",
        description="Cross-reference counts per type.",
    )
    properties: Any | None = Field(
        default=None,
        description="Type-specific properties.",
    )


class ProblemDetails(BaseModel):
    """RFC 7807 Problem Details error response."""

    type: str = Field(
        default="about:blank",
        description="Problem type URI.",
    )
    title: str = Field(description="Short human-readable summary.")
    status: int = Field(description="HTTP status code.")
    detail: str = Field(description="Human-readable explanation.")
    instance: str | None = Field(
        default=None,
        description="Request path where the error occurred.",
    )
    timestamp: str | None = Field(
        default=None,
        description="Error timestamp (ISO 8601).",
    )
    request_id: str | None = Field(
        default=None,
        alias="requestId",
        description="Request tracking ID (same as X-Request-ID header).",
    )
