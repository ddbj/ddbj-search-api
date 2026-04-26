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


def parse_facets(aggregations: dict[str, Any]) -> Facets:
    """Convert ES aggregation buckets to a Facets model.

    Each ``Facets`` field is populated from the matching ES aggregation
    name (mirroring ``_FACET_AGG_SPECS`` in :mod:`ddbj_search_api.es.query`).
    Aggregations omitted from the ES response leave the corresponding
    ``Facets`` field as ``None`` so that callers can distinguish
    "aggregated but no buckets" (``[]``) from "not aggregated" (``None``).
    """

    def _optional(agg_name: str) -> list[FacetBucket] | None:
        if agg_name not in aggregations:
            return None
        agg = aggregations[agg_name]
        return [FacetBucket(value=b["key"], count=b["doc_count"]) for b in agg.get("buckets", [])]

    # Use ``model_validate`` so the dict keys can match the public alias
    # names (camelCase) that the Facets schema exposes — keeping the
    # Python attribute names (snake_case) here would force a long list of
    # ``# type: ignore[call-arg]`` annotations against the Pydantic plugin.
    return Facets.model_validate(
        {
            "type": _optional("type"),
            "organism": _optional("organism"),
            "accessibility": _optional("accessibility"),
            "objectType": _optional("objectType"),
            "libraryStrategy": _optional("libraryStrategy"),
            "librarySource": _optional("librarySource"),
            "librarySelection": _optional("librarySelection"),
            "platform": _optional("platform"),
            "instrumentModel": _optional("instrumentModel"),
            "experimentType": _optional("experimentType"),
            "studyType": _optional("studyType"),
            "submissionType": _optional("submissionType"),
        }
    )
