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
    _COMMON_FACET_NAMES,
    _CROSS_TYPE_ONLY_FACET_NAMES,
    _DB_PORTAL_ES_SUBTYPES,
    _FACET_AGG_SPECS,
    _FACET_TO_DSL_FIELD,
    _TYPE_SPECIFIC_FACET_SCOPE,
    DEFAULT_FACET_SIZE,
    _parse_keywords,
    build_facet_aggs,
    build_facet_base_query,
    build_search_query,
    build_self_excluding_facet_aggs,
    build_sort,
    build_sort_with_tiebreaker,
    build_source_filter,
    build_status_filter,
    db_portal_es_facet_allowlist,
    facet_to_dsl_field,
    inject_status_filter,
    pagination_to_from_size,
    resolve_facets_size,
    resolve_requested_facets,
    validate_keyword_fields,
)
from ddbj_search_api.search.dsl import parse, validate
from ddbj_search_api.search.dsl.allowlist import FIELD_TYPES
from ddbj_search_api.search.phrase import ES_AUTO_PHRASE_CHARS
from tests.unit.strategies import alphanumeric_no_trigger, text_with_trigger

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
        assert set(result) == {
            "identifier",
            "title",
            "name",
            "description",
            "organism.name",
        }

    def test_single_field(self) -> None:
        assert validate_keyword_fields("title") == ["title"]

    def test_multiple_fields(self) -> None:
        result = validate_keyword_fields("identifier,title")
        assert set(result) == {"identifier", "title"}

    def test_all_valid_fields(self) -> None:
        result = validate_keyword_fields(
            "identifier,title,name,description,organism.name",
        )
        assert set(result) == {
            "identifier",
            "title",
            "name",
            "description",
            "organism.name",
        }

    def test_organism_name_field(self) -> None:
        # ピリオドを含む field 名が allowlist 検証を素通りすることを担保する。
        assert validate_keyword_fields("organism.name") == ["organism.name"]

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


def _bare_word_should(token: str, fields: list[str]) -> dict[str, Any]:
    """The should-wrapper a bare (unquoted, symbol-free) keyword token expands to.

    A bare word matches as an AND multi_match over all fields, OR a prefix-phrase
    multi_match so a trailing partial word also matches.  ``phrase_prefix`` only
    works on text fields, so the keyword-typed ``identifier`` is dropped from the
    prefix clause (ES rejects phrase prefix on keyword fields).
    """
    prefix_fields = [f for f in fields if f != "identifier"]
    return {
        "bool": {
            "should": [
                {"multi_match": {"query": token, "fields": fields, "operator": "and"}},
                {"multi_match": {"query": token, "fields": prefix_fields, "type": "phrase_prefix"}},
            ],
            "minimum_should_match": 1,
        },
    }


class TestBuildSearchQueryKeywords:
    """Keyword search → multi_match queries."""

    def test_single_keyword_creates_multi_match(self) -> None:
        result = build_search_query(keywords="cancer")
        # bare word は完全形で should-wrapper (AND multi_match + phrase_prefix) になる.
        default_fields = ["identifier", "title", "name", "description", "organism.name"]
        assert result["bool"]["must"] == [_bare_word_should("cancer", default_fields)]

    def test_single_keyword_searches_all_default_fields(self) -> None:
        result = build_search_query(keywords="cancer")
        should = result["bool"]["must"][0]["bool"]["should"]
        and_mm = next(s["multi_match"] for s in should if s["multi_match"].get("operator") == "and")
        prefix_mm = next(s["multi_match"] for s in should if s["multi_match"].get("type") == "phrase_prefix")
        # 完全語側は全 default fields、前方一致側は keyword 型 identifier を除いた text field.
        assert set(and_mm["fields"]) == {"identifier", "title", "name", "description", "organism.name"}
        assert set(prefix_mm["fields"]) == {"title", "name", "description", "organism.name"}

    def test_auto_phrase_keyword_inherits_organism_name(self) -> None:
        # auto-phrase 経路 (multi_match type=phrase) でも default fields の
        # organism.name が落ちないことを担保する。
        result = build_search_query(keywords="SARS-CoV-2")
        multi_match = result["bool"]["must"][0]["multi_match"]
        assert multi_match["type"] == "phrase"
        assert "organism.name" in multi_match["fields"]

    def test_keyword_fields_organism_name_only(self) -> None:
        # organism.name 単独指定が allowlist → fields → multi_match まで
        # ピリオドを含む field 名のまま運ばれることを確認する。
        result = build_search_query(
            keywords="Homo sapiens",
            keyword_fields="organism.name",
        )
        must = result["bool"]["must"]
        # bare word の should-wrapper 内 2 multi_match の双方に organism.name が
        # ピリオド付きのまま運ばれる.
        for sub in must[0]["bool"]["should"]:
            assert sub["multi_match"]["fields"] == ["organism.name"]

    def test_multiple_keywords_and_operator(self) -> None:
        """AND: all keywords in bool.must (all must match)."""
        result = build_search_query(
            keywords="cancer,human",
            keyword_operator="AND",
        )
        must = result["bool"]["must"]
        assert len(must) == 2
        # 各 bare word token は should-wrapper になり、その AND multi_match の query.
        queries = {m["bool"]["should"][0]["multi_match"]["query"] for m in must}
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
        """keywordFields restricts multi_match fields.

        identifier 単独だと前方一致対象の text field が無いため should-wrapper にならず、
        完全語 multi_match 単独になる (keyword 型に phrase_prefix を付けると ES が 400)。
        """
        result = build_search_query(
            keywords="PRJDB1234",
            keyword_fields="identifier",
        )
        assert result["bool"]["must"] == [
            {"multi_match": {"query": "PRJDB1234", "fields": ["identifier"], "operator": "and"}},
        ]

    def test_keyword_fields_multiple(self) -> None:
        result = build_search_query(
            keywords="test",
            keyword_fields="title,description",
        )
        for sub in result["bool"]["must"][0]["bool"]["should"]:
            assert set(sub["multi_match"]["fields"]) == {"title", "description"}


