"""dbXrefs-related response schemas."""
from typing import Any, List

from pydantic import BaseModel, Field


class DbXrefsFullResponse(BaseModel):
    """Full dbXrefs response for GET /entries/{type}/{id}/dbxrefs.json."""

    db_xrefs: List[Any] = Field(
        alias="dbXrefs",
        description="All cross-references for the entry.",
    )
