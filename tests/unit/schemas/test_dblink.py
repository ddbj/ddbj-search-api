"""Tests for ddbj_search_api.schemas.dblink."""

from __future__ import annotations

import pytest
from ddbj_search_converter.jsonl.utils import to_xref
from hypothesis import given
from hypothesis import strategies as st

from ddbj_search_api.schemas.dblink import (
    AccessionType,
    DbLinksQuery,
    DbLinksResponse,
    DbLinksTypesResponse,
)


class TestAccessionType:
    """AccessionType enum has exactly the expected 21 values."""

    EXPECTED_VALUES = sorted(
        [
            "bioproject",
            "biosample",
            "gea",
            "geo",
            "hum-id",
            "insdc-assembly",
            "insdc-master",
            "jga-dac",
            "jga-dataset",
            "jga-policy",
            "jga-study",
            "metabobank",
            "pubmed-id",
            "sra-analysis",
            "sra-experiment",
            "sra-run",
            "sra-sample",
            "sra-study",
            "sra-submission",
            "taxonomy",
            "umbrella-bioproject",
        ]
    )

    def test_has_21_members(self) -> None:
        assert len(AccessionType) == 21

    def test_all_expected_values_present(self) -> None:
        actual = sorted(e.value for e in AccessionType)
        assert actual == self.EXPECTED_VALUES

    def test_is_str_enum(self) -> None:
        for member in AccessionType:
            assert isinstance(member, str)
            assert isinstance(member.value, str)


class TestDbLinksResponse:
    def test_construction_with_links(self) -> None:
        xref = to_xref("JGAS000101", type_hint="jga-study")
        resp = DbLinksResponse(
            identifier="hum0014",
            type=AccessionType("hum-id"),
            links=[xref, to_xref("JGAS000381", type_hint="jga-study")],
        )
        assert resp.identifier == "hum0014"
        assert resp.type == AccessionType("hum-id")
        assert len(resp.links) == 2

    def test_construction_with_empty_links(self) -> None:
        resp = DbLinksResponse(
            identifier="NONEXISTENT",
            type=AccessionType("bioproject"),
            links=[],
        )
        assert resp.links == []

    def test_serialization(self) -> None:
        xref = to_xref("JGAS000101", type_hint="jga-study")
        resp = DbLinksResponse(
            identifier="hum0014",
            type=AccessionType("hum-id"),
            links=[xref],
        )
        data = resp.model_dump(by_alias=True)
        link = data["links"][0]
        assert link["identifier"] == "JGAS000101"
        assert link["type"] == "jga-study"
        assert "url" in link


class TestDbLinksTypesResponse:
    def test_types_list_is_sorted(self) -> None:
        types = sorted(AccessionType, key=lambda t: t.value)
        resp = DbLinksTypesResponse(types=types)
        values = [t.value for t in resp.types]
        assert values == sorted(values)

    def test_contains_21_types(self) -> None:
        types = list(AccessionType)
        resp = DbLinksTypesResponse(types=types)
        assert len(resp.types) == 21


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

    def test_invalid_target_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid target type"):
            DbLinksQuery(target="invalid-type")

    def test_mixed_valid_invalid_target_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid target type"):
            DbLinksQuery(target="jga-study,bogus")

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
