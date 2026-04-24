"""Tests for ddbj_search_api.schemas.db_portal (AP1).

Covers enum shape, DbPortalQuery attribute storage & sort allowlist,
Pydantic alias handling, and cursor round-trip with db-portal payload
shape.
"""

from __future__ import annotations

from typing import Any

import pydantic
import pytest
from fastapi import HTTPException
from hypothesis import given
from hypothesis import strategies as st

from ddbj_search_api.cursor import CursorPayload, decode_cursor, encode_cursor
from ddbj_search_api.schemas.db_portal import (
    ALLOWED_DB_PORTAL_SORTS,
    DbPortalCount,
    DbPortalCountError,
    DbPortalCrossSearchResponse,
    DbPortalDb,
    DbPortalErrorType,
    DbPortalHitBase,
    DbPortalHitBioProject,
    DbPortalHitsResponse,
    DbPortalQuery,
    _DbPortalHitAdapter,
)

# === Enum tests ===


class TestDbPortalDb:
    """DbPortalDb: 8 database identifiers."""

    def test_has_exactly_8_members(self) -> None:
        assert len(DbPortalDb) == 8

    def test_contains_all_expected_values(self) -> None:
        expected = {
            "trad",
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
        ["trad", "sra", "bioproject", "biosample", "jga", "gea", "metabobank", "taxonomy"],
    )
    def test_accepts_value(self, value: str) -> None:
        assert DbPortalDb(value).value == value

    def test_rejects_unknown_value(self) -> None:
        with pytest.raises(ValueError):
            DbPortalDb("unknown")


class TestDbPortalCountError:
    """DbPortalCountError: 4 error kinds."""

    def test_has_exactly_4_members(self) -> None:
        assert len(DbPortalCountError) == 4

    def test_contains_all_expected_values(self) -> None:
        expected = {
            "timeout",
            "upstream_5xx",
            "connection_refused",
            "unknown",
        }
        assert {e.value for e in DbPortalCountError} == expected


class TestDbPortalErrorType:
    """DbPortalErrorType: AP1 (3) + AP3 (7) = 10 problem type URIs."""

    def test_has_all_ap1_and_ap3_members(self) -> None:
        # AP1 base 3 + AP3 DSL 7 = 10.  advanced_search_not_implemented stays for
        # backward compatibility until a later cleanup PR but is never emitted.
        assert len(DbPortalErrorType) == 10

    def test_prefix_is_ddbj_problems(self) -> None:
        prefix = "https://ddbj.nig.ac.jp/problems/"
        for e in DbPortalErrorType:
            assert e.value.startswith(prefix)

    def test_invalid_query_combination_uri(self) -> None:
        assert (
            DbPortalErrorType.invalid_query_combination.value
            == "https://ddbj.nig.ac.jp/problems/invalid-query-combination"
        )

    def test_advanced_search_not_implemented_uri(self) -> None:
        assert (
            DbPortalErrorType.advanced_search_not_implemented.value
            == "https://ddbj.nig.ac.jp/problems/advanced-search-not-implemented"
        )

    def test_cursor_not_supported_uri(self) -> None:
        assert DbPortalErrorType.cursor_not_supported.value == "https://ddbj.nig.ac.jp/problems/cursor-not-supported"

    def test_ap3_dsl_slugs_present(self) -> None:
        """AP3 で追加した 7 slug の URI (source.md §AP1 L125-134 表)."""
        expected_slugs = {
            "unexpected-token",
            "unknown-field",
            "field-not-available-in-cross-db",
            "invalid-date-format",
            "invalid-operator-for-field",
            "nest-depth-exceeded",
            "missing-value",
        }
        actual_slugs = {e.value.rsplit("/", 1)[-1] for e in DbPortalErrorType}
        assert expected_slugs <= actual_slugs


# === DbPortalQuery ===


def _query(**overrides: Any) -> DbPortalQuery:
    """Shorthand for DbPortalQuery with sensible defaults."""
    defaults: dict[str, Any] = {
        "q": None,
        "adv": None,
        "db": None,
        "page": 1,
        "per_page": 20,
        "cursor": None,
        "sort": None,
    }
    defaults.update(overrides)
    return DbPortalQuery(**defaults)


