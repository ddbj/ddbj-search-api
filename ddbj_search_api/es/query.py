"""Elasticsearch query builder.

Pure functions that convert API parameters to Elasticsearch query DSL.
"""

from __future__ import annotations

import copy
from typing import Any, Literal

from ddbj_search_api.search.phrase import (
    ES_AUTO_PHRASE_CHARS,
    parse_keywords_with_autophrase,
)

StatusMode = Literal["public_only", "include_suppressed"]

# API field name → ES field name mapping
_SORT_FIELD_MAP: dict[str, str] = {
    "datePublished": "datePublished",
    "dateModified": "dateModified",
}

_VALID_SORT_DIRECTIONS = {"asc", "desc"}

_DEFAULT_KEYWORD_FIELDS = ["identifier", "title", "name", "description"]

_VALID_KEYWORD_FIELDS = set(_DEFAULT_KEYWORD_FIELDS)


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


_TIEBREAKER: dict[str, Any] = {"identifier": {"order": "asc"}}


def build_sort_with_tiebreaker(
    sort_param: str | None,
) -> list[dict[str, Any]]:
    """Build ES sort list with identifier tiebreaker for search_after.

    Uses ``identifier`` instead of ``_id`` because ES 8.x disables
    fielddata on ``_id`` by default. ``identifier`` is present on all
    documents and effectively unique.

    Always returns a non-empty list. If no user sort is specified,
    uses relevance scoring with a tiebreaker.

    Raises ValueError for invalid sort strings (delegated to build_sort).
    """
    base = build_sort(sort_param)
    if base is None:
        return [{"_score": {"order": "desc"}}, _TIEBREAKER]

    return [*base, _TIEBREAKER]


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


def _parse_keywords(keywords: str | None) -> list[tuple[str, bool]]:
    return parse_keywords_with_autophrase(keywords, ES_AUTO_PHRASE_CHARS)


def build_status_filter(status_mode: StatusMode) -> dict[str, Any]:
    """Build an ES filter clause that limits results by ``status``.

    ``public_only`` keeps only ``public`` entries; ``include_suppressed``
    keeps ``public`` and ``suppressed`` (used when the query matches an
    accession exactly). ``withdrawn`` / ``private`` are always excluded.
    """
    if status_mode == "include_suppressed":
        return {"terms": {"status": ["public", "suppressed"]}}
    return {"term": {"status": "public"}}


