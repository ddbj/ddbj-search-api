"""Tests for ddbj_search_api.schemas.db_portal.

Covers enum shape, DbPortalCrossSearchQuery / DbPortalSearchQuery attribute
storage & sort allowlist, Pydantic alias handling, and cursor round-trip
with db-portal payload shape.
"""

from __future__ import annotations

from typing import Any

import pydantic
import pytest
from fastapi import HTTPException
from hypothesis import given
from hypothesis import strategies as st

from ddbj_search_api.cursor import CursorPayload, decode_cursor, encode_cursor
from ddbj_search_api.schemas.common import FacetBucket, Facets
from ddbj_search_api.schemas.db_portal import (
    ALLOWED_DB_PORTAL_SORTS,
    DB_PORTAL_VALID_FACET_FIELDS,
    DbPortalCount,
    DbPortalCountError,
    DbPortalCrossSearchQuery,
    DbPortalCrossSearchResponse,
    DbPortalDb,
    DbPortalErrorType,
    DbPortalFacets,
    DbPortalHitBase,
    DbPortalHitBioProject,
    DbPortalHitsResponse,
    DbPortalLightweightHit,
    DbPortalSearchQuery,
    _DbPortalHitAdapter,
    _DbPortalLightweightHitAdapter,
)

# === Enum tests ===


class TestDbPortalDb:
    """DbPortalDb: 8 database identifiers."""

    def test_has_exactly_8_members(self) -> None:
        assert len(DbPortalDb) == 8

    def test_contains_all_expected_values(self) -> None:
        expected = {
            "ddbj",
            "sra",
            "bioproject",
            "biosample",
            "jga",
            "gea",
            "metabobank",
            "taxonomy",
        }
        assert {e.value for e in DbPortalDb} == expected

    @pytest.mark.parametrize(
        "value",
        ["ddbj", "sra", "bioproject", "biosample", "jga", "gea", "metabobank", "taxonomy"],
    )
    def test_accepts_value(self, value: str) -> None:
        assert DbPortalDb(value).value == value

    def test_rejects_unknown_value(self) -> None:
        with pytest.raises(ValueError):
            DbPortalDb("unknown")


class TestDbPortalCountError:
    """DbPortalCountError: 4 upstream error kinds + 1 per-arm signal (field_not_applicable)."""

    def test_has_exactly_5_members(self) -> None:
        assert len(DbPortalCountError) == 5

    def test_contains_all_expected_values(self) -> None:
        expected = {
            "timeout",
            "upstream_5xx",
            "connection_refused",
            "unknown",
            "field_not_applicable",
        }
        assert {e.value for e in DbPortalCountError} == expected


class TestDbPortalErrorType:
    """DbPortalErrorType: 4 routing/facets + 1 serialize body + 9 query parser slugs = 14 URIs."""

    def test_has_all_members(self) -> None:
        # routing 3 (cursor-not-supported, unexpected-parameter, missing-db)
        # + facets 1 (facet-not-applicable, scope-mismatch on the facets param)
        # + serialize body 1 (invalid-ast, POST /db-portal/serialize)
        # + query parser 10 (incl. invalid-freetext-position / duplicate-freetext / field-not-available-for-db)
        # = 15.
        assert len(DbPortalErrorType) == 15

    def test_invalid_ast_uri(self) -> None:
        assert DbPortalErrorType.invalid_ast.value == "https://ddbj.nig.ac.jp/problems/invalid-ast"

    def test_facet_not_applicable_uri(self) -> None:
        assert DbPortalErrorType.facet_not_applicable.value == "https://ddbj.nig.ac.jp/problems/facet-not-applicable"

    def test_prefix_is_ddbj_problems(self) -> None:
        prefix = "https://ddbj.nig.ac.jp/problems/"
        for e in DbPortalErrorType:
            assert e.value.startswith(prefix)

    def test_cursor_not_supported_uri(self) -> None:
        assert DbPortalErrorType.cursor_not_supported.value == "https://ddbj.nig.ac.jp/problems/cursor-not-supported"

    def test_unexpected_parameter_uri(self) -> None:
        assert DbPortalErrorType.unexpected_parameter.value == "https://ddbj.nig.ac.jp/problems/unexpected-parameter"

    def test_missing_db_uri(self) -> None:
        assert DbPortalErrorType.missing_db.value == "https://ddbj.nig.ac.jp/problems/missing-db"

    def test_query_parser_slugs_present(self) -> None:
        """Query parser 関連 9 slug の URI."""
        expected_slugs = {
            "unexpected-token",
            "unknown-field",
            "field-not-available-in-cross-db",
            "invalid-date-format",
            "invalid-operator-for-field",
            "nest-depth-exceeded",
            "missing-value",
            "invalid-freetext-position",
            "duplicate-freetext",
        }
        actual_slugs = {e.value.rsplit("/", 1)[-1] for e in DbPortalErrorType}
        assert expected_slugs <= actual_slugs


