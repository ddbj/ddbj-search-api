"""dbXrefs-related response schemas."""

from __future__ import annotations

from ddbj_search_converter.schema import Xref
from pydantic import BaseModel, Field


class DbXrefsFullResponse(BaseModel):
    """Full dbXrefs response for GET /entries/{type}/{id}/dbxrefs.json."""

    db_xrefs: list[Xref] = Field(
        alias="dbXrefs",
        examples=[
            [
                {
                    "identifier": "SAMD00012345",
                    "type": "biosample",
                    "url": "https://ddbj.nig.ac.jp/search/entry/biosample/SAMD00012345",
                },
            ],
        ],
        description=(
            "All cross-references for the entry (no truncation). "
            "Returned via streaming from DuckDB; may contain references to non-public entries "
            "(withdrawn / private accessions) since the underlying edge table is not status-filtered."
        ),
    )