def inject_status_filter(
    es_query: dict[str, Any],
    status_mode: StatusMode,
) -> dict[str, Any]:
    """Return a new ES query body with a status filter applied.

    Used by ``/db-portal/*`` adv-search routers to apply the same status
    filter as :func:`build_search_query` to ``compile_to_es`` output.
    The input is either a leaf clause (``term``, ``match_phrase``,
    ``wildcard``, ``range``, single ``nested``) or a ``bool`` wrapper.
    Leaf clauses are wrapped into ``{"bool": {"must": [original],
    "filter": [status]}}``. ``bool`` wrappers receive the status filter
    prepended to ``bool.filter`` (created if absent).

    The input dict is not mutated — the returned dict is a fresh
    structure built via :func:`copy.deepcopy` so the same body can be
    reused across 6 ES DBs in cross-search fan-out without
    cross-contamination.
    """
    status_filter = build_status_filter(status_mode)
    if "bool" in es_query and isinstance(es_query["bool"], dict):
        new_query = copy.deepcopy(es_query)
        bool_body = new_query["bool"]
        existing_filter = bool_body.get("filter")
        if existing_filter is None:
            bool_body["filter"] = [status_filter]
        elif isinstance(existing_filter, list):
            bool_body["filter"] = [status_filter, *existing_filter]
        else:
            bool_body["filter"] = [status_filter, existing_filter]
        return new_query
    return {
        "bool": {
            "must": [copy.deepcopy(es_query)],
            "filter": [status_filter],
        },
    }


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
    object_types: str | None = None,
    external_link_label: str | None = None,
    derived_from_id: str | None = None,
    library_strategy: str | None = None,
    library_source: str | None = None,
    library_selection: str | None = None,
    platform: str | None = None,
    instrument_model: str | None = None,
    library_layout: str | None = None,
    analysis_type: str | None = None,
    experiment_type: str | None = None,
    study_type: str | None = None,
    submission_type: str | None = None,
    dataset_type: str | None = None,
    project_type: str | None = None,
    host: str | None = None,
    strain: str | None = None,
    isolate: str | None = None,
    geo_loc_name: str | None = None,
    collection_date: str | None = None,
    library_name: str | None = None,
    library_construction_protocol: str | None = None,
    vendor: str | None = None,
    status_mode: StatusMode | None = "public_only",
) -> dict[str, Any]:
    """Build ES query dict from search parameters.

    ``keyword_fields`` accepts either a pre-validated ``list[str]`` or a
    raw comma-separated string (which will be validated here).

    A status filter (derived from ``status_mode``) is prepended to
    ``bool.filter`` by default so that ``withdrawn`` / ``private``
    entries never leak into search results. Pass ``status_mode=None``
    to opt out of the filter entirely.

    Type-specific term / nested / text filters are accepted for any
    request; values that do not exist on the targeted index simply
    produce no hits on the Elasticsearch side. Routers reject
    type-specific parameters that should not reach a given endpoint
    before this function is called.
    """
    keyword_list = _parse_keywords(keywords)
    operator = keyword_operator.lower() if keyword_operator else "and"
    filters: list[dict[str, Any]] = []
    if status_mode is not None:
        filters.append(build_status_filter(status_mode))
    filters.extend(
        _build_filter_clauses(
            organism=organism,
            date_published_from=date_published_from,
            date_published_to=date_published_to,
            date_modified_from=date_modified_from,
            date_modified_to=date_modified_to,
            types=types,
            organization=organization,
            publication=publication,
            grant=grant,
            object_types=object_types,
            external_link_label=external_link_label,
            derived_from_id=derived_from_id,
            library_strategy=library_strategy,
            library_source=library_source,
            library_selection=library_selection,
            platform=platform,
            instrument_model=instrument_model,
            library_layout=library_layout,
            analysis_type=analysis_type,
            experiment_type=experiment_type,
            study_type=study_type,
            submission_type=submission_type,
            dataset_type=dataset_type,
            project_type=project_type,
            host=host,
            strain=strain,
            isolate=isolate,
            geo_loc_name=geo_loc_name,
            collection_date=collection_date,
            library_name=library_name,
            library_construction_protocol=library_construction_protocol,
            vendor=vendor,
            text_match_operator=operator,
        )
    )

    if not keyword_list and not filters:
        return {"match_all": {}}

    if isinstance(keyword_fields, list):
        fields = keyword_fields
    else:
        fields = validate_keyword_fields(keyword_fields)
    bool_query: dict[str, Any] = {}

    if keyword_list:
        multi_matches = []
        for text, is_phrase in keyword_list:
            mm: dict[str, Any] = {"query": text, "fields": fields}
            if is_phrase:
                mm["type"] = "phrase"
            multi_matches.append({"multi_match": mm})
        if keyword_operator == "OR":
            bool_query["should"] = multi_matches
            bool_query["minimum_should_match"] = 1
        else:
            bool_query["must"] = multi_matches

    if filters:
        bool_query["filter"] = filters

    return {"bool": bool_query}


