"""Service info response schema."""
from typing import Literal

from pydantic import BaseModel, Field

ElasticsearchStatus = Literal["ok", "unavailable"]


class ServiceInfoResponse(BaseModel):
    """Response for GET /service-info."""

    name: str = Field(description="Service name.")
    version: str = Field(description="Service version.")
    description: str = Field(description="Service description.")
    elasticsearch: ElasticsearchStatus = Field(
        description="Elasticsearch status: 'ok' or 'unavailable'.",
    )
