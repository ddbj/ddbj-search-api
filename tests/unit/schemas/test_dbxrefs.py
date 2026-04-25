"""Tests for ddbj_search_api.schemas.dbxrefs."""

from __future__ import annotations

from typing import get_args

import pytest
from ddbj_search_converter.schema import Xref, XrefType
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from ddbj_search_api.schemas.dbxrefs import DbXrefsFullResponse

_XREF_TYPE_VALUES: list[str] = list(get_args(XrefType))

xref_strategy = st.builds(
    Xref,
    identifier=st.text(min_size=1, max_size=30),
    type=st.sampled_from(_XREF_TYPE_VALUES),
    url=st.text(min_size=1, max_size=200),
)


# === DbXrefsFullResponse ===


class TestDbXrefsFullResponse:
    """DbXrefsFullResponse: all dbXrefs for an entry."""

    def test_basic_construction(self) -> None:
        resp = DbXrefsFullResponse(
            dbXrefs=[
                Xref(identifier="BS1", type="biosample", url="http://x"),
            ],
        )
        assert len(resp.db_xrefs) == 1
        assert resp.db_xrefs[0].identifier == "BS1"
        assert resp.db_xrefs[0].type_ == "biosample"

    def test_empty_db_xrefs(self) -> None:
        resp = DbXrefsFullResponse(dbXrefs=[])
        assert resp.db_xrefs == []

    def test_alias_serialization(self) -> None:
        resp = DbXrefsFullResponse(
            dbXrefs=[Xref(identifier="X", type="biosample", url="http://x")],
        )
        data = resp.model_dump(by_alias=True)
        assert "dbXrefs" in data
        assert "db_xrefs" not in data
        # alias 経由でも `type` は alias 化されているはず (converter Xref も同形)。
        assert data["dbXrefs"][0]["type"] == "biosample"

    def test_missing_db_xrefs_raises_error(self) -> None:
        with pytest.raises(ValidationError):
            DbXrefsFullResponse()  # type: ignore[call-arg]


class TestDbXrefsFullResponseEdgeCases:
    """Boundary inputs for DbXrefsFullResponse."""

    def test_invalid_xref_type_rejected_via_dict(self) -> None:
        # converter の Xref は Literal なので、未知 type は拒否される。
        # API スキーマ経由で dict 構築しても同じ振る舞いか確認。
        with pytest.raises(ValidationError):
            DbXrefsFullResponse.model_validate(
                {"dbXrefs": [{"identifier": "X", "type": "unknown-db", "url": "http://x"}]},
            )

    def test_dict_payload_with_alias_accepted(self) -> None:
        # API で典型的に来る dict 形 (camelCase alias) でも構築できる。
        resp = DbXrefsFullResponse.model_validate(
            {
                "dbXrefs": [
                    {"identifier": "BS1", "type": "biosample", "url": "http://x"},
                    {"identifier": "PRJDB1", "type": "bioproject", "url": "http://y"},
                ],
            },
        )
        assert [x.identifier for x in resp.db_xrefs] == ["BS1", "PRJDB1"]

    def test_non_list_db_xrefs_rejected(self) -> None:
        # dbXrefs は list なので dict や str を渡したら拒否。
        with pytest.raises(ValidationError):
            DbXrefsFullResponse(dbXrefs="not-a-list")  # type: ignore[arg-type]

    def test_required_xref_field_missing_rejected(self) -> None:
        # Xref の identifier 欠落 → 構築段階で拒否されるはず。
        with pytest.raises(ValidationError):
            DbXrefsFullResponse.model_validate(
                {"dbXrefs": [{"type": "biosample", "url": "http://x"}]},
            )


class TestDbXrefsFullResponsePBT:
    """Property-based: arbitrary list[Xref] round-trips through dump/load."""

    @given(xrefs=st.lists(xref_strategy, max_size=20))
    def test_round_trip_via_alias(self, xrefs: list[Xref]) -> None:
        resp = DbXrefsFullResponse(dbXrefs=xrefs)
        dumped = resp.model_dump(by_alias=True)
        rebuilt = DbXrefsFullResponse.model_validate(dumped)
        assert len(rebuilt.db_xrefs) == len(xrefs)
        for original, after in zip(xrefs, rebuilt.db_xrefs, strict=True):
            assert after.identifier == original.identifier
            assert after.type_ == original.type_
            assert after.url == original.url

    @given(xrefs=st.lists(xref_strategy, max_size=50))
    def test_alias_dump_uses_camel_keys_only(self, xrefs: list[Xref]) -> None:
        resp = DbXrefsFullResponse(dbXrefs=xrefs)
        dumped = resp.model_dump(by_alias=True)
        # alias dump で snake_case key が混ざらないこと (リファクタで alias 設定が
        # 外れた場合に検出する)。
        assert "db_xrefs" not in dumped
        assert "dbXrefs" in dumped
