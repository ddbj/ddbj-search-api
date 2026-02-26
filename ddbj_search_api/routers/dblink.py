"""DBLinks API router -- accession cross-reference lookups via DuckDB."""

from __future__ import annotations

import logging
from typing import cast

from ddbj_search_converter.jsonl.utils import to_xref
from ddbj_search_converter.schema import XrefType
from fastapi import APIRouter, HTTPException, Path, Query

from ddbj_search_api.config import DBLINK_DB_PATH
from ddbj_search_api.dblink.client import get_linked_ids
from ddbj_search_api.schemas.dblink import AccessionType, DbLinksResponse, DbLinksTypesResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dblink", tags=["dblink"])

_VALID_ACCESSION_TYPE_VALUES: frozenset[str] = frozenset(e.value for e in AccessionType)


def _parse_target(target: str | None) -> list[AccessionType] | None:
    """Parse and validate the ``target`` query parameter."""
    if target is None:
        return None
    raw_values = [v.strip() for v in target.split(",") if v.strip()]
    if not raw_values:
        return None
    invalid = [v for v in raw_values if v not in _VALID_ACCESSION_TYPE_VALUES]
    if invalid:
        valid_types = ", ".join(sorted(_VALID_ACCESSION_TYPE_VALUES))
        msg = f"Invalid target type(s): {', '.join(invalid)}. Valid types: {valid_types}"
        raise HTTPException(status_code=422, detail=msg)
    return [AccessionType(v) for v in raw_values]


@router.get(
    "/",
    response_model=DbLinksTypesResponse,
    summary="List available accession types",
)
@router.get(
    "",
    response_model=DbLinksTypesResponse,
    include_in_schema=False,
)
def list_types() -> DbLinksTypesResponse:
    """Return all available AccessionType values (static, no DB required)."""
    return DbLinksTypesResponse(types=sorted(AccessionType, key=lambda t: t.value))


@router.get(
    "/{type}/{id}",
    response_model=DbLinksResponse,
    summary="Get linked accessions",
)
@router.get(
    "/{type}/{id}/",
    response_model=DbLinksResponse,
    include_in_schema=False,
)
def get_links(
    type: AccessionType = Path(description="Source accession type."),
    id: str = Path(description="Source accession identifier."),
    target: str | None = Query(
        default=None,
        description="Filter by target accession type(s), comma-separated.",
    ),
) -> DbLinksResponse:
    """Look up related accessions for the given type/id pair."""
    parsed_target = _parse_target(target)
    target_values: list[str] | None = None
    if parsed_target is not None:
        target_values = [t.value for t in parsed_target]

    try:
        rows = get_linked_ids(DBLINK_DB_PATH, type.value, id, target=target_values)
    except FileNotFoundError:
        logger.exception("DuckDB file not found: %s", DBLINK_DB_PATH)
        raise HTTPException(
            status_code=500,
            detail=f"dblink database is not available: {DBLINK_DB_PATH}",
        ) from None

    links = [to_xref(acc, type_hint=cast(XrefType, t)) for t, acc in rows]

    return DbLinksResponse(identifier=id, type=type, links=links)
