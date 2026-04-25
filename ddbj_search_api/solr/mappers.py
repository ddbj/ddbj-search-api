"""Solr response mappers for ARSA (Trad) and TXSearch (NCBI Taxonomy).

Convert raw Solr ``/select`` JSON to the unified ``DbPortalHit`` discriminated
union and ``DbPortalHitsResponse`` envelope consumed by
``/db-portal/search?db=trad|taxonomy``.  Mappers are pure; missing fields
map to ``None`` rather than raising so that ARSA / TXSearch schema drift
does not propagate to 500 responses.
"""

from __future__ import annotations

import re
from typing import Any

from ddbj_search_api.schemas.db_portal import (
    DbPortalHit,
    DbPortalHitsResponse,
    _DbPortalHitAdapter,
)

# GenBank Feature qualifier ``/db_xref="taxon:NNNN"`` — NCBI TaxID embedded in
# ARSA Feature blocks.  Captures the digits after ``taxon:``; whitespace or
# quoting around the value varies so the regex is intentionally lenient.
_FEATURE_TAXON_RE = re.compile(r'/db_xref="taxon:(\d+)"')

_DEEP_PAGING_LIMIT = 10_000
_ARSA_URL_PREFIX = "https://getentry.ddbj.nig.ac.jp/getentry/na/"
_TAXONOMY_URL_PREFIX = "https://ddbj.nig.ac.jp/resource/taxonomy/"


def _parse_arsa_date(raw: Any) -> str | None:
    """``YYYYMMDD`` (str) → ``YYYY-MM-DD``; anything else → ``None``."""
    if not isinstance(raw, str) or len(raw) != 8 or not raw.isdigit():
        return None
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"


def _first_or_self(value: Any) -> Any:
    """Return ``value[0]`` if value is a non-empty list, else value.

    TXSearch stores names (``common_name``, ``japanese_name`` etc.) as
    multi-valued lists even when the list only holds one element; the
    db-portal UI consumes a scalar per hit.
    """
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _extract_taxon_id(feature: Any) -> str | None:
    """Pull the TaxID out of a GenBank source feature's ``/db_xref="taxon:NNNN"``.

    ARSA's ``Feature`` field holds the flat GenBank feature table as a list of
    multi-line strings; the source feature always carries a ``db_xref`` with
    ``taxon:`` when the record has an organism linked to NCBI Taxonomy.  The
    scan stops at the first match to keep per-hit cost low; records without
    a source feature (or without a taxon xref) return ``None``.
    """
    if isinstance(feature, list):
        for entry in feature:
            if not isinstance(entry, str):
                continue
            match = _FEATURE_TAXON_RE.search(entry)
            if match is not None:
                return match.group(1)
        return None
    if isinstance(feature, str):
        match = _FEATURE_TAXON_RE.search(feature)
        return match.group(1) if match is not None else None
    return None


def _drop_self_from_lineage(value: Any, self_name: Any) -> Any:
    """TXSearch prepends the taxon's own scientific name to ``lineage``.

    Strip it so downstream consumers get the NCBI-standard ancestor-only
    lineage.  Preserves the original container shape (list → list,
    string → string) and leaves the payload untouched when the head does
    not match ``self_name``.
    """
    if isinstance(value, list):
        if value and isinstance(value[0], str) and value[0] == self_name:
            return value[1:]
        return value
    return value