class TestBuildSearchQueryKeywordsInTokenAnd:
    """1 keyword 値内 (= 1 multi_match 内) の空白が AND 結合される.

    記号なし bare word は should-wrapper に展開され、その内の AND multi_match に
    ``operator: "and"`` が入り、もう一方が ``type: "phrase_prefix"`` で前方一致を補う.
    phrase 系 (type=phrase) には operator を付けない (ES 仕様で無視されるため).
    """

    def test_single_word_keyword_has_operator_and(self) -> None:
        result = build_search_query(keywords="cancer")
        # bare word → should-wrapper の AND multi_match (operator=and, type なし) と
        # phrase_prefix multi_match の 2 つ.
        should = result["bool"]["must"][0]["bool"]["should"]
        and_mm, prefix_mm = should[0]["multi_match"], should[1]["multi_match"]
        assert and_mm["operator"] == "and"
        assert "type" not in and_mm
        assert prefix_mm["type"] == "phrase_prefix"
        assert "operator" not in prefix_mm

    def test_single_token_with_spaces_has_operator_and(self) -> None:
        # `whole genome` は phrase ではなく should-wrapper の AND multi_match 内で
        # AND 結合される. ES 内部で analyzer が tokens=[whole, genome] に分割し、
        # operator=and で両方 token を含む document のみマッチ. phrase_prefix 側で
        # 末尾語の前方一致も拾う.
        result = build_search_query(keywords="whole genome")
        should = result["bool"]["must"][0]["bool"]["should"]
        and_mm, prefix_mm = should[0]["multi_match"], should[1]["multi_match"]
        assert and_mm["query"] == "whole genome"
        assert and_mm["operator"] == "and"
        assert "type" not in and_mm
        assert prefix_mm["query"] == "whole genome"
        assert prefix_mm["type"] == "phrase_prefix"

    def test_phrase_keyword_omits_operator(self) -> None:
        # 明示クオート → phrase. operator は付けない (前方一致もしない).
        result = build_search_query(keywords='"whole genome"')
        mm = result["bool"]["must"][0]["multi_match"]
        assert mm["type"] == "phrase"
        assert "operator" not in mm

    def test_auto_phrase_keyword_omits_operator(self) -> None:
        # 記号 (-/.+:) 含み → auto phrase. operator は付けない (前方一致もしない).
        result = build_search_query(keywords="HIF-1")
        mm = result["bool"]["must"][0]["multi_match"]
        assert mm["type"] == "phrase"
        assert "operator" not in mm

    def test_comma_separated_keywords_each_have_operator_and(self) -> None:
        # カンマ区切り bare word token がそれぞれ should-wrapper に展開され、
        # 各 wrapper 内の AND multi_match に operator=and、もう一方に phrase_prefix.
        result = build_search_query(keywords="cancer,human", keyword_operator="AND")
        for clause in result["bool"]["must"]:
            should = clause["bool"]["should"]
            and_mm, prefix_mm = should[0]["multi_match"], should[1]["multi_match"]
            assert and_mm["operator"] == "and"
            assert "type" not in and_mm
            assert prefix_mm["type"] == "phrase_prefix"


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

    # --- Accessibility ---

    def test_accessibility_public_access_filter(self) -> None:
        result = build_search_query(accessibility="public-access")
        filters = result["bool"]["filter"]
        acc_filter = _find_filter(filters, "term", "accessibility")
        assert acc_filter["term"]["accessibility"] == "public-access"

    def test_accessibility_controlled_access_filter(self) -> None:
        result = build_search_query(accessibility="controlled-access")
        filters = result["bool"]["filter"]
        acc_filter = _find_filter(filters, "term", "accessibility")
        assert acc_filter["term"]["accessibility"] == "controlled-access"

    def test_accessibility_none_emits_no_filter(self) -> None:
        result = build_search_query(accessibility=None)
        filters = result["bool"].get("filter", [])
        # status filter は残るが accessibility filter は出ない
        assert not any("accessibility" in (f.get("term") or {}) for f in filters)

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
        """organization → nested query on organization.name (text match semantics)."""
        result = build_search_query(organization="DDBJ")
        filters = result["bool"]["filter"]
        nested = _find_nested_filter(filters, "organization")
        inner_query = nested["nested"]["query"]
        # 単一値・記号なし → match + operator=and (値内空白 AND).
        assert inner_query == {
            "match": {"organization.name": {"query": "DDBJ", "operator": "and"}},
        }

    def test_publication_nested_query(self) -> None:
        """publication → nested query on publication.title (text match semantics)."""
        result = build_search_query(publication="genome")
        filters = result["bool"]["filter"]
        nested = _find_nested_filter(filters, "publication")
        inner_query = nested["nested"]["query"]
        assert inner_query == {
            "match": {"publication.title": {"query": "genome", "operator": "and"}},
        }

    def test_grant_nested_query(self) -> None:
        """grant → nested query on grant.title (text match semantics)."""
        result = build_search_query(grant="NIH")
        filters = result["bool"]["filter"]
        nested = _find_nested_filter(filters, "grant")
        assert nested is not None
        assert nested["nested"]["query"] == {
            "match": {"grant.title": {"query": "NIH", "operator": "and"}},
        }


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


