"""Common schema types shared across the API."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DbType(str, Enum):
    """Database types supported by the API."""

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
    gea = "gea"
    metabobank = "metabobank"


# CamelCase form per DbType, used to build stable operationIds
# (e.g. ``listBioProjectEntries``) so generated SDKs get readable names.
DB_TYPE_DISPLAY: dict[DbType, str] = {
    DbType.bioproject: "BioProject",
    DbType.biosample: "BioSample",
    DbType.sra_submission: "SraSubmission",
    DbType.sra_study: "SraStudy",
    DbType.sra_experiment: "SraExperiment",
    DbType.sra_run: "SraRun",
    DbType.sra_sample: "SraSample",
    DbType.sra_analysis: "SraAnalysis",
    DbType.jga_study: "JgaStudy",
    DbType.jga_dataset: "JgaDataset",
    DbType.jga_dac: "JgaDac",
    DbType.jga_policy: "JgaPolicy",
    DbType.gea: "Gea",
    DbType.metabobank: "MetaboBank",
}


class Pagination(BaseModel):
    """Pagination metadata (supports both offset and cursor modes)."""

    # Examples are attached to the schema in ``main.custom_openapi`` because
    # Pydantic strips ``None`` values from ``json_schema_extra`` examples,
    # which would make a cursor-mode example (``"page": null`` / ``"nextCursor": null``)
    # invalid against the now-required + nullable schema fields.
    model_config = ConfigDict(populate_by_name=True)

    page: int | None = Field(
        description="Current page number (1-based). Null in cursor mode.",
    )
    per_page: int = Field(alias="perPage", description="Items per page.")
    total: int = Field(description="Total number of matching items.")
    next_cursor: str | None = Field(
        alias="nextCursor",
        description="Cursor token for the next page. Null on the last page.",
    )
    has_next: bool = Field(
        alias="hasNext",
        description="Whether more pages are available.",
    )


class FacetBucket(BaseModel):
    """A single bucket in a facet aggregation."""

    value: str = Field(description="Facet value (e.g. organism name, status).")
    count: int = Field(description="Number of entries matching this value.")


class Facets(BaseModel):
    """Facet aggregation results.

    Every field is optional (nullable). The ``facets`` query parameter
    on the request side selects which aggregations to compute, and only
    selected fields are returned as a list; the rest are ``null``.
    This means callers can distinguish "aggregated but no buckets"
    (empty list ``[]``) from "not aggregated" (``null``).

    Default behaviour (no ``facets`` parameter): ``organism`` and
    ``accessibility`` are populated; ``type`` is also populated on
    cross-type endpoints. All other fields default to ``null``.

    Explicit ``facets=...`` selection fully replaces the default
    (no auto-merge), so passing ``facets=objectType`` returns
    ``organism`` / ``accessibility`` as ``null``.

    The ``status`` facet is intentionally omitted: aggregations are
    always constrained to ``status:public`` upstream, so the bucket
    would be degenerate.  See ``docs/api-spec.md`` for the data
    visibility policy.
    """

    model_config = ConfigDict(populate_by_name=True)

    type: list[FacetBucket] | None = Field(
        default=None,
        description="Entry count per database type (cross-type search only; null when not aggregated).",
    )
    organism: list[FacetBucket] | None = Field(
        default=None,
        description=(
            "Entry count per organism. Null when not aggregated (e.g. excluded from an explicit ``facets`` selection)."
        ),
    )
    accessibility: list[FacetBucket] | None = Field(
        default=None,
        description="Entry count per accessibility level. Null when not aggregated.",
    )
    object_type: list[FacetBucket] | None = Field(
        default=None,
        alias="objectType",
        description="Umbrella / non-umbrella count (bioproject only, opt-in).",
    )
    library_strategy: list[FacetBucket] | None = Field(
        default=None,
        alias="libraryStrategy",
        description="Library strategy count (sra-experiment only, opt-in).",
    )
    library_source: list[FacetBucket] | None = Field(
        default=None,
        alias="librarySource",
        description="Library source count (sra-experiment only, opt-in).",
    )
    library_selection: list[FacetBucket] | None = Field(
        default=None,
        alias="librarySelection",
        description="Library selection count (sra-experiment only, opt-in).",
    )
    platform: list[FacetBucket] | None = Field(
        default=None,
        description="Sequencing platform count (sra-experiment only, opt-in).",
    )
    instrument_model: list[FacetBucket] | None = Field(
        default=None,
        alias="instrumentModel",
        description="Instrument model count (sra-experiment only, opt-in).",
    )
    experiment_type: list[FacetBucket] | None = Field(
        default=None,
        alias="experimentType",
        description="Experiment type count (gea / metabobank, opt-in).",
    )
    study_type: list[FacetBucket] | None = Field(
        default=None,
        alias="studyType",
        description="Study type count (jga-study / metabobank, opt-in).",
    )
    submission_type: list[FacetBucket] | None = Field(
        default=None,
        alias="submissionType",
        description="Submission type count (metabobank only, opt-in).",
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

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "type": "about:blank",
                    "title": "Not Found",
                    "status": 404,
                    "detail": "The requested bioproject 'PRJDB_INVALID' was not found.",
                    "instance": "/entries/bioproject/PRJDB_INVALID",
                    "timestamp": "2024-01-15T10:30:00Z",
                    "requestId": "req-abc123",
                },
            ],
        },
    )

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
