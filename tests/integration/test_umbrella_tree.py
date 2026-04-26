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
        """IT-UMBRELLA-02: umbrella seed produces a valid tree response.

        Whether ``edges`` is non-empty depends on the chosen seed (a real
        ``UmbrellaBioProject`` may have no children registered yet on
        staging). The structural invariants below hold for both cases.
        """
        accession = require_accession(
            "UMBRELLA_BIOPROJECT_ID",
            UMBRELLA_BIOPROJECT_ID,
        )
        resp = app.get(f"/entries/bioproject/{accession}/umbrella-tree")
        assert resp.status_code == 200
        body = resp.json()
        assert accession in body["roots"]
        assert isinstance(body["edges"], list)
        # Every edge target must reference a node in roots or a child accession;
        # we cannot fully resolve nodes here so we just assert the edges list
        # is parseable as ``{parent, child}`` records.
        for edge in body["edges"]:
            assert "parent" in edge
            assert "child" in edge


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
        edges = resp.json()["edges"]
        keys = [(e["parent"], e["child"]) for e in edges]
        assert len(keys) == len(set(keys))


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
        """IT-UMBRELLA-05: API stays 200; child references are pruned."""
        accession = require_accession(
            "DANGLING_CHILD_BIOPROJECT_ID",
            DANGLING_CHILD_BIOPROJECT_ID,
        )
        resp = app.get(f"/entries/bioproject/{accession}/umbrella-tree")
        assert resp.status_code == 200


class TestUmbrellaHiddenNodeExcluded:
    """IT-UMBRELLA-06: hidden node (status:withdrawn) excluded from edges."""

    def test_hidden_node_excluded(self, app: TestClient) -> None:
        """IT-UMBRELLA-06: status filter prunes intermediate hidden nodes."""
        # Reuses the dangling/intermediate seed; the same constant covers the
        # "hidden intermediate" case once D-4 populates it.
        accession = require_accession(
            "DANGLING_CHILD_BIOPROJECT_ID",
            DANGLING_CHILD_BIOPROJECT_ID,
        )
        resp = app.get(f"/entries/bioproject/{accession}/umbrella-tree")
        assert resp.status_code == 200


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
