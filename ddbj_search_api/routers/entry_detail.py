from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from ddbj_search_api.schemas import (ConverterEntry, DbType, EntryDetail,
                                     EntryDetailJsonLd, ProblemDetails)

router = APIRouter()


@router.get(
    "/entries/{type}/{id}",
    response_model=ConverterEntry,
    summary="Entry detail (JSON)",
    description="Retrieve a single entry by type and identifier. Returns the full entry detail in JSON format.",
    responses={
        404: {"model": ProblemDetails},
        500: {"model": ProblemDetails},
    },
    tags=["entries"],
)
async def get_entry(
    type: DbType,
    id: str,
    fields: Optional[str] = Query(
        None,
        description="Select specific fields to include in the response (comma-separated, e.g., 'identifier,title,organism'). If omitted, all fields are returned.",
    ),
) -> EntryDetail:
    # TODO: Phase 2 - Implement ES get
    return EntryDetail(identifier=id, type=type)  # type: ignore[call-arg]


@router.get(
    "/entries/{type}/{id}.json",
    response_model=ConverterEntry,
    summary="Entry detail (JSON) - compatibility",
    description="Compatibility endpoint. Behaves identically to GET /entries/{type}/{id} but with an explicit .json extension.",
    responses={
        404: {"model": ProblemDetails},
        500: {"model": ProblemDetails},
    },
    tags=["entries"],
)
async def get_entry_json(
    type: DbType,
    id: str,
    fields: Optional[str] = Query(
        None,
        description="Select specific fields to include in the response (comma-separated, e.g., 'identifier,title,organism'). If omitted, all fields are returned.",
    ),
) -> EntryDetail:
    # TODO: Phase 2 - Implement ES get
    return EntryDetail(identifier=id, type=type)  # type: ignore[call-arg]


@router.get(
    "/entries/{type}/{id}.jsonld",
    response_model=EntryDetailJsonLd,
    summary="Entry detail (JSON-LD)",
    description="Retrieve entry in JSON-LD format. Returns the entry with JSON-LD @context and @id annotations.",
    responses={
        404: {"model": ProblemDetails},
        500: {"model": ProblemDetails},
    },
    response_class=JSONResponse,
    tags=["entries"],
)
async def get_entry_jsonld(
    type: DbType,
    id: str,
    fields: Optional[str] = Query(
        None,
        description="Select specific fields to include in the response (comma-separated, e.g., 'identifier,title,organism'). If omitted, all fields are returned.",
    ),
) -> JSONResponse:
    # TODO: Phase 2 - Implement ES get + JSON-LD context
    data = EntryDetailJsonLd(
        **{  # type: ignore[arg-type]
            "@context": "",
            "@id": "",
            "identifier": id,
            "type": type,
        }
    )
    return JSONResponse(
        content=data.model_dump(by_alias=True),
        media_type="application/ld+json",
    )
