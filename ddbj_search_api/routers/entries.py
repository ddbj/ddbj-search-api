from collections.abc import Callable, Coroutine
from typing import Any, Optional

from fastapi import APIRouter, Query

from ddbj_search_api.schemas import (DB_TYPE_TO_ENTRY_MODEL, ConverterEntry,
                                     DbType, EntryListResponse,
                                     KeywordsOperator, Pagination,
                                     ProblemDetails, UmbrellaFilter)

router = APIRouter()

TYPE_DESCRIPTIONS: dict[str, str] = {
    "bioproject": "Search BioProject entries. BioProject provides an organizational framework for project-level information. Supports additional filters: organization, publication, grant, umbrella.",
    "biosample": "Search BioSample entries. BioSample stores sample metadata describing biological source materials.",
    "sra-submission": "Search SRA Submission entries. Submissions represent a collection of metadata and data objects submitted to SRA.",
    "sra-study": "Search SRA Study entries. Studies describe the overall goals and design of sequencing experiments.",
    "sra-experiment": "Search SRA Experiment entries. Experiments describe library construction and sequencing methods.",
    "sra-run": "Search SRA Run entries. Runs contain actual sequencing data produced by an experiment.",
    "sra-sample": "Search SRA Sample entries. Samples describe the biological specimens used in experiments.",
    "sra-analysis": "Search SRA Analysis entries. Analyses describe processed data derived from sequencing reads.",
    "jga-study": "Search JGA Study entries. JGA Studies describe controlled-access research projects.",
    "jga-dataset": "Search JGA Dataset entries. JGA Datasets represent collections of controlled-access data files.",
    "jga-dac": "Search JGA DAC (Data Access Committee) entries. DACs manage access permissions for controlled-access data.",
    "jga-policy": "Search JGA Policy entries. Policies describe the data access conditions set by the DAC.",
}


@router.get(
    "/entries/",
    response_model=EntryListResponse[ConverterEntry],
    summary="All types search",
    description="Search entries across all database types. Supports keyword search, filtering by type/organism/date, sorting, and pagination.",
    responses={
        400: {"model": ProblemDetails},
        500: {"model": ProblemDetails},
    },
    tags=["entries"],
)
async def list_all_entries(
    keywords: Optional[str] = Query(None, description="Free-text search query. Supports comma-separated multiple values (e.g., 'cancer,genome')."),
    keywords_fields: Optional[str] = Query(None, alias="keywords.fields", description="Restrict keyword search to specific fields (comma-separated, e.g., 'title,description')."),
    keywords_operator: Optional[KeywordsOperator] = Query(None, alias="keywords.operator", description="Logical operator for combining multiple keywords. 'AND' requires all keywords to match, 'OR' requires any keyword to match."),
    types: Optional[str] = Query(None, description="Filter by database types (comma-separated, e.g., 'bioproject,biosample'). See DbType enum for valid values."),
    organism: Optional[str] = Query(None, description="Filter by organism using NCBI Taxonomy ID (e.g., '9606' for Homo sapiens)."),
    date_published: Optional[str] = Query(None, alias="datePublished", description="Filter by publication date range in 'start,end' format (e.g., '2020-01-01,2024-12-31'). Use ISO 8601 dates."),
    date_updated: Optional[str] = Query(None, alias="dateUpdated", description="Filter by last update date range in 'start,end' format (e.g., '2020-01-01,2024-12-31'). Use ISO 8601 dates."),
    sort: Optional[str] = Query(None, description="Sort order in 'field:direction' format (e.g., 'datePublished:desc'). Single-field sorting only."),
    page: int = Query(1, ge=1, description="Page number (1-indexed)."),
    per_page: int = Query(10, ge=1, le=100, alias="perPage", description="Number of items per page (1-100, default: 10)."),
    fields: Optional[str] = Query(None, description="Select specific fields to include in the response (comma-separated, e.g., 'identifier,title,organism'). If omitted, all fields are returned."),
    trim_properties: bool = Query(
        False,
        alias="trimProperties",
        description="If true, exclude the 'properties' field from each entry in the response.",
    ),
) -> EntryListResponse[ConverterEntry]:
    # TODO: Phase 2 - Implement ES search
    return EntryListResponse(
        pagination=Pagination(page=page, perPage=per_page, total=0),
        items=[],
    )


