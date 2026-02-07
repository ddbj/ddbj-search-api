"""Query parameter dependency classes for FastAPI endpoints.

Each class is used as a FastAPI ``Depends()`` dependency.  Mixin classes
(PaginationQuery, SearchFilterQuery, ResponseControlQuery) are composed
at the endpoint level via multiple ``Depends()`` parameters.
"""
from enum import Enum
from typing import Optional

from fastapi import HTTPException, Query

# === Enums for query parameters ===

class KeywordOperator(str, Enum):
    """Boolean operator for combining keywords."""

    AND = "AND"
    OR = "OR"


class BulkFormat(str, Enum):
    """Output format for the Bulk API."""

    json = "json"
    ndjson = "ndjson"


# === Mixin query classes ===

class PaginationQuery:
    """Pagination parameters (page, perPage).

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
    ):
        self.page = page
        self.per_page = per_page


class SearchFilterQuery:
    """Search filter parameters.

    Used by: EntriesQuery, EntriesTypeQuery, FacetsQuery, FacetsTypeQuery.
    """

    def __init__(
        self,
        keywords: Optional[str] = Query(
            default=None,
            description="Search keywords (comma-separated for multiple).",
        ),
        keyword_fields: Optional[str] = Query(
            default=None,
            alias="keywordFields",
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
        organism: Optional[str] = Query(
            default=None,
            description="NCBI Taxonomy ID (e.g. '9606').",
        ),
        date_published_from: Optional[str] = Query(
            default=None,
            pattern=r"^\d{4}-\d{2}-\d{2}$",
            alias="datePublishedFrom",
            description="Publication date range start (YYYY-MM-DD).",
        ),
        date_published_to: Optional[str] = Query(
            default=None,
            pattern=r"^\d{4}-\d{2}-\d{2}$",
            alias="datePublishedTo",
            description="Publication date range end (YYYY-MM-DD).",
        ),
        date_updated_from: Optional[str] = Query(
            default=None,
            pattern=r"^\d{4}-\d{2}-\d{2}$",
            alias="dateUpdatedFrom",
            description="Update date range start (YYYY-MM-DD).",
        ),
        date_updated_to: Optional[str] = Query(
            default=None,
            pattern=r"^\d{4}-\d{2}-\d{2}$",
            alias="dateUpdatedTo",
            description="Update date range end (YYYY-MM-DD).",
        ),
    ):
        self.keywords = keywords
        self.keyword_fields = keyword_fields
        self.keyword_operator = keyword_operator
        self.organism = organism
        self.date_published_from = date_published_from
        self.date_published_to = date_published_to
        self.date_updated_from = date_updated_from
        self.date_updated_to = date_updated_to


class ResponseControlQuery:
    """Response control parameters (sort, fields, include*).

    Used by: EntriesQuery, EntriesTypeQuery.
    """

    def __init__(
        self,
        sort: Optional[str] = Query(
            default=None,
            description=(
                "Sort order as '{field}:{direction}'. "
                "Fields: datePublished, dateUpdated. "
                "Direction: asc, desc. "
                "Default: relevance (search score)."
            ),
        ),
        fields: Optional[str] = Query(
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
            description=(
                "Include facet aggregation alongside search results."
            ),
        ),
    ):
        self.sort = sort
        self.fields = fields
        self.include_properties = include_properties
        self.include_facets = include_facets


# === Endpoint-specific query classes ===

class EntriesTypesQuery:
    """Extra parameter for cross-type search (GET /entries/)."""

    def __init__(
        self,
        types: Optional[str] = Query(
            default=None,
            description="Filter by database types (comma-separated).",
        ),
    ):
        self.types = types


class BioProjectExtraQuery:
    """BioProject-specific filter parameters.

    Used by: GET /entries/bioproject/, GET /facets/bioproject.
    """

    def __init__(
        self,
        organization: Optional[str] = Query(
            default=None,
            description="Filter by organization name (text search).",
        ),
        publication: Optional[str] = Query(
            default=None,
            description="Filter by publication (text search).",
        ),
        grant: Optional[str] = Query(
            default=None,
            description="Filter by grant (text search).",
        ),
        umbrella: Optional[str] = Query(
            default=None,
            description=(
                "Filter by umbrella status: TRUE or FALSE "
                "(case-insensitive)."
            ),
        ),
    ):
        self.organization = organization
        self.publication = publication
        self.grant = grant
        if umbrella is not None:
            umbrella = umbrella.upper()
            if umbrella not in ("TRUE", "FALSE"):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Invalid umbrella value: "
                        "must be TRUE or FALSE (case-insensitive)."
                    ),
                )
        self.umbrella = umbrella


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
                "Maximum number of dbXrefs to return (0-1000). "
                "Use 0 to omit dbXrefs but still get dbXrefsCount."
            ),
        ),
    ):
        self.db_xrefs_limit = db_xrefs_limit


class BulkQuery:
    """Query parameters for the Bulk API (POST /entries/{type}/bulk)."""

    def __init__(
        self,
        format: BulkFormat = Query(
            default=BulkFormat.json,
            description=(
                "Response format: 'json' (JSON Array) or "
                "'ndjson' (Newline Delimited JSON)."
            ),
        ),
    ):
        self.format = format
