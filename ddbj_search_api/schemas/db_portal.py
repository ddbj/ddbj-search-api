"""DB Portal API schemas.

Request/response types for ``GET /db-portal/cross-search``, ``GET /db-portal/search``
and ``GET /db-portal/parse``.

- ``DbPortalHit`` は ``type`` discriminator を持つ Pydantic v2 discriminated union
  の 8 variant に分割して明示型化。``extra="ignore"`` で converter 側の将来新 field は
  silently drop する。
- helper DTO (`OrganismOut` / `OrganizationOut` / `PublicationOut` / `GrantOut` /
  `XrefOut` / `ExternalLinkOut` / `BioSamplePackageOut`) は converter 側 model を
  import せずに API 側で再定義 (Pin drift 回避)。
- ``_DbPortalHitAdapter`` は discriminated union の TypeAdapter。ES ``_source`` /
  Solr doc の dict → 正しい variant への dispatch を担う。
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, TypeAlias

from fastapi import HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


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
    """Error reason for a DB entry in the cross-search count response."""

    timeout = "timeout"
    upstream_5xx = "upstream_5xx"
    connection_refused = "connection_refused"
    unknown = "unknown"


class DbPortalErrorType(str, Enum):
    """Problem Details ``type`` URI for db-portal-specific errors.

    URIs are RFC 7807 §3.1 identifiers and need not be dereferenceable.

    Member names mirror ``ddbj_search_api.search.dsl.errors.ErrorType`` so the
    router can map a ``DslError`` to the db-portal enum via ``DbPortalErrorType[err.type.name]``.
    ``advanced_search_not_implemented`` is retained for backward compatibility but is
    not emitted by the DSL-backed router (slated for removal in a future cleanup PR).
    """

    invalid_query_combination = "https://ddbj.nig.ac.jp/problems/invalid-query-combination"
    advanced_search_not_implemented = "https://ddbj.nig.ac.jp/problems/advanced-search-not-implemented"
    cursor_not_supported = "https://ddbj.nig.ac.jp/problems/cursor-not-supported"
    unexpected_parameter = "https://ddbj.nig.ac.jp/problems/unexpected-parameter"
    missing_db = "https://ddbj.nig.ac.jp/problems/missing-db"
    # DSL parser error types.
    unexpected_token = "https://ddbj.nig.ac.jp/problems/unexpected-token"
    unknown_field = "https://ddbj.nig.ac.jp/problems/unknown-field"
    field_not_available_in_cross_db = "https://ddbj.nig.ac.jp/problems/field-not-available-in-cross-db"
    invalid_date_format = "https://ddbj.nig.ac.jp/problems/invalid-date-format"
    invalid_operator_for_field = "https://ddbj.nig.ac.jp/problems/invalid-operator-for-field"
    nest_depth_exceeded = "https://ddbj.nig.ac.jp/problems/nest-depth-exceeded"
    missing_value = "https://ddbj.nig.ac.jp/problems/missing-value"


ALLOWED_DB_PORTAL_SORTS: frozenset[str] = frozenset({"datePublished:desc", "datePublished:asc"})
ALLOWED_DB_PORTAL_PER_PAGE: frozenset[int] = frozenset({20, 50, 100})


_Q_DESC = (
    "Simple search keyword(s).  Comma-separated for multiple values; "
    "double quotes for explicit phrase match; symbols (-, /, ., +, :) "
    "trigger automatic phrase match."
)

_ADV_DESC = (
    "Advanced Search DSL.  Lark LALR(1)-parsed Lucene subset with "
    "field-prefixed leaves (``title:cancer``, ``date_published:[2020-01-01 TO 2024-12-31]``, "
    '``organism:"Homo sapiens"``, ``identifier:PRJ*``) joined by ``AND``/``OR``/``NOT`` '
    "(case-sensitive, uppercase).  Tier 1 (cross): ``identifier``, ``title``, "
    "``description``, ``organism``, ``date_published``, ``date_modified``, "
    "``date_created``, ``date``.  Tier 2 (cross): ``submitter``, ``publication``.  "
    "Tier 3 (single-DB only): BioProject ``project_type`` / ``grant_agency`` / "
    "SRA ``library_strategy`` etc. / JGA ``study_type`` / GEA+MetaboBank ``experiment_type`` / "
    "MetaboBank ``submission_type`` / Trad ``division`` etc. / Taxonomy ``rank`` etc.  "
    "Errors surface as RFC 7807 problem details with a dedicated ``type`` URI "
    "(``unexpected-token`` / ``unknown-field`` / ``field-not-available-in-cross-db`` etc.)."
)


class DbPortalCrossSearchQuery:
    """Query parameters for ``GET /db-portal/cross-search``.

    Cross-database search returning per-DB count and (when ``topHits>=1``)
    a lightweight hits array.  Only ``q`` / ``adv`` / ``topHits`` are
    accepted; any other query parameter (``db`` / ``cursor`` / ``page`` /
    ``perPage`` / ``sort``) is rejected by the router with 400
    ``unexpected-parameter`` so user typos surface early.  ``q`` / ``adv``
    exclusivity is checked in the router with the ``invalid-query-combination``
    type URI.
    """

    def __init__(
        self,
        q: str | None = Query(default=None, description=_Q_DESC),
        adv: str | None = Query(default=None, description=_ADV_DESC),
        top_hits: int = Query(
            default=10,
            alias="topHits",
            ge=0,
            le=50,
            description=(
                "Per-DB top hits count.  ``0`` returns count-only "
                "(``databases[i].hits`` is ``null``); ``1``-``50`` returns "
                "up to N hits per DB.  Hits are ordered by relevance "
                "(``_score`` desc) with ``identifier`` ascending as the "
                "tiebreaker; when ``q`` is omitted (``match_all``) all "
                "scores tie, so ``identifier`` ascending becomes the "
                "effective order.  Out of range (>50 or negative) returns 422."
            ),
        ),
    ) -> None:
        self.q = q
        self.adv = adv
        self.top_hits = top_hits


class DbPortalSearchQuery:
    """Query parameters for ``GET /db-portal/search``.

    Single-database hits search.  ``db`` is required; the router returns
    400 ``missing-db`` when omitted (instead of FastAPI's default 422)
    so the response contract aligns with the cross-search endpoint's
    ``unexpected-parameter`` slug.  ``q`` / ``adv`` exclusivity is checked
    in the router with the ``invalid-query-combination`` type URI.
    """

    def __init__(
        self,
        q: str | None = Query(default=None, description=_Q_DESC),
        adv: str | None = Query(default=None, description=_ADV_DESC),
        db: DbPortalDb | None = Query(
            default=None,
            description=(
                "Target database (required).  Allowed: ``trad``, ``sra``, ``bioproject``, "
                "``biosample``, ``jga``, ``gea``, ``metabobank``, ``taxonomy``.  "
                "``trad`` routes to ARSA (Solr) and ``taxonomy`` to TXSearch (Solr); "
                "the other six DBs use Elasticsearch.  Omitting returns 400 "
                "``missing-db``; for cross-database count, use ``/db-portal/cross-search``."
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
            json_schema_extra={"enum": [20, 50, 100]},
        ),
        cursor: str | None = Query(
            default=None,
            description="Cursor token for cursor-based pagination (HMAC-signed, PIT 5 min).",
        ),
        sort: Literal["datePublished:asc", "datePublished:desc"] | None = Query(
            default=None,
            description=(
                "Sort order.  Allowed: null (relevance, default), ``datePublished:desc``, ``datePublished:asc``."
            ),
        ),
    ) -> None:
        # Pydantic's Literal[int] does not coerce HTTP query strings to int,
        # so per_page is typed ``int`` and constrained explicitly here while
        # ``json_schema_extra={"enum": [...]}`` exposes the allowed set in
        # the generated OpenAPI document.  ``sort`` uses ``Literal[str]``
        # which Pydantic accepts directly from query strings.
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
    """A single DB entry in the cross-search response.

    Carries the per-DB count and, when ``topHits>=1``, up to ``topHits``
    lightweight hits.  ``hits`` is ``null`` when ``topHits=0`` and an
    empty list when the per-DB call failed (``error`` set).
    """

    model_config = ConfigDict(populate_by_name=True)

    db: DbPortalDb = Field(description="Database identifier.")
    count: int | None = Field(description="Hit count (null when error is set).")
    error: DbPortalCountError | None = Field(
        description="Failure reason (null on success).",
    )
    hits: list[DbPortalLightweightHit] | None = Field(
        default=None,
        description=(
            "Lightweight top hits for this DB (up to topHits items, "
            "relevance order).  ``null`` when ``topHits=0``; ``[]`` "
            "when ``error`` is set; otherwise 0..topHits items."
        ),
    )


class DbPortalCrossSearchResponse(BaseModel):
    """Cross-database response (8 entries, fixed order, count + top hits).

    Order: trad, sra, bioproject, biosample, jga, gea, metabobank, taxonomy.
    Each entry carries count and (when ``topHits>=1``) up to ``topHits``
    lightweight hits per DB.
    """

    databases: list[DbPortalCount] = Field(
        description=("Per-database count and (when topHits>=1) lightweight hits.  Fixed length 8, fixed order."),
    )


# === helper DTO (converter model を import せずに再定義、Pin drift 回避) ===

# Converter 側 Literal 値と一致させる (`ddbj_search_converter.schema` L8-11)。
# Status の値域: public / private / suppressed / withdrawn
# Accessibility の値域: public-access / controlled-access
HitStatus: TypeAlias = Literal["public", "private", "suppressed", "withdrawn"]
HitAccessibility: TypeAlias = Literal["public-access", "controlled-access"]
OrganizationRole: TypeAlias = Literal["owner", "participant", "submitter", "broker"]
OrganizationKind: TypeAlias = Literal["institute", "center", "consortium", "lab"]
PublicationDbType: TypeAlias = Literal["pubmed", "doi", "pmc", "other"]


class OrganismOut(BaseModel):
    """Organism information (common across all DB variants)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    identifier: str | None = Field(default=None, description="NCBI Taxonomy ID.")
    name: str | None = Field(default=None, description="Scientific / common organism name.")


class OrganizationOut(BaseModel):
    """Submitting organization (nested in BioProject / BioSample / SRA / JGA / GEA / MetaboBank)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    name: str | None = None
    abbreviation: str | None = None
    role: OrganizationRole | None = None
    organization_type: OrganizationKind | None = Field(default=None, alias="organizationType")
    department: str | None = None
    url: str | None = None


class PublicationOut(BaseModel):
    """Related publication (nested)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    # converter: `id_: str | None = Field(alias="id")` — API 側も同じ alias パターン
    id_: str | None = Field(default=None, alias="id", description="Publication ID (PubMed / DOI / PMC).")
    title: str | None = None
    date: str | None = None
    reference: str | None = None
    url: str | None = None
    db_type: PublicationDbType | None = Field(default=None, alias="dbType")


class GrantOut(BaseModel):
    """Grant information (nested, agency is nested-of-nested)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id_: str | None = Field(default=None, alias="id")
    title: str | None = None
    agency: list[OrganizationOut] = Field(default_factory=list)


class ExternalLinkOut(BaseModel):
    """External link (BioProject / JGA)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    url: str
    label: str


class XrefOut(BaseModel):
    """Cross-reference entry (``dbXrefs`` / ``sameAs``).

    The upstream cross-reference type is a closed enumeration, but this
    response model accepts ``type`` as a plain string so adding a new
    cross-reference type upstream does not break clients.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    identifier: str
    type: str
    url: str


class BioSamplePackageOut(BaseModel):
    """BioSample package metadata."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    name: str
    display_name: str | None = Field(default=None, alias="displayName")


# === DbPortalHit discriminated union (8 variant) ===


class DbPortalHitBase(BaseModel):
    """Common fields across all DB-specific hit variants.

    ``extra="ignore"`` で converter 側の将来新 field は silently drop する
    (未 allowlist 化 field は後続で明示追加する方針)。
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    identifier: str = Field(description="Entry identifier.")
    title: str | None = Field(default=None, description="Entry title.")
    description: str | None = Field(default=None, description="Entry description.")
    organism: OrganismOut | None = Field(default=None, description="Organism information.")
    date_published: str | None = Field(
        default=None,
        alias="datePublished",
        description="Publication date (ISO 8601).",
    )
    date_modified: str | None = Field(
        default=None,
        alias="dateModified",
        description="Modification date (ISO 8601).",
    )
    date_created: str | None = Field(
        default=None,
        alias="dateCreated",
        description="Creation date (ISO 8601).",
    )
    url: str | None = Field(default=None, description="Canonical URL.")
    same_as: list[XrefOut] | None = Field(
        default=None,
        alias="sameAs",
        description="Equivalent identifiers.",
    )
    db_xrefs: list[XrefOut] | None = Field(
        default=None,
        alias="dbXrefs",
        description="Cross-references.",
    )
    status: HitStatus | None = Field(default=None, description="INSDC status.")
    accessibility: HitAccessibility | None = Field(
        default=None,
        description="Accessibility level (public-access / controlled-access).",
    )
    is_part_of: str | None = Field(
        default=None,
        alias="isPartOf",
        description=(
            "Parent collection identifier.  ES-backed hits carry the "
            'index-level value (e.g. ``"bioproject"`` / ``"sra"``); '
            'Solr-backed hits use a fixed literal (``"trad"`` / '
            '``"taxonomy"``).'
        ),
    )


class DbPortalHitBioProject(DbPortalHitBase):
    """BioProject hit."""

    type: Literal["bioproject"] = Field(description="Hit type discriminator.")
    project_type: Literal["BioProject", "UmbrellaBioProject"] | None = Field(
        default=None,
        alias="objectType",
        description="Umbrella vs regular BioProject.",
    )
    organization: list[OrganizationOut] | None = None
    publication: list[PublicationOut] | None = None
    grant: list[GrantOut] | None = None
    external_link: list[ExternalLinkOut] | None = Field(default=None, alias="externalLink")


class DbPortalHitBioSample(DbPortalHitBase):
    """BioSample hit."""

    type: Literal["biosample"] = Field(description="Hit type discriminator.")
    organization: list[OrganizationOut] | None = None
    package: BioSamplePackageOut | None = None
    model: list[str] | None = None


class DbPortalHitSra(DbPortalHitBase):
    """SRA hit (6 subtypes share one variant; subtype-specific fields are optional).

    ``type`` values: ``sra-submission`` / ``sra-study`` / ``sra-experiment`` /
    ``sra-run`` / ``sra-sample`` / ``sra-analysis``.  ``library_*`` /
    ``platform`` / ``instrumentModel`` are populated only on
    ``sra-experiment`` hits, and ``analysisType`` only on
    ``sra-analysis`` hits; the remaining subtypes leave them as
    ``null``.
    """

    type: Literal[
        "sra-submission",
        "sra-study",
        "sra-experiment",
        "sra-run",
        "sra-sample",
        "sra-analysis",
    ] = Field(description="Hit type discriminator (6 SRA entity types).")
    organization: list[OrganizationOut] | None = None
    publication: list[PublicationOut] | None = None
    library_strategy: list[str] | None = Field(default=None, alias="libraryStrategy")
    library_source: list[str] | None = Field(default=None, alias="librarySource")
    library_selection: list[str] | None = Field(default=None, alias="librarySelection")
    library_layout: str | None = Field(default=None, alias="libraryLayout")
    platform: str | None = None
    instrument_model: list[str] | None = Field(default=None, alias="instrumentModel")
    analysis_type: str | None = Field(default=None, alias="analysisType")


class DbPortalHitJga(DbPortalHitBase):
    """JGA hit (4 subtypes share one variant; subtype-specific fields are optional).

    ``type`` values: ``jga-study`` / ``jga-dataset`` / ``jga-dac`` /
    ``jga-policy``.  ``studyType`` / ``grant`` / ``publication`` are
    populated only on ``jga-study`` hits, and ``datasetType`` only on
    ``jga-dataset`` hits.
    """

    type: Literal[
        "jga-study",
        "jga-dataset",
        "jga-dac",
        "jga-policy",
    ] = Field(description="Hit type discriminator (4 JGA entity types).")
    organization: list[OrganizationOut] | None = None
    publication: list[PublicationOut] | None = None
    grant: list[GrantOut] | None = None
    external_link: list[ExternalLinkOut] | None = Field(default=None, alias="externalLink")
    study_type: list[str] | None = Field(default=None, alias="studyType")
    dataset_type: list[str] | None = Field(default=None, alias="datasetType")
    vendor: list[str] | None = None


class DbPortalHitGea(DbPortalHitBase):
    """GEA hit."""

    type: Literal["gea"] = Field(description="Hit type discriminator.")
    organization: list[OrganizationOut] | None = None
    publication: list[PublicationOut] | None = None
    experiment_type: list[str] | None = Field(default=None, alias="experimentType")


class DbPortalHitMetabobank(DbPortalHitBase):
    """MetaboBank hit."""

    type: Literal["metabobank"] = Field(description="Hit type discriminator.")
    organization: list[OrganizationOut] | None = None
    publication: list[PublicationOut] | None = None
    study_type: list[str] | None = Field(default=None, alias="studyType")
    experiment_type: list[str] | None = Field(default=None, alias="experimentType")
    submission_type: list[str] | None = Field(default=None, alias="submissionType")


class DbPortalHitTrad(DbPortalHitBase):
    """Trad (ARSA-backed) hit."""

    type: Literal["trad"] = Field(description="Hit type discriminator.")
    division: str | None = None
    molecular_type: str | None = Field(default=None, alias="molecularType")
    sequence_length: int | None = Field(default=None, alias="sequenceLength")


class DbPortalHitTaxonomy(DbPortalHitBase):
    """Taxonomy (TXSearch-backed) hit.

    ``japaneseName`` is exposed in the response shape but cannot be
    used as a search field.
    """

    type: Literal["taxonomy"] = Field(description="Hit type discriminator.")
    rank: str | None = None
    common_name: str | None = Field(default=None, alias="commonName")
    japanese_name: str | None = Field(default=None, alias="japaneseName")
    lineage: list[str] | str | None = None


DbPortalHit = Annotated[
    DbPortalHitBioProject
    | DbPortalHitBioSample
    | DbPortalHitSra
    | DbPortalHitJga
    | DbPortalHitGea
    | DbPortalHitMetabobank
    | DbPortalHitTrad
    | DbPortalHitTaxonomy,
    Field(discriminator="type"),
]

_DbPortalHitAdapter: TypeAdapter[Any] = TypeAdapter(DbPortalHit)


class DbPortalLightweightHit(BaseModel):
    """12-field hit envelope returned by ``/db-portal/cross-search``.

    Carries only the common fields shared across all 8 db-portal DBs.
    DB-specific extras present in ``DbPortalHit`` (used by
    ``/db-portal/search``) — ``projectType``, ``libraryStrategy``,
    ``division``, ``rank``, ``commonName`` etc. — are not part of this
    envelope.

    ``type`` covers all 16 possible hit values: the 8 db-portal DBs with
    sub-types where applicable (``sra-*``, ``jga-*``) plus the
    Solr-backed fixed literals ``trad`` and ``taxonomy``.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    identifier: str = Field(description="Entry identifier.")
    type: Literal[
        "bioproject",
        "biosample",
        "sra-submission",
        "sra-study",
        "sra-experiment",
        "sra-run",
        "sra-sample",
        "sra-analysis",
        "jga-study",
        "jga-dataset",
        "jga-dac",
        "jga-policy",
        "gea",
        "metabobank",
        "trad",
        "taxonomy",
    ] = Field(description="Entry type.  16 possible values.")
    title: str | None = Field(default=None, description="Entry title.")
    description: str | None = Field(default=None, description="Entry description.")
    organism: OrganismOut | None = Field(default=None, description="Organism information.")
    status: HitStatus | None = Field(default=None, description="INSDC status.")
    accessibility: HitAccessibility | None = Field(
        default=None,
        description="Accessibility level (public-access / controlled-access).",
    )
    date_created: str | None = Field(
        default=None,
        alias="dateCreated",
        description="Creation date (ISO 8601).",
    )
    date_modified: str | None = Field(
        default=None,
        alias="dateModified",
        description="Modification date (ISO 8601).",
    )
    date_published: str | None = Field(
        default=None,
        alias="datePublished",
        description="Publication date (ISO 8601).",
    )
    url: str | None = Field(default=None, description="Canonical URL.")
    is_part_of: str | None = Field(
        default=None,
        alias="isPartOf",
        description=(
            "Parent collection identifier.  ES-backed hits carry the "
            'index-level value (e.g. ``"bioproject"`` / ``"sra"``); '
            'Solr-backed hits use a fixed literal (``"trad"`` / '
            '``"taxonomy"``).'
        ),
    )


_DbPortalLightweightHitAdapter: TypeAdapter[Any] = TypeAdapter(DbPortalLightweightHit)

# DbPortalCount.hits forward-references DbPortalLightweightHit defined above;
# resolve now so subsequent imports get a fully-built model.
DbPortalCount.model_rebuild()


class DbPortalHitsResponse(BaseModel):
    """DB-specific search response (hits envelope + pagination)."""

    model_config = ConfigDict(populate_by_name=True)

    total: int = Field(description="Total matching hits (track_total_hits=true).")
    hits: list[DbPortalHit] = Field(description="Search hits (oneOf 8 DB variants).")
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


# === GET /db-portal/parse response schema ===
#
# SSOT: db-portal/docs/search-backends.md §スキーマ仕様 (L363-381).
# `op` discriminator は全 7 値 (AND/OR/NOT/eq/contains/wildcard/between) が
# 重複なしで単一 discriminator 成立。BoolOp.rules は再帰 union のため
# string forward ref + ``model_rebuild()`` で解決する。


class DbPortalParseLeafValue(BaseModel):
    """Leaf clause with scalar value (eq / contains / wildcard)."""

    model_config = ConfigDict(populate_by_name=True)

    field: str = Field(description="Allowlist field name (identifier, title, ...).")
    op: Literal["eq", "contains", "wildcard"] = Field(description="Operator derived from (field_type, value_kind).")
    value: str = Field(description="Operand value.")


class DbPortalParseLeafRange(BaseModel):
    """Leaf clause for a date range (op='between')."""

    model_config = ConfigDict(populate_by_name=True)

    field: str = Field(description="Date field (date_published, date_modified, date_created, or alias 'date').")
    op: Literal["between"] = Field(description="Always 'between' for range clauses.")
    from_: str = Field(alias="from", description="Range start (YYYY-MM-DD).")
    to: str = Field(description="Range end (YYYY-MM-DD).")


class DbPortalParseBoolOp(BaseModel):
    """Boolean node combining child clauses with AND / OR / NOT."""

    model_config = ConfigDict(populate_by_name=True)

    op: Literal["AND", "OR", "NOT"] = Field(description="Boolean operator.")
    # forward ref: ``DbPortalParseNode`` は本クラス定義後に evaluate されるので
    # ``from __future__ import annotations`` + ``model_rebuild()`` で解決する。
    rules: list[DbPortalParseNode] = Field(description="Child nodes (NOT has exactly one).")


DbPortalParseNode = Annotated[
    DbPortalParseBoolOp | DbPortalParseLeafValue | DbPortalParseLeafRange,
    Field(discriminator="op"),
]

DbPortalParseBoolOp.model_rebuild()


class DbPortalParseResponse(BaseModel):
    """Response envelope for GET /db-portal/parse."""

    model_config = ConfigDict(populate_by_name=True)

    ast: DbPortalParseNode = Field(
        description="Parsed AST as SSOT query-tree JSON (search-backends.md §L363-381).",
    )