def _to_int_or_none(value: Any) -> int | None:
    """Safe int cast: str/int → int, anything else (including list) → None.

    ARSA の SequenceLength は doc で string ("5000") or int (5000) のケースがあるため。
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def arsa_docs_to_hits(docs: list[dict[str, Any]]) -> list[DbPortalHit]:
    hits: list[DbPortalHit] = []
    for doc in docs:
        acc = doc.get("PrimaryAccessionNumber")
        organism_raw = doc.get("Organism")
        organism: dict[str, Any] | None = None
        if organism_raw:
            organism = {"name": organism_raw}
            tax_id = _extract_taxon_id(doc.get("Feature"))
            if tax_id is not None:
                organism["identifier"] = tax_id
        # ``description`` is left null: Definition is already in ``title`` and
        # Organism / Division surface in their own fields, so synthesizing a
        # joined blurb only duplicates information in the UI.
        payload: dict[str, Any] = {
            "identifier": str(acc) if acc is not None else None,
            "type": "trad",
            "title": doc.get("Definition"),
            "organism": organism,
            "description": None,
            "datePublished": _parse_arsa_date(doc.get("Date")),
            "url": f"{_ARSA_URL_PREFIX}{acc}/" if acc else None,
            "sameAs": None,
            "dbXrefs": None,
            "division": doc.get("Division"),
            "molecularType": doc.get("MolecularType"),
            "sequenceLength": _to_int_or_none(doc.get("SequenceLength")),
        }
        hits.append(_DbPortalHitAdapter.validate_python(payload))
    return hits


def txsearch_docs_to_hits(docs: list[dict[str, Any]]) -> list[DbPortalHit]:
    hits: list[DbPortalHit] = []
    for doc in docs:
        tax_id_raw = doc.get("tax_id")
        tax_id = str(tax_id_raw) if tax_id_raw is not None else None
        scientific = doc.get("scientific_name")
        organism: dict[str, Any] | None = None
        if scientific or tax_id:
            organism = {}
            if scientific:
                organism["name"] = scientific
            if tax_id:
                organism["identifier"] = tax_id
        # ``description`` is left null: common_name / rank / lineage surface
        # in their own fields and the previous ``/``-joined blurb only
        # duplicated them in the UI.  ``lineage`` also drops its own head
        # entry so the list matches NCBI's ancestor-only convention.
        payload: dict[str, Any] = {
            "identifier": tax_id,
            "type": "taxonomy",
            "title": scientific,
            "organism": organism,
            "description": None,
            "datePublished": None,
            "url": f"{_TAXONOMY_URL_PREFIX}{tax_id}" if tax_id else None,
            "sameAs": None,
            "dbXrefs": None,
            "rank": doc.get("rank"),
            "commonName": _first_or_self(doc.get("common_name")),
            "japaneseName": _first_or_self(doc.get("japanese_name")),
            "lineage": _drop_self_from_lineage(doc.get("lineage"), scientific),
        }
        hits.append(_DbPortalHitAdapter.validate_python(payload))
    return hits


def _envelope_from_solr(
    resp: dict[str, Any],
    *,
    hits: list[DbPortalHit],
    page: int,
    per_page: int,
) -> DbPortalHitsResponse:
    response = resp.get("response") or {}
    try:
        total = int(response.get("numFound", 0))
    except (TypeError, ValueError):
        total = 0
    return DbPortalHitsResponse(  # type: ignore[call-arg]
        total=total,
        hits=hits,
        hard_limit_reached=(total >= _DEEP_PAGING_LIMIT),
        page=page,
        per_page=per_page,
        next_cursor=None,
        has_next=(page * per_page < total),
    )


def arsa_response_to_envelope(
    resp: dict[str, Any],
    *,
    page: int,
    per_page: int,
    sort: str | None,
) -> DbPortalHitsResponse:
    _ = sort
    docs = (resp.get("response") or {}).get("docs") or []
    hits = arsa_docs_to_hits(docs)
    return _envelope_from_solr(resp, hits=hits, page=page, per_page=per_page)


def txsearch_response_to_envelope(
    resp: dict[str, Any],
    *,
    page: int,
    per_page: int,
    sort: str | None,
) -> DbPortalHitsResponse:
    _ = sort
    docs = (resp.get("response") or {}).get("docs") or []
    hits = txsearch_docs_to_hits(docs)
    return _envelope_from_solr(resp, hits=hits, page=page, per_page=per_page)