# Mapping from API parameter name to the ES ``*.keyword`` field used as a
# term filter. Keys are kwarg-style identifiers that match the
# ``_build_filter_clauses`` signature.
_TERM_FILTER_FIELDS: list[tuple[str, str]] = [
    ("library_strategy", "libraryStrategy.keyword"),
    ("library_source", "librarySource.keyword"),
    ("library_selection", "librarySelection.keyword"),
    ("platform", "platform.keyword"),
    ("instrument_model", "instrumentModel.keyword"),
    ("library_layout", "libraryLayout.keyword"),
    ("analysis_type", "analysisType.keyword"),
    ("experiment_type", "experimentType.keyword"),
    ("study_type", "studyType.keyword"),
    ("submission_type", "submissionType.keyword"),
    ("dataset_type", "datasetType.keyword"),
]

# Mapping from API parameter name to the ES top-level text field used by
# the auto-phrase text matcher.
_TEXT_MATCH_FIELDS: list[tuple[str, str]] = [
    ("project_type", "projectType"),
    ("host", "host"),
    ("strain", "strain"),
    ("isolate", "isolate"),
    ("geo_loc_name", "geoLocName"),
    ("collection_date", "collectionDate"),
    ("library_name", "libraryName"),
    ("library_construction_protocol", "libraryConstructionProtocol"),
    ("vendor", "vendor"),
]


def _build_term_clause(field: str, value: str | None) -> dict[str, Any] | None:
    """Build a single term/terms clause for comma-separated values."""
    if not value:
        return None
    values = [v.strip() for v in value.split(",")]
    values = [v for v in values if v]
    if not values:
        return None
    if len(values) == 1:
        return {"term": {field: values[0]}}
    return {"terms": {field: values}}


def _build_nested_match_clause(
    path: str,
    sub_field: str,
    value: str | None,
) -> dict[str, Any] | None:
    """Build a nested match clause for a single nested-path/value pair."""
    if not value:
        return None
    return {
        "nested": {
            "path": path,
            "query": {"match": {sub_field: value}},
        },
    }


def _build_text_match_clause(
    field: str,
    value: str | None,
    operator: str,
) -> dict[str, Any] | None:
    """Build a match / match_phrase clause with auto-phrase semantics.

    ``operator`` is the in-token operator (``and`` / ``or``); comma-
    separated input values are split into multiple per-value clauses
    OR'd together via ``bool.should`` with ``minimum_should_match=1``.
    """
    parsed = parse_keywords_with_autophrase(value, ES_AUTO_PHRASE_CHARS)
    if not parsed:
        return None
    per_value_clauses: list[dict[str, Any]] = []
    for token, is_phrase in parsed:
        if is_phrase:
            per_value_clauses.append({"match_phrase": {field: token}})
        else:
            per_value_clauses.append({"match": {field: {"query": token, "operator": operator}}})
    if len(per_value_clauses) == 1:
        return per_value_clauses[0]
    return {"bool": {"should": per_value_clauses, "minimum_should_match": 1}}


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
    object_types: str | None = None,
    external_link_label: str | None = None,
    derived_from_id: str | None = None,
    library_strategy: str | None = None,
    library_source: str | None = None,
    library_selection: str | None = None,
    platform: str | None = None,
    instrument_model: str | None = None,
    library_layout: str | None = None,
    analysis_type: str | None = None,
    experiment_type: str | None = None,
    study_type: str | None = None,
    submission_type: str | None = None,
    dataset_type: str | None = None,
    project_type: str | None = None,
    host: str | None = None,
    strain: str | None = None,
    isolate: str | None = None,
    geo_loc_name: str | None = None,
    collection_date: str | None = None,
    library_name: str | None = None,
    library_construction_protocol: str | None = None,
    vendor: str | None = None,
    text_match_operator: str = "and",
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

    # BioProject-specific filter (kept as-is for the BioProject/UmbrellaBioProject enum).
    if object_types:
        values = sorted({v.strip() for v in object_types.split(",") if v.strip()})
        if len(values) == 1:
            clauses.append({"term": {"objectType": values[0]}})
        elif len(values) >= 2:
            clauses.append({"terms": {"objectType": values}})

    nested_specs: list[tuple[str, str, str | None]] = [
        ("organization", "organization.name", organization),
        ("publication", "publication.title", publication),
        ("grant", "grant.title", grant),
        ("externalLink", "externalLink.label", external_link_label),
        ("derivedFrom", "derivedFrom.identifier", derived_from_id),
    ]
    for path, sub_field, value in nested_specs:
        clause = _build_nested_match_clause(path, sub_field, value)
        if clause is not None:
            clauses.append(clause)

    term_values: dict[str, str | None] = {
        "library_strategy": library_strategy,
        "library_source": library_source,
        "library_selection": library_selection,
        "platform": platform,
        "instrument_model": instrument_model,
        "library_layout": library_layout,
        "analysis_type": analysis_type,
        "experiment_type": experiment_type,
        "study_type": study_type,
        "submission_type": submission_type,
        "dataset_type": dataset_type,
    }
    for kwarg_name, es_field in _TERM_FILTER_FIELDS:
        clause = _build_term_clause(es_field, term_values[kwarg_name])
        if clause is not None:
            clauses.append(clause)

    text_values: dict[str, str | None] = {
        "project_type": project_type,
        "host": host,
        "strain": strain,
        "isolate": isolate,
        "geo_loc_name": geo_loc_name,
        "collection_date": collection_date,
        "library_name": library_name,
        "library_construction_protocol": library_construction_protocol,
        "vendor": vendor,
    }
    for kwarg_name, es_field in _TEXT_MATCH_FIELDS:
        clause = _build_text_match_clause(es_field, text_values[kwarg_name], text_match_operator)
        if clause is not None:
            clauses.append(clause)

    return clauses


