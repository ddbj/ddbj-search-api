"""Bulk API request and response schemas."""

from __future__ import annotations

from ddbj_search_converter.schema import JGA, SRA, BioProject, BioSample
from pydantic import BaseModel, Field


class BulkRequest(BaseModel):
    """Request body for POST /entries/{type}/bulk."""

    ids: list[str] = Field(
        max_length=1000,
        description="List of entry identifiers to retrieve (max 1000).",
    )


class BulkResponse(BaseModel):
    """Response for POST /entries/{type}/bulk (format=json).

    ``entries`` contains the found entries as raw ES documents.
    ``notFound`` lists IDs that could not be found.
    """

    entries: list[BioProject | BioSample | SRA | JGA] = Field(
        description="Found entries (raw ES documents).",
    )
    not_found: list[str] = Field(
        alias="notFound",
        description="IDs that were not found.",
    )
