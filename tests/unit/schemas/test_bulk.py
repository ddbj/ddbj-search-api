"""Tests for ddbj_search_api.schemas.bulk."""

from __future__ import annotations

import pytest
from ddbj_search_converter.schema import GEA, JGA, SRA, BioProject, BioSample, MetaboBank
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from ddbj_search_api.schemas.bulk import BulkRequest, BulkResponse
from tests._factories import make_bioproject_dict as _bp_dict
from tests._factories import (
    make_biosample_dict,
    make_gea_dict,
    make_jga_dict,
    make_metabobank_dict,
    make_sra_dict,
)
from tests.unit.strategies import valid_bulk_ids

# === BulkRequest ===


class TestBulkRequest:
    """BulkRequest: ids list with max 1000 items."""

    def test_single_id(self) -> None:
        req = BulkRequest(ids=["PRJDB1"])
        assert req.ids == ["PRJDB1"]

    def test_empty_ids_rejected(self) -> None:
        with pytest.raises(ValidationError):
            BulkRequest(ids=[])

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


def _make_bioproject(identifier: str = "PRJDB1") -> BioProject:
    """Build a fully-validated BioProject via the shared factory.

    Uses ``BioProject(**dict)`` (not ``model_construct``) so the converter's
    Pydantic schema contract (Literal isPartOf, enum accessibility, required
    list fields) is enforced at test data construction time. Contract drift
    surfaces here instead of bleeding into router tests.
    """
    return BioProject(**_bp_dict(identifier=identifier))


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


class TestBulkResponseAllDbTypes:
    """All six entry types (BioProject / BioSample / SRA / JGA / GEA / MetaboBank)
    are valid members of ``entries``.

    Regression for the bug where ``BulkResponse.entries`` only declared
    ``BioProject | BioSample | SRA | JGA``, silently dropping GEA and
    MetaboBank from the OpenAPI schema and from validated responses.
    """

    def test_bioproject_entry_accepted(self) -> None:
        entry = BioProject(**_bp_dict(identifier="PRJDB1"))
        resp = BulkResponse(entries=[entry], notFound=[])
        assert resp.entries[0].identifier == "PRJDB1"

    def test_biosample_entry_accepted(self) -> None:
        entry = BioSample(**make_biosample_dict(identifier="SAMD00000001"))
        resp = BulkResponse(entries=[entry], notFound=[])
        assert resp.entries[0].identifier == "SAMD00000001"

    def test_sra_entry_accepted(self) -> None:
        entry = SRA(**make_sra_dict(identifier="DRR000001"))
        resp = BulkResponse(entries=[entry], notFound=[])
        assert resp.entries[0].identifier == "DRR000001"

    def test_jga_entry_accepted(self) -> None:
        entry = JGA(**make_jga_dict(identifier="JGAS000001"))
        resp = BulkResponse(entries=[entry], notFound=[])
        assert resp.entries[0].identifier == "JGAS000001"

    def test_gea_entry_accepted(self) -> None:
        entry = GEA(**make_gea_dict(identifier="E-GEAD-1"))
        resp = BulkResponse(entries=[entry], notFound=[])
        assert resp.entries[0].identifier == "E-GEAD-1"

    def test_metabobank_entry_accepted(self) -> None:
        entry = MetaboBank(**make_metabobank_dict(identifier="MTBKS1"))
        resp = BulkResponse(entries=[entry], notFound=[])
        assert resp.entries[0].identifier == "MTBKS1"

    def test_mixed_types_in_same_response(self) -> None:
        """Cross-type bulk would never happen in practice, but the union must allow it."""
        entries = [
            BioProject(**_bp_dict(identifier="PRJDB1")),
            GEA(**make_gea_dict(identifier="E-GEAD-1")),
            MetaboBank(**make_metabobank_dict(identifier="MTBKS1")),
        ]
        resp = BulkResponse(entries=entries, notFound=[])  # type: ignore[arg-type]
        assert len(resp.entries) == 3
        assert {e.identifier for e in resp.entries} == {"PRJDB1", "E-GEAD-1", "MTBKS1"}


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
