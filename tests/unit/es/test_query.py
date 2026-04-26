"""Tests for ES query builder (ddbj_search_api.es.query).

Tests verify the conversion from API parameters to Elasticsearch query DSL.
All functions are pure (no I/O), so no mocks are needed.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ddbj_search_api.es.query import (
    _parse_keywords,
    build_facet_aggs,
    build_search_query,
    build_sort,
    build_sort_with_tiebreaker,
    build_source_filter,
    build_status_filter,
    pagination_to_from_size,
    resolve_requested_facets,
    validate_keyword_fields,
)
from tests.unit.strategies import (
    ES_AUTO_PHRASE_TRIGGERS,
    alphanumeric_no_trigger,
    text_with_trigger,
)

# ===================================================================
# pagination_to_from_size
# ===================================================================


class TestPaginationToFromSize:
    """pagination_to_from_size(page, per_page) -> (from_, size)."""

    def test_first_page(self) -> None:
        assert pagination_to_from_size(1, 10) == (0, 10)

    def test_second_page(self) -> None:
        assert pagination_to_from_size(2, 10) == (10, 10)

    def test_page_three_per_page_twenty(self) -> None:
        assert pagination_to_from_size(3, 20) == (40, 20)

    def test_single_item_per_page(self) -> None:
        assert pagination_to_from_size(1, 1) == (0, 1)

    def test_max_per_page(self) -> None:
        assert pagination_to_from_size(1, 100) == (0, 100)

    def test_deep_page_boundary(self) -> None:
        """page=100, perPage=100 → from=9900 (deep paging limit boundary)."""
        assert pagination_to_from_size(100, 100) == (9900, 100)


class TestPaginationToFromSizePBT:
    """Property-based tests for pagination_to_from_size."""

    @given(
        page=st.integers(min_value=1, max_value=10000),
        per_page=st.integers(min_value=1, max_value=100),
    )
    def test_from_equals_page_minus_one_times_per_page(
        self,
        page: int,
        per_page: int,
    ) -> None:
        from_, size = pagination_to_from_size(page, per_page)
        assert from_ == (page - 1) * per_page
        assert size == per_page

    @given(
        page=st.integers(min_value=1, max_value=10000),
        per_page=st.integers(min_value=1, max_value=100),
    )
    def test_from_is_non_negative(self, page: int, per_page: int) -> None:
        from_, _ = pagination_to_from_size(page, per_page)
        assert from_ >= 0


# ===================================================================
# build_sort
# ===================================================================


class TestBuildSort:
    """build_sort(sort_param) -> ES sort list or None."""

    def test_none_returns_none(self) -> None:
        """No sort param → None (ES defaults to relevance scoring)."""
        assert build_sort(None) is None

    def test_date_published_asc(self) -> None:
        result = build_sort("datePublished:asc")
        assert result == [{"datePublished": {"order": "asc"}}]

    def test_date_published_desc(self) -> None:
        result = build_sort("datePublished:desc")
        assert result == [{"datePublished": {"order": "desc"}}]

    def test_date_modified_asc(self) -> None:
        result = build_sort("dateModified:asc")
        assert result == [{"dateModified": {"order": "asc"}}]

    def test_date_modified_desc(self) -> None:
        result = build_sort("dateModified:desc")
        assert result == [{"dateModified": {"order": "desc"}}]


class TestBuildSortEdgeCases:
    """Invalid sort strings raise ValueError."""

    def test_invalid_field_raises(self) -> None:
        with pytest.raises(ValueError, match="sort field"):
            build_sort("invalidField:asc")

    def test_invalid_direction_raises(self) -> None:
        with pytest.raises(ValueError, match="sort direction"):
            build_sort("datePublished:invalid")

    def test_missing_direction_raises(self) -> None:
        with pytest.raises(ValueError):
            build_sort("datePublished")

    def test_too_many_parts_raises(self) -> None:
        with pytest.raises(ValueError):
            build_sort("datePublished:asc:extra")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError):
            build_sort("")

    def test_colon_only_raises(self) -> None:
        with pytest.raises(ValueError):
            build_sort(":")


# === build_sort_with_tiebreaker ===


class TestBuildSortWithTiebreaker:
    """build_sort_with_tiebreaker always returns a list with identifier tiebreaker."""

    def test_none_returns_score_and_id(self) -> None:
        result = build_sort_with_tiebreaker(None)
        assert result == [
            {"_score": {"order": "desc"}},
            {"identifier": {"order": "asc"}},
        ]

    def test_date_published_asc_appends_id(self) -> None:
        result = build_sort_with_tiebreaker("datePublished:asc")
        assert result == [
            {"datePublished": {"order": "asc"}},
            {"identifier": {"order": "asc"}},
        ]

    def test_date_modified_desc_appends_id(self) -> None:
        result = build_sort_with_tiebreaker("dateModified:desc")
        assert result == [
            {"dateModified": {"order": "desc"}},
            {"identifier": {"order": "asc"}},
        ]

    def test_always_returns_list(self) -> None:
        result = build_sort_with_tiebreaker(None)
        assert isinstance(result, list)
        assert len(result) >= 2

    def test_last_element_is_id_tiebreaker(self) -> None:
        for sort_param in [None, "datePublished:asc", "dateModified:desc"]:
            result = build_sort_with_tiebreaker(sort_param)
            assert result[-1] == {"identifier": {"order": "asc"}}

    def test_invalid_sort_raises(self) -> None:
        with pytest.raises(ValueError):
            build_sort_with_tiebreaker("invalidField:asc")


# ===================================================================
# validate_keyword_fields
# ===================================================================


class TestValidateKeywordFields:
    """validate_keyword_fields(keyword_fields) -> list[str] or raise."""

    def test_none_returns_default_fields(self) -> None:
        result = validate_keyword_fields(None)
        assert set(result) == {"identifier", "title", "name", "description"}

    def test_single_field(self) -> None:
        assert validate_keyword_fields("title") == ["title"]

    def test_multiple_fields(self) -> None:
        result = validate_keyword_fields("identifier,title")
        assert set(result) == {"identifier", "title"}

    def test_all_valid_fields(self) -> None:
        result = validate_keyword_fields(
            "identifier,title,name,description",
        )
        assert set(result) == {"identifier", "title", "name", "description"}

    def test_whitespace_trimmed(self) -> None:
        result = validate_keyword_fields(" title , name ")
        assert set(result) == {"title", "name"}


class TestValidateKeywordFieldsEdgeCases:
    """Invalid keyword fields raise ValueError."""

    def test_invalid_field_raises(self) -> None:
        with pytest.raises(ValueError, match="keywordFields"):
            validate_keyword_fields("invalid")

    def test_mixed_valid_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="keywordFields"):
            validate_keyword_fields("title,invalid")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="keywordFields"):
            validate_keyword_fields("")


# ===================================================================
# build_source_filter
# ===================================================================


class TestBuildSourceFilter:
    """build_source_filter(fields, include_properties) -> ES _source."""

    def test_default_returns_none(self) -> None:
        """No fields + includeProperties=True → None (all fields)."""
        assert build_source_filter(None, True) is None

    def test_exclude_properties(self) -> None:
        """includeProperties=False → exclude properties from _source."""
        result = build_source_filter(None, False)
        assert result == {"excludes": ["properties"]}

    def test_specific_fields(self) -> None:
        result = build_source_filter("identifier,title", True)
        assert isinstance(result, list)
        assert set(result) == {"identifier", "title"}

    def test_fields_override_include_properties_false(self) -> None:
        """Explicit fields list takes precedence over includeProperties."""
        result = build_source_filter("identifier,title", False)
        assert isinstance(result, list)
        assert set(result) == {"identifier", "title"}

    def test_fields_with_properties_included(self) -> None:
        """When 'properties' is in the fields list, include it."""
        result = build_source_filter("identifier,properties", True)
        assert isinstance(result, list)
        assert set(result) == {"identifier", "properties"}

    def test_whitespace_in_fields_trimmed(self) -> None:
        result = build_source_filter(" identifier , title ", True)
        assert isinstance(result, list)
        assert set(result) == {"identifier", "title"}


# ===================================================================
# build_search_query
# ===================================================================


class TestBuildSearchQueryNoParams:
    """build_search_query with no parameters returns a bool query
    containing only the default status filter (``status:public``).

    ``match_all`` is never returned because a status filter is always
    prepended to guarantee visibility control.
    """

    def test_no_params_returns_bool_with_status_filter(self) -> None:
        result = build_search_query()
        assert result == {
            "bool": {
                "filter": [{"term": {"status": "public"}}],
            },
        }

    def test_no_params_has_no_must_or_should(self) -> None:
        result = build_search_query()
        assert "must" not in result["bool"]
        assert "should" not in result["bool"]


class TestBuildSearchQueryKeywords:
    """Keyword search → multi_match queries."""

    def test_single_keyword_creates_multi_match(self) -> None:
        result = build_search_query(keywords="cancer")
        must = result["bool"]["must"]
        assert len(must) == 1
        assert must[0]["multi_match"]["query"] == "cancer"

    def test_single_keyword_searches_all_default_fields(self) -> None:
        result = build_search_query(keywords="cancer")
        fields = result["bool"]["must"][0]["multi_match"]["fields"]
        assert set(fields) == {"identifier", "title", "name", "description"}

    def test_multiple_keywords_and_operator(self) -> None:
        """AND: all keywords in bool.must (all must match)."""
        result = build_search_query(
            keywords="cancer,human",
            keyword_operator="AND",
        )
        must = result["bool"]["must"]
        assert len(must) == 2
        queries = {m["multi_match"]["query"] for m in must}
        assert queries == {"cancer", "human"}

    def test_multiple_keywords_or_operator(self) -> None:
        """OR: all keywords in bool.should with minimum_should_match=1."""
        result = build_search_query(
            keywords="cancer,human",
            keyword_operator="OR",
        )
        should = result["bool"]["should"]
        assert len(should) == 2
        assert result["bool"].get("minimum_should_match") == 1

    def test_keyword_fields_limits_search(self) -> None:
        """keywordFields restricts multi_match fields."""
        result = build_search_query(
            keywords="PRJDB1234",
            keyword_fields="identifier",
        )
        must = result["bool"]["must"]
        assert must[0]["multi_match"]["fields"] == ["identifier"]

    def test_keyword_fields_multiple(self) -> None:
        result = build_search_query(
            keywords="test",
            keyword_fields="title,description",
        )
        fields = result["bool"]["must"][0]["multi_match"]["fields"]
        assert set(fields) == {"title", "description"}


class TestBuildSearchQueryKeywordsEdgeCases:
    """Edge cases for keyword handling."""

    def test_empty_string_returns_status_only(self) -> None:
        result = build_search_query(keywords="")
        assert result == {
            "bool": {
                "filter": [{"term": {"status": "public"}}],
            },
        }

    def test_whitespace_only_returns_status_only(self) -> None:
        result = build_search_query(keywords="   ")
        assert result == {
            "bool": {
                "filter": [{"term": {"status": "public"}}],
            },
        }

    def test_trailing_comma_ignores_empty(self) -> None:
        result = build_search_query(keywords="cancer,")
        must = result["bool"]["must"]
        assert len(must) == 1

    def test_extra_commas_ignored(self) -> None:
        result = build_search_query(keywords=",cancer,,human,")
        must = result["bool"]["must"]
        assert len(must) == 2

    def test_invalid_keyword_fields_raises(self) -> None:
        with pytest.raises(ValueError, match="keywordFields"):
            build_search_query(keywords="test", keyword_fields="invalid")


class TestBuildSearchQueryFilters:
    """Filter clauses (organism, dates, types)."""

    # --- Organism ---

    def test_organism_filter(self) -> None:
        result = build_search_query(organism="9606")
        filters = result["bool"]["filter"]
        organism_filter = _find_filter(filters, "term", "organism.identifier")
        assert organism_filter["term"]["organism.identifier"] == "9606"

    # --- Date published ---

    def test_date_published_from(self) -> None:
        result = build_search_query(date_published_from="2024-01-01")
        filters = result["bool"]["filter"]
        date_filter = _find_filter(filters, "range", "datePublished")
        assert date_filter["range"]["datePublished"]["gte"] == "2024-01-01"

    def test_date_published_to(self) -> None:
        result = build_search_query(date_published_to="2024-12-31")
        filters = result["bool"]["filter"]
        date_filter = _find_filter(filters, "range", "datePublished")
        assert date_filter["range"]["datePublished"]["lte"] == "2024-12-31"

    def test_date_published_range(self) -> None:
        result = build_search_query(
            date_published_from="2024-01-01",
            date_published_to="2024-12-31",
        )
        filters = result["bool"]["filter"]
        date_filter = _find_filter(filters, "range", "datePublished")
        assert date_filter["range"]["datePublished"]["gte"] == "2024-01-01"
        assert date_filter["range"]["datePublished"]["lte"] == "2024-12-31"

    # --- Date modified ---

    def test_date_modified_from(self) -> None:
        result = build_search_query(date_modified_from="2024-06-01")
        filters = result["bool"]["filter"]
        date_filter = _find_filter(filters, "range", "dateModified")
        assert date_filter["range"]["dateModified"]["gte"] == "2024-06-01"

    def test_date_modified_to(self) -> None:
        result = build_search_query(date_modified_to="2024-06-30")
        filters = result["bool"]["filter"]
        date_filter = _find_filter(filters, "range", "dateModified")
        assert date_filter["range"]["dateModified"]["lte"] == "2024-06-30"

    # --- Types ---

    def test_single_type_filter(self) -> None:
        result = build_search_query(types="bioproject")
        filters = result["bool"]["filter"]
        type_filter = _find_filter(filters, "terms", "type")
        assert type_filter["terms"]["type"] == ["bioproject"]

    def test_multiple_types_filter(self) -> None:
        result = build_search_query(types="bioproject,biosample")
        filters = result["bool"]["filter"]
        type_filter = _find_filter(filters, "terms", "type")
        assert set(type_filter["terms"]["type"]) == {
            "bioproject",
            "biosample",
        }


class TestBuildSearchQueryBioProject:
    """BioProject-specific filter parameters."""

    def test_object_types_none_emits_no_filter(self) -> None:
        result = build_search_query(object_types=None)
        # status_mode は public_only がデフォルトなので status filter は残る
        filters = result["bool"].get("filter", [])
        assert not any("objectType" in (f.get("term") or {}) for f in filters)
        assert not any("objectType" in (f.get("terms") or {}) for f in filters)

    def test_object_types_single_bioproject_emits_term(self) -> None:
        result = build_search_query(object_types="BioProject")
        filters = result["bool"]["filter"]
        obj_filter = _find_filter(filters, "term", "objectType")
        assert obj_filter["term"]["objectType"] == "BioProject"

    def test_object_types_single_umbrella_emits_term(self) -> None:
        result = build_search_query(object_types="UmbrellaBioProject")
        filters = result["bool"]["filter"]
        obj_filter = _find_filter(filters, "term", "objectType")
        assert obj_filter["term"]["objectType"] == "UmbrellaBioProject"

    def test_object_types_both_emits_terms_sorted(self) -> None:
        result = build_search_query(object_types="UmbrellaBioProject,BioProject")
        filters = result["bool"]["filter"]
        obj_filter = _find_filter(filters, "terms", "objectType")
        assert obj_filter["terms"]["objectType"] == [
            "BioProject",
            "UmbrellaBioProject",
        ]

    def test_object_types_duplicates_dedup_to_term(self) -> None:
        result = build_search_query(object_types="BioProject,BioProject")
        filters = result["bool"]["filter"]
        obj_filter = _find_filter(filters, "term", "objectType")
        assert obj_filter["term"]["objectType"] == "BioProject"
        # 重複除去後は 1 値なので terms は出さない
        assert not any("objectType" in (f.get("terms") or {}) for f in filters)

    @given(
        sample=st.lists(
            st.sampled_from(["BioProject", "UmbrellaBioProject"]),
            min_size=1,
            max_size=4,
        )
    )
    def test_object_types_pbt_deterministic(self, sample: list[str]) -> None:
        """PBT: 入力の順序・重複に依らず生成 ES query は決定論的に等価になる。"""
        result = build_search_query(object_types=",".join(sample))
        filters = result["bool"]["filter"]
        unique_sorted = sorted(set(sample))
        if len(unique_sorted) == 1:
            obj_filter = _find_filter(filters, "term", "objectType")
            assert obj_filter["term"]["objectType"] == unique_sorted[0]
            assert not any("objectType" in (f.get("terms") or {}) for f in filters)
        else:
            obj_filter = _find_filter(filters, "terms", "objectType")
            assert obj_filter["terms"]["objectType"] == unique_sorted
            assert not any("objectType" in (f.get("term") or {}) for f in filters)

    def test_organization_nested_query(self) -> None:
        """organization → nested query on organization.name."""
        result = build_search_query(organization="DDBJ")
        filters = result["bool"]["filter"]
        nested = _find_nested_filter(filters, "organization")
        inner_query = nested["nested"]["query"]
        assert inner_query["match"]["organization.name"] == "DDBJ"

    def test_publication_nested_query(self) -> None:
        """publication → nested query on publication.title."""
        result = build_search_query(publication="genome")
        filters = result["bool"]["filter"]
        nested = _find_nested_filter(filters, "publication")
        inner_query = nested["nested"]["query"]
        assert inner_query["match"]["publication.title"] == "genome"

    def test_grant_nested_query(self) -> None:
        """grant → nested query on grant.title."""
        result = build_search_query(grant="NIH")
        filters = result["bool"]["filter"]
        nested = _find_nested_filter(filters, "grant")
        assert nested is not None


class TestBuildSearchQueryCombined:
    """Combined keyword + filter queries.

    A status filter (``status:public`` by default) is always prepended
    to ``bool.filter``, so ``filter`` length is 1 greater than the
    number of user-provided filter params.
    """

    def test_keywords_with_organism(self) -> None:
        result = build_search_query(keywords="cancer", organism="9606")
        assert len(result["bool"]["must"]) == 1
        # 2 = status filter と organism filter の 2 clause
        assert len(result["bool"]["filter"]) == 2

    def test_keywords_with_multiple_filters(self) -> None:
        result = build_search_query(
            keywords="cancer",
            organism="9606",
            date_published_from="2024-01-01",
        )
        assert len(result["bool"]["must"]) == 1
        # 3 = status / organism / datePublished の 3 clause
        assert len(result["bool"]["filter"]) == 3

    def test_only_filters_no_must(self) -> None:
        """Filters without keywords: bool.filter only, no bool.must."""
        result = build_search_query(organism="9606")
        assert "must" not in result["bool"]
        # 2 = status filter と organism filter の 2 clause
        assert len(result["bool"]["filter"]) == 2

    def test_or_keywords_with_filters(self) -> None:
        """OR keywords + filter → should + filter in same bool."""
        result = build_search_query(
            keywords="cancer,human",
            keyword_operator="OR",
            organism="9606",
        )
        assert len(result["bool"]["should"]) == 2
        assert result["bool"]["minimum_should_match"] == 1
        # status + organism
        assert len(result["bool"]["filter"]) == 2


# ===================================================================
# build_facet_aggs
# ===================================================================


class TestBuildFacetAggs:
    """build_facet_aggs(is_cross_type, requested_facets) -> ES aggs dict.

    ``requested_facets=None`` (default) returns common facets only;
    cross-type endpoints additionally include ``type``. All other
    facet aggregations are opt-in via an explicit list. ``status`` is
    intentionally never aggregated (see docs § データ可視性).
    """

    # --- Default (requested_facets=None) ---

    def test_default_returns_common_facets_only(self) -> None:
        result = build_facet_aggs()
        assert set(result) == {"organism", "accessibility"}

    def test_default_cross_type_adds_type_facet(self) -> None:
        result = build_facet_aggs(is_cross_type=True)
        assert set(result) == {"organism", "accessibility", "type"}

    def test_status_agg_always_omitted(self) -> None:
        # Status は常に public のみに絞り込んで集計するため、
        # status facet 自体は aggs に含めない (docs/api-spec.md
        # § データ可視性 参照)。
        assert "status" not in build_facet_aggs()
        assert "status" not in build_facet_aggs(is_cross_type=True)
        assert "status" not in build_facet_aggs(requested_facets=["organism"])
        assert "status" not in build_facet_aggs(requested_facets=["status"])

    def test_organism_agg_field(self) -> None:
        result = build_facet_aggs()
        assert result["organism"]["terms"]["field"] == "organism.name"

    def test_accessibility_agg_field(self) -> None:
        result = build_facet_aggs()
        assert result["accessibility"]["terms"]["field"] == "accessibility"

    def test_default_cross_type_type_field(self) -> None:
        result = build_facet_aggs(is_cross_type=True)
        assert result["type"]["terms"]["field"] == "type"

    def test_default_excludes_object_type(self) -> None:
        # objectType は default では返らず opt-in 専用
        # (docs/api-spec.md § ファセット集計対象の選択)。
        assert "objectType" not in build_facet_aggs()
        assert "objectType" not in build_facet_aggs(is_cross_type=True)

    def test_default_excludes_all_type_specific_facets(self) -> None:
        result = build_facet_aggs(is_cross_type=True)
        for name in (
            "objectType",
            "libraryStrategy",
            "librarySource",
            "librarySelection",
            "platform",
            "instrumentModel",
            "experimentType",
            "studyType",
            "submissionType",
        ):
            assert name not in result

    # --- Explicit requested_facets ---

    def test_empty_list_returns_empty_aggs(self) -> None:
        # facets="" → resolve_requested_facets returns []
        # → no aggregations at all.
        assert build_facet_aggs(requested_facets=[]) == {}

    def test_explicit_subset_returns_only_those(self) -> None:
        result = build_facet_aggs(requested_facets=["organism"])
        assert set(result) == {"organism"}

    def test_explicit_object_type_returns_object_type_agg(self) -> None:
        result = build_facet_aggs(requested_facets=["objectType"])
        assert result["objectType"]["terms"]["field"] == "objectType"

    @pytest.mark.parametrize(
        ("facet_name", "es_field"),
        [
            ("libraryStrategy", "libraryStrategy.keyword"),
            ("librarySource", "librarySource.keyword"),
            ("librarySelection", "librarySelection.keyword"),
            ("platform", "platform.keyword"),
            ("instrumentModel", "instrumentModel.keyword"),
            ("experimentType", "experimentType.keyword"),
            ("studyType", "studyType.keyword"),
            ("submissionType", "submissionType.keyword"),
        ],
    )
    def test_explicit_type_specific_facet_uses_keyword_field(
        self,
        facet_name: str,
        es_field: str,
    ) -> None:
        result = build_facet_aggs(requested_facets=[facet_name])
        assert facet_name in result
        assert result[facet_name]["terms"]["field"] == es_field

    def test_unknown_facet_name_silently_skipped(self) -> None:
        # The router catches typos at the FacetsParamQuery boundary; if
        # an unknown name slips through, the agg builder is intentionally
        # silent so requests do not blow up.
        result = build_facet_aggs(requested_facets=["organism", "zzznotfacet"])
        assert set(result) == {"organism"}


class TestBuildFacetAggsPBT:
    """PBT: default behavior across input combinations.

    Common facets must be present whenever ``requested_facets`` is
    omitted; ``type`` rides on ``is_cross_type`` exactly. ``status`` is
    never present.
    """

    @given(is_cross_type=st.booleans())
    def test_default_common_facets_always_included(
        self,
        is_cross_type: bool,
    ) -> None:
        result = build_facet_aggs(is_cross_type=is_cross_type)
        assert "organism" in result
        assert "accessibility" in result
        assert "status" not in result
        assert ("type" in result) is is_cross_type

    @given(
        is_cross_type=st.booleans(),
        names=st.sets(
            st.sampled_from(
                [
                    "organism",
                    "accessibility",
                    "type",
                    "objectType",
                    "libraryStrategy",
                    "librarySource",
                    "librarySelection",
                    "platform",
                    "instrumentModel",
                    "experimentType",
                    "studyType",
                    "submissionType",
                ],
            ),
            max_size=5,
        ),
    )
    def test_explicit_request_returns_exactly_requested(
        self,
        is_cross_type: bool,
        names: set[str],
    ) -> None:
        result = build_facet_aggs(
            is_cross_type=is_cross_type,
            requested_facets=list(names),
        )
        # An explicit request never adds default facets and never drops
        # known facet names.
        assert set(result) == names


class TestResolveRequestedFacets:
    """resolve_requested_facets returns None / [] / list and rejects
    type-mismatch via ValueError (router maps to HTTP 400)."""

    def test_none_returns_none(self) -> None:
        assert resolve_requested_facets(None, is_cross_type=False, db_type="bioproject") is None

    def test_empty_string_returns_empty_list(self) -> None:
        assert resolve_requested_facets("", is_cross_type=False, db_type="bioproject") == []

    def test_common_facets_accepted_anywhere(self) -> None:
        for db_type in (None, "bioproject", "biosample", "gea", "metabobank", "sra-study"):
            result = resolve_requested_facets(
                "organism,accessibility",
                is_cross_type=db_type is None,
                db_type=db_type,
            )
            assert result == ["organism", "accessibility"]

    def test_type_facet_only_cross_type(self) -> None:
        assert resolve_requested_facets("type", is_cross_type=True, db_type=None) == ["type"]
        with pytest.raises(ValueError, match="not applicable"):
            resolve_requested_facets("type", is_cross_type=False, db_type="bioproject")

    def test_object_type_only_for_bioproject(self) -> None:
        assert resolve_requested_facets("objectType", is_cross_type=False, db_type="bioproject") == ["objectType"]
        with pytest.raises(ValueError, match="not applicable"):
            resolve_requested_facets("objectType", is_cross_type=False, db_type="biosample")

    def test_object_type_loose_on_cross_type(self) -> None:
        assert resolve_requested_facets("objectType", is_cross_type=True, db_type=None) == ["objectType"]

    @pytest.mark.parametrize(
        ("name", "valid_db_types"),
        [
            ("libraryStrategy", {"sra-experiment"}),
            ("librarySource", {"sra-experiment"}),
            ("librarySelection", {"sra-experiment"}),
            ("platform", {"sra-experiment"}),
            ("instrumentModel", {"sra-experiment"}),
            ("experimentType", {"gea", "metabobank"}),
            ("studyType", {"jga-study", "metabobank"}),
            ("submissionType", {"metabobank"}),
        ],
    )
    def test_type_specific_facet_applicability(
        self,
        name: str,
        valid_db_types: set[str],
    ) -> None:
        for db_type in valid_db_types:
            result = resolve_requested_facets(name, is_cross_type=False, db_type=db_type)
            assert result == [name]
        # Cross-type endpoint accepts any allowlisted facet (loose).
        assert resolve_requested_facets(name, is_cross_type=True, db_type=None) == [name]
        # Wrong type-specific endpoint raises.
        wrong_type = next(
            t
            for t in (
                "bioproject",
                "biosample",
                "sra-experiment",
                "sra-study",
                "jga-study",
                "jga-dataset",
                "gea",
                "metabobank",
            )
            if t not in valid_db_types
        )
        with pytest.raises(ValueError, match="not applicable"):
            resolve_requested_facets(name, is_cross_type=False, db_type=wrong_type)

    def test_unknown_name_raises(self) -> None:
        # Allowlist typo is normally caught by FacetsParamQuery (422), but
        # we still defend against direct callers landing here.
        with pytest.raises(ValueError, match="not applicable"):
            resolve_requested_facets("totallyUnknown", is_cross_type=False, db_type="bioproject")

    def test_partial_failure_raises(self) -> None:
        # Even when one facet is valid, an inapplicable sibling fails.
        with pytest.raises(ValueError, match="not applicable"):
            resolve_requested_facets(
                "organism,libraryStrategy",
                is_cross_type=False,
                db_type="bioproject",
            )

    def test_whitespace_trimmed(self) -> None:
        result = resolve_requested_facets(
            " organism , accessibility ",
            is_cross_type=False,
            db_type="bioproject",
        )
        assert result == ["organism", "accessibility"]


# ===================================================================
# build_status_filter / build_search_query: status_mode
# ===================================================================


class TestBuildStatusFilter:
    """build_status_filter returns the ES filter clause for status."""

    def test_public_only(self) -> None:
        assert build_status_filter("public_only") == {"term": {"status": "public"}}

    def test_include_suppressed(self) -> None:
        assert build_status_filter("include_suppressed") == {
            "terms": {"status": ["public", "suppressed"]},
        }


class TestBuildSearchQueryStatusMode:
    """status_mode prepends a status filter to bool.filter.

    ``public_only`` (default) uses ``{"term": {"status": "public"}}``;
    ``include_suppressed`` broadens to ``public`` + ``suppressed``.
    The status filter is always the first element of ``bool.filter``.
    """

    def test_default_is_public_only(self) -> None:
        result = build_search_query(keywords="cancer")
        filters = result["bool"]["filter"]
        assert filters[0] == {"term": {"status": "public"}}

    def test_public_only_explicit(self) -> None:
        result = build_search_query(keywords="cancer", status_mode="public_only")
        filters = result["bool"]["filter"]
        assert filters[0] == {"term": {"status": "public"}}

    def test_include_suppressed(self) -> None:
        result = build_search_query(keywords="PRJDB1234", status_mode="include_suppressed")
        filters = result["bool"]["filter"]
        assert filters[0] == {"terms": {"status": ["public", "suppressed"]}}

    def test_status_filter_is_first(self) -> None:
        result = build_search_query(
            keywords="cancer",
            organism="9606",
            date_published_from="2024-01-01",
            status_mode="public_only",
        )
        filters = result["bool"]["filter"]
        # status が先頭にあることで ES の filter cache 効率も期待
        assert filters[0] == {"term": {"status": "public"}}

    def test_filter_contains_only_one_status_clause(self) -> None:
        result = build_search_query(
            keywords="cancer",
            organism="9606",
            status_mode="include_suppressed",
        )
        filters = result["bool"]["filter"]
        status_clauses = [
            f
            for f in filters
            if ("term" in f and "status" in f.get("term", {})) or ("terms" in f and "status" in f.get("terms", {}))
        ]
        assert len(status_clauses) == 1

    def test_no_params_with_include_suppressed(self) -> None:
        result = build_search_query(status_mode="include_suppressed")
        assert result == {
            "bool": {
                "filter": [{"terms": {"status": ["public", "suppressed"]}}],
            },
        }

    def test_none_opts_out_of_status_filter(self) -> None:
        """status_mode=None は status filter を追加しない
        (db-portal の Future work 用)。"""
        result = build_search_query(status_mode=None)
        assert result == {"match_all": {}}

    def test_none_with_keywords_no_status_filter(self) -> None:
        result = build_search_query(keywords="cancer", status_mode=None)
        # bool.must のみ、bool.filter は keyword filter 以外の user filter が無いので空
        assert "filter" not in result["bool"]

    def test_none_with_organism_no_status_filter(self) -> None:
        result = build_search_query(organism="9606", status_mode=None)
        filters = result["bool"]["filter"]
        # organism のみ
        assert len(filters) == 1
        assert filters[0]["term"]["organism.identifier"] == "9606"


# ===================================================================
# Test helpers
# ===================================================================


def _find_filter(
    filters: list[dict[str, Any]],
    query_type: str,
    field: str,
) -> dict[str, Any]:
    """Find a filter clause by query type and field name."""
    for f in filters:
        if query_type in f and field in f[query_type]:
            return f
    raise AssertionError(
        f"No {query_type} filter on '{field}' found in {filters}",
    )


def _find_nested_filter(filters: list[dict[str, Any]], path: str) -> dict[str, Any]:
    """Find a nested filter clause by path."""
    for f in filters:
        if "nested" in f and f["nested"]["path"] == path:
            return f
    raise AssertionError(
        f"No nested filter with path '{path}' found in {filters}",
    )


# ===================================================================
# _parse_keywords (phrase match)
# ===================================================================


class TestParseKeywordsPhrase:
    """_parse_keywords: phrase matching with double quotes."""

    def test_quoted_keyword_is_phrase(self) -> None:
        result = _parse_keywords('"RNA-Seq"')
        assert result == [("RNA-Seq", True)]

    def test_unquoted_keyword_is_not_phrase(self) -> None:
        result = _parse_keywords("cancer")
        assert result == [("cancer", False)]

    def test_mixed_quoted_and_unquoted(self) -> None:
        result = _parse_keywords('"RNA-Seq",cancer')
        assert result == [("RNA-Seq", True), ("cancer", False)]

    def test_comma_inside_quotes_preserved(self) -> None:
        result = _parse_keywords('"RNA-Seq, cancer"')
        assert result == [("RNA-Seq, cancer", True)]

    def test_empty_quotes_ignored(self) -> None:
        result = _parse_keywords('""')
        assert result == []

    def test_unclosed_quote_treated_as_literal(self) -> None:
        """Unclosed quote is stripped; inner content is treated as a literal."""
        result = _parse_keywords('"cancer')
        assert result == [("cancer", False)]

    def test_multiple_quoted_keywords(self) -> None:
        result = _parse_keywords('"RNA-Seq","whole genome"')
        assert result == [("RNA-Seq", True), ("whole genome", True)]

    def test_none_returns_empty(self) -> None:
        assert _parse_keywords(None) == []

    def test_empty_string_returns_empty(self) -> None:
        assert _parse_keywords("") == []

    @given(st.text(max_size=200))
    def test_never_crashes(self, keywords: str) -> None:
        """PBT: _parse_keywords never raises on arbitrary input."""
        result = _parse_keywords(keywords)
        assert isinstance(result, list)
        for text, is_phrase in result:
            assert isinstance(text, str)
            assert len(text) > 0
            assert isinstance(is_phrase, bool)


# ===================================================================
# _parse_keywords (auto-phrase for symbol-containing tokens)
# ===================================================================


class TestParseKeywordsAutoPhrase:
    """Tokens containing trigger symbols (-, /, ., +, :) are auto-phrased."""

    def test_hyphen_triggers_phrase(self) -> None:
        assert _parse_keywords("HIF-1") == [("HIF-1", True)]

    def test_slash_triggers_phrase(self) -> None:
        assert _parse_keywords("GSE12345/analysis") == [
            ("GSE12345/analysis", True),
        ]

    def test_dot_triggers_phrase(self) -> None:
        assert _parse_keywords("pH7.4") == [("pH7.4", True)]

    def test_plus_triggers_phrase(self) -> None:
        assert _parse_keywords("C++") == [("C++", True)]

    def test_colon_triggers_phrase(self) -> None:
        assert _parse_keywords("CAS:12345-67-8") == [("CAS:12345-67-8", True)]

    def test_covid_19_triggers_phrase(self) -> None:
        assert _parse_keywords("COVID-19") == [("COVID-19", True)]

    def test_sars_cov_2_triggers_phrase(self) -> None:
        assert _parse_keywords("SARS-CoV-2") == [("SARS-CoV-2", True)]

    def test_alphanumeric_no_phrase(self) -> None:
        assert _parse_keywords("cancer") == [("cancer", False)]

    def test_digits_only_no_phrase(self) -> None:
        assert _parse_keywords("12345") == [("12345", False)]

    def test_japanese_no_phrase(self) -> None:
        assert _parse_keywords("がん") == [("がん", False)]


class TestParseKeywordsAutoPhraseEdgeCases:
    """Boundary and interaction cases for auto-phrase detection."""

    def test_trailing_symbol_triggers(self) -> None:
        assert _parse_keywords("gene-") == [("gene-", True)]

    def test_leading_symbol_triggers(self) -> None:
        assert _parse_keywords("-gene") == [("-gene", True)]

    def test_bare_symbol_triggers(self) -> None:
        assert _parse_keywords("-") == [("-", True)]

    def test_multiple_bare_symbols_trigger(self) -> None:
        assert _parse_keywords("--") == [("--", True)]

    def test_mixed_auto_explicit_and_normal(self) -> None:
        result = _parse_keywords('HIF-1,"whole genome",cancer')
        assert result == [
            ("HIF-1", True),
            ("whole genome", True),
            ("cancer", False),
        ]

    def test_explicit_phrase_without_symbol_stays_phrase(self) -> None:
        assert _parse_keywords('"whole genome"') == [("whole genome", True)]

    def test_explicit_phrase_with_symbol_stays_phrase(self) -> None:
        assert _parse_keywords('"RNA-Seq"') == [("RNA-Seq", True)]

    def test_multiple_auto_phrase_tokens(self) -> None:
        result = _parse_keywords("HIF-1,COVID-19")
        assert result == [("HIF-1", True), ("COVID-19", True)]

    def test_whitespace_around_auto_phrase_trimmed(self) -> None:
        result = _parse_keywords(" HIF-1 ,  cancer ")
        assert result == [("HIF-1", True), ("cancer", False)]

    def test_unclosed_quote_with_trigger_becomes_auto_phrase(self) -> None:
        assert _parse_keywords('"RNA-Seq') == [("RNA-Seq", True)]


class TestParseKeywordsAutoPhrasePBT:
    """Property-based tests for auto-phrase detection."""

    @given(text=alphanumeric_no_trigger(ES_AUTO_PHRASE_TRIGGERS))
    def test_alphanumeric_without_trigger_is_not_phrase(self, text: str) -> None:
        """Any alphanumeric text without trigger chars → is_phrase=False."""
        assert _parse_keywords(text) == [(text, False)]

    @given(text=text_with_trigger(ES_AUTO_PHRASE_TRIGGERS))
    def test_text_containing_trigger_is_phrase(self, text: str) -> None:
        """Any text containing a trigger char → is_phrase=True."""
        result = _parse_keywords(text)
        assert len(result) == 1
        returned_text, is_phrase = result[0]
        assert returned_text == text
        assert is_phrase is True

    @given(text=alphanumeric_no_trigger(ES_AUTO_PHRASE_TRIGGERS))
    def test_quoted_alphanumeric_is_always_phrase(self, text: str) -> None:
        """Explicit quote always produces phrase, even without trigger chars."""
        assert _parse_keywords(f'"{text}"') == [(text, True)]


# ===================================================================
# build_search_query (phrase match integration)
# ===================================================================


class TestBuildSearchQueryPhraseMatch:
    """Phrase match: quoted keywords use multi_match type=phrase."""

    def test_phrase_keyword_has_type_phrase(self) -> None:
        result = build_search_query(keywords='"RNA-Seq"')
        must = result["bool"]["must"]
        assert len(must) == 1
        mm = must[0]["multi_match"]
        assert mm["query"] == "RNA-Seq"
        assert mm["type"] == "phrase"

    def test_normal_keyword_no_type(self) -> None:
        result = build_search_query(keywords="cancer")
        must = result["bool"]["must"]
        mm = must[0]["multi_match"]
        assert mm["query"] == "cancer"
        assert "type" not in mm

    def test_mixed_phrase_and_normal(self) -> None:
        result = build_search_query(keywords='"RNA-Seq",cancer')
        must = result["bool"]["must"]
        assert len(must) == 2
        # First is phrase
        assert must[0]["multi_match"]["type"] == "phrase"
        assert must[0]["multi_match"]["query"] == "RNA-Seq"
        # Second is normal
        assert "type" not in must[1]["multi_match"]
        assert must[1]["multi_match"]["query"] == "cancer"

    def test_phrase_with_or_operator(self) -> None:
        result = build_search_query(
            keywords='"RNA-Seq",cancer',
            keyword_operator="OR",
        )
        should = result["bool"]["should"]
        assert len(should) == 2
        assert should[0]["multi_match"]["type"] == "phrase"
        assert "type" not in should[1]["multi_match"]


class TestBuildSearchQueryAutoPhrase:
    """Integration: auto-phrased tokens produce multi_match(type=phrase)."""

    def test_hyphen_keyword_uses_phrase(self) -> None:
        result = build_search_query(keywords="HIF-1")
        mm = result["bool"]["must"][0]["multi_match"]
        assert mm["query"] == "HIF-1"
        assert mm["type"] == "phrase"

    def test_plain_keyword_omits_type(self) -> None:
        result = build_search_query(keywords="cancer")
        mm = result["bool"]["must"][0]["multi_match"]
        assert mm["query"] == "cancer"
        assert "type" not in mm

    def test_mixed_auto_and_normal_tokens(self) -> None:
        result = build_search_query(keywords="HIF-1,cancer")
        must = result["bool"]["must"]
        assert len(must) == 2
        assert must[0]["multi_match"]["type"] == "phrase"
        assert must[0]["multi_match"]["query"] == "HIF-1"
        assert "type" not in must[1]["multi_match"]
        assert must[1]["multi_match"]["query"] == "cancer"

    def test_auto_and_explicit_phrase_both_phrase(self) -> None:
        result = build_search_query(keywords='HIF-1,"whole genome"')
        must = result["bool"]["must"]
        assert len(must) == 2
        for clause in must:
            assert clause["multi_match"]["type"] == "phrase"

    def test_auto_phrase_with_or_operator(self) -> None:
        result = build_search_query(
            keywords="HIF-1,cancer",
            keyword_operator="OR",
        )
        should = result["bool"]["should"]
        assert len(should) == 2
        assert should[0]["multi_match"]["type"] == "phrase"
        assert "type" not in should[1]["multi_match"]

    def test_auto_phrase_honors_keyword_fields(self) -> None:
        result = build_search_query(
            keywords="HIF-1",
            keyword_fields="title,description",
        )
        mm = result["bool"]["must"][0]["multi_match"]
        assert mm["type"] == "phrase"
        assert set(mm["fields"]) == {"title", "description"}


# ===================================================================
# nested filters: organization / publication / grant
#
# converter の ES mapping は organization / publication / grant を
# `type: nested` で定義している。query は nested clause を生成する
# 必要があり、flat field に変えるとマッチしなくなる。converter bump
# 時の回帰防止として shape を固定する。
# ===================================================================


class TestBuildSearchQueryNestedFilters:
    """organization/publication/grant は nested query として組み立てられる."""

    def test_organization_builds_nested_clause(self) -> None:
        result = build_search_query(organization="DDBJ")
        filters = result["bool"]["filter"]
        nested_clauses = [c for c in filters if "nested" in c]
        assert len(nested_clauses) == 1
        nested = nested_clauses[0]["nested"]
        assert nested["path"] == "organization"
        assert nested["query"] == {"match": {"organization.name": "DDBJ"}}

    def test_publication_builds_nested_clause(self) -> None:
        result = build_search_query(publication="Genomic variants")
        filters = result["bool"]["filter"]
        nested_clauses = [c for c in filters if "nested" in c]
        assert len(nested_clauses) == 1
        nested = nested_clauses[0]["nested"]
        assert nested["path"] == "publication"
        assert nested["query"] == {"match": {"publication.title": "Genomic variants"}}

    def test_grant_builds_nested_clause(self) -> None:
        result = build_search_query(grant="JST CREST")
        filters = result["bool"]["filter"]
        nested_clauses = [c for c in filters if "nested" in c]
        assert len(nested_clauses) == 1
        nested = nested_clauses[0]["nested"]
        assert nested["path"] == "grant"
        assert nested["query"] == {"match": {"grant.title": "JST CREST"}}

    def test_multiple_nested_filters_combined(self) -> None:
        result = build_search_query(
            organization="DDBJ",
            publication="Genomic variants",
            grant="JST CREST",
        )
        filters = result["bool"]["filter"]
        nested_paths = {c["nested"]["path"] for c in filters if "nested" in c}
        assert nested_paths == {"organization", "publication", "grant"}

    def test_no_nested_filter_when_params_absent(self) -> None:
        result = build_search_query(keywords="cancer")
        # keywords のみ → filter は存在しないか、nested を含まない
        filters = result.get("bool", {}).get("filter", [])
        assert not any("nested" in c for c in filters)


# ===================================================================
# nested filters: externalLinkLabel / derivedFromId
# ===================================================================


class TestBuildSearchQueryNewNestedFilters:
    """externalLinkLabel / derivedFromId build nested match clauses."""

    def test_external_link_label_builds_nested_clause(self) -> None:
        result = build_search_query(external_link_label="GEO Series")
        nested = _find_nested_filter(result["bool"]["filter"], "externalLink")
        assert nested["nested"]["query"] == {"match": {"externalLink.label": "GEO Series"}}

    def test_derived_from_id_builds_nested_clause(self) -> None:
        result = build_search_query(derived_from_id="SAMD00012345")
        nested = _find_nested_filter(result["bool"]["filter"], "derivedFrom")
        assert nested["nested"]["query"] == {"match": {"derivedFrom.identifier": "SAMD00012345"}}

    def test_all_nested_filters_combined(self) -> None:
        result = build_search_query(
            organization="DDBJ",
            publication="cancer",
            grant="NIH",
            external_link_label="GEO",
            derived_from_id="SAMD00001",
        )
        nested_paths = {c["nested"]["path"] for c in result["bool"]["filter"] if "nested" in c}
        assert nested_paths == {
            "organization",
            "publication",
            "grant",
            "externalLink",
            "derivedFrom",
        }


# ===================================================================
# Type-specific term filters
# ===================================================================


class TestBuildSearchQueryTypeSpecificTermFilters:
    """Type-specific term filters use ``*.keyword`` and OR comma values."""

    def test_library_strategy_single_value_uses_term(self) -> None:
        result = build_search_query(library_strategy="WGS")
        f = _find_filter(result["bool"]["filter"], "term", "libraryStrategy.keyword")
        assert f["term"]["libraryStrategy.keyword"] == "WGS"

    def test_library_strategy_multiple_values_use_terms(self) -> None:
        result = build_search_query(library_strategy="WGS,RNA-Seq")
        f = _find_filter(result["bool"]["filter"], "terms", "libraryStrategy.keyword")
        assert set(f["terms"]["libraryStrategy.keyword"]) == {"WGS", "RNA-Seq"}

    @pytest.mark.parametrize(
        ("kwarg", "es_field"),
        [
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
        ],
    )
    def test_each_term_filter_routes_to_keyword_field(
        self,
        kwarg: str,
        es_field: str,
    ) -> None:
        result = build_search_query(**{kwarg: "X"})  # type: ignore[arg-type]
        f = _find_filter(result["bool"]["filter"], "term", es_field)
        assert f["term"][es_field] == "X"

    def test_empty_string_skips_term_filter(self) -> None:
        result = build_search_query(library_strategy="")
        clauses = result["bool"]["filter"]
        assert not any("libraryStrategy.keyword" in c.get("term", {}) for c in clauses)
        assert not any("libraryStrategy.keyword" in c.get("terms", {}) for c in clauses)

    def test_whitespace_stripped(self) -> None:
        result = build_search_query(library_strategy=" WGS , RNA-Seq ")
        f = _find_filter(result["bool"]["filter"], "terms", "libraryStrategy.keyword")
        assert set(f["terms"]["libraryStrategy.keyword"]) == {"WGS", "RNA-Seq"}

    def test_duplicates_collapse_via_terms(self) -> None:
        # Comma duplicates are kept only if distinct after strip; the
        # builder doesn't dedup but ES tolerates duplicates inside ``terms``.
        result = build_search_query(library_strategy="WGS,WGS,RNA-Seq")
        f = _find_filter(result["bool"]["filter"], "terms", "libraryStrategy.keyword")
        assert "WGS" in f["terms"]["libraryStrategy.keyword"]
        assert "RNA-Seq" in f["terms"]["libraryStrategy.keyword"]


# ===================================================================
# Type-specific text match filters
# ===================================================================


def _find_text_match_clause(
    filters: list[dict[str, Any]],
    field: str,
) -> dict[str, Any] | None:
    for clause in filters:
        match = clause.get("match", {})
        if isinstance(match, dict) and field in match:
            return clause
    return None


def _find_match_phrase_clause(
    filters: list[dict[str, Any]],
    field: str,
) -> dict[str, Any] | None:
    for clause in filters:
        match_phrase = clause.get("match_phrase", {})
        if isinstance(match_phrase, dict) and field in match_phrase:
            return clause
    return None


class TestBuildSearchQueryTextMatchFilters:
    """Type-specific text match filters use auto-phrase + match/match_phrase."""

    def test_host_simple_token_uses_match_with_and_operator(self) -> None:
        result = build_search_query(host="Homo sapiens")
        clause = _find_text_match_clause(result["bool"]["filter"], "host")
        assert clause is not None
        assert clause["match"]["host"]["query"] == "Homo sapiens"
        assert clause["match"]["host"]["operator"] == "and"

    def test_host_with_quoted_phrase_uses_match_phrase(self) -> None:
        result = build_search_query(host='"Homo sapiens"')
        clause = _find_match_phrase_clause(result["bool"]["filter"], "host")
        assert clause is not None
        assert clause["match_phrase"]["host"] == "Homo sapiens"

    def test_host_with_hyphen_auto_phrases(self) -> None:
        result = build_search_query(host="HIF-1")
        clause = _find_match_phrase_clause(result["bool"]["filter"], "host")
        assert clause is not None
        assert clause["match_phrase"]["host"] == "HIF-1"

    def test_host_comma_separated_or_combines(self) -> None:
        result = build_search_query(host="Homo,Mus musculus")
        bool_clause = next(
            (c for c in result["bool"]["filter"] if "bool" in c and "should" in c.get("bool", {})),
            None,
        )
        assert bool_clause is not None
        assert len(bool_clause["bool"]["should"]) == 2
        assert bool_clause["bool"]["minimum_should_match"] == 1
        # one phrase ("Mus musculus" is plain → match), one match.
        # Ensure both refer to the host field.
        for sub in bool_clause["bool"]["should"]:
            target = sub.get("match") or sub.get("match_phrase") or {}
            assert "host" in target

    def test_text_match_respects_keyword_operator_or(self) -> None:
        result = build_search_query(host="Homo sapiens", keyword_operator="OR")
        clause = _find_text_match_clause(result["bool"]["filter"], "host")
        assert clause is not None
        assert clause["match"]["host"]["operator"] == "or"

    def test_text_match_empty_string_skipped(self) -> None:
        result = build_search_query(host="")
        assert _find_text_match_clause(result["bool"]["filter"], "host") is None
        assert _find_match_phrase_clause(result["bool"]["filter"], "host") is None

    @pytest.mark.parametrize(
        ("kwarg", "es_field"),
        [
            ("project_type", "projectType"),
            ("strain", "strain"),
            ("isolate", "isolate"),
            ("geo_loc_name", "geoLocName"),
            ("collection_date", "collectionDate"),
            ("library_name", "libraryName"),
            ("library_construction_protocol", "libraryConstructionProtocol"),
            ("vendor", "vendor"),
        ],
    )
    def test_text_match_routes_to_top_level_field(
        self,
        kwarg: str,
        es_field: str,
    ) -> None:
        result = build_search_query(**{kwarg: "value"})  # type: ignore[arg-type]
        clause = _find_text_match_clause(result["bool"]["filter"], es_field)
        assert clause is not None
        assert clause["match"][es_field]["query"] == "value"

    def test_text_match_in_filter_section(self) -> None:
        # text match clauses should live under bool.filter so they
        # behave as AND constraints alongside the status filter.
        result = build_search_query(keywords="cancer", host="Homo sapiens")
        # keyword multi_match goes under must, host match under filter
        assert "must" in result["bool"]
        assert _find_text_match_clause(result["bool"]["filter"], "host") is not None
