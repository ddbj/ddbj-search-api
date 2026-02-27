"""Schema types for the DBLinks API."""

from __future__ import annotations

from enum import Enum
from typing import get_args

from ddbj_search_converter.dblink.db import AccessionType as _AccessionTypeLiteral
from ddbj_search_converter.schema import Xref
from fastapi import HTTPException, Query
from pydantic import BaseModel, Field

AccessionType = Enum(  # type: ignore[misc]
    "AccessionType",
    {v.replace("-", "_"): v for v in get_args(_AccessionTypeLiteral)},
    type=str,
)


_VALID_ACCESSION_TYPES: frozenset[str] = frozenset(e.value for e in AccessionType)


class DbLinksResponse(BaseModel):
    """Response for GET /dblink/{type}/{id}."""

    identifier: str = Field(description="Source accession identifier.")
    type: AccessionType = Field(description="Source accession type.")
    dbXrefs: list[Xref] = Field(description="Related entries (sorted by type, then identifier).")


class DbLinksTypesResponse(BaseModel):
    """Response for GET /dblink/."""

    types: list[AccessionType] = Field(description="Available accession types.")


class DbLinksQuery:
    """Query parameters for GET /dblink/{type}/{id}."""

    def __init__(
        self,
        target: str | None = Query(
            default=None,
            description="Filter by target accession type(s), comma-separated.",
        ),
    ) -> None:
        self.target: list[AccessionType] | None = None
        if target is not None:
            raw_values = [v.strip() for v in target.split(",") if v.strip()]
            if not raw_values:
                return
            invalid = [v for v in raw_values if v not in _VALID_ACCESSION_TYPES]
            if invalid:
                valid_types = ", ".join(sorted(_VALID_ACCESSION_TYPES))
                msg = f"Invalid target type(s): {', '.join(invalid)}. Valid types: {valid_types}"
                raise HTTPException(status_code=422, detail=msg)
            self.target = [AccessionType(v) for v in raw_values]
