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
from ddbj_search_api.schemas.umbrella_tree import UmbrellaTreeEdge, UmbrellaTreeResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Entry Detail"])

MAX_DEPTH = 10
_INDEX = "bioproject"
_SOURCE_FIELDS = ["identifier", "parentBioProjects", "childBioProjects"]
_SOURCE_FIELDS_CSV = ",".join(_SOURCE_FIELDS)


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
    """Resolve seed BioProject (with sameAs fallback).

    Returns ``(primary_id, parent_ids, child_ids)``.  Raises 404 if
    neither direct lookup nor sameAs resolution finds the entry.
    """
    source = await es_get_source(client, _INDEX, accession, source_includes=_SOURCE_FIELDS_CSV)
    if source is None:
        resolved = await es_resolve_same_as(client, _INDEX, accession)
        if resolved is None:
            raise HTTPException(
                status_code=404,
                detail=f"The requested bioproject '{accession}' was not found.",
            )
        source = await es_get_source(client, _INDEX, resolved, source_includes=_SOURCE_FIELDS_CSV)
        if source is None:
            raise HTTPException(
                status_code=404,
                detail=f"The requested bioproject '{accession}' was not found.",
            )

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
) -> set[str]:
    """Walk upward through parentBioProjects; return root identifiers.

    Mutates ``doc_cache`` with fetched ``(parent_ids, child_ids)`` per
    visited node.  Raises 500 if traversal exceeds ``MAX_DEPTH``.
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
            if source is None:
                logger.warning(
                    "bioproject umbrella-tree: referenced parent '%s' not found",
                    node_id,
                )
                continue
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
) -> set[tuple[str, str]]:
    """BFS downward through childBioProjects; return unique edges.

    Reuses ``doc_cache`` from upward traversal to avoid re-fetching.
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
                if source is None:
                    logger.warning(
                        "bioproject umbrella-tree: referenced child '%s' not found",
                        node_id,
                    )
                    doc_cache[node_id] = ([], [])
                    continue
                parent_ids = _extract_identifiers(source.get("parentBioProjects"))
                child_ids = _extract_identifiers(source.get("childBioProjects"))
                doc_cache[node_id] = (parent_ids, child_ids)

        next_frontier: set[str] = set()
        for node_id in frontier:
            _, child_ids = doc_cache.get(node_id, ([], []))
            for child_id in child_ids:
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

    roots = await _traverse_upward(client, primary_id, seed_parents, doc_cache)
    if not roots:
        roots = {primary_id}

    edges_set = await _traverse_downward(client, roots, doc_cache)

    edges = [UmbrellaTreeEdge(parent=p, child=c) for p, c in sorted(edges_set)]
    return UmbrellaTreeResponse(query=primary_id, roots=sorted(roots), edges=edges)
