"""Schema types for the DBLinks API."""

from __future__ import annotations

from enum import Enum
from typing import get_args

from ddbj_search_converter.dblink.db import AccessionType as _AccessionTypeLiteral
from ddbj_search_converter.schema import Xref
from fastapi import HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

AccessionType = Enum(  # type: ignore[misc]
    "AccessionType",
    {v.replace("-", "_"): v for v in get_args(_AccessionTypeLiteral)},
    type=str,
)


_VALID_ACCESSION_TYPES: frozenset[str] = frozenset(e.value for e in AccessionType)


class DbLinksResponse(BaseModel):
    """Response for GET /dblink/{type}/{id}."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "identifier": "hum0014",
                    "type": "humandbs",
                    "dbXrefs": [
                        {
                            "identifier": "JGAS000101",
                            "type": "jga-study",
                            "url": "https://ddbj.nig.ac.jp/search/entry/jga-study/JGAS000101",
                        },
                        {
                            "identifier": "JGAS000381",
                            "type": "jga-study",
                            "url": "https://ddbj.nig.ac.jp/search/entry/jga-study/JGAS000381",
                        },
                    ],
                },
            ],
        },
    )

    identifier: str = Field(examples=["hum0014"], description="Source accession identifier.")
    type: AccessionType = Field(examples=["humandbs"], description="Source accession type.")
    dbXrefs: list[Xref] = Field(
        examples=[
            [
                {
                    "identifier": "JGAS000101",
                    "type": "jga-study",
                    "url": "https://ddbj.nig.ac.jp/search/entry/jga-study/JGAS000101",
                },
            ],
        ],
        description="Related entries (sorted by type, then identifier).",
    )


class DbLinksTypesResponse(BaseModel):
    """Response for GET /dblink/."""

    types: list[AccessionType] = Field(
        examples=[["biosample", "bioproject", "sra-experiment"]],
        description="Available accession types.",
    )


class DbLinksQuery:
    """Query parameters for GET /dblink/{type}/{id}."""

    def __init__(
        self,
        target: str | None = Query(
            default=None,
            examples=["biosample,sra-experiment"],
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


# --- POST /dblink/counts ---


class DbLinksCountsRequestItem(BaseModel):
    """A single item in the bulk counts request."""

    type: AccessionType = Field(examples=["biosample"], description="Accession type.")
    id: str = Field(examples=["SAMD00012345"], description="Accession identifier.")


class DbLinksCountsRequest(BaseModel):
    """Request body for POST /dblink/counts."""

    items: list[DbLinksCountsRequestItem] = Field(
        examples=[[{"type": "biosample", "id": "SAMD00012345"}]],
        description="Accessions to count linked entries for.",
        min_length=1,
        max_length=100,
    )


class DbLinksCountsResponseItem(BaseModel):
    """A single item in the bulk counts response."""

    identifier: str = Field(examples=["SAMD00012345"], description="Accession identifier.")
    type: AccessionType = Field(examples=["biosample"], description="Accession type.")
    counts: dict[str, int] = Field(
        examples=[{"sra-experiment": 3, "bioproject": 1}],
        description="Per-type linked accession counts.",
    )


class DbLinksCountsResponse(BaseModel):
    """Response for POST /dblink/counts."""

    items: list[DbLinksCountsResponseItem] = Field(
        examples=[
            [
                {
                    "identifier": "SAMD00012345",
                    "type": "biosample",
                    "counts": {"sra-experiment": 3, "bioproject": 1},
                },
            ],
        ],
        description="Counts per requested accession.",
    )
