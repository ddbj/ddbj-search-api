"""Elasticsearch query builder.

Pure functions that convert API parameters to Elasticsearch query DSL.
"""

from __future__ import annotations

from typing import Any

# API field name → ES field name mapping
_SORT_FIELD_MAP: dict[str, str] = {
    "datePublished": "datePublished",
    "dateModified": "dateModified",
}

_VALID_SORT_DIRECTIONS = {"asc", "desc"}

_DEFAULT_KEYWORD_FIELDS = ["identifier", "title", "name", "description"]

_VALID_KEYWORD_FIELDS = set(_DEFAULT_KEYWORD_FIELDS)

_UMBRELLA_MAP: dict[str, str] = {
    "TRUE": "UmbrellaBioProject",
    "FALSE": "BioProject",
}


def pagination_to_from_size(
    page: int,
    per_page: int,
) -> tuple[int, int]:
    """Convert page/perPage to ES from/size."""
    from_ = (page - 1) * per_page
    return (from_, per_page)


def build_sort(
    sort_param: str | None,
) -> list[dict[str, Any]] | None:
    """Convert sort string to ES sort list.

    Returns None for relevance scoring (default).
    Raises ValueError for invalid sort strings.
    """
    if sort_param is None:
        return None

    parts = sort_param.split(":")
    if len(parts) != 2:
        raise ValueError(
            f"Invalid sort format: '{sort_param}'. Expected '{{field}}:{{direction}}'.",
        )

    field, direction = parts
    if not field or field not in _SORT_FIELD_MAP:
        raise ValueError(
            f"Invalid sort field: '{field}'. Allowed: {', '.join(sorted(_SORT_FIELD_MAP))}.",
        )
    if not direction or direction not in _VALID_SORT_DIRECTIONS:
        raise ValueError(
            f"Invalid sort direction: '{direction}'. Allowed: {', '.join(sorted(_VALID_SORT_DIRECTIONS))}.",
        )

    es_field = _SORT_FIELD_MAP[field]
    return [{es_field: {"order": direction}}]


def validate_keyword_fields(
    keyword_fields: str | None,
) -> list[str]:
    """Validate and parse keywordFields parameter.

    Returns the list of valid field names to search.
    If None, returns all default fields.
    Raises ValueError for invalid field names.
    """
    if keyword_fields is None:
        return list(_DEFAULT_KEYWORD_FIELDS)

    fields = [f.strip() for f in keyword_fields.split(",")]
    fields = [f for f in fields if f]

    if not fields:
        raise ValueError(
            f"Invalid keywordFields: empty value. Allowed: {', '.join(sorted(_VALID_KEYWORD_FIELDS))}.",
        )

    invalid = [f for f in fields if f not in _VALID_KEYWORD_FIELDS]
    if invalid:
        raise ValueError(
            f"Invalid keywordFields: {', '.join(invalid)}. Allowed: {', '.join(sorted(_VALID_KEYWORD_FIELDS))}.",
        )

    return fields


def build_source_filter(
    fields: str | None,
    include_properties: bool,
) -> list[str] | dict[str, Any] | None:
    """Build ES _source parameter from fields/includeProperties."""
    if fields is not None:
        parsed = [f.strip() for f in fields.split(",")]
        return [f for f in parsed if f]

    if not include_properties:
        return {"excludes": ["properties"]}

    return None


def _parse_keywords(keywords: str | None) -> list[str]:
    """Split comma-separated keywords, stripping whitespace."""
    if not keywords:
        return []
    parts = [k.strip() for k in keywords.split(",")]
    return [k for k in parts if k]


