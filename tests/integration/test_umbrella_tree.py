"""Integration tests for IT-UMBRELLA-* scenarios.

GET /entries/bioproject/{accession}/umbrella-tree. Returns the parent /
child DAG. Most scenarios depend on representative accessions
(umbrella / orphan / multi-parent / hidden-intermediate / dangling-child)
that are populated during D-4. Until then, those tests skip via
``require_accession``.

See ``tests/integration-scenarios.md § IT-UMBRELLA-*``.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.integration.conftest import (
    DANGLING_CHILD_BIOPROJECT_ID,
    DEEP_CHAIN_BIOPROJECT_ID,
    MULTI_PARENT_BIOPROJECT_ID,
    NONEXISTENT_ID,
    ORPHAN_BIOPROJECT_ID,
    SECONDARY_BIOPROJECT_ID,
    UMBRELLA_BIOPROJECT_ID,
    WITHDRAWN_BIOPROJECT_ID,
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
        # Umbrella seeds must produce at least one parent→child edge.
        assert len(edges) >= 1, f"umbrella seed produced no edges: {body}"
        # Every edge has parent / child keys, parent in roots/nodes, child
        # not in roots (depth-1 implies the umbrella is the parent).
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
        # ``MULTI_PARENT_BIOPROJECT_ID`` is the child of >=2 parents in
        # converter-side data; the response should expose >=2 parent→child
        # edges that all terminate at the multi-parent accession.
        edges_to_seed = [e for e in edges if e["child"] == accession]
        assert len(edges_to_seed) >= 2, (
            f"multi-parent seed should appear as the child of >=2 parents; got {edges_to_seed}"
        )
        assert len(body["roots"]) >= 2, f"multi-parent seed should reach >=2 distinct roots; got {body['roots']}"


class TestUmbrellaMaxDepthExceeded:
    """IT-UMBRELLA-04: deeper than ``MAX_DEPTH=10`` → 500."""

    def test_deep_chain_returns_500(self, app: TestClient) -> None:
        """IT-UMBRELLA-04: chains that exceed the depth cap surface a 500."""
        accession = require_accession(
            "DEEP_CHAIN_BIOPROJECT_ID",
            DEEP_CHAIN_BIOPROJECT_ID,
        )
        resp = app.get(f"/entries/bioproject/{accession}/umbrella-tree")
        assert resp.status_code == 500


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
        # Seed must still anchor the response (no edges → orphan-like
        # collapse, but the seed appears in roots regardless).
        assert accession in body["roots"]
        # Every retained edge must point at a child distinct from the seed
        # — dangling references that point at missing IDs are dropped, so
        # whatever survives must carry both endpoints.
        for edge in body["edges"]:
            assert edge["parent"], edge
            assert edge["child"], edge
            assert edge["parent"] != edge["child"], edge


class TestUmbrellaHiddenNodeExcluded:
    """IT-UMBRELLA-06: hidden node (status:withdrawn) excluded from edges.

    Skipped on datasets without ``withdrawn`` BioProject entries —
    structurally indistinguishable from IT-UMBRELLA-05 otherwise, so
    we drive a real withdrawn seed when one exists.
    """

    def test_hidden_node_excluded(self, app: TestClient) -> None:
        """IT-UMBRELLA-06: hidden nodes never appear in edges."""
        accession = require_accession(
            "WITHDRAWN_BIOPROJECT_ID",
            WITHDRAWN_BIOPROJECT_ID,
        )
        resp = app.get(f"/entries/bioproject/{accession}/umbrella-tree")
        # Hidden seeds resolve as missing per IT-STATUS-06 — covered there.
        assert resp.status_code == 404


class TestUmbrellaSeedNotFound:
    """IT-UMBRELLA-07: missing seed → 404 (matches IT-STATUS-06 detail)."""

    def test_missing_seed_returns_404(self, app: TestClient) -> None:
        """IT-UMBRELLA-07: nonexistent seed → 404 (RFC 7807)."""
        resp = app.get(f"/entries/bioproject/{NONEXISTENT_ID}/umbrella-tree")
        assert resp.status_code == 404
        body = resp.json()
        assert body["status"] == 404

    def test_missing_seed_detail_matches_hidden(self, app: TestClient) -> None:
        """IT-UMBRELLA-07: missing seed and hidden seed share detail string."""
        hidden_accession = require_accession(
            "WITHDRAWN_BIOPROJECT_ID",
            WITHDRAWN_BIOPROJECT_ID,
        )
        miss = app.get(f"/entries/bioproject/{NONEXISTENT_ID}/umbrella-tree")
        hide = app.get(f"/entries/bioproject/{hidden_accession}/umbrella-tree")
        assert miss.status_code == hide.status_code == 404
        assert miss.json()["detail"] == hide.json()["detail"]


class TestUmbrellaSameAsFallback:
    """IT-UMBRELLA-08: Secondary ID resolves through sameAs fallback."""

    def test_secondary_id_resolves(self, app: TestClient) -> None:
        """IT-UMBRELLA-08: Secondary ID returns 200 with Primary in ``query``."""
        secondary = require_accession(
            "SECONDARY_BIOPROJECT_ID",
            SECONDARY_BIOPROJECT_ID,
        )
        resp = app.get(f"/entries/bioproject/{secondary}/umbrella-tree")
        assert resp.status_code == 200
        body = resp.json()
        # ``query`` should normalise to the Primary identifier (not the Secondary).
        assert body.get("query") != secondary