class TestFacetToDslField:
    """facet 名 → DSL field 逆引き表と agg-spec / allowlist の整合 (self-exclusion)."""

    def test_keys_match_facet_agg_specs(self) -> None:
        """逆引き表のキーは全 facet (``_FACET_AGG_SPECS``) と 1:1 対応する。"""
        assert set(_FACET_TO_DSL_FIELD) == set(_FACET_AGG_SPECS)

    def test_values_are_allowlisted_dsl_fields(self) -> None:
        """再注入先 DSL field は全て allowlist に存在する (除外後に compile 可能)。"""
        assert set(_FACET_TO_DSL_FIELD.values()) <= set(FIELD_TYPES)

    def test_known_facets_map_to_expected_field(self) -> None:
        assert facet_to_dsl_field("organism") == "organism_id"
        assert facet_to_dsl_field("objectType") == "object_type"
        assert facet_to_dsl_field("accessibility") == "accessibility"

    def test_unknown_facet_raises_key_error(self) -> None:
        with pytest.raises(KeyError):
            facet_to_dsl_field("no_such_facet")


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
        # bucket は TaxID で集計し (検索 API の ``?organism=`` と整合)、
        # sub-aggregation で ``organism.name.keyword`` の代表値を ``label``
        # として取り出す (docs/api-spec.md § ファセット § bucket 形式)。
        assert result["organism"]["terms"]["field"] == "organism.identifier"
        assert result["organism"]["aggs"]["name"]["terms"] == {
            "field": "organism.name.keyword",
            "size": 1,
        }

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
            # relevance / model は ES mapping が keyword 単独 (text+keyword multi-field ではない)
            # なので suffix `.keyword` を付けない
            ("relevance", "relevance"),
            ("model", "model"),
            # package は object{name:keyword,displayName:keyword} で name サブフィールド
            ("package", "package.name"),
            # libraryLayout / analysisType / datasetType は text+keyword multi-field
            ("libraryLayout", "libraryLayout.keyword"),
            ("analysisType", "analysisType.keyword"),
            ("datasetType", "datasetType.keyword"),
            # text + .keyword の text match param とペアになる facet。
            # bucket 集計は .keyword 側で行い、search 側は analyzed text match。
            ("projectType", "projectType.keyword"),
            ("host", "host.keyword"),
            ("vendor", "vendor.keyword"),
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

    # --- size injection (facetsSize parameter) ---

    def test_default_size_is_100(self) -> None:
        """Server-side default for ``terms.size`` is 100 across every facet."""
        assert DEFAULT_FACET_SIZE == 100
        result = build_facet_aggs(is_cross_type=True)
        for name in result:
            assert result[name]["terms"]["size"] == 100, f"{name} should default to size=100"

    def test_size_injected_uniformly(self) -> None:
        """A non-default ``size`` is applied to every facet's ``terms.size``."""
        result = build_facet_aggs(
            is_cross_type=True,
            requested_facets=["organism", "libraryLayout", "analysisType", "type", "objectType"],
            size=7,
        )
        for name in ("organism", "libraryLayout", "analysisType", "objectType"):
            assert result[name]["terms"]["size"] == 7

    def test_size_does_not_affect_organism_sub_agg(self) -> None:
        """The ``organism.name`` sub-aggregation that fetches the display
        label is pinned at ``size: 1`` and must not move with
        ``facetsSize``."""
        result = build_facet_aggs(requested_facets=["organism"], size=500)
        assert result["organism"]["terms"]["size"] == 500
        assert result["organism"]["aggs"]["name"]["terms"]["size"] == 1

    def test_size_call_isolation(self) -> None:
        """deepcopy guarantees: two calls with different ``size`` values
        must not bleed into each other (regression: shared template
        mutation would break second call's expected size)."""
        first = build_facet_aggs(size=3)
        second = build_facet_aggs(size=99)
        assert first["organism"]["terms"]["size"] == 3
        assert second["organism"]["terms"]["size"] == 99


class TestResolveFacetsSize:
    """resolve_facets_size: ``None`` -> server default; int -> passthrough."""

    def test_none_returns_default(self) -> None:
        assert resolve_facets_size(None) == DEFAULT_FACET_SIZE

    @pytest.mark.parametrize("value", [1, 50, 100, 1000])
    def test_int_passthrough(self, value: int) -> None:
        assert resolve_facets_size(value) == value


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
                    "relevance",
                    "package",
                    "model",
                    "libraryLayout",
                    "analysisType",
                    "datasetType",
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
        """status_mode=None は status filter を追加しない。"""
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


