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

from __future__ import annotations

import pytest
from fastapi import HTTPException
from hypothesis import given

from ddbj_search_api.schemas.queries import (
    BioProjectExtraQuery,
    BioSampleExtraQuery,
    BulkFormat,
    BulkQuery,
    DbXrefsLimitQuery,
    EntryDetailQuery,
    FacetsParamQuery,
    GeaExtraQuery,
    JgaExtraQuery,
    KeywordOperator,
    MetaboBankExtraQuery,
    PaginationQuery,
    ResponseControlQuery,
    SearchFilterQuery,
    SraExtraQuery,
    TypesFilterQuery,
)
from tests.unit.strategies import valid_page, valid_per_page

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

    @given(page=valid_page, per_page=valid_per_page)
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
            organization=None,
            publication=None,
            grant=None,
            date_published_from=None,
            date_published_to=None,
            date_modified_from=None,
            date_modified_to=None,
        )
        assert q.keywords is None
        assert q.keyword_fields is None
        assert q.keyword_operator == KeywordOperator.AND
        assert q.organism is None
        assert q.organization is None
        assert q.publication is None
        assert q.grant is None

    def test_stores_custom_values(self) -> None:
        q = SearchFilterQuery(
            keywords="cancer,human",
            keyword_fields="title,description",
            keyword_operator=KeywordOperator.OR,
            organism="9606",
            organization=None,
            publication=None,
            grant=None,
            date_published_from="2024-01-01",
            date_published_to="2024-12-31",
            date_modified_from="2024-06-01",
            date_modified_to="2024-06-30",
        )
        assert q.keywords == "cancer,human"
        assert q.keyword_fields == "title,description"
        assert q.keyword_operator == KeywordOperator.OR
        assert q.organism == "9606"
        assert q.organization is None
        assert q.publication is None
        assert q.grant is None
        assert q.date_published_from == "2024-01-01"
        assert q.date_published_to == "2024-12-31"
        assert q.date_modified_from == "2024-06-01"
        assert q.date_modified_to == "2024-06-30"

    def test_stores_nested_filter_values(self) -> None:
        q = SearchFilterQuery(
            keywords=None,
            keyword_fields=None,
            keyword_operator=KeywordOperator.AND,
            organism=None,
            organization="DDBJ",
            publication="Genomic variants",
            grant="JST CREST",
            date_published_from=None,
            date_published_to=None,
            date_modified_from=None,
            date_modified_to=None,
        )
        assert q.organization == "DDBJ"
        assert q.publication == "Genomic variants"
        assert q.grant == "JST CREST"


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


# === TypesFilterQuery ===


class TestTypesFilterQuery:
    """TypesFilterQuery: types parameter."""

    def test_stores_none(self) -> None:
        q = TypesFilterQuery(types=None)
        assert q.types is None

    def test_stores_value(self) -> None:
        q = TypesFilterQuery(types="bioproject,biosample")
        assert q.types == "bioproject,biosample"


# === DbXrefsLimitQuery ===


class TestDbXrefsLimitQuery:
    """DbXrefsLimitQuery: dbXrefsLimit parameter."""

    def test_stores_default_value(self) -> None:
        q = DbXrefsLimitQuery(db_xrefs_limit=100)
        assert q.db_xrefs_limit == 100

    def test_stores_custom_value(self) -> None:
        q = DbXrefsLimitQuery(db_xrefs_limit=500)
        assert q.db_xrefs_limit == 500

    def test_stores_zero(self) -> None:
        q = DbXrefsLimitQuery(db_xrefs_limit=0)
        assert q.db_xrefs_limit == 0

    def test_stores_max(self) -> None:
        q = DbXrefsLimitQuery(db_xrefs_limit=1000)
        assert q.db_xrefs_limit == 1000


# === BioProjectExtraQuery ===


