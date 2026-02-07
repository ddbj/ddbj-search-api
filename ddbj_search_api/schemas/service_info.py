"""Service info response schema."""
from pydantic import BaseModel, Field


class ServiceInfoResponse(BaseModel):
    """Response for GET /service-info."""

    name: str = Field(description="Service name.")
    version: str = Field(description="Service version.")
    description: str = Field(description="Service description.")