class TestInjectStatusFilter:
    """inject_status_filter prepends a status filter to compile_to_es output."""

    def test_leaf_term_wrapped_in_bool(self) -> None:
        """leaf clause (term) は bool.must でラップされ、bool.filter に status が付く。"""
        leaf = {"term": {"identifier": "PRJDB1234"}}
        result = inject_status_filter(leaf, "public_only")
        assert result == {
            "bool": {
                "must": [{"term": {"identifier": "PRJDB1234"}}],
                "filter": [{"term": {"status": "public"}}],
            },
        }

    def test_leaf_match_phrase_wrapped(self) -> None:
        leaf = {"match_phrase": {"title": "RNA-Seq"}}
        result = inject_status_filter(leaf, "public_only")
        assert result["bool"]["must"] == [leaf]
        assert result["bool"]["filter"] == [{"term": {"status": "public"}}]

    def test_leaf_wildcard_wrapped(self) -> None:
        leaf = {"wildcard": {"title": {"value": "cancer*", "case_insensitive": True}}}
        result = inject_status_filter(leaf, "public_only")
        assert result["bool"]["must"] == [leaf]
        assert result["bool"]["filter"] == [{"term": {"status": "public"}}]

    def test_leaf_nested_wrapped(self) -> None:
        leaf = {
            "nested": {
                "path": "organization",
                "query": {"term": {"organization.name": "ddbj"}},
            },
        }
        result = inject_status_filter(leaf, "public_only")
        assert result["bool"]["must"] == [leaf]
        assert result["bool"]["filter"] == [{"term": {"status": "public"}}]

    def test_bool_wrapper_filter_added_when_absent(self) -> None:
        bool_query = {"bool": {"must": [{"term": {"x": "y"}}]}}
        result = inject_status_filter(bool_query, "public_only")
        assert result == {
            "bool": {
                "must": [{"term": {"x": "y"}}],
                "filter": [{"term": {"status": "public"}}],
            },
        }

    def test_bool_wrapper_status_prepended_to_existing_filter(self) -> None:
        bool_query = {
            "bool": {
                "must": [{"term": {"x": "y"}}],
                "filter": [{"term": {"a": "b"}}],
            },
        }
        result = inject_status_filter(bool_query, "include_suppressed")
        assert result["bool"]["filter"] == [
            {"terms": {"status": ["public", "suppressed"]}},
            {"term": {"a": "b"}},
        ]

    def test_bool_should_wrapper_filter_added(self) -> None:
        """OR (should) 構造でも filter は同列に追加される。"""
        bool_query = {
            "bool": {
                "should": [
                    {"term": {"a": "1"}},
                    {"term": {"a": "2"}},
                ],
                "minimum_should_match": 1,
            },
        }
        result = inject_status_filter(bool_query, "public_only")
        assert result["bool"]["should"] == bool_query["bool"]["should"]
        assert result["bool"]["minimum_should_match"] == 1
        assert result["bool"]["filter"] == [{"term": {"status": "public"}}]

    def test_bool_must_not_wrapper_filter_added(self) -> None:
        """NOT (must_not) 構造でも filter は同列に追加される。"""
        bool_query = {"bool": {"must_not": [{"term": {"x": "y"}}]}}
        result = inject_status_filter(bool_query, "public_only")
        assert result["bool"]["must_not"] == [{"term": {"x": "y"}}]
        assert result["bool"]["filter"] == [{"term": {"status": "public"}}]

    def test_input_leaf_not_mutated(self) -> None:
        """元 dict (leaf) を変更しない。"""
        original = {"term": {"identifier": "PRJDB1234"}}
        _ = inject_status_filter(original, "public_only")
        assert original == {"term": {"identifier": "PRJDB1234"}}

    def test_input_bool_not_mutated(self) -> None:
        """元 dict (bool wrapper) を変更しない。must / filter どちらも。"""
        original: dict[str, Any] = {
            "bool": {
                "must": [{"term": {"x": "y"}}],
                "filter": [{"term": {"a": "b"}}],
            },
        }
        _ = inject_status_filter(original, "include_suppressed")
        assert original == {
            "bool": {
                "must": [{"term": {"x": "y"}}],
                "filter": [{"term": {"a": "b"}}],
            },
        }

    def test_include_suppressed_on_leaf(self) -> None:
        leaf = {"term": {"identifier": "PRJDB1234"}}
        result = inject_status_filter(leaf, "include_suppressed")
        assert result["bool"]["filter"] == [
            {"terms": {"status": ["public", "suppressed"]}},
        ]

    def test_filter_as_dict_promoted_to_list(self) -> None:
        """ES では filter を単一 dict で書ける legal フォーマット。list に正規化される。"""
        bool_query = {
            "bool": {
                "must": [{"term": {"x": "y"}}],
                "filter": {"term": {"a": "b"}},
            },
        }
        result = inject_status_filter(bool_query, "public_only")
        assert result["bool"]["filter"] == [
            {"term": {"status": "public"}},
            {"term": {"a": "b"}},
        ]


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

    @given(text=alphanumeric_no_trigger(ES_AUTO_PHRASE_CHARS))
    def test_alphanumeric_without_trigger_is_not_phrase(self, text: str) -> None:
        """Any alphanumeric text without trigger chars → is_phrase=False."""
        assert _parse_keywords(text) == [(text, False)]

    @given(text=text_with_trigger(ES_AUTO_PHRASE_CHARS))
    def test_text_containing_trigger_is_phrase(self, text: str) -> None:
        """Any text containing a trigger char → is_phrase=True."""
        result = _parse_keywords(text)
        assert len(result) == 1
        returned_text, is_phrase = result[0]
        assert returned_text == text
        assert is_phrase is True

    @given(text=alphanumeric_no_trigger(ES_AUTO_PHRASE_CHARS))
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
        # bare word → should-wrapper: AND multi_match (type なし) と phrase_prefix.
        should = must[0]["bool"]["should"]
        and_mm, prefix_mm = should[0]["multi_match"], should[1]["multi_match"]
        assert and_mm["query"] == "cancer"
        assert "type" not in and_mm
        assert prefix_mm["query"] == "cancer"
        assert prefix_mm["type"] == "phrase_prefix"

    def test_mixed_phrase_and_normal(self) -> None:
        result = build_search_query(keywords='"RNA-Seq",cancer')
        must = result["bool"]["must"]
        assert len(must) == 2
        # First is a phrase (quoted) → 単一 multi_match のまま、前方一致しない.
        assert must[0]["multi_match"]["type"] == "phrase"
        assert must[0]["multi_match"]["query"] == "RNA-Seq"
        # Second is a bare word → should-wrapper の AND multi_match と phrase_prefix.
        should = must[1]["bool"]["should"]
        assert "type" not in should[0]["multi_match"]
        assert should[0]["multi_match"]["query"] == "cancer"
        assert should[1]["multi_match"]["type"] == "phrase_prefix"

    def test_phrase_with_or_operator(self) -> None:
        result = build_search_query(
            keywords='"RNA-Seq",cancer',
            keyword_operator="OR",
        )
        should = result["bool"]["should"]
        assert len(should) == 2
        # quoted token は単一 phrase multi_match のまま.
        assert should[0]["multi_match"]["type"] == "phrase"
        # bare word は should-wrapper として nest される.
        inner = should[1]["bool"]["should"]
        assert "type" not in inner[0]["multi_match"]
        assert inner[1]["multi_match"]["type"] == "phrase_prefix"


