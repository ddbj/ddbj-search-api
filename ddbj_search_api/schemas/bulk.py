from typing import List

from pydantic import BaseModel, Field


class BulkRequest(BaseModel):
    ids: List[str] = Field(
        ...,
        max_length=1000,
        examples=[["PRJNA16", "PRJNA17", "PRJNA18"]],
        description="List of entry IDs to retrieve (max 1000)",
    )