def build_search_query(
    keywords: str | None = None,
    keyword_fields: str | list[str] | None = None,
    keyword_operator: str = "AND",
    organism: str | None = None,
    date_published_from: str | None = None,
    date_published_to: str | None = None,
    date_modified_from: str | None = None,
    date_modified_to: str | None = None,
    types: str | None = None,
    organization: str | None = None,
    publication: str | None = None,
    grant: str | None = None,
    umbrella: str | None = None,
) -> dict[str, Any]:
    """Build ES query dict from search parameters.

    ``keyword_fields`` accepts either a pre-validated ``list[str]`` or a
    raw comma-separated string (which will be validated here).
    """
    keyword_list = _parse_keywords(keywords)
    filters = _build_filter_clauses(
        organism=organism,
        date_published_from=date_published_from,
        date_published_to=date_published_to,
        date_modified_from=date_modified_from,
        date_modified_to=date_modified_to,
        types=types,
        organization=organization,
        publication=publication,
        grant=grant,
        umbrella=umbrella,
    )

    if not keyword_list and not filters:
        return {"match_all": {}}

    if isinstance(keyword_fields, list):
        fields = keyword_fields
    else:
        fields = validate_keyword_fields(keyword_fields)
    bool_query: dict[str, Any] = {}

    if keyword_list:
        multi_matches = [{"multi_match": {"query": kw, "fields": fields}} for kw in keyword_list]
        if keyword_operator == "OR":
            bool_query["should"] = multi_matches
            bool_query["minimum_should_match"] = 1
        else:
            bool_query["must"] = multi_matches

    if filters:
        bool_query["filter"] = filters

    return {"bool": bool_query}


def _build_filter_clauses(
    organism: str | None = None,
    date_published_from: str | None = None,
    date_published_to: str | None = None,
    date_modified_from: str | None = None,
    date_modified_to: str | None = None,
    types: str | None = None,
    organization: str | None = None,
    publication: str | None = None,
    grant: str | None = None,
    umbrella: str | None = None,
) -> list[dict[str, Any]]:
    """Build list of ES filter clauses."""
    clauses: list[dict[str, Any]] = []

    if organism:
        clauses.append({"term": {"organism.identifier": organism}})

    # datePublished range
    date_pub_range: dict[str, str] = {}
    if date_published_from:
        date_pub_range["gte"] = date_published_from
    if date_published_to:
        date_pub_range["lte"] = date_published_to
    if date_pub_range:
        clauses.append({"range": {"datePublished": date_pub_range}})

    # dateModified range
    date_mod_range: dict[str, str] = {}
    if date_modified_from:
        date_mod_range["gte"] = date_modified_from
    if date_modified_to:
        date_mod_range["lte"] = date_modified_to
    if date_mod_range:
        clauses.append({"range": {"dateModified": date_mod_range}})

    # types filter
    if types:
        type_list = [t.strip() for t in types.split(",")]
        type_list = [t for t in type_list if t]
        if type_list:
            clauses.append({"terms": {"type": type_list}})

    # BioProject-specific filters
    if umbrella and umbrella in _UMBRELLA_MAP:
        clauses.append(
            {"term": {"objectType": _UMBRELLA_MAP[umbrella]}},
        )

    if organization:
        clauses.append(
            {
                "nested": {
                    "path": "organization",
                    "query": {"match": {"organization.name": organization}},
                },
            }
        )

    if publication:
        clauses.append(
            {
                "nested": {
                    "path": "publication",
                    "query": {"match": {"publication.title": publication}},
                },
            }
        )

    if grant:
        clauses.append(
            {
                "nested": {
                    "path": "grant",
                    "query": {"match": {"grant.title": grant}},
                },
            }
        )

    return clauses


def build_facet_aggs(
    is_cross_type: bool = False,
    db_type: str | None = None,
) -> dict[str, Any]:
    """Build ES aggregation queries for facets."""
    aggs: dict[str, Any] = {
        "organism": {"terms": {"field": "organism.name", "size": 50}},
        "status": {"terms": {"field": "status", "size": 50}},
        "accessibility": {"terms": {"field": "accessibility", "size": 50}},
    }

    if is_cross_type:
        aggs["type"] = {"terms": {"field": "type", "size": 50}}

    if db_type == "bioproject":
        aggs["objectType"] = {"terms": {"field": "objectType", "size": 50}}

    return aggs
