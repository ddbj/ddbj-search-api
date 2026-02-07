"""Tests for ddbj_search_api.schemas.queries.

Query classes are FastAPI Depends()-based, NOT Pydantic models.
Validation constraints (ge, le, etc.) are enforced by FastAPI at the HTTP
level, so boundary-value validation is tested in router tests.

Direct instantiation without arguments stores Query() descriptor objects,
not resolved values.  Default-value tests therefore pass explicit arguments
matching the expected defaults.  True HTTP-level default behaviour is tested
in router tests via TestClient.

Here we test: enum values, attribute storage, and custom-value acceptance.
"""
import pytest
from hypothesis import given
from hypothesis import strategies as st

from ddbj_search_api.schemas.queries import (BioProjectExtraQuery,
                                             BulkFormat, BulkQuery,
                                             EntriesTypesQuery,
                                             EntryDetailQuery,
                                             KeywordOperator,
                                             PaginationQuery,
                                             ResponseControlQuery,
                                             SearchFilterQuery)


# === Enums ===


class TestKeywordOperator:
    """KeywordOperator enum: AND / OR."""

    def test_and(self) -> None:
        assert KeywordOperator("AND") == KeywordOperator.AND

    def test_or(self) -> None:
        assert KeywordOperator("OR") == KeywordOperator.OR

    def test_has_exactly_2_members(self) -> None:
        assert len(KeywordOperator) == 2

    def test_invalid_value_raises_error(self) -> None:
        with pytest.raises(ValueError):
            KeywordOperator("NOT")


class TestBulkFormat:
    """BulkFormat enum: json / ndjson."""

    def test_json(self) -> None:
        assert BulkFormat("json") == BulkFormat.json

    def test_ndjson(self) -> None:
        assert BulkFormat("ndjson") == BulkFormat.ndjson

    def test_has_exactly_2_members(self) -> None:
        assert len(BulkFormat) == 2

    def test_invalid_value_raises_error(self) -> None:
        with pytest.raises(ValueError):
            BulkFormat("csv")


# === PaginationQuery ===


class TestPaginationQuery:
    """PaginationQuery: attribute storage with explicit values."""

    def test_stores_page_and_per_page(self) -> None:
        q = PaginationQuery(page=1, per_page=10)
        assert q.page == 1
        assert q.per_page == 10

    def test_custom_values(self) -> None:
        q = PaginationQuery(page=5, per_page=50)
        assert q.page == 5
        assert q.per_page == 50


class TestPaginationQueryPBT:
    """Property-based tests for PaginationQuery attribute storage."""

    @given(
        page=st.integers(min_value=1, max_value=10000),
        per_page=st.integers(min_value=1, max_value=100),
    )
    def test_stores_values(self, page: int, per_page: int) -> None:
        q = PaginationQuery(page=page, per_page=per_page)
        assert q.page == page
        assert q.per_page == per_page


# === SearchFilterQuery ===


class TestSearchFilterQuery:
    """SearchFilterQuery: attribute storage."""

    def test_stores_none_values(self) -> None:
        q = SearchFilterQuery(
            keywords=None,
            keyword_fields=None,
            keyword_operator=KeywordOperator.AND,
            organism=None,
            date_published_from=None,
            date_published_to=None,
            date_updated_from=None,
            date_updated_to=None,
        )
        assert q.keywords is None
        assert q.keyword_fields is None
        assert q.keyword_operator == KeywordOperator.AND
        assert q.organism is None

    def test_stores_custom_values(self) -> None:
        q = SearchFilterQuery(
            keywords="cancer,human",
            keyword_fields="title,description",
            keyword_operator=KeywordOperator.OR,
            organism="9606",
            date_published_from="2024-01-01",
            date_published_to="2024-12-31",
            date_updated_from="2024-06-01",
            date_updated_to="2024-06-30",
        )
        assert q.keywords == "cancer,human"
        assert q.keyword_fields == "title,description"
        assert q.keyword_operator == KeywordOperator.OR
        assert q.organism == "9606"
        assert q.date_published_from == "2024-01-01"
        assert q.date_published_to == "2024-12-31"
        assert q.date_updated_from == "2024-06-01"
        assert q.date_updated_to == "2024-06-30"


# === ResponseControlQuery ===


class TestResponseControlQuery:
    """ResponseControlQuery: attribute storage."""

    def test_stores_default_equivalent_values(self) -> None:
        q = ResponseControlQuery(
            sort=None,
            fields=None,
            include_properties=True,
            include_facets=False,
        )
        assert q.sort is None
        assert q.fields is None
        assert q.include_properties is True
        assert q.include_facets is False

    def test_stores_custom_values(self) -> None:
        q = ResponseControlQuery(
            sort="datePublished:desc",
            fields="identifier,title",
            include_properties=False,
            include_facets=True,
        )
        assert q.sort == "datePublished:desc"
        assert q.fields == "identifier,title"
        assert q.include_properties is False
        assert q.include_facets is True


# === EntriesTypesQuery ===


class TestEntriesTypesQuery:
    """EntriesTypesQuery: types parameter."""

    def test_stores_none(self) -> None:
        q = EntriesTypesQuery(types=None)
        assert q.types is None

    def test_stores_value(self) -> None:
        q = EntriesTypesQuery(types="bioproject,biosample")
        assert q.types == "bioproject,biosample"


# === BioProjectExtraQuery ===


class TestBioProjectExtraQuery:
    """BioProjectExtraQuery: bioproject-specific filters."""

    def test_stores_none_values(self) -> None:
        q = BioProjectExtraQuery(
            organization=None,
            publication=None,
            grant=None,
            umbrella=None,
        )
        assert q.organization is None
        assert q.publication is None
        assert q.grant is None
        assert q.umbrella is None

    def test_stores_custom_values(self) -> None:
        q = BioProjectExtraQuery(
            organization="DDBJ",
            publication="nature",
            grant="JSPS",
            umbrella="TRUE",
        )
        assert q.organization == "DDBJ"
        assert q.publication == "nature"
        assert q.grant == "JSPS"
        assert q.umbrella == "TRUE"


# === EntryDetailQuery ===


class TestEntryDetailQuery:
    """EntryDetailQuery: dbXrefsLimit."""

    def test_stores_value(self) -> None:
        q = EntryDetailQuery(db_xrefs_limit=100)
        assert q.db_xrefs_limit == 100

    def test_stores_custom_value(self) -> None:
        q = EntryDetailQuery(db_xrefs_limit=500)
        assert q.db_xrefs_limit == 500

    def test_stores_zero(self) -> None:
        q = EntryDetailQuery(db_xrefs_limit=0)
        assert q.db_xrefs_limit == 0

    def test_stores_max(self) -> None:
        q = EntryDetailQuery(db_xrefs_limit=1000)
        assert q.db_xrefs_limit == 1000


# === BulkQuery ===


class TestBulkQuery:
    """BulkQuery: format parameter."""

    def test_stores_json_format(self) -> None:
        q = BulkQuery(format=BulkFormat.json)
        assert q.format == BulkFormat.json

    def test_stores_ndjson_format(self) -> None:
        q = BulkQuery(format=BulkFormat.ndjson)
        assert q.format == BulkFormat.ndjson
