"""Solr response mappers for ARSA (Trad) and TXSearch (NCBI Taxonomy).

Convert raw Solr ``/select`` JSON to the unified ``DbPortalHit`` /
``DbPortalHitsResponse`` shapes consumed by ``/db-portal/search``.
Mappers are pure; missing fields map to ``None`` rather than raising so
that ARSA schema drift does not propagate to 500 responses.
"""

from __future__ import annotations

from typing import Any

from ddbj_search_api.schemas.db_portal import DbPortalHit, DbPortalHitsResponse

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


def _join_nonempty(parts: list[str | None], sep: str = " / ") -> str | None:
    cleaned = [p for p in parts if p is not None and p != ""]
    if not cleaned:
        return None
    return sep.join(cleaned)


def _lineage_to_str(value: Any) -> str | None:
    if isinstance(value, list):
        non_empty = [str(x) for x in value if x is not None and x != ""]
        return "; ".join(non_empty) if non_empty else None
    if isinstance(value, str):
        return value or None
    return None


def _arsa_description(
    definition: Any,
    organism: Any,
    division: Any,
) -> str | None:
    parts: list[str | None] = [
        str(definition) if isinstance(definition, str) and definition else None,
        str(organism) if isinstance(organism, str) and organism else None,
        f"Division: {division}" if isinstance(division, str) and division else None,
    ]
    return _join_nonempty(parts)


def _txsearch_description(
    common_name: Any,
    rank: Any,
    lineage: Any,
) -> str | None:
    common = _first_or_self(common_name)
    common_str = common if isinstance(common, str) and common else None
    rank_str = f"rank: {rank}" if isinstance(rank, str) and rank else None
    lineage_str = _lineage_to_str(lineage)
    lineage_part = f"lineage: {lineage_str}" if lineage_str else None
    return _join_nonempty([common_str, rank_str, lineage_part])


def arsa_docs_to_hits(docs: list[dict[str, Any]]) -> list[DbPortalHit]:
    hits: list[DbPortalHit] = []
    for doc in docs:
        acc = doc.get("PrimaryAccessionNumber")
        organism_raw = doc.get("Organism")
        payload: dict[str, Any] = {
            "identifier": str(acc) if acc is not None else None,
            "type": "trad",
            "title": doc.get("Definition"),
            "organism": {"name": organism_raw} if organism_raw else None,
            "description": _arsa_description(
                doc.get("Definition"),
                organism_raw,
                doc.get("Division"),
            ),
            "datePublished": _parse_arsa_date(doc.get("Date")),
            "url": f"{_ARSA_URL_PREFIX}{acc}/" if acc else None,
            "sameAs": None,
            "dbXrefs": None,
        }
        if "Division" in doc and doc["Division"] is not None:
            payload["division"] = doc["Division"]
        hits.append(DbPortalHit.model_validate(payload))
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
        payload: dict[str, Any] = {
            "identifier": tax_id,
            "type": "taxonomy",
            "title": scientific,
            "organism": organism,
            "description": _txsearch_description(
                doc.get("common_name"),
                doc.get("rank"),
                doc.get("lineage"),
            ),
            "datePublished": None,
            "url": f"{_TAXONOMY_URL_PREFIX}{tax_id}" if tax_id else None,
            "sameAs": None,
            "dbXrefs": None,
        }
        if "rank" in doc and doc["rank"] is not None:
            payload["rank"] = doc["rank"]
        common_scalar = _first_or_self(doc.get("common_name"))
        if common_scalar is not None:
            payload["commonName"] = common_scalar
        japanese_scalar = _first_or_self(doc.get("japanese_name"))
        if japanese_scalar is not None:
            payload["japaneseName"] = japanese_scalar
        hits.append(DbPortalHit.model_validate(payload))
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
