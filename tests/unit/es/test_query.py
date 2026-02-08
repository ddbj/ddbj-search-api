"""Tests for ES query builder (ddbj_search_api.es.query).

Tests verify the conversion from API parameters to Elasticsearch query DSL.
All functions are pure (no I/O), so no mocks are needed.
"""
import pytest
from hypothesis import given
from hypothesis import strategies as st

from ddbj_search_api.es.query import (
    build_facet_aggs,
    build_search_query,
    build_sort,
    build_source_filter,
    pagination_to_from_size,
    validate_keyword_fields,
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
        self, page: int, per_page: int,
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
    """build_search_query with no parameters → match_all."""

    def test_no_params_returns_match_all(self) -> None:
        result = build_search_query()
        assert result == {"match_all": {}}


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

    def test_empty_string_returns_match_all(self) -> None:
        result = build_search_query(keywords="")
        assert result == {"match_all": {}}

    def test_whitespace_only_returns_match_all(self) -> None:
        result = build_search_query(keywords="   ")
        assert result == {"match_all": {}}

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
            "bioproject", "biosample",
        }


class TestBuildSearchQueryBioProject:
    """BioProject-specific filter parameters."""

    def test_umbrella_true(self) -> None:
        result = build_search_query(umbrella="TRUE")
        filters = result["bool"]["filter"]
        obj_filter = _find_filter(filters, "term", "objectType")
        assert obj_filter["term"]["objectType"] == "UmbrellaBioProject"

    def test_umbrella_false(self) -> None:
        result = build_search_query(umbrella="FALSE")
        filters = result["bool"]["filter"]
        obj_filter = _find_filter(filters, "term", "objectType")
        assert obj_filter["term"]["objectType"] == "BioProject"

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
    """Combined keyword + filter queries."""

    def test_keywords_with_organism(self) -> None:
        result = build_search_query(keywords="cancer", organism="9606")
        assert len(result["bool"]["must"]) == 1
        assert len(result["bool"]["filter"]) == 1

    def test_keywords_with_multiple_filters(self) -> None:
        result = build_search_query(
            keywords="cancer",
            organism="9606",
            date_published_from="2024-01-01",
        )
        assert len(result["bool"]["must"]) == 1
        assert len(result["bool"]["filter"]) == 2

    def test_only_filters_no_must(self) -> None:
        """Filters without keywords: bool.filter only, no bool.must."""
        result = build_search_query(organism="9606")
        assert "must" not in result["bool"]
        assert len(result["bool"]["filter"]) == 1

    def test_or_keywords_with_filters(self) -> None:
        """OR keywords + filter → should + filter in same bool."""
        result = build_search_query(
            keywords="cancer,human",
            keyword_operator="OR",
            organism="9606",
        )
        assert len(result["bool"]["should"]) == 2
        assert result["bool"]["minimum_should_match"] == 1
        assert len(result["bool"]["filter"]) == 1


# ===================================================================
# build_facet_aggs
# ===================================================================


class TestBuildFacetAggs:
    """build_facet_aggs(is_cross_type, db_type) -> ES aggs dict."""

    # --- Common facets ---

    def test_common_facets_always_present(self) -> None:
        result = build_facet_aggs()
        assert "organism" in result
        assert "status" in result
        assert "accessibility" in result

    def test_organism_agg_field(self) -> None:
        result = build_facet_aggs()
        assert result["organism"]["terms"]["field"] == "organism.name"

    def test_status_agg_field(self) -> None:
        result = build_facet_aggs()
        assert result["status"]["terms"]["field"] == "status"

    def test_accessibility_agg_field(self) -> None:
        result = build_facet_aggs()
        assert result["accessibility"]["terms"]["field"] == "accessibility"

    # --- Cross-type: includes type facet ---

    def test_cross_type_includes_type_facet(self) -> None:
        result = build_facet_aggs(is_cross_type=True)
        assert "type" in result
        assert result["type"]["terms"]["field"] == "type"

    def test_type_specific_excludes_type_facet(self) -> None:
        result = build_facet_aggs(is_cross_type=False)
        assert "type" not in result

    # --- BioProject: includes objectType facet ---

    def test_bioproject_includes_object_type(self) -> None:
        result = build_facet_aggs(db_type="bioproject")
        assert "objectType" in result
        assert result["objectType"]["terms"]["field"] == "objectType"

    def test_non_bioproject_excludes_object_type(self) -> None:
        result = build_facet_aggs(db_type="biosample")
        assert "objectType" not in result

    def test_no_db_type_excludes_object_type(self) -> None:
        result = build_facet_aggs(db_type=None)
        assert "objectType" not in result


class TestBuildFacetAggsPBT:
    """PBT: facet aggs always contain common facets regardless of params."""

    @given(
        is_cross_type=st.booleans(),
        db_type=st.sampled_from([
            None, "bioproject", "biosample",
            "sra-study", "jga-study",
        ]),
    )
    def test_common_facets_always_included(
        self, is_cross_type: bool, db_type: str,
    ) -> None:
        result = build_facet_aggs(
            is_cross_type=is_cross_type, db_type=db_type,
        )
        assert "organism" in result
        assert "status" in result
        assert "accessibility" in result


# ===================================================================
# Test helpers
# ===================================================================


def _find_filter(
    filters: list,
    query_type: str,
    field: str,
) -> dict:
    """Find a filter clause by query type and field name."""
    for f in filters:
        if query_type in f and field in f[query_type]:
            return f
    raise AssertionError(
        f"No {query_type} filter on '{field}' found in {filters}",
    )


def _find_nested_filter(filters: list, path: str) -> dict:
    """Find a nested filter clause by path."""
    for f in filters:
        if "nested" in f and f["nested"]["path"] == path:
            return f
    raise AssertionError(
        f"No nested filter with path '{path}' found in {filters}",
    )
