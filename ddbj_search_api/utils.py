"""Utility helpers."""

from __future__ import annotations

from typing import Any

from ddbj_search_api.schemas.common import FacetBucket, Facets


def parse_facets(
    aggregations: dict[str, Any],
    is_cross_type: bool = False,
    db_type: str | None = None,
) -> Facets:
    """Convert ES aggregation buckets to a Facets model."""

    def _buckets(agg_name: str) -> list[FacetBucket]:
        agg = aggregations.get(agg_name, {})
        return [FacetBucket(value=b["key"], count=b["doc_count"]) for b in agg.get("buckets", [])]

    type_facet = _buckets("type") if is_cross_type else None
    object_type_facet = _buckets("objectType") if db_type == "bioproject" else None

    return Facets(
        type=type_facet,
        organism=_buckets("organism"),
        status=_buckets("status"),
        accessibility=_buckets("accessibility"),
        object_type=object_type_facet,  # type: ignore[call-arg]
    )