# Facet aggregation specifications. ``size`` is intentionally fixed at 50
# for now; per-facet ``shard_size`` tuning can be revisited if cardinality
# growth on the SRA ``instrumentModel`` / ``libraryStrategy`` fields starts
# leaving meaningful counts in ``sum_other_doc_count`` (see docs § ファセット).
_FACET_AGG_SPECS: dict[str, dict[str, Any]] = {
    "organism": {"terms": {"field": "organism.name", "size": 50}},
    "accessibility": {"terms": {"field": "accessibility", "size": 50}},
    "type": {"terms": {"field": "type", "size": 50}},
    "objectType": {"terms": {"field": "objectType", "size": 50}},
    "libraryStrategy": {"terms": {"field": "libraryStrategy.keyword", "size": 50}},
    "librarySource": {"terms": {"field": "librarySource.keyword", "size": 50}},
    "librarySelection": {"terms": {"field": "librarySelection.keyword", "size": 50}},
    "platform": {"terms": {"field": "platform.keyword", "size": 50}},
    "instrumentModel": {"terms": {"field": "instrumentModel.keyword", "size": 50}},
    "experimentType": {"terms": {"field": "experimentType.keyword", "size": 50}},
    "studyType": {"terms": {"field": "studyType.keyword", "size": 50}},
    "submissionType": {"terms": {"field": "submissionType.keyword", "size": 50}},
}

# Default common facets when ``requested_facets`` is omitted. ``type`` is
# appended on cross-type endpoints inside :func:`build_facet_aggs`.
_DEFAULT_COMMON_FACETS: tuple[str, ...] = ("organism", "accessibility")

# Facets always available, regardless of endpoint scope (derived from the
# default tuple to keep the two views in sync).
_COMMON_FACET_NAMES: frozenset[str] = frozenset(_DEFAULT_COMMON_FACETS)

# Facets accepted only on cross-type endpoints.
_CROSS_TYPE_ONLY_FACET_NAMES: frozenset[str] = frozenset({"type"})

# Public allowlist for the ``facets`` query parameter.  Sourced from the
# agg-spec keys so the wire-level allowlist (in
# :mod:`ddbj_search_api.schemas.queries`) and the aggregation builder
# stay in sync without manual duplication.
VALID_FACET_FIELDS: frozenset[str] = frozenset(_FACET_AGG_SPECS)

