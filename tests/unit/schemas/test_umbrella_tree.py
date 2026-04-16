"""Tests for ddbj_search_api.schemas.umbrella_tree."""

from __future__ import annotations

import pytest
from hypothesis import given
from pydantic import ValidationError

from ddbj_search_api.schemas.umbrella_tree import UmbrellaTreeEdge, UmbrellaTreeResponse
from tests.unit.strategies import bioproject_accession

# === UmbrellaTreeEdge ===


class TestUmbrellaTreeEdge:
    def test_basic_construction(self) -> None:
        edge = UmbrellaTreeEdge(parent="PRJDB0001", child="PRJDB1234")
        assert edge.parent == "PRJDB0001"
        assert edge.child == "PRJDB1234"

    def test_missing_parent_raises(self) -> None:
        with pytest.raises(ValidationError):
            UmbrellaTreeEdge(child="PRJDB1234")  # type: ignore[call-arg]

    def test_missing_child_raises(self) -> None:
        with pytest.raises(ValidationError):
            UmbrellaTreeEdge(parent="PRJDB0001")  # type: ignore[call-arg]

    @given(parent=bioproject_accession, child=bioproject_accession)
    def test_accepts_any_accession_strings(self, parent: str, child: str) -> None:
        edge = UmbrellaTreeEdge(parent=parent, child=child)
        assert edge.parent == parent
        assert edge.child == child


# === UmbrellaTreeResponse ===


class TestUmbrellaTreeResponse:
    def test_orphan_shape(self) -> None:
        resp = UmbrellaTreeResponse(query="PRJDB9999", roots=["PRJDB9999"], edges=[])
        assert resp.query == "PRJDB9999"
        assert resp.roots == ["PRJDB9999"]
        assert resp.edges == []

    def test_tree_with_edges(self) -> None:
        resp = UmbrellaTreeResponse(
            query="PRJDB1234",
            roots=["PRJDB0001"],
            edges=[
                UmbrellaTreeEdge(parent="PRJDB0001", child="PRJDB1234"),
                UmbrellaTreeEdge(parent="PRJDB0001", child="PRJDB1235"),
            ],
        )
        assert len(resp.edges) == 2
        assert resp.edges[0].parent == "PRJDB0001"

    def test_multi_root_allowed(self) -> None:
        resp = UmbrellaTreeResponse(
            query="PRJDB0555",
            roots=["PRJDB0001", "PRJDB0002"],
            edges=[
                UmbrellaTreeEdge(parent="PRJDB0001", child="PRJDB0555"),
                UmbrellaTreeEdge(parent="PRJDB0002", child="PRJDB0555"),
            ],
        )
        assert len(resp.roots) == 2

    def test_missing_query_raises(self) -> None:
        with pytest.raises(ValidationError):
            UmbrellaTreeResponse(roots=["PRJDB0001"], edges=[])  # type: ignore[call-arg]

    def test_missing_roots_raises(self) -> None:
        with pytest.raises(ValidationError):
            UmbrellaTreeResponse(query="PRJDB1", edges=[])  # type: ignore[call-arg]

    def test_missing_edges_raises(self) -> None:
        with pytest.raises(ValidationError):
            UmbrellaTreeResponse(query="PRJDB1", roots=["PRJDB1"])  # type: ignore[call-arg]

    def test_edges_accept_dict_input(self) -> None:
        resp = UmbrellaTreeResponse(
            query="PRJDB1",
            roots=["PRJDB0"],
            edges=[{"parent": "PRJDB0", "child": "PRJDB1"}],  # type: ignore[list-item]
        )
        assert resp.edges[0].parent == "PRJDB0"
        assert resp.edges[0].child == "PRJDB1"

    def test_model_dump_shape(self) -> None:
        resp = UmbrellaTreeResponse(
            query="PRJDB1",
            roots=["PRJDB0"],
            edges=[UmbrellaTreeEdge(parent="PRJDB0", child="PRJDB1")],
        )
        assert resp.model_dump() == {
            "query": "PRJDB1",
            "roots": ["PRJDB0"],
            "edges": [{"parent": "PRJDB0", "child": "PRJDB1"}],
        }