class TestBioProjectExtraQuery:
    """BioProjectExtraQuery: bioproject-specific filters.

    organization / publication / grant moved out of this class into
    SearchFilterQuery: they are now common across all type-specific
    endpoints and the cross-type endpoint.
    """

    def test_stores_none_values(self) -> None:
        q = BioProjectExtraQuery(
            object_types=None,
            external_link_label=None,
            project_type=None,
        )
        assert q.object_types is None
        assert q.external_link_label is None
        assert q.project_type is None

    @pytest.mark.parametrize(
        "value",
        [
            "BioProject",
            "UmbrellaBioProject",
            "BioProject,UmbrellaBioProject",
            "UmbrellaBioProject,BioProject",
        ],
    )
    def test_stores_object_types(self, value: str) -> None:
        q = BioProjectExtraQuery(
            object_types=value,
            external_link_label="GEO",
            project_type="genome sequencing",
        )
        assert q.object_types == value
        assert q.external_link_label == "GEO"
        assert q.project_type == "genome sequencing"


class TestBioSampleExtraQuery:
    """BioSampleExtraQuery: nested + text-match filters scoped to biosample."""

    def test_stores_none_values(self) -> None:
        q = BioSampleExtraQuery(
            derived_from_id=None,
            host=None,
            strain=None,
            isolate=None,
            geo_loc_name=None,
            collection_date=None,
        )
        assert q.derived_from_id is None
        assert q.host is None
        assert q.strain is None
        assert q.isolate is None
        assert q.geo_loc_name is None
        assert q.collection_date is None

    def test_stores_custom_values(self) -> None:
        q = BioSampleExtraQuery(
            derived_from_id="SAMD00012345",
            host="Homo sapiens",
            strain="K12",
            isolate="patient-1",
            geo_loc_name="Japan",
            collection_date="2020-05-01",
        )
        assert q.derived_from_id == "SAMD00012345"
        assert q.host == "Homo sapiens"
        assert q.strain == "K12"
        assert q.isolate == "patient-1"
        assert q.geo_loc_name == "Japan"
        assert q.collection_date == "2020-05-01"


class TestSraExtraQuery:
    """SraExtraQuery: shared across all sra-* endpoints.

    All twelve parameters are required at SRA-* endpoints; values not
    relevant to the selected sra-* type yield no hits naturally on the
    Elasticsearch side.
    """

    def test_stores_none_values(self) -> None:
        q = SraExtraQuery(
            library_strategy=None,
            library_source=None,
            library_selection=None,
            platform=None,
            instrument_model=None,
            library_layout=None,
            analysis_type=None,
            derived_from_id=None,
            library_name=None,
            library_construction_protocol=None,
            geo_loc_name=None,
            collection_date=None,
        )
        for attr in (
            "library_strategy",
            "library_source",
            "library_selection",
            "platform",
            "instrument_model",
            "library_layout",
            "analysis_type",
            "derived_from_id",
            "library_name",
            "library_construction_protocol",
            "geo_loc_name",
            "collection_date",
        ):
            assert getattr(q, attr) is None

    def test_stores_custom_values(self) -> None:
        q = SraExtraQuery(
            library_strategy="WGS",
            library_source="GENOMIC",
            library_selection="RANDOM",
            platform="ILLUMINA",
            instrument_model="HiSeq X Ten",
            library_layout="PAIRED",
            analysis_type="ALIGNMENT",
            derived_from_id="SAMD00012345",
            library_name="my_lib",
            library_construction_protocol="PCR-free",
            geo_loc_name="Japan",
            collection_date="2020-05-01",
        )
        assert q.library_strategy == "WGS"
        assert q.library_source == "GENOMIC"
        assert q.library_selection == "RANDOM"
        assert q.platform == "ILLUMINA"
        assert q.instrument_model == "HiSeq X Ten"
        assert q.library_layout == "PAIRED"
        assert q.analysis_type == "ALIGNMENT"
        assert q.derived_from_id == "SAMD00012345"
        assert q.library_name == "my_lib"
        assert q.library_construction_protocol == "PCR-free"
        assert q.geo_loc_name == "Japan"
        assert q.collection_date == "2020-05-01"


