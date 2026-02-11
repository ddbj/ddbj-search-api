"""Tests for ddbj_search_api.schemas.bulk."""

from __future__ import annotations

import pytest
from ddbj_search_converter.schema import BioProject
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from ddbj_search_api.schemas.bulk import BulkRequest, BulkResponse
from tests.unit.strategies import valid_bulk_ids

# === BulkRequest ===


class TestBulkRequest:
    """BulkRequest: ids list with max 1000 items."""

    def test_single_id(self) -> None:
        req = BulkRequest(ids=["PRJDB1"])
        assert req.ids == ["PRJDB1"]

    def test_empty_ids_accepted(self) -> None:
        req = BulkRequest(ids=[])
        assert req.ids == []

    def test_boundary_1000_ids_accepted(self) -> None:
        ids = [f"PRJDB{i}" for i in range(1000)]
        req = BulkRequest(ids=ids)
        assert len(req.ids) == 1000

    def test_boundary_1001_ids_rejected(self) -> None:
        ids = [f"PRJDB{i}" for i in range(1001)]
        with pytest.raises(ValidationError):
            BulkRequest(ids=ids)

    def test_missing_ids_raises_error(self) -> None:
        with pytest.raises(ValidationError):
            BulkRequest()  # type: ignore[call-arg]


class TestBulkRequestPBT:
    """Property-based tests for BulkRequest."""

    @given(ids=valid_bulk_ids)
    def test_valid_ids_accepted(self, ids: list[str]) -> None:
        req = BulkRequest(ids=ids)
        assert req.ids == ids
        assert len(req.ids) <= 1000

    @given(count=st.integers(min_value=1001, max_value=2000))
    def test_oversized_ids_rejected(self, count: int) -> None:
        ids = [f"ID{i}" for i in range(count)]
        with pytest.raises(ValidationError):
            BulkRequest(ids=ids)


class TestBulkRequestEdgeCases:
    """Edge cases for BulkRequest."""

    def test_duplicate_ids_allowed(self) -> None:
        req = BulkRequest(ids=["PRJDB1", "PRJDB1", "PRJDB1"])
        assert len(req.ids) == 3

    def test_ids_preserve_order(self) -> None:
        ids = ["PRJDB3", "PRJDB1", "PRJDB2"]
        req = BulkRequest(ids=ids)
        assert req.ids == ids


# === BulkResponse ===


def _make_bioproject(identifier: str) -> BioProject:
    """Create a BioProject instance for testing via model_construct."""

    return BioProject.model_construct(
        identifier=identifier,
        type_="bioproject",
        isPartOf="BioProject",
        objectType="BioProject",
        status="live",
        accessibility="public-access",
        url=f"https://example.com/{identifier}",
        properties={},
        distribution=[],
        organization=[],
        publication=[],
        grant=[],
        externalLink=[],
        dbXrefs=[],
        sameAs=[],
    )


class TestBulkResponse:
    """BulkResponse: entries + notFound."""

    def test_basic_construction(self) -> None:
        entry = _make_bioproject("PRJDB1")
        resp = BulkResponse(
            entries=[entry],
            notFound=["PRJDB_MISSING"],
        )
        assert len(resp.entries) == 1
        assert resp.not_found == ["PRJDB_MISSING"]

    def test_empty_entries_and_not_found(self) -> None:
        resp = BulkResponse(entries=[], notFound=[])
        assert resp.entries == []
        assert resp.not_found == []

    def test_alias_serialization(self) -> None:
        resp = BulkResponse(
            entries=[],
            notFound=["MISSING1"],
        )
        data = resp.model_dump(by_alias=True)
        assert "notFound" in data
        assert "not_found" not in data

    def test_missing_not_found_raises_error(self) -> None:
        with pytest.raises(ValidationError):
            BulkResponse(entries=[])  # type: ignore[call-arg]


class TestBulkResponsePBT:
    """Property-based tests for BulkResponse."""

    @given(
        n_entries=st.integers(min_value=0, max_value=10),
        n_not_found=st.integers(min_value=0, max_value=10),
    )
    def test_entries_and_not_found_sizes_match(self, n_entries: int, n_not_found: int) -> None:
        entries = [_make_bioproject(f"PRJDB{i}") for i in range(n_entries)]
        not_found = [f"MISSING{i}" for i in range(n_not_found)]
        resp = BulkResponse(entries=entries, notFound=not_found)  # type: ignore[arg-type]
        assert len(resp.entries) == n_entries
        assert len(resp.not_found) == n_not_found
