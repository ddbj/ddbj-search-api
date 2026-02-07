"""Utility helpers."""
import pathlib
from typing import Any, Dict, List, Optional

from ddbj_search_api.schemas.common import EntryListItem, FacetBucket, Facets


def entry_to_dict(
    entry: Any,
    include_properties: bool = True,
) -> Dict[str, Any]:
    """Convert a Pydantic entry model to a serializable dict.

    Args:
        entry: A converter Pydantic model (BioProject, BioSample, SRA, JGA).
        include_properties: If False, drop the ``properties`` field.

    Returns:
        A dict ready for JSON serialization (using aliases).
    """
    data: Dict[str, Any] = entry.model_dump(by_alias=True)
    if not include_properties:
        data.pop("properties", None)

    return data


def truncate_db_xrefs(
    db_xrefs: List[Dict[str, Any]],
    limit: int,
) -> List[Dict[str, Any]]:
    """Truncate dbXrefs to the given limit."""

    return db_xrefs[:limit]


def count_db_xrefs_by_type(
    db_xrefs: List[Dict[str, Any]],
) -> Dict[str, int]:
    """Count dbXrefs grouped by their type field."""
    counts: Dict[str, int] = {}
    for xref in db_xrefs:
        xref_type = xref.get("type", "unknown")
        counts[xref_type] = counts.get(xref_type, 0) + 1

    return counts


def parse_es_hits(
    hits: List[Dict[str, Any]],
    db_xrefs_limit: int,
) -> List[EntryListItem]:
    """Convert ES hits to entry list items with dbXrefs handling.

    For each hit:
    - If ``dbXrefs`` exists, truncate and add ``dbXrefsCount``.
    - Otherwise, leave as-is.
    """
    items: List[EntryListItem] = []
    for hit in hits:
        source = dict(hit["_source"])
        db_xrefs = source.get("dbXrefs")
        if db_xrefs is not None:
            source["dbXrefsCount"] = count_db_xrefs_by_type(db_xrefs)
            source["dbXrefs"] = truncate_db_xrefs(db_xrefs, db_xrefs_limit)
        items.append(EntryListItem(**source))

    return items


def parse_facets(
    aggregations: Dict[str, Any],
    is_cross_type: bool = False,
    db_type: Optional[str] = None,
) -> Facets:
    """Convert ES aggregation buckets to a Facets model."""

    def _buckets(agg_name: str) -> List[FacetBucket]:
        agg = aggregations.get(agg_name, {})
        return [
            FacetBucket(value=b["key"], count=b["doc_count"])
            for b in agg.get("buckets", [])
        ]

    type_facet = _buckets("type") if is_cross_type else None
    object_type_facet = _buckets(
        "objectType") if db_type == "bioproject" else None

    return Facets(
        type=type_facet,
        organism=_buckets("organism"),
        status=_buckets("status"),
        accessibility=_buckets("accessibility"),
        object_type=object_type_facet,  # type: ignore[call-arg]
    )


def inside_container() -> bool:
    """Detect whether we are running inside a Docker/Podman container."""

    return (
        pathlib.Path("/.dockerenv").exists()
        or pathlib.Path("/run/.containerenv").exists()
    )
