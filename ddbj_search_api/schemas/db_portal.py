"""DB Portal API schemas (AP1).

Request/response types for ``GET /db-portal/search``.  Kept independent
from ``schemas/queries.py`` and ``schemas/common.py`` because db-portal
uses a distinct response envelope (``hits``) and a reduced sort
allowlist.  Existing ``EntryListItem`` is not reused since the SSOT
(`docs/api-spec.md` § DB Portal API) defines a separate hit shape.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from fastapi import HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field


class DbPortalDb(str, Enum):
    """Database identifier for db-portal search (8 values)."""

    trad = "trad"
    sra = "sra"
    bioproject = "bioproject"
    biosample = "biosample"
    jga = "jga"
    gea = "gea"
    metabobank = "metabobank"
    taxonomy = "taxonomy"


class DbPortalCountError(str, Enum):
    """Error reason for a DB entry in the cross-search count response.

    ``not_implemented`` is used for AP1 Solr-backed databases
    (``trad``, ``taxonomy``) and will be removed when AP4 lands.
    """

    timeout = "timeout"
    upstream_5xx = "upstream_5xx"
    connection_refused = "connection_refused"
    not_implemented = "not_implemented"
    unknown = "unknown"


class DbPortalErrorType(str, Enum):
    """Problem Details ``type`` URI for db-portal-specific errors.

    URIs are RFC 7807 §3.1 identifiers and need not be dereferenceable.
    """

    invalid_query_combination = "https://ddbj.nig.ac.jp/problems/invalid-query-combination"
    advanced_search_not_implemented = "https://ddbj.nig.ac.jp/problems/advanced-search-not-implemented"
    db_not_implemented = "https://ddbj.nig.ac.jp/problems/db-not-implemented"


ALLOWED_DB_PORTAL_SORTS: frozenset[str] = frozenset({"datePublished:desc", "datePublished:asc"})
ALLOWED_DB_PORTAL_PER_PAGE: frozenset[int] = frozenset({20, 50, 100})


class DbPortalQuery:
    """Query parameters for ``GET /db-portal/search``.

    FastAPI ``Depends()``-injectable class (same pattern as
    ``schemas/queries.py``).  ``q``/``adv`` exclusivity is checked in
    the router so the proper Problem Details ``type`` URI can be
    attached to the 400 response.
    """

    def __init__(
        self,
        q: str | None = Query(
            default=None,
            description=(
                "Simple search keyword(s).  Comma-separated for multiple values; "
                "double quotes for explicit phrase match; symbols (-, /, ., +, :) "
                "trigger automatic phrase match."
            ),
        ),
        adv: str | None = Query(
            default=None,
            description=(
                "Advanced Search DSL.  Not implemented in AP1 (returns 501 "
                "``advanced-search-not-implemented``); planned for AP3."
            ),
        ),
        db: DbPortalDb | None = Query(
            default=None,
            description=(
                "Target database.  Omit for cross-db count-only.  "
                "``trad`` / ``taxonomy`` return 501 in AP1 (planned for AP4)."
            ),
        ),
        page: int = Query(
            default=1,
            ge=1,
            description="Page number (1-based).",
        ),
        per_page: int = Query(
            default=20,
            alias="perPage",
            description="Items per page.  Allowed: 20, 50, 100.",
        ),
        cursor: str | None = Query(
            default=None,
            description="Cursor token for cursor-based pagination (HMAC-signed, PIT 5 min).",
        ),
        sort: str | None = Query(
            default=None,
            description=(
                "Sort order.  Allowed: null (relevance, default), ``datePublished:desc``, ``datePublished:asc``."
            ),
        ),
    ) -> None:
        if per_page not in ALLOWED_DB_PORTAL_PER_PAGE:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid perPage value: '{per_page}'.  Allowed: 20, 50, 100.",
            )
        if sort is not None and sort not in ALLOWED_DB_PORTAL_SORTS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Invalid sort value: '{sort}'.  Allowed: null (relevance), datePublished:desc, datePublished:asc."
                ),
            )
        self.q = q
        self.adv = adv
        self.db = db
        self.page = page
        self.per_page = per_page
        self.cursor = cursor
        self.sort = sort


class DbPortalCount(BaseModel):
    """A single DB entry in the cross-search count-only response."""

    model_config = ConfigDict(populate_by_name=True)

    db: DbPortalDb = Field(description="Database identifier.")
    count: int | None = Field(description="Hit count (null when error is set).")
    error: DbPortalCountError | None = Field(
        description="Failure reason (null on success).",
    )


class DbPortalCrossSearchResponse(BaseModel):
    """Cross-database count-only response (8 entries, fixed order).

    Order: trad, sra, bioproject, biosample, jga, gea, metabobank, taxonomy.
    """

    databases: list[DbPortalCount] = Field(
        description="Count per database.  Fixed length 8, fixed order.",
    )


class DbPortalHit(BaseModel):
    """A single hit in the DB-specific search response.

    ``extra="allow"`` keeps DB-specific ``_source`` fields intact so the
    response can evolve without schema churn.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    identifier: str = Field(description="Entry identifier.")
    type: str = Field(description="Entry type (e.g. 'bioproject', 'sra-study').")
    title: str | None = Field(default=None, description="Entry title.")
    description: str | None = Field(default=None, description="Entry description.")
    organism: Any | None = Field(default=None, description="Organism information.")
    date_published: str | None = Field(
        default=None,
        alias="datePublished",
        description="Publication date (ISO 8601).",
    )
    url: str | None = Field(default=None, description="Canonical URL.")
    same_as: list[Any] | None = Field(
        default=None,
        alias="sameAs",
        description="Equivalent identifiers (from ES _source).",
    )
    db_xrefs: list[Any] | None = Field(
        default=None,
        alias="dbXrefs",
        description=("Cross-references.  In AP1 this passes through ES _source only (DuckDB enrichment is deferred)."),
    )


class DbPortalHitsResponse(BaseModel):
    """DB-specific search response (hits envelope + pagination)."""

    model_config = ConfigDict(populate_by_name=True)

    total: int = Field(description="Total matching hits (track_total_hits=true).")
    hits: list[DbPortalHit] = Field(description="Search hits.")
    hard_limit_reached: bool = Field(
        alias="hardLimitReached",
        description="True when total >= 10000 (aligned with Solr hard limit).",
    )
    page: int | None = Field(
        description="Current page (null in cursor mode).",
    )
    per_page: int = Field(
        alias="perPage",
        description="Items per page (20, 50, or 100).",
    )
    next_cursor: str | None = Field(
        default=None,
        alias="nextCursor",
        description="Cursor token for the next page (null on last page).",
    )
    has_next: bool = Field(
        default=False,
        alias="hasNext",
        description="Whether more pages are available.",
    )
