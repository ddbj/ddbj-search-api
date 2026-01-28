from typing import Optional

from fastapi import APIRouter, Query

from ddbj_search_api.schemas import KeywordsOperator, ProblemDetails, TypeCounts

router = APIRouter()


@router.get(
    "/count/types/",
    response_model=TypeCounts,
    summary="Count by types",
    description="Count entries for each database type. Returns the number of entries per type, optionally filtered by keywords, organism, or date range.",
    responses={
        400: {"model": ProblemDetails},
        500: {"model": ProblemDetails},
    },
    tags=["count"],
)
async def count_by_types(
    keywords: Optional[str] = Query(None, description="Free-text search query. Supports comma-separated multiple values (e.g., 'cancer,genome')."),
    keywords_fields: Optional[str] = Query(None, alias="keywords.fields", description="Restrict keyword search to specific fields (comma-separated, e.g., 'title,description')."),
    keywords_operator: Optional[KeywordsOperator] = Query(None, alias="keywords.operator", description="Logical operator for combining multiple keywords. 'AND' requires all keywords to match, 'OR' requires any keyword to match."),
    organism: Optional[str] = Query(None, description="Filter by organism using NCBI Taxonomy ID (e.g., '9606' for Homo sapiens)."),
    date_published: Optional[str] = Query(None, alias="datePublished", description="Filter by publication date range in 'start,end' format (e.g., '2020-01-01,2024-12-31'). Use ISO 8601 dates."),
    date_updated: Optional[str] = Query(None, alias="dateUpdated", description="Filter by last update date range in 'start,end' format (e.g., '2020-01-01,2024-12-31'). Use ISO 8601 dates."),
) -> TypeCounts:
    # TODO: Phase 2 - Implement ES count
    return TypeCounts(
        bioproject=0,
        biosample=0,
        **{
            "sra-submission": 0,
            "sra-study": 0,
            "sra-experiment": 0,
            "sra-run": 0,
            "sra-sample": 0,
            "sra-analysis": 0,
            "jga-study": 0,
            "jga-dataset": 0,
            "jga-dac": 0,
            "jga-policy": 0,
        },
    )