# === DbPortalCrossSearchQuery / DbPortalSearchQuery ===


def _search_query(**overrides: Any) -> DbPortalSearchQuery:
    """Shorthand for DbPortalSearchQuery with sensible defaults."""
    defaults: dict[str, Any] = {
        "q": None,
        "db": None,
        "page": 1,
        "per_page": 20,
        "cursor": None,
        "sort": None,
        "facets": None,
        "facets_size": None,
    }
    defaults.update(overrides)
    return DbPortalSearchQuery(**defaults)


def _cross_query(**overrides: Any) -> DbPortalCrossSearchQuery:
    """Shorthand for DbPortalCrossSearchQuery with sensible defaults."""
    defaults: dict[str, Any] = {
        "q": None,
        "top_hits": 10,
        "facets": None,
        "facets_size": None,
    }
    defaults.update(overrides)
    return DbPortalCrossSearchQuery(**defaults)


class TestDbPortalCrossSearchQuery:
    """DbPortalCrossSearchQuery: only q / topHits accepted at the schema layer.

    Other parameters (db / cursor / page / perPage / sort) are not part of
    the constructor; the router rejects them at runtime via
    ``_reject_unexpected_cross_params`` with 400 ``unexpected-parameter``.
    """

    def test_stores_defaults(self) -> None:
        q = _cross_query()
        assert q.q is None
        assert q.top_hits == 10

    def test_stores_q(self) -> None:
        q = _cross_query(q="cancer AND title:cancer")
        assert q.q == "cancer AND title:cancer"

    def test_stores_top_hits(self) -> None:
        q = _cross_query(top_hits=25)
        assert q.top_hits == 25

    def test_top_hits_zero_allowed(self) -> None:
        q = _cross_query(top_hits=0)
        assert q.top_hits == 0

    def test_constructor_rejects_db(self) -> None:
        with pytest.raises(TypeError):
            DbPortalCrossSearchQuery(q=None, top_hits=10, db=DbPortalDb.bioproject)  # type: ignore[call-arg]

    def test_constructor_rejects_cursor(self) -> None:
        with pytest.raises(TypeError):
            DbPortalCrossSearchQuery(q=None, top_hits=10, cursor="abc")  # type: ignore[call-arg]

    @pytest.mark.parametrize("kwarg, value", [("page", 5), ("per_page", 50), ("sort", "datePublished:desc")])
    def test_constructor_rejects_paging_and_sort(self, kwarg: str, value: Any) -> None:
        with pytest.raises(TypeError):
            DbPortalCrossSearchQuery(q=None, top_hits=10, **{kwarg: value})


class TestDbPortalSearchQuery:
    """DbPortalSearchQuery: attribute storage."""

    def test_stores_defaults(self) -> None:
        q = _search_query()
        assert q.q is None
        assert q.db is None
        assert q.page == 1
        assert q.per_page == 20
        assert q.cursor is None
        assert q.sort is None

    def test_stores_q(self) -> None:
        q = _search_query(q="cancer AND title:cancer")
        assert q.q == "cancer AND title:cancer"

    def test_stores_db(self) -> None:
        q = _search_query(db=DbPortalDb.bioproject)
        assert q.db == DbPortalDb.bioproject

    def test_stores_cursor(self) -> None:
        q = _search_query(cursor="abc.def")
        assert q.cursor == "abc.def"

    def test_stores_custom_page_and_per_page(self) -> None:
        q = _search_query(page=5, per_page=50)
        assert q.page == 5
        assert q.per_page == 50


