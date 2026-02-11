"""Facets API response schema."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ddbj_search_api.schemas.common import Facets


class FacetsResponse(BaseModel):
    """Response for GET /facets and GET /facets/{type}."""

    facets: Facets = Field(description="Facet aggregation data.")
