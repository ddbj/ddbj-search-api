"""Query parameter dependency classes for FastAPI endpoints.

Each class is used as a FastAPI ``Depends()`` dependency.  Mixin classes
(PaginationQuery, SearchFilterQuery, ResponseControlQuery) are composed
at the endpoint level via multiple ``Depends()`` parameters.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum

from fastapi import HTTPException, Query

from ddbj_search_api.es.query import VALID_FACET_FIELDS
from ddbj_search_api.schemas.common import DbType

# Patterns expressed in OpenAPI for client codegen / docs.  Server-side
# semantic validation is still performed below (e.g. unknown-type lookup
# in TypesFilterQuery) so that legacy detail messages are preserved.
_KEYWORD_FIELDS_PATTERN = r"^(identifier|title|name|description)(,(identifier|title|name|description))*$"
_DB_TYPES_PATTERN = (
    r"^(bioproject|biosample|sra-submission|sra-study|sra-experiment|sra-run|sra-sample|sra-analysis|"
    r"jga-study|jga-dataset|jga-dac|jga-policy|gea|metabobank)"
    r"(,(bioproject|biosample|sra-submission|sra-study|sra-experiment|sra-run|sra-sample|sra-analysis|"
    r"jga-study|jga-dataset|jga-dac|jga-policy|gea|metabobank))*$"
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

# Allowlist of facet fields accepted by the ``facets`` query parameter on
# ``/facets`` / ``/facets/{type}`` / ``/entries/*?includeFacets=true``.
# The set is sourced from :data:`ddbj_search_api.es.query.VALID_FACET_FIELDS`
# so the wire-level allowlist and the aggregation builder stay in sync
# without duplication.  Type-mismatch (a valid name on the wrong
# endpoint) is enforced by the router and returns 400; this set governs
# the typo-class 422 path.


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
            examples=["eyJwaXRfaWQiOiJhYmMxMjMifQ.def456"],
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
            examples=["cancer"],
            description="Search keywords (comma-separated for multiple).",
        ),
        keyword_fields: str | None = Query(
            default=None,
            alias="keywordFields",
            pattern=_KEYWORD_FIELDS_PATTERN,
            examples=["title,description"],
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
            examples=["9606"],
            description="NCBI Taxonomy ID, digits only (e.g. '9606').",
        ),
        organization: str | None = Query(
            default=None,
            examples=["DDBJ"],
            description=(
                "Nested filter on organization.name. "
                "Accepted on cross-type and all type-specific endpoints; "
                "types whose schema has no organization nested path yield "
                "no hits naturally on Elasticsearch side."
            ),
        ),
        publication: str | None = Query(
            default=None,
            examples=["Genomic variants"],
            description=("Nested filter on publication.title. Accepted on cross-type and all type-specific endpoints."),
        ),
        grant: str | None = Query(
            default=None,
            examples=["JST CREST"],
            description=("Nested filter on grant.title. Accepted on cross-type and all type-specific endpoints."),
        ),
        date_published_from: str | None = Query(
            default=None,
            pattern=r"^\d{4}-\d{2}-\d{2}$",
            alias="datePublishedFrom",
            examples=["2020-01-01"],
            description="Publication date range start (YYYY-MM-DD).",
        ),
        date_published_to: str | None = Query(
            default=None,
            pattern=r"^\d{4}-\d{2}-\d{2}$",
            alias="datePublishedTo",
            examples=["2024-12-31"],
            description="Publication date range end (YYYY-MM-DD).",
        ),
        date_modified_from: str | None = Query(
            default=None,
            pattern=r"^\d{4}-\d{2}-\d{2}$",
            alias="dateModifiedFrom",
            examples=["2024-01-01"],
            description="Modification date range start (YYYY-MM-DD).",
        ),
        date_modified_to: str | None = Query(
            default=None,
            pattern=r"^\d{4}-\d{2}-\d{2}$",
            alias="dateModifiedTo",
            examples=["2024-12-31"],
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
        self.organization = organization
        self.publication = publication
        self.grant = grant
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
            examples=["datePublished:desc"],
            description=(
                "Sort order as '{field}:{direction}'. "
                "Fields: datePublished, dateModified. "
                "Direction: asc, desc. "
                "Default: relevance (search score)."
            ),
        ),
        fields: str | None = Query(
            default=None,
            examples=["identifier,organism,datePublished"],
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
            examples=["bioproject,sra-study"],
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


@dataclass(frozen=True)
class TypeSpecificFilters:
    """Filter values consumed by :func:`build_search_query` and the
    facets/entries router pipelines.

    Routers convert their endpoint-scoped ``*ExtraQuery`` and
    :class:`TypesFilterQuery` dependencies into this dataclass and pass
    a single ``filters`` argument downstream. ``None`` indicates no
    filter on that field. Router factories fill only the fields
    applicable to the endpoint's type group; non-applicable fields
    stay ``None`` and become inert in :func:`build_search_query`.

    The field set mirrors the kwargs that ``build_search_query`` /
    ``_build_filter_clauses`` accept; extending one without the other
    will surface as a missing-attribute or unexpected-kwarg error.
    """

    types: str | None = None
    object_types: str | None = None
    external_link_label: str | None = None
    derived_from_id: str | None = None
    project_type: str | None = None
    host: str | None = None
    strain: str | None = None
    isolate: str | None = None
    geo_loc_name: str | None = None
    collection_date: str | None = None
    library_strategy: str | None = None
    library_source: str | None = None
    library_selection: str | None = None
    platform: str | None = None
    instrument_model: str | None = None
    library_layout: str | None = None
    analysis_type: str | None = None
    library_name: str | None = None
    library_construction_protocol: str | None = None
    study_type: str | None = None
    dataset_type: str | None = None
    experiment_type: str | None = None
    submission_type: str | None = None
    vendor: str | None = None


class BioProjectExtraQuery:
    """BioProject-specific filter parameters.

    Used by: GET /entries/bioproject/, GET /facets/bioproject.
    """

    def __init__(
        self,
        object_types: str | None = Query(
            default=None,
            alias="objectTypes",
            pattern=_OBJECT_TYPES_PATTERN,
            examples=["BioProject,UmbrellaBioProject"],
            description=(
                "Filter by BioProject objectType (comma-separated). "
                "Allowed: BioProject, UmbrellaBioProject. "
                "Specifying both is equivalent to omitting the filter."
            ),
        ),
        external_link_label: str | None = Query(
            default=None,
            alias="externalLinkLabel",
            examples=["GEO"],
            description=("Nested filter on externalLink.label (text match within nested objects)."),
        ),
        project_type: str | None = Query(
            default=None,
            alias="projectType",
            examples=["metagenome"],
            description=("Text match on projectType (auto-phrase enabled; comma-separated values are OR'd)."),
        ),
    ):
        self.object_types = object_types
        self.external_link_label = external_link_label
        self.project_type = project_type


class BioSampleExtraQuery:
    """BioSample-specific filter parameters.

    Used by: GET /entries/biosample/, GET /facets/biosample.
    """

    def __init__(
        self,
        derived_from_id: str | None = Query(
            default=None,
            alias="derivedFromId",
            examples=["SAMD00012345"],
            description=("Nested filter on derivedFrom.identifier (match within nested objects)."),
        ),
        host: str | None = Query(
            default=None,
            examples=["Homo sapiens"],
            description=("Text match on host (auto-phrase enabled; comma-separated values are OR'd)."),
        ),
        strain: str | None = Query(
            default=None,
            examples=["K12"],
            description=("Text match on strain (auto-phrase enabled; comma-separated values are OR'd)."),
        ),
        isolate: str | None = Query(
            default=None,
            examples=["patient-1"],
            description=("Text match on isolate (auto-phrase enabled; comma-separated values are OR'd)."),
        ),
        geo_loc_name: str | None = Query(
            default=None,
            alias="geoLocName",
            examples=["Japan"],
            description=("Text match on geoLocName (auto-phrase enabled; comma-separated values are OR'd)."),
        ),
        collection_date: str | None = Query(
            default=None,
            alias="collectionDate",
            examples=["2020-05-01"],
            description=("Text match on collectionDate (auto-phrase enabled; comma-separated values are OR'd)."),
        ),
    ):
        self.derived_from_id = derived_from_id
        self.host = host
        self.strain = strain
        self.isolate = isolate
        self.geo_loc_name = geo_loc_name
        self.collection_date = collection_date


class SraExtraQuery:
    """SRA-specific filter parameters shared by all sra-* endpoints.

    Used by: GET /entries/sra-{submission|study|experiment|run|sample|analysis}/
    and the corresponding /facets endpoints. The same parameter set is
    accepted on every sra-* endpoint; parameters not relevant to the
    selected type yield no hits naturally on the Elasticsearch side.
    """

    def __init__(
        self,
        library_strategy: str | None = Query(
            default=None,
            alias="libraryStrategy",
            examples=["WGS"],
            description=("Term filter on libraryStrategy.keyword (comma-separated values are OR'd)."),
        ),
        library_source: str | None = Query(
            default=None,
            alias="librarySource",
            examples=["GENOMIC"],
            description=("Term filter on librarySource.keyword (comma-separated values are OR'd)."),
        ),
        library_selection: str | None = Query(
            default=None,
            alias="librarySelection",
            examples=["RANDOM"],
            description=("Term filter on librarySelection.keyword (comma-separated values are OR'd)."),
        ),
        platform: str | None = Query(
            default=None,
            examples=["ILLUMINA"],
            description=("Term filter on platform.keyword (comma-separated values are OR'd)."),
        ),
        instrument_model: str | None = Query(
            default=None,
            alias="instrumentModel",
            examples=["HiSeq X Ten"],
            description=("Term filter on instrumentModel.keyword (comma-separated values are OR'd)."),
        ),
        library_layout: str | None = Query(
            default=None,
            alias="libraryLayout",
            examples=["PAIRED"],
            description=("Term filter on libraryLayout.keyword (comma-separated values are OR'd)."),
        ),
        analysis_type: str | None = Query(
            default=None,
            alias="analysisType",
            examples=["ALIGNMENT"],
            description=("Term filter on analysisType.keyword (comma-separated values are OR'd)."),
        ),
        derived_from_id: str | None = Query(
            default=None,
            alias="derivedFromId",
            examples=["SAMD00012345"],
            description=("Nested filter on derivedFrom.identifier (match within nested objects)."),
        ),
        library_name: str | None = Query(
            default=None,
            alias="libraryName",
            examples=["my_lib"],
            description=("Text match on libraryName (auto-phrase enabled; comma-separated values are OR'd)."),
        ),
        library_construction_protocol: str | None = Query(
            default=None,
            alias="libraryConstructionProtocol",
            examples=["PCR-free"],
            description=(
                "Text match on libraryConstructionProtocol (auto-phrase enabled; comma-separated values are OR'd)."
            ),
        ),
        geo_loc_name: str | None = Query(
            default=None,
            alias="geoLocName",
            examples=["Japan"],
            description=("Text match on geoLocName (auto-phrase enabled; comma-separated values are OR'd)."),
        ),
        collection_date: str | None = Query(
            default=None,
            alias="collectionDate",
            examples=["2020-05-01"],
            description=("Text match on collectionDate (auto-phrase enabled; comma-separated values are OR'd)."),
        ),
    ):
        self.library_strategy = library_strategy
        self.library_source = library_source
        self.library_selection = library_selection
        self.platform = platform
        self.instrument_model = instrument_model
        self.library_layout = library_layout
        self.analysis_type = analysis_type
        self.derived_from_id = derived_from_id
        self.library_name = library_name
        self.library_construction_protocol = library_construction_protocol
        self.geo_loc_name = geo_loc_name
        self.collection_date = collection_date


class JgaExtraQuery:
    """JGA-specific filter parameters shared by all jga-* endpoints.

    Used by: GET /entries/jga-{study|dataset|policy|dac}/ and the
    corresponding /facets endpoints.
    """

    def __init__(
        self,
        study_type: str | None = Query(
            default=None,
            alias="studyType",
            examples=["GWAS"],
            description=("Term filter on studyType.keyword (comma-separated values are OR'd)."),
        ),
        dataset_type: str | None = Query(
            default=None,
            alias="datasetType",
            examples=["Whole-genome sequencing"],
            description=("Term filter on datasetType.keyword (comma-separated values are OR'd)."),
        ),
        external_link_label: str | None = Query(
            default=None,
            alias="externalLinkLabel",
            examples=["dbGaP"],
            description=("Nested filter on externalLink.label (text match within nested objects)."),
        ),
        vendor: str | None = Query(
            default=None,
            examples=["Illumina"],
            description=("Text match on vendor (auto-phrase enabled; comma-separated values are OR'd)."),
        ),
    ):
        self.study_type = study_type
        self.dataset_type = dataset_type
        self.external_link_label = external_link_label
        self.vendor = vendor


class GeaExtraQuery:
    """GEA-specific filter parameters.

    Used by: GET /entries/gea/, GET /facets/gea.
    """

    def __init__(
        self,
        experiment_type: str | None = Query(
            default=None,
            alias="experimentType",
            examples=["RNA-Seq of coding RNA"],
            description=("Term filter on experimentType.keyword (comma-separated values are OR'd)."),
        ),
    ):
        self.experiment_type = experiment_type


class MetaboBankExtraQuery:
    """MetaboBank-specific filter parameters.

    Used by: GET /entries/metabobank/, GET /facets/metabobank.
    """

    def __init__(
        self,
        study_type: str | None = Query(
            default=None,
            alias="studyType",
            examples=["metabolomic"],
            description=("Term filter on studyType.keyword (comma-separated values are OR'd)."),
        ),
        experiment_type: str | None = Query(
            default=None,
            alias="experimentType",
            examples=["LC-MS"],
            description=("Term filter on experimentType.keyword (comma-separated values are OR'd)."),
        ),
        submission_type: str | None = Query(
            default=None,
            alias="submissionType",
            examples=["open"],
            description=("Term filter on submissionType.keyword (comma-separated values are OR'd)."),
        ),
    ):
        self.study_type = study_type
        self.experiment_type = experiment_type
        self.submission_type = submission_type


class FacetsParamQuery:
    """Optional facet pick parameter.

    Used by: GET /entries/, GET /entries/{type}/, GET /facets, GET /facets/{type}.

    ``None`` (default) yields common facets only (organism, accessibility,
    plus type for cross-type endpoints). An empty string suppresses
    aggregation entirely. A comma-separated list opts in to specific
    facet aggregations (allowlist enforced here; type-mismatch is checked
    in the router and returns 400).
    """

    def __init__(
        self,
        facets: str | None = Query(
            default=None,
            examples=["organism,accessibility"],
            description=(
                "Comma-separated facet fields to aggregate. "
                "Omitting the parameter returns common facets only "
                "(organism, accessibility, and type for cross-type endpoints). "
                "An empty string returns no facets. "
                "Explicit values fully replace the default selection (no auto-merge with common facets); "
                "to keep common facets, list them explicitly (e.g. 'organism,accessibility,objectType'). "
                "Allowed: organism, accessibility, type (cross-type only), "
                "objectType (bioproject only), libraryStrategy, librarySource, "
                "librarySelection, platform, instrumentModel "
                "(sra-experiment-only buckets), experimentType "
                "(gea / metabobank), studyType (jga-study / metabobank), "
                "submissionType (metabobank)."
            ),
        ),
    ):
        if facets is None or facets == "":
            self.facets = facets
            return
        requested = [f.strip() for f in facets.split(",")]
        requested = [f for f in requested if f]
        if not requested:
            raise HTTPException(
                status_code=422,
                detail="Invalid facets: empty value after splitting commas.",
            )
        invalid = [f for f in requested if f not in VALID_FACET_FIELDS]
        if invalid:
            raise HTTPException(
                status_code=422,
                detail=(f"Invalid facets: {', '.join(invalid)}. Allowed: {', '.join(sorted(VALID_FACET_FIELDS))}."),
            )
        # Store the normalized form so downstream parsing does not need
        # to re-strip; this also gives ``request.query_params`` callers
        # a canonical value to log.
        self.facets = ",".join(requested)


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