class TestDbPortalQuery:
    """DbPortalQuery: attribute storage."""

    def test_stores_defaults(self) -> None:
        q = _query()
        assert q.q is None
        assert q.adv is None
        assert q.db is None
        assert q.page == 1
        assert q.per_page == 20
        assert q.cursor is None
        assert q.sort is None

    def test_stores_q(self) -> None:
        q = _query(q="cancer")
        assert q.q == "cancer"

    def test_stores_adv(self) -> None:
        q = _query(adv="type=bioproject")
        assert q.adv == "type=bioproject"

    def test_stores_db(self) -> None:
        q = _query(db=DbPortalDb.bioproject)
        assert q.db == DbPortalDb.bioproject

    def test_stores_cursor(self) -> None:
        q = _query(cursor="abc.def")
        assert q.cursor == "abc.def"

    def test_stores_custom_page_and_per_page(self) -> None:
        q = _query(page=5, per_page=50)
        assert q.page == 5
        assert q.per_page == 50


class TestDbPortalQuerySort:
    """DbPortalQuery.sort allowlist validation."""

    def test_accepts_none(self) -> None:
        q = _query(sort=None)
        assert q.sort is None

    def test_accepts_date_published_desc(self) -> None:
        q = _query(sort="datePublished:desc")
        assert q.sort == "datePublished:desc"

    def test_accepts_date_published_asc(self) -> None:
        q = _query(sort="datePublished:asc")
        assert q.sort == "datePublished:asc"

    def test_rejects_date_modified(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            _query(sort="dateModified:desc")
        assert exc_info.value.status_code == 422

    def test_rejects_identifier_sort(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            _query(sort="identifier:asc")
        assert exc_info.value.status_code == 422

    def test_rejects_random_string(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            _query(sort="bogus")
        assert exc_info.value.status_code == 422

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            _query(sort="")
        assert exc_info.value.status_code == 422


class TestDbPortalQueryPBT:
    """DbPortalQuery PBT: sort allowlist."""

    @given(sort=st.sampled_from(sorted(ALLOWED_DB_PORTAL_SORTS)))
    def test_accepts_allowlisted_sort(self, sort: str) -> None:
        q = _query(sort=sort)
        assert q.sort == sort

    @given(
        sort=st.text(min_size=1, max_size=50).filter(
            lambda s: s not in ALLOWED_DB_PORTAL_SORTS,
        ),
    )
    def test_rejects_non_allowlisted_sort(self, sort: str) -> None:
        with pytest.raises(HTTPException) as exc_info:
            _query(sort=sort)
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
                    db=DbPortalDb.trad,
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
    """DbPortalHit: discriminated union dispatch via TypeAdapter (AP6).

    AP1 の ``extra="allow"`` は AP6 で撤去 (decisions.md §A1-3)。status /
    accessibility 等は ``DbPortalHitBase`` に明示 field として定義されているため
    pass-through 挙動は維持されるが、未定義の field は silently drop される。
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
        """AP6: status / accessibility は DbPortalHitBase の明示 field。"""
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
        """AP6: extra="ignore" で converter 側の将来 field は silently drop。"""
        h = _DbPortalHitAdapter.validate_python(
            {
                "identifier": "PRJDB1",
                "type": "bioproject",
                "some_new_field_in_ap7": "value",
            },
        )
        dumped = h.model_dump(by_alias=True)
        assert "some_new_field_in_ap7" not in dumped

    def test_date_published_alias(self) -> None:
        h = _DbPortalHitAdapter.validate_python(
            {"identifier": "X", "type": "bioproject", "datePublished": "2024-01-15"},
        )
        assert h.date_published == "2024-01-15"
        dumped = h.model_dump(by_alias=True)
        assert dumped["datePublished"] == "2024-01-15"

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
        assert h.same_as[0].type == "bioproject"
        assert h.db_xrefs is not None
        assert h.db_xrefs[0].identifier == "Z"
        assert h.db_xrefs[0].type == "biosample"
        # serialize 時は alias "type" に戻る
        dumped = h.model_dump(by_alias=True)
        assert dumped["sameAs"][0]["type"] == "bioproject"
        assert dumped["dbXrefs"][0]["type"] == "biosample"

    def test_missing_type_rejected(self) -> None:
        """AP6: discriminator strict — type 欠損は ValidationError。"""
        with pytest.raises(pydantic.ValidationError):
            _DbPortalHitAdapter.validate_python({"identifier": "X"})

    def test_unknown_type_rejected(self) -> None:
        """AP6: discriminator strict — 未知 type は ValidationError。"""
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
