"""Service info response schema."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ElasticsearchStatus = Literal["ok", "unavailable"]


class ServiceInfoResponse(BaseModel):
    """Response for GET /service-info."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "name": "DDBJ Search API",
                    "version": "0.2.1",
                    "description": (
                        "RESTful API for searching and retrieving BioProject, BioSample, SRA, and JGA entries."
                    ),
                    "elasticsearch": "ok",
                },
            ],
        },
    )

    name: str = Field(description="Service name.")
    version: str = Field(description="Service version.")
    description: str = Field(description="Service description.")
    elasticsearch: ElasticsearchStatus = Field(
        description="Elasticsearch status: 'ok' or 'unavailable'.",
    )