class TestBuildSearchQueryAutoPhrase:
    """Integration: auto-phrased tokens produce multi_match(type=phrase)."""

    def test_hyphen_keyword_uses_phrase(self) -> None:
        result = build_search_query(keywords="HIF-1")
        mm = result["bool"]["must"][0]["multi_match"]
        assert mm["query"] == "HIF-1"
        assert mm["type"] == "phrase"

    def test_plain_keyword_omits_type(self) -> None:
        result = build_search_query(keywords="cancer")
        # bare word → should-wrapper: AND multi_match (type なし) と phrase_prefix.
        should = result["bool"]["must"][0]["bool"]["should"]
        and_mm, prefix_mm = should[0]["multi_match"], should[1]["multi_match"]
        assert and_mm["query"] == "cancer"
        assert "type" not in and_mm
        assert prefix_mm["type"] == "phrase_prefix"

    def test_mixed_auto_and_normal_tokens(self) -> None:
        result = build_search_query(keywords="HIF-1,cancer")
        must = result["bool"]["must"]
        assert len(must) == 2
        # 記号含み HIF-1 は phrase のまま (前方一致しない).
        assert must[0]["multi_match"]["type"] == "phrase"
        assert must[0]["multi_match"]["query"] == "HIF-1"
        # bare word cancer は should-wrapper の AND multi_match と phrase_prefix.
        should = must[1]["bool"]["should"]
        assert "type" not in should[0]["multi_match"]
        assert should[0]["multi_match"]["query"] == "cancer"
        assert should[1]["multi_match"]["type"] == "phrase_prefix"

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
        # 記号含み HIF-1 は単一 phrase multi_match のまま.
        assert should[0]["multi_match"]["type"] == "phrase"
        # bare word cancer は should-wrapper として nest される.
        inner = should[1]["bool"]["should"]
        assert "type" not in inner[0]["multi_match"]
        assert inner[1]["multi_match"]["type"] == "phrase_prefix"

    def test_auto_phrase_honors_keyword_fields(self) -> None:
        result = build_search_query(
            keywords="HIF-1",
            keyword_fields="title,description",
        )
        mm = result["bool"]["must"][0]["multi_match"]
        assert mm["type"] == "phrase"
        assert set(mm["fields"]) == {"title", "description"}


# ===================================================================
# nested filters: organization / publication / grant / externalLinkLabel
#
# converter の ES mapping は nested 型で定義している. query は
# nested wrapper を生成する必要があり、flat field に変えるとマッチしなくなる.
# 統一仕様 (api-spec.md § 検索 query parameter のセマンティクス共通ルール):
#   値内空白 = AND, カンマ = OR, クオート = phrase, 記号 = auto-phrase.
# ===================================================================


@pytest.mark.parametrize(
    ("kwarg", "path", "sub_field"),
    [
        ("organization", "organization", "organization.name"),
        ("publication", "publication", "publication.title"),
        ("grant", "grant", "grant.title"),
        ("external_link_label", "externalLink", "externalLink.label"),
    ],
)
class TestBuildSearchQueryNestedTextParams:
    """nested 4 text param が _build_text_match_clause + nested wrapper で組まれる."""

    def test_single_token_uses_match_with_operator_and(
        self,
        kwarg: str,
        path: str,
        sub_field: str,
    ) -> None:
        # 値内空白あり (記号なし) → match + operator=and.
        result = build_search_query(**{kwarg: "foo bar"})  # type: ignore[arg-type]
        nested = _find_nested_filter(result["bool"]["filter"], path)
        assert nested["nested"]["query"] == {
            "match": {sub_field: {"query": "foo bar", "operator": "and"}},
        }

    def test_quoted_value_uses_match_phrase(
        self,
        kwarg: str,
        path: str,
        sub_field: str,
    ) -> None:
        result = build_search_query(**{kwarg: '"foo bar"'})  # type: ignore[arg-type]
        nested = _find_nested_filter(result["bool"]["filter"], path)
        assert nested["nested"]["query"] == {"match_phrase": {sub_field: "foo bar"}}

    def test_symbol_value_auto_phrases(
        self,
        kwarg: str,
        path: str,
        sub_field: str,
    ) -> None:
        # 記号 (-/.+:) 含み → 自動 phrase 化.
        result = build_search_query(**{kwarg: "HIF-1"})  # type: ignore[arg-type]
        nested = _find_nested_filter(result["bool"]["filter"], path)
        assert nested["nested"]["query"] == {"match_phrase": {sub_field: "HIF-1"}}

    def test_comma_separated_values_use_should(
        self,
        kwarg: str,
        path: str,
        sub_field: str,
    ) -> None:
        # カンマ区切り → bool.should + minimum_should_match=1.
        result = build_search_query(**{kwarg: "alpha,beta"})  # type: ignore[arg-type]
        nested = _find_nested_filter(result["bool"]["filter"], path)
        inner = nested["nested"]["query"]
        assert "bool" in inner
        assert inner["bool"]["minimum_should_match"] == 1
        assert len(inner["bool"]["should"]) == 2

    def test_keyword_operator_or_does_not_affect_inner_match(
        self,
        kwarg: str,
        path: str,
        sub_field: str,
    ) -> None:
        # keyword_operator は keywords (multi_match) のカンマ区切り token 間 operator
        # にのみ影響し、nested 4 text param の inner match.operator は常に "and"
        # (値内空白 = AND 固定、api-spec.md § セマンティクス共通ルール).
        result = build_search_query(keyword_operator="OR", **{kwarg: "foo bar"})  # type: ignore[arg-type]
        nested = _find_nested_filter(result["bool"]["filter"], path)
        assert nested["nested"]["query"] == {
            "match": {sub_field: {"query": "foo bar", "operator": "and"}},
        }

    def test_nested_clause_sets_ignore_unmapped(
        self,
        kwarg: str,
        path: str,
        sub_field: str,
    ) -> None:
        # 対応 nested path を持たない index (cross-type alias・型グループ内の非実在
        # subtype) で shard exception を出さず 0 件化するため ignore_unmapped を立てる.
        result = build_search_query(**{kwarg: "foo"})  # type: ignore[arg-type]
        nested = _find_nested_filter(result["bool"]["filter"], path)
        assert nested["nested"]["ignore_unmapped"] is True


