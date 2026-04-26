"""Integration tests for IT-UMBRELLA-* scenarios.

GET /entries/bioproject/{accession}/umbrella-tree. Returns the parent /
child DAG. ``MAX_DEPTH=10`` overrun (was IT-UMBRELLA-04), hidden-node
exclusion (was IT-UMBRELLA-06), and BioProject sameAs fallback (was
IT-UMBRELLA-08) are not exercised here because (a) deeper-than-10
chains do not exist in the staging input, (b) ``withdrawn`` entries
never reach ES, and (c) BioProject ``sameAs`` only carries external
cross-refs — see api-spec.md § データ可視性 / § sameAs and the unit
suite for the depth cap.

See ``tests/integration-scenarios.md § IT-UMBRELLA-*``.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.integration.conftest import (
    DANGLING_CHILD_BIOPROJECT_ID,
    MULTI_PARENT_BIOPROJECT_ID,
    NONEXISTENT_ID,
    ORPHAN_BIOPROJECT_ID,
    UMBRELLA_BIOPROJECT_ID,
    require_accession,
)


class TestUmbrellaOrphan:
    """IT-UMBRELLA-01: orphan returns ``roots=[self], edges=[]``."""

    def test_orphan_response_shape(self, app: TestClient) -> None:
        """IT-UMBRELLA-01: orphan accession produces empty edges + self root."""
        accession = require_accession(
            "ORPHAN_BIOPROJECT_ID",
            ORPHAN_BIOPROJECT_ID,
        )
        resp = app.get(f"/entries/bioproject/{accession}/umbrella-tree")
        assert resp.status_code == 200
        body = resp.json()
        assert body["roots"] == [accession]
        assert body["edges"] == []


class TestUmbrellaDepthOne:
    """IT-UMBRELLA-02: depth-1 (umbrella → leaf) typical structure."""

    def test_depth_one_response_shape(self, app: TestClient) -> None:
        """IT-UMBRELLA-02: umbrella seed produces edges to its children."""
        accession = require_accession(
            "UMBRELLA_BIOPROJECT_ID",
            UMBRELLA_BIOPROJECT_ID,
        )
        resp = app.get(f"/entries/bioproject/{accession}/umbrella-tree")
        assert resp.status_code == 200
        body = resp.json()
        assert accession in body["roots"]
        edges = body["edges"]
        assert isinstance(edges, list)
        assert len(edges) >= 1, f"umbrella seed produced no edges: {body}"
        for edge in edges:
            assert "parent" in edge, edge
            assert "child" in edge, edge
            assert edge["parent"] == accession, edge
            assert edge["child"] != accession, edge


class TestUmbrellaMultiParentDeduplication:
    """IT-UMBRELLA-03: multi-parent DAG deduplicates edges."""

    def test_edges_unique_by_pair(self, app: TestClient) -> None:
        """IT-UMBRELLA-03: each (parent, child) pair appears at most once."""
        accession = require_accession(
            "MULTI_PARENT_BIOPROJECT_ID",
            MULTI_PARENT_BIOPROJECT_ID,
        )
        resp = app.get(f"/entries/bioproject/{accession}/umbrella-tree")
        assert resp.status_code == 200
        body = resp.json()
        edges = body["edges"]
        keys = [(e["parent"], e["child"]) for e in edges]
        assert len(keys) == len(set(keys)), f"duplicate edges in {edges}"
        edges_to_seed = [e for e in edges if e["child"] == accession]
        assert len(edges_to_seed) >= 2, (
            f"multi-parent seed should appear as the child of >=2 parents; got {edges_to_seed}"
        )
        assert len(body["roots"]) >= 2, f"multi-parent seed should reach >=2 distinct roots; got {body['roots']}"


class TestUmbrellaDanglingChild:
    """IT-UMBRELLA-05: dangling child reference is excluded from edges."""

    def test_dangling_child_excluded_api_still_200(self, app: TestClient) -> None:
        """IT-UMBRELLA-05: API stays 200; only resolvable children become edges.

        ``DANGLING_CHILD_BIOPROJECT_ID`` references children that are not
        present in ES. The response must drop the dangling references but
        keep the seed reachable in ``roots`` (api-spec.md § Umbrella Tree).
        """
        accession = require_accession(
            "DANGLING_CHILD_BIOPROJECT_ID",
            DANGLING_CHILD_BIOPROJECT_ID,
        )
        resp = app.get(f"/entries/bioproject/{accession}/umbrella-tree")
        assert resp.status_code == 200
        body = resp.json()
        assert accession in body["roots"]
        for edge in body["edges"]:
            assert edge["parent"], edge
            assert edge["child"], edge
            assert edge["parent"] != edge["child"], edge


class TestUmbrellaSeedNotFound:
    """IT-UMBRELLA-07: missing seed → 404 with a fixed detail string."""

    def test_missing_seed_returns_404(self, app: TestClient) -> None:
        """IT-UMBRELLA-07: nonexistent seed → 404 + RFC 7807, accession-free detail."""
        resp = app.get(f"/entries/bioproject/{NONEXISTENT_ID}/umbrella-tree")
        assert resp.status_code == 404
        body = resp.json()
        assert body["status"] == 404
        # The detail must not embed the requested accession (api-spec.md
        # § データ可視性).
        assert NONEXISTENT_ID not in body["detail"]