# Mapping from a type-specific facet to the DbType values that own it on
# their indices. cross-type endpoints accept the union of these (the
# router treats them as loosely scoped — empty buckets fall out
# naturally on indices that lack the field).
_TYPE_SPECIFIC_FACET_SCOPE: dict[str, frozenset[str]] = {
    "objectType": frozenset({"bioproject"}),
    "libraryStrategy": frozenset({"sra-experiment"}),
    "librarySource": frozenset({"sra-experiment"}),
    "librarySelection": frozenset({"sra-experiment"}),
    "platform": frozenset({"sra-experiment"}),
    "instrumentModel": frozenset({"sra-experiment"}),
    "experimentType": frozenset({"gea", "metabobank"}),
    "studyType": frozenset({"jga-study", "metabobank"}),
    "submissionType": frozenset({"metabobank"}),
}


def resolve_requested_facets(
    facets_param: str | None,
    *,
    is_cross_type: bool,
    db_type: str | None = None,
) -> list[str] | None:
    """Resolve the raw ``facets`` query value into an explicit selection.

    Returns:
        ``None`` when ``facets_param`` is ``None`` (caller falls back to
        :func:`build_facet_aggs`'s default common-facet behaviour);
        ``[]`` when ``facets_param`` is the empty string (no
        aggregation); otherwise the parsed list of facet names.

    Raises:
        ValueError: when a requested facet name is valid in the global
        allowlist but not applicable to the endpoint (caller maps to
        HTTP 400). Allowlist typos are caught upstream in
        :class:`FacetsParamQuery` and produce HTTP 422.
    """
    if facets_param is None:
        return None
    if facets_param == "":
        return []
    requested = [f.strip() for f in facets_param.split(",")]
    requested = [f for f in requested if f]

    invalid: list[str] = []
    for name in requested:
        if name in _COMMON_FACET_NAMES:
            continue
        if name in _CROSS_TYPE_ONLY_FACET_NAMES:
            if not is_cross_type:
                invalid.append(name)
            continue
        scope = _TYPE_SPECIFIC_FACET_SCOPE.get(name)
        if scope is None:
            invalid.append(name)
            continue
        if is_cross_type:
            continue
        if db_type not in scope:
            invalid.append(name)
    if invalid:
        raise ValueError(
            "Facets not applicable to this endpoint: " + ", ".join(invalid) + ".",
        )
    return requested


def build_facet_aggs(
    is_cross_type: bool = False,
    requested_facets: list[str] | None = None,
) -> dict[str, Any]:
    """Build ES aggregation queries for facets.

    ``requested_facets`` controls which buckets to compute:
    - ``None`` (default): common facets only (organism, accessibility);
      cross-type endpoints additionally include ``type``.
    - ``[]``: no aggregations (caller asked for an empty facet set).
    - explicit list: just those facet names. Unknown names are silently
      ignored — the router validates the allowlist via
      :func:`resolve_requested_facets` before reaching here.

    ``status`` is intentionally not aggregated: the query that feeds the
    facet aggregations is always constrained to ``status:public``
    upstream, so a status bucket would always carry a single ``public``
    value and provides no information (see
    ``docs/api-spec.md`` § データ可視性).
    """
    if requested_facets is None:
        wanted: list[str] = list(_DEFAULT_COMMON_FACETS)
        if is_cross_type:
            wanted.append("type")
    else:
        wanted = list(requested_facets)

    aggs: dict[str, Any] = {}
    for name in wanted:
        spec = _FACET_AGG_SPECS.get(name)
        if spec is None:
            continue
        # deepcopy keeps the per-call agg dict independent from the
        # module-level template; callers can safely mutate the result
        # (e.g. inject ``shard_size``) without leaking changes.
        aggs[name] = copy.deepcopy(spec)
    return aggs