class TestJgaExtraQuery:
    """JgaExtraQuery: shared across all jga-* endpoints."""

    def test_stores_none_values(self) -> None:
        q = JgaExtraQuery(
            study_type=None,
            dataset_type=None,
            external_link_label=None,
            vendor=None,
        )
        assert q.study_type is None
        assert q.dataset_type is None
        assert q.external_link_label is None
        assert q.vendor is None

    def test_stores_custom_values(self) -> None:
        q = JgaExtraQuery(
            study_type="Tumor profiling",
            dataset_type="Whole-genome sequencing",
            external_link_label="dbGaP",
            vendor="Illumina",
        )
        assert q.study_type == "Tumor profiling"
        assert q.dataset_type == "Whole-genome sequencing"
        assert q.external_link_label == "dbGaP"
        assert q.vendor == "Illumina"


class TestGeaExtraQuery:
    """GeaExtraQuery: experimentType only."""

    def test_stores_none_values(self) -> None:
        q = GeaExtraQuery(experiment_type=None)
        assert q.experiment_type is None

    def test_stores_custom_value(self) -> None:
        q = GeaExtraQuery(experiment_type="RNA-Seq of coding RNA")
        assert q.experiment_type == "RNA-Seq of coding RNA"


class TestMetaboBankExtraQuery:
    """MetaboBankExtraQuery: study/experiment/submission types."""

    def test_stores_none_values(self) -> None:
        q = MetaboBankExtraQuery(
            study_type=None,
            experiment_type=None,
            submission_type=None,
        )
        assert q.study_type is None
        assert q.experiment_type is None
        assert q.submission_type is None

    def test_stores_custom_values(self) -> None:
        q = MetaboBankExtraQuery(
            study_type="metabolomic",
            experiment_type="LC-MS",
            submission_type="open",
        )
        assert q.study_type == "metabolomic"
        assert q.experiment_type == "LC-MS"
        assert q.submission_type == "open"


class TestFacetsParamQuery:
    """FacetsParamQuery: allowlist enforcement at the wire boundary."""

    def test_default_none_passthrough(self) -> None:
        q = FacetsParamQuery(facets=None)
        assert q.facets is None

    def test_empty_string_preserved(self) -> None:
        # docs/api-spec.md § ファセット集計対象の選択
        # facets="" → 集計 0 個
        q = FacetsParamQuery(facets="")
        assert q.facets == ""

    @pytest.mark.parametrize(
        "value",
        [
            "organism",
            "organism,accessibility",
            "objectType",
            "libraryStrategy,librarySource",
            "experimentType",
            "type",
            "type,objectType",
        ],
    )
    def test_valid_values_pass(self, value: str) -> None:
        q = FacetsParamQuery(facets=value)
        assert q.facets == value

    def test_whitespace_around_tokens_normalized(self) -> None:
        """Whitespace は strip され、再結合された正規形が attribute に
        格納される (downstream は再 split/strip する必要がない)。"""
        q = FacetsParamQuery(facets=" organism , accessibility ")
        assert q.facets == "organism,accessibility"

    def test_trailing_comma_normalized(self) -> None:
        """``organism,`` のように trailing comma で空トークンが含まれる
        場合は空要素を除外した正規形にする。"""
        q = FacetsParamQuery(facets="organism,")
        assert q.facets == "organism"

    @pytest.mark.parametrize(
        "value",
        [
            "totallyUnknown",
            "organism,fakeFacet",
        ],
    )
    def test_invalid_values_raise_422(self, value: str) -> None:
        with pytest.raises(HTTPException) as exc_info:
            FacetsParamQuery(facets=value)
        assert exc_info.value.status_code == 422

    def test_only_commas_raises_422(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            FacetsParamQuery(facets=",,,")
        assert exc_info.value.status_code == 422


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