def _make_list_entries_handler(
    db_type: DbType, include_bioproject_params: bool
) -> Callable[..., Coroutine[Any, Any, EntryListResponse[Any]]]:
    if include_bioproject_params:

        async def list_entries_bioproject(
            keywords: Optional[str] = Query(None, description="Free-text search query. Supports comma-separated multiple values (e.g., 'cancer,genome')."),
            keywords_fields: Optional[str] = Query(None, alias="keywords.fields", description="Restrict keyword search to specific fields (comma-separated, e.g., 'title,description')."),
            keywords_operator: Optional[KeywordsOperator] = Query(None, alias="keywords.operator", description="Logical operator for combining multiple keywords. 'AND' requires all keywords to match, 'OR' requires any keyword to match."),
            organism: Optional[str] = Query(None, description="Filter by organism using NCBI Taxonomy ID (e.g., '9606' for Homo sapiens)."),
            date_published: Optional[str] = Query(None, alias="datePublished", description="Filter by publication date range in 'start,end' format (e.g., '2020-01-01,2024-12-31'). Use ISO 8601 dates."),
            date_updated: Optional[str] = Query(None, alias="dateUpdated", description="Filter by last update date range in 'start,end' format (e.g., '2020-01-01,2024-12-31'). Use ISO 8601 dates."),
            sort: Optional[str] = Query(None, description="Sort order in 'field:direction' format (e.g., 'datePublished:desc'). Single-field sorting only."),
            page: int = Query(1, ge=1, description="Page number (1-indexed)."),
            per_page: int = Query(10, ge=1, le=100, alias="perPage", description="Number of items per page (1-100, default: 10)."),
            fields: Optional[str] = Query(None, description="Select specific fields to include in the response (comma-separated, e.g., 'identifier,title,organism'). If omitted, all fields are returned."),
            organization: Optional[str] = Query(None, description="Filter by organization name (BioProject only)."),
            publication: Optional[str] = Query(None, description="Filter by publication (BioProject only)."),
            grant: Optional[str] = Query(None, description="Filter by grant (BioProject only)."),
            umbrella: Optional[UmbrellaFilter] = Query(None, description="Filter by umbrella BioProject status (BioProject only)."),
            trim_properties: bool = Query(
                False,
                alias="trimProperties",
                description="If true, exclude the 'properties' field from each entry in the response.",
            ),
        ) -> EntryListResponse[Any]:
            # TODO: Phase 2 - Implement ES search
            return EntryListResponse(
                pagination=Pagination(page=page, perPage=per_page, total=0),
                items=[],
            )

        list_entries_bioproject.__name__ = f"list_entries_{db_type.name}"
        return list_entries_bioproject

    else:

        async def list_entries(
            keywords: Optional[str] = Query(None, description="Free-text search query. Supports comma-separated multiple values (e.g., 'cancer,genome')."),
            keywords_fields: Optional[str] = Query(None, alias="keywords.fields", description="Restrict keyword search to specific fields (comma-separated, e.g., 'title,description')."),
            keywords_operator: Optional[KeywordsOperator] = Query(None, alias="keywords.operator", description="Logical operator for combining multiple keywords. 'AND' requires all keywords to match, 'OR' requires any keyword to match."),
            organism: Optional[str] = Query(None, description="Filter by organism using NCBI Taxonomy ID (e.g., '9606' for Homo sapiens)."),
            date_published: Optional[str] = Query(None, alias="datePublished", description="Filter by publication date range in 'start,end' format (e.g., '2020-01-01,2024-12-31'). Use ISO 8601 dates."),
            date_updated: Optional[str] = Query(None, alias="dateUpdated", description="Filter by last update date range in 'start,end' format (e.g., '2020-01-01,2024-12-31'). Use ISO 8601 dates."),
            sort: Optional[str] = Query(None, description="Sort order in 'field:direction' format (e.g., 'datePublished:desc'). Single-field sorting only."),
            page: int = Query(1, ge=1, description="Page number (1-indexed)."),
            per_page: int = Query(10, ge=1, le=100, alias="perPage", description="Number of items per page (1-100, default: 10)."),
            fields: Optional[str] = Query(None, description="Select specific fields to include in the response (comma-separated, e.g., 'identifier,title,organism'). If omitted, all fields are returned."),
            trim_properties: bool = Query(
                False,
                alias="trimProperties",
                description="If true, exclude the 'properties' field from each entry in the response.",
            ),
        ) -> EntryListResponse[Any]:
            # TODO: Phase 2 - Implement ES search
            return EntryListResponse(
                pagination=Pagination(page=page, perPage=per_page, total=0),
                items=[],
            )

        list_entries.__name__ = f"list_entries_{db_type.name}"
        return list_entries


for _db_type in DbType:
    _is_bioproject = _db_type == DbType.bioproject
    _handler = _make_list_entries_handler(_db_type, include_bioproject_params=_is_bioproject)
    _entry_model = DB_TYPE_TO_ENTRY_MODEL[_db_type]
    router.add_api_route(
        f"/entries/{_db_type.value}/",
        _handler,
        methods=["GET"],
        response_model=EntryListResponse[_entry_model],  # type: ignore[valid-type]
        summary=f"{_db_type.value} search",
        description=TYPE_DESCRIPTIONS[_db_type.value],
        responses={
            400: {"model": ProblemDetails},
            500: {"model": ProblemDetails},
        },
        tags=["entries"],
    )
