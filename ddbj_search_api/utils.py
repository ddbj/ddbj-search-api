"""Utility helpers."""

from __future__ import annotations

import logging
from typing import Any, cast

from ddbj_search_converter.jsonl.utils import to_xref
from ddbj_search_converter.schema import XrefType

from ddbj_search_api.es.query import _FACET_AGG_SPECS
from ddbj_search_api.schemas.common import FacetBucket, Facets, OrganismFacetBucket

logger = logging.getLogger(__name__)


def format_xref(type_: str, accession: str) -> str:
    """Format a single xref as a JSON object string."""
    xref = to_xref(accession, type_hint=cast(XrefType, type_))

    return xref.model_dump_json(by_alias=True)


def _optional_organism(aggregations: dict[str, Any]) -> list[OrganismFacetBucket] | None:
    """Build the ``organism`` facet bucket list from an ES aggregation.

    The aggregation is expected to be ``terms`` on ``organism.identifier``
    with a ``name`` sub-aggregation (``terms`` on ``organism.name.keyword``,
    ``size=1``) — see ``_FACET_AGG_SPECS["organism"]`` in
    :mod:`ddbj_search_api.es.query`.

    If the sub-aggregation produces no buckets for an entry (a rare data-
    quality case where ``organism.identifier`` is set but ``organism.name``
    is missing), the bucket's ``label`` falls back to the TaxID itself so
    the response stays valid against ``OrganismFacetBucket`` (which marks
    ``label`` required).  The fallback is logged at ``WARNING`` level to
    surface the data-quality issue.
    """
    if "organism" not in aggregations:
        return None
    agg = aggregations["organism"]
    buckets: list[OrganismFacetBucket] = []
    for b in agg.get("buckets", []):
        tax_id = b["key"]
        sub_buckets = b.get("name", {}).get("buckets", [])
        if sub_buckets:
            label = sub_buckets[0]["key"]
        else:
            logger.warning(
                "organism facet bucket for TaxID %r has no organism.name sub-bucket; "
                "falling back to TaxID as label",
                tax_id,
            )
            label = tax_id
        buckets.append(OrganismFacetBucket(value=tax_id, count=b["doc_count"], label=label))
    return buckets


def parse_facets(aggregations: dict[str, Any]) -> Facets:
    """Convert ES aggregation buckets to a Facets model.

    Each ``Facets`` field is populated from the matching ES aggregation
    name (mirroring ``_FACET_AGG_SPECS`` in :mod:`ddbj_search_api.es.query`).
    Aggregations omitted from the ES response leave the corresponding
    ``Facets`` field as ``None`` so that callers can distinguish
    "aggregated but no buckets" (``[]``) from "not aggregated" (``None``).

    ``organism`` takes a dedicated path (:func:`_optional_organism`)
    because its bucket carries an extra ``label`` extracted from a
    nested ``name`` sub-aggregation; every other facet maps the ES
    bucket key/doc_count straight onto ``FacetBucket``.
    """

    def _optional(agg_name: str) -> list[FacetBucket] | None:
        if agg_name not in aggregations:
            return None
        agg = aggregations[agg_name]
        return [FacetBucket(value=b["key"], count=b["doc_count"]) for b in agg.get("buckets", [])]

    payload: dict[str, Any] = {name: _optional(name) for name in _FACET_AGG_SPECS if name != "organism"}
    payload["organism"] = _optional_organism(aggregations)
    # Use ``model_validate`` so the dict keys can match the public alias
    # names (camelCase) that the Facets schema exposes — keeping the
    # Python attribute names (snake_case) here would force a long list of
    # ``# type: ignore[call-arg]`` annotations against the Pydantic plugin.
    return Facets.model_validate(payload)
