"""Utility helpers."""

from __future__ import annotations

from typing import Any, cast

from ddbj_search_converter.jsonl.utils import to_xref
from ddbj_search_converter.schema import XrefType

from ddbj_search_api.schemas.common import FacetBucket, Facets


def format_xref(type_: str, accession: str) -> str:
    """Format a single xref as a JSON object string."""
    xref = to_xref(accession, type_hint=cast(XrefType, type_))

    return xref.model_dump_json(by_alias=True)


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
        accessibility=_buckets("accessibility"),
        object_type=object_type_facet,  # type: ignore[call-arg]
    )
