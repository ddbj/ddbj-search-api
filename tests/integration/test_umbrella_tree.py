"""Integration tests for GET /entries/bioproject/{accession}/umbrella-tree.

Data-agnostic: discovers representative accessions at runtime rather
than hard-coding specific IDs, so the tests do not break when the
underlying dataset changes.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ddbj_search_api.schemas.umbrella_tree import UmbrellaTreeResponse


def _first_bioproject_accession(app: TestClient) -> str:
    resp = app.get("/entries/bioproject/", params={"perPage": 1})
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert items, "ES has no bioproject entries"
    identifier: str = items[0]["identifier"]
    return identifier


def _first_umbrella_accession(app: TestClient) -> str | None:
    resp = app.get("/entries/bioproject/", params={"umbrella": "TRUE", "perPage": 1})
    if resp.status_code != 200:
        return None
    items = resp.json().get("items") or []
    if not items:
        return None
    identifier: str = items[0]["identifier"]
    return identifier


def test_umbrella_tree_shape_for_arbitrary_bioproject(app: TestClient) -> None:
    """Any indexed bioproject returns a UmbrellaTreeResponse-shaped body."""
    accession = _first_bioproject_accession(app)
    resp = app.get(f"/entries/bioproject/{accession}/umbrella-tree")
    assert resp.status_code == 200, resp.text

    parsed = UmbrellaTreeResponse.model_validate(resp.json())
    assert parsed.query
    assert parsed.roots
    assert isinstance(parsed.edges, list)


def test_umbrella_tree_404_for_missing(app: TestClient) -> None:
    resp = app.get("/entries/bioproject/PRJDB_DOES_NOT_EXIST_xyz/umbrella-tree")
    assert resp.status_code == 404
    body = resp.json()
    assert "PRJDB_DOES_NOT_EXIST" in body.get("detail", "")


def test_umbrella_tree_known_umbrella_has_edges(app: TestClient) -> None:
    """A known umbrella bioproject should expose its descendant edges."""
    umbrella_acc = _first_umbrella_accession(app)
    if umbrella_acc is None:
        pytest.skip("No umbrella bioprojects found in this ES instance")

    resp = app.get(f"/entries/bioproject/{umbrella_acc}/umbrella-tree")
    assert resp.status_code == 200, resp.text
    parsed = UmbrellaTreeResponse.model_validate(resp.json())

    assert parsed.query == umbrella_acc
    assert parsed.edges, f"Umbrella {umbrella_acc} returned no edges"
    # All edges must reference the umbrella as part of the graph.
    node_ids = {parsed.query, *parsed.roots}
    for edge in parsed.edges:
        node_ids.update({edge.parent, edge.child})
    assert umbrella_acc in node_ids