class TestBuildSearchQueryNestedTextParamsCombined:
    """nested 4 text param が同時指定で 4 nested clause を生成する."""

    def test_multiple_nested_filters_combined(self) -> None:
        result = build_search_query(
            organization="DDBJ",
            publication="Genomic variants",
            grant="JST CREST",
            external_link_label="GEO",
        )
        filters = result["bool"]["filter"]
        nested_paths = {c["nested"]["path"] for c in filters if "nested" in c}
        assert nested_paths == {"organization", "publication", "grant", "externalLink"}

    def test_no_nested_filter_when_params_absent(self) -> None:
        result = build_search_query(keywords="cancer")
        # keywords のみ → filter は status のみで nested を含まない.
        filters = result.get("bool", {}).get("filter", [])
        assert not any("nested" in c for c in filters)


# ===================================================================
# nested filter: derivedFromId (keyword field、accession 完全一致)
# ===================================================================


class TestBuildSearchQueryDerivedFromId:
    """derivedFromId は _build_term_clause を nested wrapper で包む.

    derivedFrom.identifier は keyword field なので analyzer 不要.
    単一値 → term、複数値 → terms (OR).
    """

    def test_single_value_uses_term(self) -> None:
        result = build_search_query(derived_from_id="SAMD00012345")
        nested = _find_nested_filter(result["bool"]["filter"], "derivedFrom")
        assert nested["nested"]["query"] == {
            "term": {"derivedFrom.identifier": "SAMD00012345"},
        }

    def test_nested_clause_sets_ignore_unmapped(self) -> None:
        result = build_search_query(derived_from_id="SAMD00012345")
        nested = _find_nested_filter(result["bool"]["filter"], "derivedFrom")
        assert nested["nested"]["ignore_unmapped"] is True

    def test_multiple_values_use_terms(self) -> None:
        result = build_search_query(derived_from_id="SAMD00012345,SAMD00067890")
        nested = _find_nested_filter(result["bool"]["filter"], "derivedFrom")
        terms_clause = nested["nested"]["query"]["terms"]
        assert terms_clause["derivedFrom.identifier"] == ["SAMD00012345", "SAMD00067890"]

    def test_whitespace_around_values_stripped(self) -> None:
        result = build_search_query(derived_from_id=" SAMD00012345 , SAMD00067890 ")
        nested = _find_nested_filter(result["bool"]["filter"], "derivedFrom")
        assert nested["nested"]["query"]["terms"]["derivedFrom.identifier"] == [
            "SAMD00012345",
            "SAMD00067890",
        ]

    def test_all_five_nested_filters_combined(self) -> None:
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
            # relevance / model は keyword 単独 mapping なので suffix `.keyword` を付けない
            ("relevance", "relevance"),
            ("model", "model"),
            # package は object{name:keyword,displayName:keyword} で name サブフィールドへ解決
            ("package", "package.name"),
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

    @pytest.mark.parametrize(
        ("kwarg", "es_field", "single_value", "comma_values"),
        [
            ("relevance", "relevance", "Medical", "Medical,ModelOrganism"),
            ("package", "package.name", "MIGS.ba", "MIGS.ba,MIMS.me"),
            ("model", "model", "Generic.1.0", "Generic.1.0,Plant.1.0"),
        ],
    )
    def test_facet_backed_term_filter_or(
        self,
        kwarg: str,
        es_field: str,
        single_value: str,
        comma_values: str,
    ) -> None:
        single = build_search_query(**{kwarg: single_value})  # type: ignore[arg-type]
        f_single = _find_filter(single["bool"]["filter"], "term", es_field)
        assert f_single["term"][es_field] == single_value
        multi = build_search_query(**{kwarg: comma_values})  # type: ignore[arg-type]
        f_multi = _find_filter(multi["bool"]["filter"], "terms", es_field)
        assert set(f_multi["terms"][es_field]) == set(comma_values.split(","))
        assert "term" not in {k for c in multi["bool"]["filter"] for k in c if es_field in c.get(k, {})}

    @pytest.mark.parametrize("kwarg", ["relevance", "package", "model"])
    def test_facet_backed_term_filter_empty_skips(self, kwarg: str) -> None:
        result = build_search_query(**{kwarg: ""})  # type: ignore[arg-type]
        clauses = result["bool"].get("filter", [])
        for es_field in ("relevance", "package.name", "model"):
            assert not any(es_field in c.get("term", {}) for c in clauses)
            assert not any(es_field in c.get("terms", {}) for c in clauses)


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

    def test_text_match_operator_is_and_regardless_of_keyword_operator(self) -> None:
        # keyword_operator は keywords (multi_match) のカンマ区切り token 間
        # operator にのみ影響し、text match の値内空白 operator は常に "and"
        # (api-spec.md § セマンティクス共通ルール).
        result = build_search_query(host="Homo sapiens", keyword_operator="OR")
        clause = _find_text_match_clause(result["bool"]["filter"], "host")
        assert clause is not None
        assert clause["match"]["host"]["operator"] == "and"

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


