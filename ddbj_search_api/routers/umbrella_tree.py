"""GET /entries/bioproject/{accession}/umbrella-tree endpoint.

Returns a flat graph (query / roots / edges) of the BioProject
umbrella tree that contains the given accession.  DAG-safe.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path

from ddbj_search_api.es import get_es_client
from ddbj_search_api.es.client import es_get_source, es_mget_source, es_resolve_same_as
from ddbj_search_api.schemas.common import ProblemDetails
from ddbj_search_api.schemas.umbrella_tree import UmbrellaTreeEdge, UmbrellaTreeResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Entry Detail"])

MAX_DEPTH = 10
_INDEX = "bioproject"
_SOURCE_FIELDS = ["identifier", "parentBioProjects", "childBioProjects", "status"]
_SOURCE_FIELDS_CSV = ",".join(_SOURCE_FIELDS)
_VISIBLE_STATUSES = ("public", "suppressed")


def _is_visible(source: dict[str, Any] | None) -> bool:
    """Return True if the ES ``_source`` represents a visible bioproject.

    Missing documents and non-visible statuses (``withdrawn`` / ``private``)
    both return False so that intermediate non-public nodes collapse
    into the existing "参照切れ" path (docs/api-spec.md § データ可視性).
    """
    if source is None:
        return False
    return source.get("status") in _VISIBLE_STATUSES


def _extract_identifiers(xrefs: Any) -> list[str]:
    """Extract identifier strings from a list of Xref-like dicts.

    Defensive: tolerates non-list input and entries missing the
    ``identifier`` key.
    """
    if not isinstance(xrefs, list):
        return []
    out: list[str] = []
    for x in xrefs:
        if isinstance(x, dict):
            identifier = x.get("identifier")
            if isinstance(identifier, str) and identifier:
                out.append(identifier)
    return out


async def _fetch_seed(
    client: httpx.AsyncClient,
    accession: str,
) -> tuple[str, list[str], list[str]]:
    """Resolve seed BioProject (with sameAs fallback and visibility check).

    Returns ``(primary_id, parent_ids, child_ids)``. Raises 404 when the
    entry is missing, or its status is ``withdrawn`` / ``private``
    (hidden; see docs/api-spec.md § データ可視性).
    """
    not_found = HTTPException(
        status_code=404,
        detail=f"The requested bioproject '{accession}' was not found.",
    )

    source = await es_get_source(client, _INDEX, accession, source_includes=_SOURCE_FIELDS_CSV)
    if source is None:
        resolved = await es_resolve_same_as(client, _INDEX, accession)
        if resolved is None:
            raise not_found
        source = await es_get_source(client, _INDEX, resolved, source_includes=_SOURCE_FIELDS_CSV)
        if source is None:
            raise not_found

    if not _is_visible(source):
        raise not_found

    primary_id_raw = source.get("identifier")
    primary_id = primary_id_raw if isinstance(primary_id_raw, str) and primary_id_raw else accession
    parent_ids = _extract_identifiers(source.get("parentBioProjects"))
    child_ids = _extract_identifiers(source.get("childBioProjects"))
    return primary_id, parent_ids, child_ids


async def _traverse_upward(
    client: httpx.AsyncClient,
    seed_primary: str,
    seed_parents: list[str],
    doc_cache: dict[str, tuple[list[str], list[str]]],
    invisible: set[str],
) -> set[str]:
    """Walk upward through parentBioProjects; return root identifiers.

    Mutates ``doc_cache`` with fetched ``(parent_ids, child_ids)`` per
    visited node. Non-visible intermediate nodes (``withdrawn`` /
    ``private`` or missing) are recorded in ``invisible`` so that
    downward traversal can drop edges pointing at them
    (docs/api-spec.md § データ可視性).

    Raises 500 if traversal exceeds ``MAX_DEPTH``.
    """
    roots: set[str] = set()
    visited: set[str] = {seed_primary}
    frontier: set[str] = {p for p in seed_parents if p not in visited}

    depth = 0
    while frontier:
        if depth > MAX_DEPTH:
            raise HTTPException(
                status_code=500,
                detail=f"Umbrella tree exceeded MAX_DEPTH={MAX_DEPTH} during upward traversal.",
            )
        visited.update(frontier)
        fetched = await es_mget_source(
            client,
            _INDEX,
            sorted(frontier),
            source_includes=_SOURCE_FIELDS,
        )
        next_frontier: set[str] = set()
        for node_id in frontier:
            source = fetched.get(node_id)
            if not _is_visible(source):
                logger.warning(
                    "bioproject umbrella-tree: referenced parent '%s' not visible",
                    node_id,
                )
                doc_cache[node_id] = ([], [])
                invisible.add(node_id)
                continue
            assert source is not None
            parent_ids = _extract_identifiers(source.get("parentBioProjects"))
            child_ids = _extract_identifiers(source.get("childBioProjects"))
            doc_cache[node_id] = (parent_ids, child_ids)
            if not parent_ids:
                roots.add(node_id)
            else:
                next_frontier.update(p for p in parent_ids if p not in visited)
        depth += 1
        frontier = next_frontier

    return roots


async def _traverse_downward(
    client: httpx.AsyncClient,
    roots: set[str],
    doc_cache: dict[str, tuple[list[str], list[str]]],
    invisible: set[str],
) -> set[tuple[str, str]]:
    """BFS downward through childBioProjects; return unique edges.

    Reuses ``doc_cache`` from upward traversal to avoid re-fetching.
    Non-visible nodes (``withdrawn`` / ``private`` or missing) are
    recorded in ``invisible`` during fetch and edges pointing at them
    are dropped from the result (docs/api-spec.md § データ可視性).

    Raises 500 if traversal exceeds ``MAX_DEPTH``.
    """
    edges: set[tuple[str, str]] = set()
    visited: set[str] = set(roots)
    frontier: set[str] = set(roots)

    depth = 0
    while frontier:
        if depth > MAX_DEPTH:
            raise HTTPException(
                status_code=500,
                detail=f"Umbrella tree exceeded MAX_DEPTH={MAX_DEPTH} during downward traversal.",
            )

        # 1. Fetch any frontier nodes that haven't been loaded yet.
        to_fetch = sorted(node for node in frontier if node not in doc_cache)
        if to_fetch:
            fetched = await es_mget_source(
                client,
                _INDEX,
                to_fetch,
                source_includes=_SOURCE_FIELDS,
            )
            for node_id in to_fetch:
                source = fetched.get(node_id)
                if not _is_visible(source):
                    logger.warning(
                        "bioproject umbrella-tree: referenced child '%s' not visible",
                        node_id,
                    )
                    doc_cache[node_id] = ([], [])
                    invisible.add(node_id)
                    continue
                assert source is not None
                parent_ids = _extract_identifiers(source.get("parentBioProjects"))
                child_ids = _extract_identifiers(source.get("childBioProjects"))
                doc_cache[node_id] = (parent_ids, child_ids)

        # 2. Pre-fetch children so their visibility is known before we
        # emit edges pointing at them.
        candidate_children: set[str] = set()
        for node_id in frontier:
            if node_id in invisible:
                continue
            _, child_ids = doc_cache.get(node_id, ([], []))
            candidate_children.update(child_ids)

        children_to_fetch = sorted(c for c in candidate_children if c not in doc_cache)
        if children_to_fetch:
            fetched_children = await es_mget_source(
                client,
                _INDEX,
                children_to_fetch,
                source_includes=_SOURCE_FIELDS,
            )
            for child_id in children_to_fetch:
                child_source = fetched_children.get(child_id)
                if not _is_visible(child_source):
                    logger.warning(
                        "bioproject umbrella-tree: referenced child '%s' not visible",
                        child_id,
                    )
                    doc_cache[child_id] = ([], [])
                    invisible.add(child_id)
                    continue
                assert child_source is not None
                p_ids = _extract_identifiers(child_source.get("parentBioProjects"))
                c_ids = _extract_identifiers(child_source.get("childBioProjects"))
                doc_cache[child_id] = (p_ids, c_ids)

        # 3. Emit edges only to visible children.
        next_frontier: set[str] = set()
        for node_id in frontier:
            if node_id in invisible:
                continue
            _, child_ids = doc_cache.get(node_id, ([], []))
            for child_id in child_ids:
                if child_id in invisible:
                    continue
                edges.add((node_id, child_id))
                if child_id not in visited:
                    visited.add(child_id)
                    next_frontier.add(child_id)
        depth += 1
        frontier = next_frontier

    return edges


@router.get(
    "/entries/bioproject/{accession}/umbrella-tree",
    response_model=UmbrellaTreeResponse,
    summary="Get BioProject umbrella tree (flat graph).",
    description="Return the umbrella tree containing the BioProject as a flat graph (query / roots / edges). DAG-safe.",
    operation_id="getUmbrellaTree",
    responses={
        404: {
            "description": "Not Found (entry does not exist, or is withdrawn / private).",
            "model": ProblemDetails,
        },
        422: {
            "description": "Unprocessable Entity (path validation error).",
            "model": ProblemDetails,
        },
    },
)
async def get_umbrella_tree(
    accession: str = Path(
        min_length=1,
        description="BioProject accession (primary identifier or sameAs secondary ID).",
    ),
    client: httpx.AsyncClient = Depends(get_es_client),
) -> UmbrellaTreeResponse:
    primary_id, seed_parents, seed_children = await _fetch_seed(client, accession)

    if not seed_parents and not seed_children:
        return UmbrellaTreeResponse(query=primary_id, roots=[primary_id], edges=[])

    doc_cache: dict[str, tuple[list[str], list[str]]] = {
        primary_id: (seed_parents, seed_children),
    }
    invisible: set[str] = set()

    roots = await _traverse_upward(client, primary_id, seed_parents, doc_cache, invisible)
    if not roots:
        roots = {primary_id}

    edges_set = await _traverse_downward(client, roots, doc_cache, invisible)

    edges = [UmbrellaTreeEdge(parent=p, child=c) for p, c in sorted(edges_set)]
    return UmbrellaTreeResponse(query=primary_id, roots=sorted(roots), edges=edges)
