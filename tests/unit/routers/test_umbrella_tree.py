"""Tests for GET /entries/bioproject/{accession}/umbrella-tree."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings

from tests.unit.strategies import bioproject_accession

# === Helpers ===


def _xref(identifier: str) -> dict[str, Any]:
    return {"identifier": identifier, "type": "bioproject", "url": f"http://x/{identifier}"}


def _source(
    identifier: str,
    parents: list[str] | None = None,
    children: list[str] | None = None,
    object_type: str = "BioProject",
    status: str = "public",
) -> dict[str, Any]:
    return {
        "identifier": identifier,
        "objectType": object_type,
        "status": status,
        "parentBioProjects": [_xref(p) for p in (parents or [])],
        "childBioProjects": [_xref(c) for c in (children or [])],
    }


# === Routing ===


class TestUmbrellaTreeRouting:
    def test_route_exists_for_orphan(
        self,
        app_with_umbrella_tree: TestClient,
        mock_es_get_source: AsyncMock,
    ) -> None:
        mock_es_get_source.return_value = _source("PRJDB1")
        resp = app_with_umbrella_tree.get("/entries/bioproject/PRJDB1/umbrella-tree")
        assert resp.status_code == 200

    def test_other_type_not_registered(
        self,
        app_with_umbrella_tree: TestClient,
    ) -> None:
        resp = app_with_umbrella_tree.get("/entries/biosample/SAMD1/umbrella-tree")
        assert resp.status_code == 404


# === Orphan ===


class TestOrphan:
    def test_orphan_returns_self_as_root(
        self,
        app_with_umbrella_tree: TestClient,
        mock_es_get_source: AsyncMock,
        mock_es_mget_source: AsyncMock,
    ) -> None:
        mock_es_get_source.return_value = _source("PRJDB1")
        resp = app_with_umbrella_tree.get("/entries/bioproject/PRJDB1/umbrella-tree")
        assert resp.status_code == 200
        assert resp.json() == {"query": "PRJDB1", "roots": ["PRJDB1"], "edges": []}
        mock_es_mget_source.assert_not_called()


# === Depth 1 ===


class TestDepth1:
    def test_single_parent_only(
        self,
        app_with_umbrella_tree: TestClient,
        mock_es_get_source: AsyncMock,
        mock_es_mget_source: AsyncMock,
    ) -> None:
        mock_es_get_source.return_value = _source("PRJDB1", parents=["PRJDB0"])
        mock_es_mget_source.return_value = {
            "PRJDB0": _source("PRJDB0", children=["PRJDB1"]),
        }
        resp = app_with_umbrella_tree.get("/entries/bioproject/PRJDB1/umbrella-tree")
        assert resp.status_code == 200
        assert resp.json() == {
            "query": "PRJDB1",
            "roots": ["PRJDB0"],
            "edges": [{"parent": "PRJDB0", "child": "PRJDB1"}],
        }
        assert mock_es_mget_source.call_count == 1

    def test_single_umbrella_multi_children(
        self,
        app_with_umbrella_tree: TestClient,
        mock_es_get_source: AsyncMock,
        mock_es_mget_source: AsyncMock,
    ) -> None:
        mock_es_get_source.return_value = _source("PRJDB0", children=["PRJDB1", "PRJDB2", "PRJDB3"])
        mock_es_mget_source.return_value = {
            "PRJDB1": _source("PRJDB1", parents=["PRJDB0"]),
            "PRJDB2": _source("PRJDB2", parents=["PRJDB0"]),
            "PRJDB3": _source("PRJDB3", parents=["PRJDB0"]),
        }
        resp = app_with_umbrella_tree.get("/entries/bioproject/PRJDB0/umbrella-tree")
        assert resp.status_code == 200
        body = resp.json()
        assert body["query"] == "PRJDB0"
        assert body["roots"] == ["PRJDB0"]
        assert body["edges"] == [
            {"parent": "PRJDB0", "child": "PRJDB1"},
            {"parent": "PRJDB0", "child": "PRJDB2"},
            {"parent": "PRJDB0", "child": "PRJDB3"},
        ]


# === Chain depth 2-5 ===


class TestChainDepth:
    @staticmethod
    def _build_chain(depth: int) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
        """Build a seed + hop_results for a linear chain.

        Seed is at the bottom (P{depth}); root is P0. Each hop fetches
        one ancestor going up.
        """
        seed_id = f"P{depth}"
        seed_source = _source(seed_id, parents=[f"P{depth - 1}"])
        hop_results: list[dict[str, Any]] = []
        for i in range(1, depth + 1):
            node_id = f"P{depth - i}"
            if i == depth:
                node_src = _source(node_id, children=[f"P{depth - i + 1}"])
            else:
                node_src = _source(
                    node_id,
                    parents=[f"P{depth - i - 1}"],
                    children=[f"P{depth - i + 1}"],
                )
            hop_results.append({node_id: node_src})
        return seed_source, hop_results, seed_id

    @pytest.mark.parametrize("depth", [2, 3, 4, 5])
    def test_linear_chain(
        self,
        app_with_umbrella_tree: TestClient,
        mock_es_get_source: AsyncMock,
        mock_es_mget_source: AsyncMock,
        depth: int,
    ) -> None:
        seed_source, hop_results, seed_id = self._build_chain(depth)
        mock_es_get_source.return_value = seed_source
        mock_es_mget_source.side_effect = hop_results

        resp = app_with_umbrella_tree.get(f"/entries/bioproject/{seed_id}/umbrella-tree")
        assert resp.status_code == 200
        body = resp.json()
        assert body["query"] == seed_id
        assert body["roots"] == ["P0"]
        expected_edges = [{"parent": f"P{i}", "child": f"P{i + 1}"} for i in range(depth)]
        assert body["edges"] == expected_edges
        # mget called exactly `depth` times (upward only; downward cache hits)
        assert mock_es_mget_source.call_count == depth


# === DAG ===


class TestDAG:
    def test_diamond_edges_deduplicated(
        self,
        app_with_umbrella_tree: TestClient,
        mock_es_get_source: AsyncMock,
        mock_es_mget_source: AsyncMock,
    ) -> None:
        # ROOT -> A, ROOT -> B, A -> LEAF, B -> LEAF. seed is LEAF.
        mock_es_get_source.return_value = _source("LEAF", parents=["A", "B"])
        mock_es_mget_source.side_effect = [
            {
                "A": _source("A", parents=["ROOT"], children=["LEAF"]),
                "B": _source("B", parents=["ROOT"], children=["LEAF"]),
            },
            {"ROOT": _source("ROOT", children=["A", "B"])},
        ]

        resp = app_with_umbrella_tree.get("/entries/bioproject/LEAF/umbrella-tree")
        assert resp.status_code == 200
        body = resp.json()
        assert body["query"] == "LEAF"
        assert body["roots"] == ["ROOT"]
        assert body["edges"] == [
            {"parent": "A", "child": "LEAF"},
            {"parent": "B", "child": "LEAF"},
            {"parent": "ROOT", "child": "A"},
            {"parent": "ROOT", "child": "B"},
        ]
        assert mock_es_mget_source.call_count == 2

    def test_multi_parent_seed_has_multiple_roots(
        self,
        app_with_umbrella_tree: TestClient,
        mock_es_get_source: AsyncMock,
        mock_es_mget_source: AsyncMock,
    ) -> None:
        mock_es_get_source.return_value = _source("SEED", parents=["P1", "P2"])
        mock_es_mget_source.return_value = {
            "P1": _source("P1", children=["SEED"]),
            "P2": _source("P2", children=["SEED"]),
        }

        resp = app_with_umbrella_tree.get("/entries/bioproject/SEED/umbrella-tree")
        assert resp.status_code == 200
        body = resp.json()
        assert body["roots"] == ["P1", "P2"]
        assert body["edges"] == [
            {"parent": "P1", "child": "SEED"},
            {"parent": "P2", "child": "SEED"},
        ]


# === MAX_DEPTH ===


class TestMaxDepth:
    def test_upward_depth_10_ok(
        self,
        app_with_umbrella_tree: TestClient,
        mock_es_get_source: AsyncMock,
        mock_es_mget_source: AsyncMock,
    ) -> None:
        seed_source, hop_results, seed_id = TestChainDepth._build_chain(10)
        mock_es_get_source.return_value = seed_source
        mock_es_mget_source.side_effect = hop_results

        resp = app_with_umbrella_tree.get(f"/entries/bioproject/{seed_id}/umbrella-tree")
        assert resp.status_code == 200

    def test_upward_depth_11_returns_500(
        self,
        app_with_umbrella_tree: TestClient,
        mock_es_get_source: AsyncMock,
        mock_es_mget_source: AsyncMock,
    ) -> None:
        # Parent chain never terminates: every node has a new parent
        mock_es_get_source.return_value = _source("SEED", parents=["A1"])

        def _infinite_upward(_client: Any, _index: str, ids: list[str], source_includes: Any = None) -> dict[str, Any]:
            return {id_: _source(id_, parents=[id_ + "x"]) for id_ in ids}

        mock_es_mget_source.side_effect = _infinite_upward

        resp = app_with_umbrella_tree.get("/entries/bioproject/SEED/umbrella-tree")
        assert resp.status_code == 500
        assert "MAX_DEPTH" in resp.json().get("detail", "")

    def test_downward_depth_11_returns_500(
        self,
        app_with_umbrella_tree: TestClient,
        mock_es_get_source: AsyncMock,
        mock_es_mget_source: AsyncMock,
    ) -> None:
        # Seed is a root with infinite descendants
        mock_es_get_source.return_value = _source("R0", children=["R1"])

        def _infinite_downward(
            _client: Any, _index: str, ids: list[str], source_includes: Any = None
        ) -> dict[str, Any]:
            return {id_: _source(id_, children=[id_ + "a"]) for id_ in ids}

        mock_es_mget_source.side_effect = _infinite_downward

        resp = app_with_umbrella_tree.get("/entries/bioproject/R0/umbrella-tree")
        assert resp.status_code == 500
        assert "MAX_DEPTH" in resp.json().get("detail", "")


# === 404 / sameAs ===


class TestNotFound:
    def test_direct_miss_returns_404(
        self,
        app_with_umbrella_tree: TestClient,
        mock_es_get_source: AsyncMock,
        mock_es_resolve_same_as_umbrella: AsyncMock,
    ) -> None:
        mock_es_get_source.return_value = None
        mock_es_resolve_same_as_umbrella.return_value = None
        resp = app_with_umbrella_tree.get("/entries/bioproject/NOTEXIST/umbrella-tree")
        assert resp.status_code == 404
        # The detail must NOT echo the requested accession back, otherwise
        # callers can infer existence from a withdrawn / private entry's
        # 404 vs a missing accession's 404 (api-spec.md § データ可視性).
        detail = resp.json()["detail"]
        assert "NOTEXIST" not in detail
        assert detail == "The requested bioproject entry was not found."

    def test_sameas_fallback_resolves(
        self,
        app_with_umbrella_tree: TestClient,
        mock_es_get_source: AsyncMock,
        mock_es_resolve_same_as_umbrella: AsyncMock,
    ) -> None:
        mock_es_get_source.side_effect = [None, _source("PRJDB_PRIMARY")]
        mock_es_resolve_same_as_umbrella.return_value = "PRJDB_PRIMARY"

        resp = app_with_umbrella_tree.get("/entries/bioproject/SEC_ID/umbrella-tree")
        assert resp.status_code == 200
        assert resp.json() == {
            "query": "PRJDB_PRIMARY",
            "roots": ["PRJDB_PRIMARY"],
            "edges": [],
        }

    def test_sameas_resolved_but_second_fetch_misses(
        self,
        app_with_umbrella_tree: TestClient,
        mock_es_get_source: AsyncMock,
        mock_es_resolve_same_as_umbrella: AsyncMock,
    ) -> None:
        mock_es_get_source.return_value = None
        mock_es_resolve_same_as_umbrella.return_value = "PRJDB_PRIMARY"

        resp = app_with_umbrella_tree.get("/entries/bioproject/SEC_ID/umbrella-tree")
        assert resp.status_code == 404

    def test_reference_not_found_dropped(
        self,
        app_with_umbrella_tree: TestClient,
        mock_es_get_source: AsyncMock,
        mock_es_mget_source: AsyncMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        mock_es_get_source.return_value = _source("SEED", parents=["GOOD", "BAD"])
        mock_es_mget_source.return_value = {
            "GOOD": _source("GOOD", children=["SEED"]),
            "BAD": None,
        }

        with caplog.at_level(logging.WARNING):
            resp = app_with_umbrella_tree.get("/entries/bioproject/SEED/umbrella-tree")
        assert resp.status_code == 200
        body = resp.json()
        assert body["roots"] == ["GOOD"]
        assert body["edges"] == [{"parent": "GOOD", "child": "SEED"}]
        assert any("BAD" in rec.message for rec in caplog.records)


# === objectType independence ===


class TestObjectTypeIgnored:
    def test_non_umbrella_object_type_still_expands(
        self,
        app_with_umbrella_tree: TestClient,
        mock_es_get_source: AsyncMock,
        mock_es_mget_source: AsyncMock,
    ) -> None:
        mock_es_get_source.return_value = _source("LEAF", parents=["UM"], object_type="BioProject")
        mock_es_mget_source.return_value = {
            "UM": _source("UM", children=["LEAF"], object_type="BioProject"),
        }
        resp = app_with_umbrella_tree.get("/entries/bioproject/LEAF/umbrella-tree")
        assert resp.status_code == 200
        body = resp.json()
        assert body["roots"] == ["UM"]
        assert body["edges"] == [{"parent": "UM", "child": "LEAF"}]


# === mget batching (N+1 avoidance) ===


class TestMgetBatching:
    def test_mget_batches_unique_ids_per_hop(
        self,
        app_with_umbrella_tree: TestClient,
        mock_es_get_source: AsyncMock,
        mock_es_mget_source: AsyncMock,
    ) -> None:
        mock_es_get_source.return_value = _source("LEAF", parents=["A", "B"])
        mock_es_mget_source.side_effect = [
            {
                "A": _source("A", parents=["ROOT"], children=["LEAF"]),
                "B": _source("B", parents=["ROOT"], children=["LEAF"]),
            },
            {"ROOT": _source("ROOT", children=["A", "B"])},
        ]

        resp = app_with_umbrella_tree.get("/entries/bioproject/LEAF/umbrella-tree")
        assert resp.status_code == 200

        hop_call_ids = [call.args[2] for call in mock_es_mget_source.call_args_list]
        assert hop_call_ids == [["A", "B"], ["ROOT"]]


# === PBT ===


class TestOrphanPBT:
    @settings(
        deadline=None,
        max_examples=40,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(accession=bioproject_accession)
    def test_orphan_always_returns_self(
        self,
        app_with_umbrella_tree: TestClient,
        mock_es_get_source: AsyncMock,
        mock_es_mget_source: AsyncMock,
        accession: str,
    ) -> None:
        mock_es_mget_source.reset_mock()
        mock_es_get_source.reset_mock()
        mock_es_get_source.return_value = _source(accession)

        resp = app_with_umbrella_tree.get(f"/entries/bioproject/{accession}/umbrella-tree")
        assert resp.status_code == 200
        assert resp.json() == {"query": accession, "roots": [accession], "edges": []}
        mock_es_mget_source.assert_not_called()


# === Status gating (docs/api-spec.md § データ可視性) ===


class TestUmbrellaTreeStatusGating:
    """umbrella-tree は status に基づいて可視性を制御する。

    - seed が withdrawn/private → 404 (存在秘匿)
    - 中間 node (parent/child) が withdrawn/private → 該当 edge を削除
    """

    @pytest.mark.parametrize("status", ["withdrawn", "private"])
    def test_hidden_seed_returns_404(
        self,
        app_with_umbrella_tree: TestClient,
        mock_es_get_source: AsyncMock,
        status: str,
    ) -> None:
        mock_es_get_source.return_value = _source("PRJDB1", status=status)
        resp = app_with_umbrella_tree.get("/entries/bioproject/PRJDB1/umbrella-tree")
        assert resp.status_code == 404
        body = resp.json()
        # Hidden seeds must produce the same detail as missing seeds —
        # accession must not leak through (api-spec.md § データ可視性).
        assert body["detail"] == "The requested bioproject entry was not found."
        assert "PRJDB1" not in body["detail"]

    def test_hidden_parent_is_dropped(
        self,
        app_with_umbrella_tree: TestClient,
        mock_es_get_source: AsyncMock,
        mock_es_mget_source: AsyncMock,
    ) -> None:
        """中間 parent が withdrawn の場合、その edge は結果から除外される。"""
        # seed (PRJDB1) has parent PRJDB_HIDDEN (withdrawn)
        mock_es_get_source.return_value = _source("PRJDB1", parents=["PRJDB_HIDDEN"])
        mock_es_mget_source.return_value = {
            "PRJDB_HIDDEN": _source("PRJDB_HIDDEN", status="withdrawn"),
        }

        resp = app_with_umbrella_tree.get("/entries/bioproject/PRJDB1/umbrella-tree")
        assert resp.status_code == 200
        data = resp.json()
        # parent が withdrawn なので、PRJDB1 自身が root に昇格し edge 0
        assert data["roots"] == ["PRJDB1"]
        assert data["edges"] == []

    def test_hidden_child_is_dropped(
        self,
        app_with_umbrella_tree: TestClient,
        mock_es_get_source: AsyncMock,
        mock_es_mget_source: AsyncMock,
    ) -> None:
        """中間 child が private の場合、その edge は結果から除外される。"""
        # seed (PRJDB1) has child PRJDB_PRIVATE (private)
        mock_es_get_source.return_value = _source("PRJDB1", children=["PRJDB_PRIVATE"])
        mock_es_mget_source.return_value = {
            "PRJDB_PRIVATE": _source("PRJDB_PRIVATE", status="private"),
        }

        resp = app_with_umbrella_tree.get("/entries/bioproject/PRJDB1/umbrella-tree")
        assert resp.status_code == 200
        data = resp.json()
        assert data["roots"] == ["PRJDB1"]
        assert data["edges"] == []

    def test_visible_siblings_survive_when_one_sibling_hidden(
        self,
        app_with_umbrella_tree: TestClient,
        mock_es_get_source: AsyncMock,
        mock_es_mget_source: AsyncMock,
    ) -> None:
        """複数 children のうち 1 件だけ withdrawn でも、他の可視 edge は残る。"""
        mock_es_get_source.return_value = _source(
            "PRJDB_ROOT",
            children=["PRJDB_OK", "PRJDB_HIDDEN"],
        )
        mock_es_mget_source.return_value = {
            "PRJDB_OK": _source("PRJDB_OK", status="public"),
            "PRJDB_HIDDEN": _source("PRJDB_HIDDEN", status="withdrawn"),
        }

        resp = app_with_umbrella_tree.get("/entries/bioproject/PRJDB_ROOT/umbrella-tree")
        assert resp.status_code == 200
        data = resp.json()
        edges = {(e["parent"], e["child"]) for e in data["edges"]}
        assert edges == {("PRJDB_ROOT", "PRJDB_OK")}

    def test_suppressed_seed_returns_200(
        self,
        app_with_umbrella_tree: TestClient,
        mock_es_get_source: AsyncMock,
    ) -> None:
        """suppressed の seed は直接アクセスに相当し、200 で返る
        (docs/api-spec.md § データ可視性 の直接アクセス ルール)。"""
        mock_es_get_source.return_value = _source("PRJDB_SUPP", status="suppressed")
        resp = app_with_umbrella_tree.get("/entries/bioproject/PRJDB_SUPP/umbrella-tree")
        assert resp.status_code == 200

    def test_hidden_seed_same_as_missing(
        self,
        app_with_umbrella_tree: TestClient,
        mock_es_get_source: AsyncMock,
        mock_es_resolve_same_as_umbrella: AsyncMock,
    ) -> None:
        """withdrawn seed と missing の 404 レスポンスは同一文面。"""
        mock_es_get_source.return_value = _source("PRJDB1", status="withdrawn")
        resp_hidden = app_with_umbrella_tree.get("/entries/bioproject/PRJDB1/umbrella-tree")

        mock_es_get_source.return_value = None
        mock_es_resolve_same_as_umbrella.return_value = None
        resp_missing = app_with_umbrella_tree.get("/entries/bioproject/PRJDB1/umbrella-tree")

        assert resp_hidden.status_code == resp_missing.status_code == 404
        assert resp_hidden.json()["detail"] == resp_missing.json()["detail"]
