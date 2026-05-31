"""Utility helpers."""

from __future__ import annotations

import logging
from typing import Any, cast

from ddbj_search_converter.jsonl.utils import to_xref
from ddbj_search_converter.schema import XrefType

from ddbj_search_api.es.query import _FACET_AGG_SPECS
from ddbj_search_api.schemas.common import FacetBucket, Facets, OrganismFacetBucket
from ddbj_search_api.schemas.db_portal import DbPortalFacets

logger = logging.getLogger(__name__)


def format_xref(type_: str, accession: str) -> str:
    """Format a single xref as a JSON object string."""
    xref = to_xref(accession, type_hint=cast(XrefType, type_))

    return xref.model_dump_json(by_alias=True)


def format_xref_dict(type_: str, accession: str) -> dict[str, Any]:
    """Format a single xref as a dict (camelCase aliases applied).

    Equivalent to ``json.loads(format_xref(...))`` but avoids the
    JSON serialize/parse round-trip.  Used by the bulk endpoint, which
    builds the response by ``json.dumps``-ing a doc-with-injected-
    dbXrefs dict in one pass rather than splicing serialized fragments.
    """
    xref = to_xref(accession, type_hint=cast(XrefType, type_))
    return xref.model_dump(by_alias=True)


def _unwrap_terms_agg(aggregations: dict[str, Any], agg_name: str) -> dict[str, Any] | None:
    """Return the terms aggregation body for ``agg_name`` (or ``None`` if absent).

    Handles both shapes the db-portal facet path can produce:

    - plain terms (``facetSelfExclude`` off): ``aggregations[name]`` already
      carries ``buckets`` directly.
    - self-exclusion filter-wrap (``facetSelfExclude`` on): ``aggregations[name]``
      is a ``filter`` aggregation (``{"doc_count": N, name: {"buckets": ...}}``),
      so the inner same-named terms aggregation holds the buckets (see
      :func:`ddbj_search_api.es.query.build_self_excluding_facet_aggs`).

    Returning ``None`` when the name is missing lets callers distinguish "not
    aggregated" from "aggregated but empty".
    """
    agg: dict[str, Any] | None = aggregations.get(agg_name)
    if agg is None:
        return None
    if "buckets" in agg:
        return agg
    inner = agg.get(agg_name)

    return inner if isinstance(inner, dict) else None


def _optional_organism(aggregations: dict[str, Any]) -> list[OrganismFacetBucket] | None:
    """Build the ``organism`` facet bucket list from an ES aggregation.

    The aggregation is expected to be ``terms`` on ``organism.identifier``
    with a ``name`` sub-aggregation (``terms`` on ``organism.name.keyword``,
    ``size=1``) — see ``_FACET_AGG_SPECS["organism"]`` in
    :mod:`ddbj_search_api.es.query`.  Under ``facetSelfExclude`` the terms
    aggregation is wrapped in a ``filter`` aggregation; :func:`_unwrap_terms_agg`
    normalises both shapes.

    If the sub-aggregation produces no buckets for an entry (a rare data-
    quality case where ``organism.identifier`` is set but ``organism.name``
    is missing), the bucket's ``label`` falls back to the TaxID itself so
    the response stays valid against ``OrganismFacetBucket`` (which marks
    ``label`` required).  The fallback is logged at ``WARNING`` level to
    surface the data-quality issue.
    """
    agg = _unwrap_terms_agg(aggregations, "organism")
    if agg is None:
        return None
    buckets: list[OrganismFacetBucket] = []
    for b in agg.get("buckets", []):
        tax_id = b["key"]
        sub_buckets = b.get("name", {}).get("buckets", [])
        if sub_buckets:
            label = sub_buckets[0]["key"]
        else:
            logger.warning(
                "organism facet bucket for TaxID %r has no organism.name sub-bucket; falling back to TaxID as label",
                tax_id,
            )
            label = tax_id
        buckets.append(OrganismFacetBucket(value=tax_id, count=b["doc_count"], label=label))
    return buckets


def _buckets_or_none(aggregations: dict[str, Any], agg_name: str) -> list[FacetBucket] | None:
    """Map one ES ``terms`` aggregation to a ``FacetBucket`` list (or ``None``).

    Returns ``None`` when the aggregation is absent from the ES response
    (so callers can distinguish "not aggregated" from "aggregated but no
    buckets", ``[]``).  Both plain terms and self-exclusion filter-wrap
    shapes are accepted via :func:`_unwrap_terms_agg`.
    """
    agg = _unwrap_terms_agg(aggregations, agg_name)
    if agg is None:
        return None

    return [FacetBucket(value=b["key"], count=b["doc_count"]) for b in agg.get("buckets", [])]


def _es_facets_payload(aggregations: dict[str, Any]) -> dict[str, Any]:
    """Build the ``{facet_name: buckets|None}`` payload shared by the facet models.

    Keys mirror ``_FACET_AGG_SPECS`` (the camelCase aliases exposed by the
    ``Facets`` schema); ``organism`` takes the dedicated label-aware path.
    """
    payload: dict[str, Any] = {
        name: _buckets_or_none(aggregations, name) for name in _FACET_AGG_SPECS if name != "organism"
    }
    payload["organism"] = _optional_organism(aggregations)
    return payload


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
    # ``model_validate`` lets the dict keys match the public alias names
    # (camelCase) that the Facets schema exposes without a long list of
    # ``# type: ignore[call-arg]`` annotations against the Pydantic plugin.
    return Facets.model_validate(_es_facets_payload(aggregations))


def parse_db_portal_es_facets(aggregations: dict[str, Any]) -> DbPortalFacets:
    """Convert ES aggregation buckets to a DbPortalFacets model.

    Same ES facet extraction as :func:`parse_facets`, but returns the
    db-portal envelope so the Solr-only facets (``division`` /
    ``molecularType`` / ``rank`` / ``kingdom``) stay ``None`` — they are
    populated separately from the Solr response via
    :func:`parse_solr_facets`.  Used by the db-portal single-DB ES path
    and the cross-search entries-alias aggregation.
    """
    return DbPortalFacets.model_validate(_es_facets_payload(aggregations))


def _pairs_to_buckets(raw: list[Any]) -> list[FacetBucket]:
    """Convert a Solr ``facet_fields`` flat array to ``FacetBucket`` list.

    Solr returns each terms-facet field as a flat ``[value, count, value,
    count, ...]`` list.  An odd trailing entry (malformed response) is
    ignored defensively rather than raising.
    """
    buckets: list[FacetBucket] = []
    for i in range(0, len(raw) - 1, 2):
        buckets.append(FacetBucket(value=str(raw[i]), count=int(raw[i + 1])))
    return buckets


def parse_solr_facets(facet_counts: dict[str, Any], name_to_field: dict[str, str]) -> DbPortalFacets:
    """Convert a Solr ``facet_counts`` block to a DbPortalFacets model.

    ``name_to_field`` maps each requested db-portal facet name to its Solr
    ``facet.field`` (e.g. ``{"division": "Division"}``).  Every requested
    name becomes a (possibly empty) bucket list, so ``[]`` ("aggregated,
    zero buckets") is distinguished from ``None`` ("not aggregated").
    Facets outside ``name_to_field`` are left ``None``.
    """
    facet_fields = (facet_counts or {}).get("facet_fields") or {}
    payload: dict[str, Any] = {
        name: _pairs_to_buckets(facet_fields.get(solr_field) or []) for name, solr_field in name_to_field.items()
    }
    return DbPortalFacets.model_validate(payload)