class TestDbPortalSearchQuerySort:
    """DbPortalSearchQuery.sort allowlist validation."""

    def test_accepts_none(self) -> None:
        q = _search_query(sort=None)
        assert q.sort is None

    def test_accepts_date_published_desc(self) -> None:
        q = _search_query(sort="datePublished:desc")
        assert q.sort == "datePublished:desc"

    def test_accepts_date_published_asc(self) -> None:
        q = _search_query(sort="datePublished:asc")
        assert q.sort == "datePublished:asc"

    def test_rejects_date_modified(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            _search_query(sort="dateModified:desc")
        assert exc_info.value.status_code == 422

    def test_rejects_identifier_sort(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            _search_query(sort="identifier:asc")
        assert exc_info.value.status_code == 422

    def test_rejects_random_string(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            _search_query(sort="bogus")
        assert exc_info.value.status_code == 422

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            _search_query(sort="")
        assert exc_info.value.status_code == 422


class TestDbPortalSearchQueryPBT:
    """DbPortalSearchQuery PBT: sort allowlist."""

    @given(sort=st.sampled_from(sorted(ALLOWED_DB_PORTAL_SORTS)))
    def test_accepts_allowlisted_sort(self, sort: str) -> None:
        q = _search_query(sort=sort)
        assert q.sort == sort

    @given(
        sort=st.text(min_size=1, max_size=50).filter(
            lambda s: s not in ALLOWED_DB_PORTAL_SORTS,
        ),
    )
    def test_rejects_non_allowlisted_sort(self, sort: str) -> None:
        with pytest.raises(HTTPException) as exc_info:
            _search_query(sort=sort)
        assert exc_info.value.status_code == 422


# === DbPortalCount ===


class TestDbPortalCount:
    """DbPortalCount: single DB entry in cross-search response."""

    def test_success(self) -> None:
        c = DbPortalCount(db=DbPortalDb.bioproject, count=1234, error=None)
        assert c.count == 1234
        assert c.error is None

    def test_failure_timeout(self) -> None:
        c = DbPortalCount(
            db=DbPortalDb.sra,
            count=None,
            error=DbPortalCountError.timeout,
        )
        assert c.error == DbPortalCountError.timeout

    def test_hits_default_is_none(self) -> None:
        """count-only モードでは ``hits`` は ``None``。"""
        c = DbPortalCount(db=DbPortalDb.bioproject, count=10, error=None)
        assert c.hits is None

    def test_hits_can_be_empty_list(self) -> None:
        """per-DB error 時は ``hits=[]`` (topHits>=1 のとき)。"""
        c = DbPortalCount(
            db=DbPortalDb.sra,
            count=None,
            error=DbPortalCountError.timeout,
            hits=[],
        )
        assert c.hits == []

    def test_hits_can_carry_lightweight_hit(self) -> None:
        """通常時は ``hits`` に DbPortalLightweightHit を入れられる。"""
        hit = DbPortalLightweightHit(identifier="PRJDB1", type="bioproject")
        c = DbPortalCount(
            db=DbPortalDb.bioproject,
            count=1,
            error=None,
            hits=[hit],
        )
        assert c.hits is not None
        assert len(c.hits) == 1
        assert c.hits[0].identifier == "PRJDB1"

    def test_hits_serialize_with_alias_keys(self) -> None:
        """``hits`` 内の DbPortalLightweightHit は alias (camelCase) で serialize される。"""
        hit = _DbPortalLightweightHitAdapter.validate_python(
            {
                "identifier": "PRJDB1",
                "type": "bioproject",
                "datePublished": "2024-01-15",
                "isPartOf": "bioproject",
            },
        )
        c = DbPortalCount(db=DbPortalDb.bioproject, count=1, error=None, hits=[hit])
        dumped = c.model_dump(by_alias=True)
        assert dumped["hits"][0]["datePublished"] == "2024-01-15"
        assert dumped["hits"][0]["isPartOf"] == "bioproject"

    def test_hits_drop_db_specific_extras(self) -> None:
        """``DbPortalLightweightHit`` は ``extra="ignore"`` で db 拡張を drop する。"""
        hit = _DbPortalLightweightHitAdapter.validate_python(
            {
                "identifier": "PRJDB1",
                "type": "bioproject",
                "projectType": "BioProject",
                "objectType": "BioProject",
            },
        )
        dumped = hit.model_dump(by_alias=True)
        assert "projectType" not in dumped
        assert "objectType" not in dumped


# === DbPortalCrossSearchResponse ===


class TestDbPortalCrossSearchResponse:
    """DbPortalCrossSearchResponse: list of DB counts."""

    def test_eight_databases(self) -> None:
        resp = DbPortalCrossSearchResponse(databases=[DbPortalCount(db=db, count=100, error=None) for db in DbPortalDb])
        assert len(resp.databases) == 8

    def test_serialization_shape(self) -> None:
        resp = DbPortalCrossSearchResponse(
            databases=[
                DbPortalCount(db=DbPortalDb.sra, count=10, error=None),
                DbPortalCount(
                    db=DbPortalDb.ddbj,
                    count=None,
                    error=DbPortalCountError.timeout,
                ),
            ]
        )
        dumped = resp.model_dump()
        assert dumped["databases"][0]["db"] == "sra"
        assert dumped["databases"][0]["count"] == 10
        assert dumped["databases"][0]["error"] is None
        assert dumped["databases"][1]["error"] == "timeout"


# === DbPortalHit ===


class TestDbPortalHit:
    """DbPortalHit: discriminated union dispatch via TypeAdapter.

    ``extra="ignore"`` により、``DbPortalHitBase`` に明示 field として定義される
    status / accessibility などは pass-through される一方、未定義の field は
    silently drop される。
    """

    def test_minimal_variant_instantiation(self) -> None:
        # Union を直接インスタンス化はできないので、variant class を使う
        h = DbPortalHitBioProject(identifier="PRJDB1", type="bioproject")
        assert h.identifier == "PRJDB1"
        assert h.type == "bioproject"
        assert h.title is None

    def test_adapter_dispatches_to_bioproject(self) -> None:
        h = _DbPortalHitAdapter.validate_python(
            {"identifier": "PRJDB1", "type": "bioproject"},
        )
        assert isinstance(h, DbPortalHitBioProject)
        assert h.identifier == "PRJDB1"
        assert isinstance(h, DbPortalHitBase)

    def test_status_and_accessibility_preserved_as_explicit_fields(self) -> None:
        """status / accessibility は DbPortalHitBase の明示 field。"""
        h = _DbPortalHitAdapter.validate_python(
            {
                "identifier": "PRJDB1",
                "type": "bioproject",
                "status": "public",
                "accessibility": "public-access",
            },
        )
        dumped = h.model_dump(by_alias=True)
        assert dumped["status"] == "public"
        assert dumped["accessibility"] == "public-access"

    def test_unknown_field_silently_ignored(self) -> None:
        """extra="ignore" で converter 側の将来 field は silently drop。"""
        h = _DbPortalHitAdapter.validate_python(
            {
                "identifier": "PRJDB1",
                "type": "bioproject",
                "some_future_field": "value",
            },
        )
        dumped = h.model_dump(by_alias=True)
        assert "some_future_field" not in dumped

    def test_date_published_alias(self) -> None:
        h = _DbPortalHitAdapter.validate_python(
            {"identifier": "X", "type": "bioproject", "datePublished": "2024-01-15"},
        )
        assert h.date_published == "2024-01-15"
        dumped = h.model_dump(by_alias=True)
        assert dumped["datePublished"] == "2024-01-15"

    def test_is_part_of_alias_round_trip(self) -> None:
        """``isPartOf`` (camelCase) ⇄ ``is_part_of`` (snake)。"""
        h = _DbPortalHitAdapter.validate_python(
            {"identifier": "X", "type": "bioproject", "isPartOf": "bioproject"},
        )
        assert h.is_part_of == "bioproject"
        dumped = h.model_dump(by_alias=True)
        assert dumped["isPartOf"] == "bioproject"

    def test_is_part_of_default_none(self) -> None:
        h = _DbPortalHitAdapter.validate_python(
            {"identifier": "X", "type": "bioproject"},
        )
        assert h.is_part_of is None

    def test_same_as_and_db_xrefs_aliases(self) -> None:
        h = _DbPortalHitAdapter.validate_python(
            {
                "identifier": "X",
                "type": "bioproject",
                "sameAs": [{"identifier": "Y", "type": "bioproject", "url": "https://example.com/Y"}],
                "dbXrefs": [{"identifier": "Z", "type": "biosample", "url": "https://example.com/Z"}],
            },
        )
        assert h.same_as is not None
        assert h.same_as[0].identifier == "Y"
        assert h.same_as[0].type_ == "bioproject"
        assert h.db_xrefs is not None
        assert h.db_xrefs[0].identifier == "Z"
        assert h.db_xrefs[0].type_ == "biosample"
        # serialize 時は alias "type" に戻る
        dumped = h.model_dump(by_alias=True)
        assert dumped["sameAs"][0]["type"] == "bioproject"
        assert dumped["dbXrefs"][0]["type"] == "biosample"

    def test_missing_type_rejected(self) -> None:
        """discriminator strict — type 欠損は ValidationError。"""
        with pytest.raises(pydantic.ValidationError):
            _DbPortalHitAdapter.validate_python({"identifier": "X"})

    def test_unknown_type_rejected(self) -> None:
        """discriminator strict — 未知 type は ValidationError。"""
        with pytest.raises(pydantic.ValidationError):
            _DbPortalHitAdapter.validate_python({"identifier": "X", "type": "xxx-unknown"})


# === DbPortalHitsResponse ===


class TestDbPortalHitsResponse:
    """DbPortalHitsResponse: hits envelope + pagination."""

    def test_empty(self) -> None:
        resp = DbPortalHitsResponse(  # type: ignore[call-arg]
            total=0,
            hits=[],
            hard_limit_reached=False,
            page=1,
            per_page=20,
            next_cursor=None,
            has_next=False,
        )
        assert resp.total == 0
        assert resp.hits == []
        assert resp.hard_limit_reached is False
        assert resp.page == 1
        assert resp.per_page == 20

    def test_alias_serialization(self) -> None:
        resp = DbPortalHitsResponse(  # type: ignore[call-arg]
            total=5,
            hits=[],
            hard_limit_reached=True,
            page=1,
            per_page=50,
            next_cursor="abc",
            has_next=True,
        )
        dumped = resp.model_dump(by_alias=True)
        assert dumped["hardLimitReached"] is True
        assert dumped["perPage"] == 50
        assert dumped["nextCursor"] == "abc"
        assert dumped["hasNext"] is True

    def test_cursor_mode_page_null(self) -> None:
        resp = DbPortalHitsResponse(  # type: ignore[call-arg]
            total=100,
            hits=[],
            hard_limit_reached=False,
            page=None,
            per_page=20,
            next_cursor="c",
            has_next=True,
        )
        assert resp.page is None


# === Cursor round-trip with db-portal shape ===


class TestDbPortalCursorRoundTrip:
    """Cursor encode/decode preserves db-portal-style payloads."""

    def test_round_trip_with_db_portal_query(self) -> None:
        payload = CursorPayload(
            pit_id="pit-xyz",
            search_after=["2024-01-15", "PRJDB1234"],
            sort=[
                {"datePublished": {"order": "desc"}},
                {"identifier": {"order": "asc"}},
            ],
            query={
                "bool": {
                    "must": [
                        {"multi_match": {"query": "cancer", "fields": ["title", "description"]}},
                    ],
                },
            },
        )
        token = encode_cursor(payload)
        decoded = decode_cursor(token)
        assert decoded == payload

    @given(
        q=st.one_of(st.none(), st.text(min_size=1, max_size=30)),
        db=st.sampled_from([e.value for e in DbPortalDb]),
        sort_field=st.sampled_from(["datePublished", "_score"]),
        sort_order=st.sampled_from(["asc", "desc"]),
        pit_id=st.one_of(st.none(), st.text(min_size=1, max_size=50)),
    )
    def test_round_trip_property(
        self,
        q: str | None,
        db: str,
        sort_field: str,
        sort_order: str,
        pit_id: str | None,
    ) -> None:
        # db is used to simulate different search contexts; not
        # encoded in the cursor itself (router-level state).
        query_body: dict[str, Any] = (
            {"match_all": {}}
            if q is None
            else {
                "bool": {
                    "must": [
                        {"multi_match": {"query": q, "fields": ["identifier", "title"]}},
                    ],
                },
            }
        )
        sort_body = [
            {sort_field: {"order": sort_order}},
            {"identifier": {"order": "asc"}},
        ]
        payload = CursorPayload(
            pit_id=pit_id,
            search_after=["2024-01-15", f"{db}-DOC-0001"],
            sort=sort_body,
            query=query_body,
        )
        token = encode_cursor(payload)
        decoded = decode_cursor(token)
        assert decoded.pit_id == pit_id
        assert decoded.sort == sort_body
        assert decoded.query == query_body


# === DbPortalFacets (Facets 拡張) ===


class TestDbPortalFacets:
    """DbPortalFacets: Facets を継承し Solr 4 facet を追加."""

    def test_is_facets_subclass(self) -> None:
        assert issubclass(DbPortalFacets, Facets)

    def test_inherits_es_facet_fields(self) -> None:
        f = DbPortalFacets()
        # a few inherited ES facets default to None
        assert f.organism is None
        assert f.type is None
        assert f.accessibility is None
        assert f.object_type is None

    def test_solr_fields_default_none(self) -> None:
        f = DbPortalFacets()
        assert f.division is None
        assert f.molecular_type is None
        assert f.rank is None
        assert f.kingdom is None

    def test_molecular_type_alias_in_and_out(self) -> None:
        f = DbPortalFacets.model_validate({"molecularType": [{"value": "DNA", "count": 1}]})
        assert f.molecular_type == [FacetBucket(value="DNA", count=1)]
        dumped = f.model_dump(by_alias=True)
        assert "molecularType" in dumped
        assert "molecular_type" not in dumped

    def test_null_vs_empty_distinction(self) -> None:
        f = DbPortalFacets.model_validate({"division": []})
        assert f.division == []
        assert f.molecular_type is None

    def test_single_word_solr_fields_have_no_alias(self) -> None:
        f = DbPortalFacets.model_validate({"division": [{"value": "BCT", "count": 2}], "rank": [], "kingdom": []})
        dumped = f.model_dump(by_alias=True)
        assert dumped["division"] == [{"value": "BCT", "count": 2}]
        assert dumped["rank"] == []
        assert dumped["kingdom"] == []


class TestDbPortalFacetsAllowlist:
    """DB_PORTAL_VALID_FACET_FIELDS: wire-level facets allowlist (ES + Solr names)."""

    def test_includes_solr_facets(self) -> None:
        assert {"division", "molecularType", "rank", "kingdom"} <= DB_PORTAL_VALID_FACET_FIELDS

    def test_includes_common_es_facets(self) -> None:
        assert {"organism", "accessibility", "type", "objectType", "libraryStrategy"} <= DB_PORTAL_VALID_FACET_FIELDS


class TestDbPortalFacetsParam:
    """facets / facetsSize パラメタの正規化 (422 typo, scope は router)."""

    def test_none_passes_through(self) -> None:
        assert _cross_query(facets=None).facets is None
        assert _search_query(facets=None).facets is None

    def test_empty_string_passes_through(self) -> None:
        assert _cross_query(facets="").facets == ""

    def test_valid_names_normalized(self) -> None:
        assert _search_query(facets=" organism , objectType ").facets == "organism,objectType"

    def test_solr_name_is_wire_valid(self) -> None:
        # division is in the wire allowlist; scope (ddbj-only) is enforced in
        # the router, not here — so even a cross query stores it at this layer.
        assert _cross_query(facets="division").facets == "division"

    def test_unknown_name_422(self) -> None:
        with pytest.raises(HTTPException) as exc:
            _cross_query(facets="bogusFacet")
        assert exc.value.status_code == 422

    def test_partly_unknown_name_422(self) -> None:
        with pytest.raises(HTTPException) as exc:
            _search_query(facets="organism,bogus")
        assert exc.value.status_code == 422

    def test_facets_size_stored(self) -> None:
        assert _search_query(facets_size=50).facets_size == 50
        assert _cross_query(facets_size=7).facets_size == 7

    def test_facets_size_default_none(self) -> None:
        assert _search_query().facets_size is None


class TestDbPortalResponsesFacetField:
    """HitsResponse / CrossSearchResponse の facets フィールド (既定 null)."""

    def test_hits_response_facets_defaults_none(self) -> None:
        resp = DbPortalHitsResponse.model_validate(
            {"total": 0, "hits": [], "hardLimitReached": False, "page": 1, "perPage": 20},
        )
        assert resp.facets is None

    def test_hits_response_accepts_db_portal_facets(self) -> None:
        resp = DbPortalHitsResponse.model_validate(
            {
                "total": 1,
                "hits": [],
                "hardLimitReached": False,
                "page": 1,
                "perPage": 20,
                "facets": {"rank": [{"value": "species", "count": 1}]},
            },
        )
        assert resp.facets is not None
        assert resp.facets.rank == [FacetBucket(value="species", count=1)]

    def test_cross_response_facets_defaults_none(self) -> None:
        resp = DbPortalCrossSearchResponse.model_validate({"databases": []})
        assert resp.facets is None