# === db-portal facet scope allowlist (db → ES subtype 展開) ===


class TestDbPortalEsFacetAllowlist:
    """db_portal_es_facet_allowlist: db-portal の db 値 → 許容 ES facet 集合."""

    def test_cross_is_common_plus_type(self) -> None:
        assert db_portal_es_facet_allowlist(None) == frozenset({"organism", "accessibility", "type"})

    @pytest.mark.parametrize(
        "db, expected",
        [
            ("bioproject", {"organism", "accessibility", "objectType", "relevance", "projectType"}),
            ("biosample", {"organism", "accessibility", "package", "model", "host"}),
            (
                "sra",
                {
                    "organism",
                    "accessibility",
                    "libraryStrategy",
                    "librarySource",
                    "librarySelection",
                    "platform",
                    "instrumentModel",
                    "libraryLayout",
                    "analysisType",
                    # sra は複数 subtype を跨ぐので type facet で subtype 別集計を許す
                    "type",
                },
            ),
            ("jga", {"organism", "accessibility", "studyType", "datasetType", "vendor", "type"}),
            ("gea", {"organism", "accessibility", "experimentType"}),
            ("metabobank", {"organism", "accessibility", "experimentType", "studyType", "submissionType"}),
        ],
    )
    def test_single_db_exact_set(self, db: str, expected: set[str]) -> None:
        assert db_portal_es_facet_allowlist(db) == frozenset(expected)

    def test_type_in_sra_jga_only_among_single_dbs(self) -> None:
        # ``type`` facet は複数 subtype を跨ぐ per-db scope sra / jga にだけ開く。
        # 単一 subtype の db (bioproject / biosample / gea / metabobank) では
        # subtype 分解の意味が無いので従来どおり非許可 (400)。
        assert "type" in db_portal_es_facet_allowlist("sra")
        assert "type" in db_portal_es_facet_allowlist("jga")
        for db in ("bioproject", "biosample", "gea", "metabobank"):
            assert "type" not in db_portal_es_facet_allowlist(db)

    def test_common_always_present(self) -> None:
        for db in [None, *_DB_PORTAL_ES_SUBTYPES]:
            assert db_portal_es_facet_allowlist(db) >= _COMMON_FACET_NAMES

    def test_solr_db_value_raises_keyerror(self) -> None:
        # trad / taxonomy must be routed to the Solr facet scope; reaching
        # the ES allowlist with them is a programming error, not a 0-facet
        # silent fallthrough.
        for solr_db in ("trad", "taxonomy"):
            with pytest.raises(KeyError):
                db_portal_es_facet_allowlist(solr_db)

    def test_derivation_matches_type_specific_scope(self) -> None:
        """Property: a type-specific facet is in db's allowlist iff its scope
        intersects db's subtypes (the allowlist is derived, not hardcoded)."""
        for db, subtypes in _DB_PORTAL_ES_SUBTYPES.items():
            allowed = db_portal_es_facet_allowlist(db)
            for facet, scope in _TYPE_SPECIFIC_FACET_SCOPE.items():
                assert (facet in allowed) == bool(scope & subtypes)

    def test_cross_excludes_type_specific(self) -> None:
        cross = db_portal_es_facet_allowlist(None)
        # ``type`` is intentionally a member of both the cross-only set and
        # the type-specific scope; every *other* type-specific facet stays
        # out of the cross allowlist.
        for facet in _TYPE_SPECIFIC_FACET_SCOPE:
            if facet == "type":
                continue
            assert facet not in cross

    def test_cross_type_only_member_present_for_cross(self) -> None:
        assert db_portal_es_facet_allowlist(None) >= _CROSS_TYPE_ONLY_FACET_NAMES


# ===================================================================
# build_self_excluding_facet_aggs (facet self-exclusion)
# ===================================================================


def _single_ast(q: str) -> Any:
    ast = parse(q)
    validate(ast, mode="single")
    return ast


