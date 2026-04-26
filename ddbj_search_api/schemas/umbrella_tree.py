"""Umbrella tree response schema (flat graph: query / roots / edges)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class UmbrellaTreeEdge(BaseModel):
    """Directed edge in the umbrella tree (BioProject accessions)."""

    parent: str = Field(
        description="Parent BioProject accession (umbrella side).",
        examples=["PRJDB0001"],
    )
    child: str = Field(
        description="Child BioProject accession (leaf or sub-umbrella).",
        examples=["PRJDB1234"],
    )


class UmbrellaTreeResponse(BaseModel):
    """Flat graph representation of a BioProject umbrella tree.

    Orphan entries return ``roots == [query]`` and ``edges == []``.
    """

    query: str = Field(
        description="Resolved primary identifier of the requested BioProject.",
        examples=["PRJDB1234"],
    )
    roots: list[str] = Field(
        description="Root BioProject accessions (parentBioProjects is empty).",
        examples=[["PRJDB0001"]],
    )
    edges: list[UmbrellaTreeEdge] = Field(
        examples=[[{"parent": "PRJDB0001", "child": "PRJDB1234"}]],
        description="Unique (parent, child) pairs covering the reachable subgraph.",
    )
