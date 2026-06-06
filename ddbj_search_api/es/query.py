"""Elasticsearch query builder.

Pure functions that convert API parameters to Elasticsearch query DSL.
"""

from __future__ import annotations

import copy
from typing import Any, Literal

from ddbj_search_api.search.dsl.ast import Node
from ddbj_search_api.search.dsl.compiler_es import compile_free_text, compile_to_es
from ddbj_search_api.search.dsl.transform import exclude_field_from_ast
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

_DEFAULT_KEYWORD_FIELDS = ["identifier", "title", "name", "description", "organism.name"]

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

    Used by ``/db-portal/*`` routers to apply the same status filter as
    :func:`build_search_query` to ``compile_to_es`` output.
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
    # 内部 default は AND のまま (テストフィクスチャ互換用).
    # wire-level の default は schemas.queries.KeywordOperator / schemas.db_portal で
    # OR に切り替え済みで、production caller (routers/entries.py, routers/facets.py,
    # routers/db_portal.py) はいずれも search_filter.keyword_operator.value を
    # 明示渡しするため、本関数の引数 default 値は production 動作に影響しない.
    keyword_operator: str = "AND",
    organism: str | None = None,
    accessibility: str | None = None,
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
    relevance: str | None = None,
    package: str | None = None,
    model: str | None = None,
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
    # text match / nested 4 text param の値内空白は **常に AND 固定** (api-spec.md
    # § 検索 query parameter のセマンティクス共通ルール). ``keyword_operator``
    # は keywords (multi_match) のカンマ区切り token 間 (AND/OR) にのみ影響する
    # ように分離する.
    filters: list[dict[str, Any]] = []
    if status_mode is not None:
        filters.append(build_status_filter(status_mode))
    filters.extend(
        _build_filter_clauses(
            organism=organism,
            accessibility=accessibility,
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
            relevance=relevance,
            package=package,
            model=model,
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
        # Keyword 構築を DSL の compile_free_text に委譲して AST 経路と単一実装に揃える
        # (db-portal も entries / facets も同じ multi_match dict を出す)。
        op_upper: Literal["AND", "OR"] = "OR" if keyword_operator == "OR" else "AND"
        # keyword_list が non-empty な時点で keywords は non-None.
        assert keywords is not None
        # accession 完全一致で suppressed を解禁したクエリ (status_mode=include_suppressed)
        # では free-text の前方一致を抑止する。解禁した accession の prefix で別 accession の
        # suppressed を漏らさないため (docs/api-spec.md § データ可視性)。
        enable_prefix = status_mode != "include_suppressed"
        free_text_dict = compile_free_text(
            keywords,
            operator=op_upper,
            fields=fields,
            enable_prefix=enable_prefix,
        )
        bool_query.update(free_text_dict["bool"])

    if filters:
        bool_query["filter"] = filters

    return {"bool": bool_query}


# 前方一致を相乗りさせる最小 literal 長 (compiler_es._MIN_PREFIX_LITERAL_LEN と同基準)。
# 1 文字 prefix は ES の max_expansions で頭打ちになり Solr では全 term スキャンを誘発するため
# 最小 2 文字を要求する (wildcard ガード validator._MIN_WILDCARD_LITERAL_LEN = 2 と同基準)。
_MIN_PREFIX_LITERAL_LEN: int = 2

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
    ("relevance", "relevance"),
    ("package", "package.name"),
    ("model", "model"),
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


def _build_text_match_clause(
    field: str,
    value: str | None,
) -> dict[str, Any] | None:
    """Build a match_phrase / (match_phrase + match_phrase_prefix) clause with auto-phrase semantics.

    DSL 経路 (_basic_leaf / contains) と同じ前方一致ルールを適用する
    (docs/api-spec.md § 前方一致):
    - quoted / 記号含み (auto-phrase) トークン → ``match_phrase`` 単独 (厳密一致)。
    - bare word 1 文字 → ``match_phrase`` 単独 (全 term スキャン回避)。
    - bare word 2 文字以上 → ``bool.should[match_phrase, match_phrase_prefix]`` (前方一致付き)。

    カンマ区切り入力値は複数の per-value 句に分割され ``bool.should`` で OR 結合される。
    """
    parsed = parse_keywords_with_autophrase(value, ES_AUTO_PHRASE_CHARS)
    if not parsed:
        return None
    per_value_clauses: list[dict[str, Any]] = []
    for token, is_phrase in parsed:
        if is_phrase or len(token) < _MIN_PREFIX_LITERAL_LEN:
            per_value_clauses.append({"match_phrase": {field: token}})
        else:
            per_value_clauses.append(
                {
                    "bool": {
                        "should": [
                            {"match_phrase": {field: token}},
                            {"match_phrase_prefix": {field: token}},
                        ],
                        "minimum_should_match": 1,
                    },
                }
            )
    if len(per_value_clauses) == 1:
        return per_value_clauses[0]
    return {"bool": {"should": per_value_clauses, "minimum_should_match": 1}}


def _build_filter_clauses(
    organism: str | None = None,
    accessibility: str | None = None,
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
    relevance: str | None = None,
    package: str | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """Build list of ES filter clauses.

    text match / nested 4 text param の値内空白 operator は常に AND 固定で
    ``_build_text_match_clause`` の default に任せる
    (``docs/api-spec.md § セマンティクス共通ルール``).  ``keyword_operator``
    は keywords (multi_match) のカンマ区切り token 間 (AND/OR) にのみ影響し,
    本関数の clause 群には派生させない.
    """
    clauses: list[dict[str, Any]] = []

    if organism:
        clauses.append({"term": {"organism.identifier": organism}})

    if accessibility:
        clauses.append({"term": {"accessibility": accessibility}})

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

    # nested の text sub-field (organization/publication/grant/externalLinkLabel)
    # は _build_text_match_clause を nested wrapper で包む. これで text match
    # 9 param と同じ semantics (値内 AND / カンマ OR / クオート phrase /
    # auto-phrase) に揃う.
    nested_text_specs: list[tuple[str, str, str | None]] = [
        ("organization", "organization.name", organization),
        ("publication", "publication.title", publication),
        ("grant", "grant.title", grant),
        ("externalLink", "externalLink.label", external_link_label),
    ]
    # ``ignore_unmapped`` で、対応 nested path を持たない index (cross-type の
    # entries alias、型グループ内の非実在 subtype) では shard exception を出さず
    # 0 件化する (docs/api-spec.md § nested フィールド検索).
    for path, sub_field, value in nested_text_specs:
        inner = _build_text_match_clause(sub_field, value)
        if inner is not None:
            clauses.append({"nested": {"path": path, "query": inner, "ignore_unmapped": True}})

    # derivedFromId は accession ID の完全一致用 (derivedFrom.identifier は
    # keyword field). カンマ区切りで複数 accession の OR (terms) を表現する.
    derived_inner = _build_term_clause("derivedFrom.identifier", derived_from_id)
    if derived_inner is not None:
        clauses.append({"nested": {"path": "derivedFrom", "query": derived_inner, "ignore_unmapped": True}})

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
        "relevance": relevance,
        "package": package,
        "model": model,
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
        clause = _build_text_match_clause(es_field, text_values[kwarg_name])
        if clause is not None:
            clauses.append(clause)

    return clauses


# Facet aggregation specifications.  ``terms.size`` is injected per-call
# by :func:`build_facet_aggs` from the ``facetsSize`` query parameter
# (default 100), so the templates here only carry the bucket key field
# and any sub-aggregations.  Per-facet ``shard_size`` tuning can be
# revisited if cardinality growth on the SRA ``instrumentModel`` /
# ``libraryStrategy`` fields starts leaving meaningful counts in
# ``sum_other_doc_count`` (see docs § ファセット).
DEFAULT_FACET_SIZE: int = 100

_FACET_AGG_SPECS: dict[str, dict[str, Any]] = {
    # ``organism`` buckets on ``organism.identifier`` (TaxID) so the bucket
    # value can be re-injected into ``?organism=`` (which only accepts
    # ``^\d+$``).  A sub-aggregation pulls the doc_count-most-frequent
    # ``organism.name.keyword`` value as the display ``label`` (see
    # :func:`ddbj_search_api.utils._optional_organism`); its ``size`` is
    # intentionally pinned at 1 and is *not* affected by ``facetsSize``.
    "organism": {
        "terms": {"field": "organism.identifier"},
        "aggs": {
            "name": {"terms": {"field": "organism.name.keyword", "size": 1}},
        },
    },
    "accessibility": {"terms": {"field": "accessibility"}},
    "type": {"terms": {"field": "type"}},
    "objectType": {"terms": {"field": "objectType"}},
    "libraryStrategy": {"terms": {"field": "libraryStrategy.keyword"}},
    "librarySource": {"terms": {"field": "librarySource.keyword"}},
    "librarySelection": {"terms": {"field": "librarySelection.keyword"}},
    "platform": {"terms": {"field": "platform.keyword"}},
    "instrumentModel": {"terms": {"field": "instrumentModel.keyword"}},
    "experimentType": {"terms": {"field": "experimentType.keyword"}},
    "studyType": {"terms": {"field": "studyType.keyword"}},
    "submissionType": {"terms": {"field": "submissionType.keyword"}},
    # === BioProject ===
    "relevance": {"terms": {"field": "relevance"}},
    # text + .keyword の text match param とペア。bucket value は .keyword 値で
    # 集計するが、再注入先の `?projectType=` は analyzed match なので部分一致が
    # 紛れ込む可能性あり (docs/api-spec.md § ファセット — bucket 再注入)。
    "projectType": {"terms": {"field": "projectType.keyword"}},
    # === BioSample (package は object{name:keyword,displayName:keyword} の name サブフィールド) ===
    "package": {"terms": {"field": "package.name"}},
    "model": {"terms": {"field": "model"}},
    # host は text + .keyword (cardinality 134K)、text match `?host=` とペア。
    "host": {"terms": {"field": "host.keyword"}},
    # === SRA ===
    "libraryLayout": {"terms": {"field": "libraryLayout.keyword"}},
    "analysisType": {"terms": {"field": "analysisType.keyword"}},
    # === JGA ===
    "datasetType": {"terms": {"field": "datasetType.keyword"}},
    # vendor は text + .keyword (jga-study)、text match `?vendor=` とペア。
    "vendor": {"terms": {"field": "vendor.keyword"}},
}

# Facet 名 → bucket 値を再注入する DSL allowlist field (docs/db-portal-api-spec.md
# § facet 値の DSL 再注入)。self-exclusion (§ 集計母集団と self-exclusion) で、
# facet F の母集団から外すべき ``q`` フィルタの DSL field を引くのに使う。キーは
# ``_FACET_AGG_SPECS`` と 1:1 対応 (organism は organism_id、accessibility / type
# は同名)。値が DSL allowlist (``FIELD_TYPES``) に無いとコンパイルできないため、両者
# の整合は unit test で担保する。
_FACET_TO_DSL_FIELD: dict[str, str] = {
    "organism": "organism_id",
    "accessibility": "accessibility",
    "type": "type",
    "objectType": "object_type",
    "libraryStrategy": "library_strategy",
    "librarySource": "library_source",
    "librarySelection": "library_selection",
    "platform": "platform",
    "instrumentModel": "instrument_model",
    "experimentType": "experiment_type",
    "studyType": "study_type",
    "submissionType": "submission_type",
    "relevance": "relevance",
    "projectType": "project_type",
    "package": "package",
    "model": "model",
    "host": "host",
    "libraryLayout": "library_layout",
    "analysisType": "analysis_type",
    "datasetType": "dataset_type",
    "vendor": "vendor",
}


def facet_to_dsl_field(facet_name: str) -> str:
    """ES facet 名を、bucket 値を再注入する DSL allowlist field 名に変換する。

    self-exclusion で facet 自身の ``q`` フィルタを母集団から外す際、どの DSL field の
    clause を除外するかを引く (docs/db-portal-api-spec.md § 集計母集団と self-exclusion)。
    facet 名は ``resolve_requested_facets`` で allowlist 済みのものが渡る前提。
    """
    return _FACET_TO_DSL_FIELD[facet_name]


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

# db-portal exposes 6 ES-backed databases whose ``db`` value spans one or
# more ``type`` subtypes (e.g. ``db=sra`` covers the 6 ``sra-*`` subtypes).
# A type-specific facet is allowed for a db-portal scope when its
# ``_TYPE_SPECIFIC_FACET_SCOPE`` intersects the scope's subtypes, so the
# db-portal allowlist stays derived from the ES SSOT (``_TYPE_SPECIFIC_FACET_SCOPE``)
# rather than hardcoded (docs/db-portal-api-spec.md § facet 集計).
_DB_PORTAL_ES_SUBTYPES: dict[str, frozenset[str]] = {
    "bioproject": frozenset({"bioproject"}),
    "biosample": frozenset({"biosample"}),
    "sra": frozenset(
        {"sra-submission", "sra-study", "sra-experiment", "sra-run", "sra-sample", "sra-analysis"},
    ),
    "jga": frozenset({"jga-study", "jga-dataset", "jga-dac", "jga-policy"}),
    "gea": frozenset({"gea"}),
    "metabobank": frozenset({"metabobank"}),
}

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
    "relevance": frozenset({"bioproject"}),
    "package": frozenset({"biosample"}),
    "model": frozenset({"biosample"}),
    "libraryLayout": frozenset({"sra-experiment"}),
    "analysisType": frozenset({"sra-analysis"}),
    "datasetType": frozenset({"jga-dataset"}),
    "projectType": frozenset({"bioproject"}),
    "host": frozenset({"biosample"}),
    "vendor": frozenset({"jga-study"}),
    # ``type`` (the subtype identifier) is opened to the db-portal per-db sra /
    # jga scopes so a single ``db`` that spans multiple subtypes can be broken
    # down into sra-* / jga-* buckets. Its scope is deliberately limited to the
    # sra + jga subtypes: those two dbs' subtypes intersect it (→ allowed),
    # while the single-subtype dbs (bioproject / biosample / gea / metabobank)
    # do not, so they keep returning 400 for ``facets=type`` (a single-value
    # bucket carries no information). ``type`` also stays in
    # ``_CROSS_TYPE_ONLY_FACET_NAMES`` so the cross scope (db=None) keeps
    # aggregating it over the ES 6-DB union (resolve_requested_facets short-
    # circuits on the cross-only set, so REST /facets stays cross-only).
    "type": _DB_PORTAL_ES_SUBTYPES["sra"] | _DB_PORTAL_ES_SUBTYPES["jga"],
}


def db_portal_es_facet_allowlist(db: str | None) -> frozenset[str]:
    """Facet names accepted for a db-portal ES scope.

    ``db=None`` is the cross-search scope: common facets (organism,
    accessibility) plus ``type``.  A single ES-backed ``db`` value
    (``bioproject`` / ``biosample`` / ``sra`` / ``jga`` / ``gea`` /
    ``metabobank``) allows the common facets plus every type-specific
    facet whose :data:`_TYPE_SPECIFIC_FACET_SCOPE` overlaps the db's
    subtypes.  ``type`` is scoped to the sra + jga subtypes, so it
    additionally appears in the ``sra`` / ``jga`` single-DB allowlists
    (their subtypes intersect that scope) but in no other single-DB
    allowlist.

    Raises:
        KeyError: when ``db`` is not a db-portal ES database name (Solr
        DBs ``ddbj`` / ``taxonomy`` have their own allowlist and must not
        reach this function).
    """
    if db is None:
        return _COMMON_FACET_NAMES | _CROSS_TYPE_ONLY_FACET_NAMES
    subtypes = _DB_PORTAL_ES_SUBTYPES[db]
    allowed = set(_COMMON_FACET_NAMES)
    for name, scope in _TYPE_SPECIFIC_FACET_SCOPE.items():
        if scope & subtypes:
            allowed.add(name)
    return frozenset(allowed)


def resolve_facets_size(facets_size: int | None) -> int:
    """Map the ``facetsSize`` query value to an effective bucket size.

    ``None`` (parameter omitted) falls back to :data:`DEFAULT_FACET_SIZE`
    so the OpenAPI surface keeps ``facetsSize`` optional while the
    aggregation builder always receives an integer.  Range validation
    (1-1000) is enforced by :class:`FacetsParamQuery` upstream.
    """
    if facets_size is None:
        return DEFAULT_FACET_SIZE
    return facets_size


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
    size: int = DEFAULT_FACET_SIZE,
) -> dict[str, Any]:
    """Build ES aggregation queries for facets.

    ``requested_facets`` controls which buckets to compute:
    - ``None`` (default): common facets only (organism, accessibility);
      cross-type endpoints additionally include ``type``.
    - ``[]``: no aggregations (caller asked for an empty facet set).
    - explicit list: just those facet names. Unknown names are silently
      ignored — the router validates the allowlist via
      :func:`resolve_requested_facets` before reaching here.

    ``size`` applies to every facet's ``terms.size`` uniformly
    (per-facet override is not exposed). The ``organism`` facet keeps
    its sub-aggregation ``size: 1`` for the label lookup — that bucket
    is unaffected. ``docs/api-spec.md`` § ファセット bucket 数の指定
    documents the ``facetsSize`` query parameter that feeds this.

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
        agg = copy.deepcopy(spec)
        agg["terms"]["size"] = size
        aggs[name] = agg
    return aggs


def es_query_from_ast(
    ast: Node | None,
    status_mode: StatusMode,
    *,
    free_text_operator: Literal["AND", "OR"] = "AND",
) -> dict[str, Any]:
    """AST から ES query body を生成し status filter を注入する。

    ``ast=None`` (``q`` 未指定) は keyword 無し + status filter のみの bool query
    (:func:`build_search_query` の ``keywords=None`` と同形)。それ以外は
    :func:`compile_to_es` 出力に :func:`inject_status_filter` を被せる。``/db-portal/*``
    の hits 検索と facet 集計 (self-exclusion を含む) で同一の母集団 query を組むため
    共有する。
    """
    if ast is None:
        return build_search_query(keywords=None, keyword_operator="AND", status_mode=status_mode)

    # suppressed 解禁時 (accession 完全一致) は FreeText の前方一致を抑止する
    # (docs/api-spec.md § データ可視性。build_search_query と同じ規則)。
    return inject_status_filter(
        compile_to_es(
            ast,
            free_text_operator=free_text_operator,
            enable_prefix=status_mode != "include_suppressed",
        ),
        status_mode,
    )


def build_facet_base_query(
    ast: Node | None,
    status_mode: StatusMode,
    *,
    requested_facets: list[str] | None = None,
    free_text_operator: Literal["AND", "OR"] = "AND",
) -> dict[str, Any]:
    """Top-level ``query`` for a self-excluding facet request.

    ES ``filter`` aggregations can only *narrow* the documents already selected
    by the top-level ``query`` — they cannot widen the population back out. So
    for self-exclusion the top-level query must drop **every** requested facet's
    own clause; each facet's ``filter`` aggregation then re-adds the *other*
    facets' clauses (:func:`build_self_excluding_facet_aggs`), making the
    population for facet ``F`` exactly "``q`` minus ``F``"
    (docs/db-portal-api-spec.md § 集計母集団と self-exclusion).

    The hit population (``q`` の全フィルタ) is restored separately — by
    ``post_filter`` on a hits-bearing request, or it simply does not apply on the
    cross-search size=0 aggregation request.
    """
    base = ast
    for name in requested_facets or []:
        dsl_field = _FACET_TO_DSL_FIELD.get(name)
        if dsl_field is not None:
            base = exclude_field_from_ast(base, dsl_field)

    return es_query_from_ast(base, status_mode, free_text_operator=free_text_operator)


def build_self_excluding_facet_aggs(
    *,
    ast: Node | None,
    status_mode: StatusMode,
    is_cross_type: bool = False,
    requested_facets: list[str] | None = None,
    size: int = DEFAULT_FACET_SIZE,
    free_text_operator: Literal["AND", "OR"] = "AND",
) -> dict[str, Any]:
    """各 facet を ``filter`` aggregation で包み self-exclusion を適用した aggs を返す。

    facet ``F`` ごとに、``F`` に対応する DSL field (:func:`facet_to_dsl_field`) の clause
    を ``ast`` から除外した ES query を ``filter`` に被せ、その下に ``F`` の terms 集計
    (:func:`build_facet_aggs` と同一 spec) を置く。これは **top-level ``query`` が
    :func:`build_facet_base_query` の出力 (全 requested facet を除外した base)** であることを
    前提にする: ES の filter aggregation は top-level query を超えて母集団を広げられないため、
    base を top-level に置き、各 filter agg で「``F`` 以外の facet 句」を足し戻すことで
    母集団 = ``q`` から ``F`` の句だけを外した集合になる (docs/db-portal-api-spec.md
    § 集計母集団と self-exclusion)。``ast`` が空 (全 facet 除外で残らない) なら status の
    みの母集団になる。

    内側 terms 集計の名前を facet 名と同じにするため、レスポンスは
    ``aggregations[F][F]["buckets"]`` の 2 段構造になる。
    :func:`ddbj_search_api.utils._unwrap_terms_agg` が素 terms / filter-wrap の両構造を
    吸収するので ``parse_db_portal_es_facets`` のシグネチャは不変。
    """
    inner = build_facet_aggs(is_cross_type=is_cross_type, requested_facets=requested_facets, size=size)
    aggs: dict[str, Any] = {}
    for name, terms_agg in inner.items():
        excluded = exclude_field_from_ast(ast, facet_to_dsl_field(name))
        filter_query = es_query_from_ast(excluded, status_mode, free_text_operator=free_text_operator)
        aggs[name] = {"filter": filter_query, "aggs": {name: terms_agg}}

    return aggs