def _term_fields(node: Any) -> list[str]:
    """Collect every ``term`` clause field name anywhere in an ES query dict."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "term" and isinstance(value, dict):
                found.extend(value.keys())
            else:
                found.extend(_term_fields(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_term_fields(item))
    return found


class TestBuildSelfExcludingFacetAggs:
    """build_self_excluding_facet_aggs: each facet's terms agg is wrapped in a
    ``filter`` aggregation whose population drops that facet's own clause but
    keeps the other clauses + status filter (docs § 集計母集団と self-exclusion).
    """

    def test_each_facet_wrapped_in_filter_with_inner_terms(self) -> None:
        ast = _single_ast("organism_id:9606 AND package:foo")
        aggs = build_self_excluding_facet_aggs(
            ast=ast,
            status_mode="public_only",
            requested_facets=["organism", "package"],
            size=100,
        )
        assert set(aggs) == {"organism", "package"}
        for name in ("organism", "package"):
            assert "filter" in aggs[name]
            # The inner terms aggregation keeps the facet's own name so
            # _unwrap_terms_agg can normalise both shapes.
            assert name in aggs[name]["aggs"]
            assert "terms" in aggs[name]["aggs"][name]

    def test_facet_population_excludes_own_clause_keeps_others(self) -> None:
        ast = _single_ast("organism_id:9606 AND package:foo")
        aggs = build_self_excluding_facet_aggs(
            ast=ast,
            status_mode="public_only",
            requested_facets=["organism", "package"],
            size=100,
        )
        organism_filter = aggs["organism"]["filter"]
        package_filter = aggs["package"]["filter"]
        # organism facet drops its own organism.identifier clause but keeps package.
        assert "organism.identifier" not in _term_fields(organism_filter)
        assert "package.name" in _term_fields(organism_filter)
        # package facet drops its own package.name clause but keeps organism.
        assert "package.name" not in _term_fields(package_filter)
        assert "organism.identifier" in _term_fields(package_filter)

    def test_status_filter_applied_to_every_facet(self) -> None:
        ast = _single_ast("organism_id:9606 AND package:foo")
        aggs = build_self_excluding_facet_aggs(
            ast=ast,
            status_mode="public_only",
            requested_facets=["organism", "package"],
            size=100,
        )
        # status is injected independently of the DSL q, so it survives
        # self-exclusion for every facet.
        assert "status" in _term_fields(aggs["organism"]["filter"])
        assert "status" in _term_fields(aggs["package"]["filter"])

    def test_inner_terms_size_flows_and_organism_label_fixed(self) -> None:
        ast = _single_ast("package:foo")
        aggs = build_self_excluding_facet_aggs(
            ast=ast,
            status_mode="public_only",
            requested_facets=["organism", "package"],
            size=7,
        )
        assert aggs["package"]["aggs"]["package"]["terms"]["size"] == 7
        assert aggs["organism"]["aggs"]["organism"]["terms"]["size"] == 7
        # organism's label sub-aggregation stays size 1 regardless of facetsSize.
        assert aggs["organism"]["aggs"]["organism"]["aggs"]["name"]["terms"]["size"] == 1

    def test_only_own_clause_yields_status_only_population(self) -> None:
        """q=organism_id:9606 単独で organism を集計すると、母集団は status のみ
        (全 organism が候補に残る self-exclusion の核心ケース)。"""
        ast = _single_ast("organism_id:9606")
        aggs = build_self_excluding_facet_aggs(
            ast=ast,
            status_mode="public_only",
            requested_facets=["organism"],
            size=100,
        )
        fields = _term_fields(aggs["organism"]["filter"])
        assert "organism.identifier" not in fields
        assert fields == ["status"]

    def test_or_multiselect_fully_excluded(self) -> None:
        """organism_id:9606 OR organism_id:10090 を organism 集計から外すと、
        母集団から organism 句が完全に消える (OR 全体が除外される)。"""
        ast = _single_ast("organism_id:9606 OR organism_id:10090")
        aggs = build_self_excluding_facet_aggs(
            ast=ast,
            status_mode="public_only",
            requested_facets=["organism"],
            size=100,
        )
        assert "organism.identifier" not in _term_fields(aggs["organism"]["filter"])

    def test_ast_none_population_is_status_only(self) -> None:
        aggs = build_self_excluding_facet_aggs(
            ast=None,
            status_mode="public_only",
            requested_facets=["organism", "accessibility"],
            size=100,
        )
        for name in ("organism", "accessibility"):
            assert _term_fields(aggs[name]["filter"]) == ["status"]

    def test_cross_type_includes_type_facet(self) -> None:
        ast = _single_ast("organism_id:9606")
        aggs = build_self_excluding_facet_aggs(
            ast=ast,
            status_mode="public_only",
            is_cross_type=True,
            requested_facets=["organism", "type"],
            size=100,
        )
        assert set(aggs) == {"organism", "type"}
        assert aggs["type"]["aggs"]["type"]["terms"]["field"] == "type"


class TestBuildFacetBaseQuery:
    """build_facet_base_query: top-level query that drops EVERY requested
    facet's own clause (the population the filter aggs narrow back down)."""

    def test_drops_all_requested_facet_clauses(self) -> None:
        ast = _single_ast("organism_id:9606 AND package:foo")
        q = build_facet_base_query(ast, "public_only", requested_facets=["organism", "package"])
        # both facet clauses removed; only status survives.
        assert _term_fields(q) == ["status"]

    def test_keeps_non_requested_facet_clause(self) -> None:
        """Only the requested facets are excluded; a clause whose facet is not
        requested stays in the base (it is a fixed condition, not a facet)."""
        ast = _single_ast("organism_id:9606 AND package:foo")
        q = build_facet_base_query(ast, "public_only", requested_facets=["organism"])
        fields = _term_fields(q)
        assert "organism.identifier" not in fields
        assert "package.name" in fields
        assert "status" in fields

    def test_keeps_free_text_and_non_facet_fields(self) -> None:
        ast = _single_ast("cancer AND organism_id:9606")
        q = build_facet_base_query(ast, "public_only", requested_facets=["organism"])
        # organism dropped, but the free-text part of the query remains.
        assert "organism.identifier" not in _term_fields(q)
        assert q != build_search_query(keywords=None, keyword_operator="AND", status_mode="public_only")

    def test_none_ast_is_status_only(self) -> None:
        q = build_facet_base_query(None, "public_only", requested_facets=["organism"])
        assert _term_fields(q) == ["status"]

    def test_no_requested_facets_returns_full_query(self) -> None:
        ast = _single_ast("organism_id:9606")
        base = build_facet_base_query(ast, "public_only", requested_facets=[])
        full = build_facet_base_query(ast, "public_only", requested_facets=None)
        # Nothing excluded → identical to compiling the full query.
        assert "organism.identifier" in _term_fields(base)
        assert base == full
