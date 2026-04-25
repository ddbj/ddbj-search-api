"""Query parameter dependency classes for FastAPI endpoints.

Each class is used as a FastAPI ``Depends()`` dependency.  Mixin classes
(PaginationQuery, SearchFilterQuery, ResponseControlQuery) are composed
at the endpoint level via multiple ``Depends()`` parameters.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum

from fastapi import HTTPException, Query

from ddbj_search_api.schemas.common import DbType

# Patterns expressed in OpenAPI for client codegen / docs.  Server-side
# semantic validation is still performed below (e.g. unknown-type lookup
# in TypesFilterQuery) so that legacy detail messages are preserved.
_KEYWORD_FIELDS_PATTERN = r"^(identifier|title|name|description)(,(identifier|title|name|description))*$"
_DB_TYPES_PATTERN = (
    r"^(bioproject|biosample|sra-submission|sra-study|sra-experiment|sra-run|sra-sample|sra-analysis|"
    r"jga-study|jga-dataset|jga-dac|jga-policy)"
    r"(,(bioproject|biosample|sra-submission|sra-study|sra-experiment|sra-run|sra-sample|sra-analysis|"
    r"jga-study|jga-dataset|jga-dac|jga-policy))*$"
)
_SORT_PATTERN = r"^(datePublished|dateModified):(asc|desc)$"
_OBJECT_TYPES_PATTERN = r"^(BioProject|UmbrellaBioProject)(,(BioProject|UmbrellaBioProject))*$"
_ORGANISM_PATTERN = r"^\d+$"

# === Enums for query parameters ===


class KeywordOperator(str, Enum):
    """Boolean operator for combining keywords."""

    AND = "AND"
    OR = "OR"


class BulkFormat(str, Enum):
    """Output format for the Bulk API."""

    json = "json"
    ndjson = "ndjson"


_VALID_DB_TYPES = {e.value for e in DbType}


def _validate_date(value: str | None, param_name: str) -> None:
    """Validate that a date string is a real calendar date.

    The regex ``^\\d{4}-\\d{2}-\\d{2}$`` on the Query parameter already
    rejects non-YYYY-MM-DD formats.  This function catches semantically
    invalid dates such as ``2024-02-30`` or ``2024-13-01``.
    """
    if value is None:
        return
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=(f"Invalid date for {param_name}: '{value}'. Must be a valid calendar date in YYYY-MM-DD format."),
        ) from exc


# === Mixin query classes ===


class PaginationQuery:
    """Pagination parameters (page, perPage, cursor).

    Used by: EntriesQuery, EntriesTypeQuery, DbXrefsQuery.
    """

    def __init__(
        self,
        page: int = Query(
            default=1,
            ge=1,
            description="Page number (1-based).",
        ),
        per_page: int = Query(
            default=10,
            ge=1,
            le=100,
            alias="perPage",
            description="Items per page (1-100).",
        ),
        cursor: str | None = Query(
            default=None,
            description="Cursor token for cursor-based pagination.",
        ),
    ):
        self.page = page
        self.per_page = per_page
        self.cursor = cursor


class SearchFilterQuery:
    """Search filter parameters.

    Used by: EntriesQuery, EntriesTypeQuery, FacetsQuery, FacetsTypeQuery.
    """

    def __init__(
        self,
        keywords: str | None = Query(
            default=None,
            description="Search keywords (comma-separated for multiple).",
        ),
        keyword_fields: str | None = Query(
            default=None,
            alias="keywordFields",
            pattern=_KEYWORD_FIELDS_PATTERN,
            description=(
                "Limit keyword search to specific fields "
                "(comma-separated). "
                "Allowed: identifier, title, name, description."
            ),
        ),
        keyword_operator: KeywordOperator = Query(
            default=KeywordOperator.AND,
            alias="keywordOperator",
            description="Boolean operator for keywords: AND or OR.",
        ),
        organism: str | None = Query(
            default=None,
            pattern=_ORGANISM_PATTERN,
            description="NCBI Taxonomy ID, digits only (e.g. '9606').",
        ),
        date_published_from: str | None = Query(
            default=None,
            pattern=r"^\d{4}-\d{2}-\d{2}$",
            alias="datePublishedFrom",
            description="Publication date range start (YYYY-MM-DD).",
        ),
        date_published_to: str | None = Query(
            default=None,
            pattern=r"^\d{4}-\d{2}-\d{2}$",
            alias="datePublishedTo",
            description="Publication date range end (YYYY-MM-DD).",
        ),
        date_modified_from: str | None = Query(
            default=None,
            pattern=r"^\d{4}-\d{2}-\d{2}$",
            alias="dateModifiedFrom",
            description="Modification date range start (YYYY-MM-DD).",
        ),
        date_modified_to: str | None = Query(
            default=None,
            pattern=r"^\d{4}-\d{2}-\d{2}$",
            alias="dateModifiedTo",
            description="Modification date range end (YYYY-MM-DD).",
        ),
    ):
        _validate_date(date_published_from, "datePublishedFrom")
        _validate_date(date_published_to, "datePublishedTo")
        _validate_date(date_modified_from, "dateModifiedFrom")
        _validate_date(date_modified_to, "dateModifiedTo")

        self.keywords = keywords
        self.keyword_fields = keyword_fields
        self.keyword_operator = keyword_operator
        self.organism = organism
        self.date_published_from = date_published_from
        self.date_published_to = date_published_to
        self.date_modified_from = date_modified_from
        self.date_modified_to = date_modified_to


class ResponseControlQuery:
    """Response control parameters (sort, fields, include*).

    Used by: EntriesQuery, EntriesTypeQuery.
    """

    def __init__(
        self,
        sort: str | None = Query(
            default=None,
            pattern=_SORT_PATTERN,
            description=(
                "Sort order as '{field}:{direction}'. "
                "Fields: datePublished, dateModified. "
                "Direction: asc, desc. "
                "Default: relevance (search score)."
            ),
        ),
        fields: str | None = Query(
            default=None,
            description=(
                "Limit response to specific top-level fields "
                "(comma-separated). "
                "Example: 'identifier,organism,datePublished'."
            ),
        ),
        include_properties: bool = Query(
            default=True,
            alias="includeProperties",
            description="Include the 'properties' field in the response.",
        ),
        include_facets: bool = Query(
            default=False,
            alias="includeFacets",
            description=("Include facet aggregation alongside search results."),
        ),
    ):
        self.sort = sort
        self.fields = fields
        self.include_properties = include_properties
        self.include_facets = include_facets


# === Shared endpoint query classes ===


class TypesFilterQuery:
    """Filter by database types (comma-separated).

    Used by: EntriesQuery, FacetsQuery.
    """

    def __init__(
        self,
        types: str | None = Query(
            default=None,
            pattern=_DB_TYPES_PATTERN,
            description="Filter by database types (comma-separated). Allowed: any of DbType.",
        ),
    ):
        if types is not None:
            type_list = [t.strip() for t in types.split(",")]
            type_list = [t for t in type_list if t]
            if type_list:
                invalid = [t for t in type_list if t not in _VALID_DB_TYPES]
                if invalid:
                    raise HTTPException(
                        status_code=422,
                        detail=(f"Invalid types: {', '.join(invalid)}. Allowed: {', '.join(sorted(_VALID_DB_TYPES))}."),
                    )
        self.types = types


class DbXrefsLimitQuery:
    """dbXrefs truncation limit.

    Used by: EntriesQuery, EntriesTypeQuery.
    """

    def __init__(
        self,
        db_xrefs_limit: int = Query(
            default=100,
            ge=0,
            le=1000,
            alias="dbXrefsLimit",
            description=(
                "Maximum number of dbXrefs to return per type (0-1000). "
                "Use 0 to omit dbXrefs but still get dbXrefsCount."
            ),
        ),
        include_db_xrefs: bool = Query(
            default=True,
            alias="includeDbXrefs",
            description=(
                "Include dbXrefs and dbXrefsCount from DuckDB. When false, both are omitted and DuckDB is not queried."
            ),
        ),
    ):
        self.db_xrefs_limit = db_xrefs_limit
        self.include_db_xrefs = include_db_xrefs


# === Endpoint-specific query classes ===


class BioProjectExtraQuery:
    """BioProject-specific filter parameters.

    Used by: GET /entries/bioproject/, GET /facets/bioproject.
    """

    def __init__(
        self,
        organization: str | None = Query(
            default=None,
            description="Filter by organization name (text search).",
        ),
        publication: str | None = Query(
            default=None,
            description="Filter by publication (text search).",
        ),
        grant: str | None = Query(
            default=None,
            description="Filter by grant (text search).",
        ),
        object_types: str | None = Query(
            default=None,
            alias="objectTypes",
            pattern=_OBJECT_TYPES_PATTERN,
            description=(
                "Filter by BioProject objectType (comma-separated). "
                "Allowed: BioProject, UmbrellaBioProject. "
                "Specifying both is equivalent to omitting the filter."
            ),
        ),
    ):
        self.organization = organization
        self.publication = publication
        self.grant = grant
        self.object_types = object_types


class EntryDetailQuery:
    """Query parameters for entry detail endpoint (GET /entries/{type}/{id}).

    Controls dbXrefs truncation for frontend-oriented responses.
    """

    def __init__(
        self,
        db_xrefs_limit: int = Query(
            default=100,
            ge=0,
            le=1000,
            alias="dbXrefsLimit",
            description=(
                "Maximum number of dbXrefs to return per type (0-1000). "
                "Use 0 to omit dbXrefs but still get dbXrefsCount."
            ),
        ),
        include_db_xrefs: bool = Query(
            default=True,
            alias="includeDbXrefs",
            description=(
                "Include dbXrefs and dbXrefsCount from DuckDB. When false, both are omitted and DuckDB is not queried."
            ),
        ),
    ):
        self.db_xrefs_limit = db_xrefs_limit
        self.include_db_xrefs = include_db_xrefs


class BulkQuery:
    """Query parameters for the Bulk API (POST /entries/{type}/bulk)."""

    def __init__(
        self,
        format: BulkFormat = Query(
            default=BulkFormat.json,
            description=("Response format: 'json' (JSON Array) or 'ndjson' (Newline Delimited JSON)."),
        ),
        include_db_xrefs: bool = Query(
            default=True,
            alias="includeDbXrefs",
            description=("Include dbXrefs from DuckDB. When false, DuckDB is not queried and dbXrefs are omitted."),
        ),
    ):
        self.format = format
        self.include_db_xrefs = include_db_xrefs
