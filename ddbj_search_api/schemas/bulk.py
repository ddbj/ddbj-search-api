"""Bulk API request and response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ddbj_search_api.schemas.entries import EntryResponse


class BulkRequest(BaseModel):
    """Request body for POST /entries/{type}/bulk."""

    ids: list[str] = Field(
        min_length=1,
        max_length=1000,
        examples=[["PRJDB1", "PRJDB2"]],
        description=(
            "List of entry identifiers to retrieve (1-1000). "
            "Duplicates are deduplicated server-side; each id is returned at most once "
            "either in 'entries' or 'notFound'."
        ),
    )


class BulkResponse(BaseModel):
    """Response for POST /entries/{type}/bulk (format=json).

    ``entries`` contains the found entries as raw ES documents.
    ``notFound`` lists IDs that could not be found.
    """

    entries: list[EntryResponse] = Field(
        examples=[[{"identifier": "PRJDB1", "type": "bioproject", "title": "Example BioProject"}]],
        description=(
            "Found entries (raw ES documents). "
            "'public' and 'suppressed' entries are returned here; "
            "'withdrawn', 'private', and missing ids are listed under notFound."
        ),
    )
    not_found: list[str] = Field(
        alias="notFound",
        examples=[["PRJDB_INVALID"]],
        description=(
            "IDs that were not found (missing or hidden by visibility filter: 'withdrawn' / 'private'). "
            "Always satisfies len(entries) + len(notFound) == len(set(ids))."
        ),
    )
