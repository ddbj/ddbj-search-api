"""Tests for ddbj_search_api.schemas.dblink."""

from __future__ import annotations

import pytest
from ddbj_search_converter.jsonl.utils import to_xref
from fastapi import HTTPException
from hypothesis import given
from hypothesis import strategies as st

from ddbj_search_api.schemas.dblink import (
    AccessionType,
    DbLinksQuery,
    DbLinksResponse,
    DbLinksTypesResponse,
)


class TestAccessionType:
    """AccessionType enum exposes the documented union exactly.

    docs/api-spec.md § dblink で列挙されている 21 種類が ``AccessionType``
    に揃っているかを set 一致で検証する。要素数を ``== 21`` と固定値で書くと、
    Enum から 1 つ消えて別の 1 つが追加された ``±0`` の drift を捕捉できない
    ので、必ず member 集合そのものと比較する。
    """

    EXPECTED_VALUES = frozenset(
        {
            "bioproject",
            "biosample",
            "gea",
            "geo",
            "humandbs",
            "insdc",
            "insdc-assembly",
            "insdc-master",
            "jga-dac",
            "jga-dataset",
            "jga-policy",
            "jga-study",
            "metabobank",
            "pubmed",
            "sra-analysis",
            "sra-experiment",
            "sra-run",
            "sra-sample",
            "sra-study",
            "sra-submission",
            "taxonomy",
        }
    )

    def test_member_set_matches_expected(self) -> None:
        assert {e.value for e in AccessionType} == self.EXPECTED_VALUES

    def test_is_str_enum(self) -> None:
        for member in AccessionType:
            assert isinstance(member, str)
            assert isinstance(member.value, str)


class TestDbLinksResponse:
    def test_construction_with_db_xrefs(self) -> None:
        xref = to_xref("JGAS000101", type_hint="jga-study")
        resp = DbLinksResponse(
            identifier="hum0014",
            type=AccessionType("humandbs"),
            dbXrefs=[xref, to_xref("JGAS000381", type_hint="jga-study")],
        )
        assert resp.identifier == "hum0014"
        assert resp.type == AccessionType("humandbs")
        assert len(resp.dbXrefs) == 2

    def test_construction_with_empty_db_xrefs(self) -> None:
        resp = DbLinksResponse(
            identifier="NONEXISTENT",
            type=AccessionType("bioproject"),
            dbXrefs=[],
        )
        assert resp.dbXrefs == []

    def test_serialization(self) -> None:
        xref = to_xref("JGAS000101", type_hint="jga-study")
        resp = DbLinksResponse(
            identifier="hum0014",
            type=AccessionType("humandbs"),
            dbXrefs=[xref],
        )
        data = resp.model_dump(by_alias=True)
        link = data["dbXrefs"][0]
        assert link["identifier"] == "JGAS000101"
        assert link["type"] == "jga-study"
        assert "url" in link


class TestDbLinksTypesResponse:
    def test_types_list_is_sorted(self) -> None:
        types = sorted(AccessionType, key=lambda t: t.value)
        resp = DbLinksTypesResponse(types=types)
        values = [t.value for t in resp.types]
        assert values == sorted(values)

    def test_response_round_trips_full_enum(self) -> None:
        """全 AccessionType メンバーを response に詰めて round-trip できる。

        固定件数 ``== 21`` で確認すると enum サイズが ``±0`` で drift したケース
        を見逃すので、enum と response の set 一致で検証する。"""
        types = list(AccessionType)
        resp = DbLinksTypesResponse(types=types)
        assert {t.value for t in resp.types} == {e.value for e in AccessionType}


class TestDbLinksQuery:
    def test_no_target(self) -> None:
        query = DbLinksQuery(target=None)
        assert query.target is None

    def test_single_target(self) -> None:
        query = DbLinksQuery(target="jga-study")
        assert query.target is not None
        assert len(query.target) == 1
        assert query.target[0] == AccessionType("jga-study")

    def test_multiple_targets(self) -> None:
        query = DbLinksQuery(target="jga-study,bioproject")
        assert query.target is not None
        assert len(query.target) == 2
        values = {t.value for t in query.target}
        assert values == {"jga-study", "bioproject"}

    def test_target_with_spaces(self) -> None:
        query = DbLinksQuery(target=" jga-study , bioproject ")
        assert query.target is not None
        assert len(query.target) == 2

    def test_invalid_target_raises_http_exception(self) -> None:
        with pytest.raises(HTTPException):
            DbLinksQuery(target="invalid-type")

    def test_mixed_valid_invalid_target_raises(self) -> None:
        with pytest.raises(HTTPException):
            DbLinksQuery(target="jga-study,bogus")

    def test_invalid_target_returns_422_status(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            DbLinksQuery(target="invalid-type")
        assert exc_info.value.status_code == 422

    def test_invalid_target_error_message_contains_invalid_value(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            DbLinksQuery(target="bogus-value")
        assert "bogus-value" in exc_info.value.detail

    def test_invalid_target_error_message_lists_valid_types(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            DbLinksQuery(target="nope")
        for acc_type in AccessionType:
            assert acc_type.value in exc_info.value.detail

    def test_duplicate_targets_accepted(self) -> None:
        query = DbLinksQuery(target="jga-study,jga-study")
        assert query.target is not None
        assert len(query.target) == 2
        assert all(t == AccessionType("jga-study") for t in query.target)

    def test_empty_string_target_no_values(self) -> None:
        query = DbLinksQuery(target="")
        assert query.target is None

    def test_comma_only_target(self) -> None:
        query = DbLinksQuery(target=",,,")
        assert query.target is None


class TestDbLinksQueryPBT:
    @given(acc_type=st.sampled_from([e.value for e in AccessionType]))
    def test_any_valid_accession_type_accepted_as_target(self, acc_type: str) -> None:
        query = DbLinksQuery(target=acc_type)
        assert query.target is not None
        assert len(query.target) == 1
        assert query.target[0].value == acc_type

    @given(
        random_str=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N", "Pd")),
            min_size=1,
            max_size=30,
        ).filter(lambda s: s not in frozenset(e.value for e in AccessionType)),
    )
    def test_invalid_random_string_rejected(self, random_str: str) -> None:
        with pytest.raises(HTTPException) as exc_info:
            DbLinksQuery(target=random_str)
        assert exc_info.value.status_code == 422
