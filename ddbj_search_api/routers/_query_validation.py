"""Query parameter allowlist validation.

FastAPI silently ignores unknown query parameters by default. The
``/entries/*`` and ``/facets/*`` endpoints rely on explicit rejection so
that type-mismatched filters (e.g. ``GET /facets/bioproject?libraryStrategy=WGS``
or ``GET /entries/?host=Homo+sapiens``) surface as 422 instead of being
silently dropped (docs/api-spec.md § エンドポイント固有のパラメータ).

This module also exposes :func:`extra_to_filters`, the conversion
helper that maps an endpoint-specific ``*ExtraQuery`` instance plus
optional base kwargs into a :class:`TypeSpecificFilters` dataclass.
Routers use it to collapse the long type-specific kwarg list of
``_do_search`` / ``_do_facets`` into a single ``filters`` argument.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from fastapi import HTTPException, Request

from ddbj_search_api.schemas.common import DbType
from ddbj_search_api.schemas.queries import TypeSpecificFilters

# Wire-level (alias) names accepted by each Depends() class. Keep these
# synchronised with the ``Query()`` aliases on each parameter.
_PAGINATION_PARAM_NAMES = frozenset({"page", "perPage", "cursor"})
_SEARCH_FILTER_PARAM_NAMES = frozenset(
    {
        "keywords",
        "keywordFields",
        "keywordOperator",
        "organism",
        "organization",
        "publication",
        "grant",
        "datePublishedFrom",
        "datePublishedTo",
        "dateModifiedFrom",
        "dateModifiedTo",
    }
)
_RESPONSE_CONTROL_PARAM_NAMES = frozenset({"sort", "fields", "includeProperties", "includeFacets"})
_DB_XREFS_LIMIT_PARAM_NAMES = frozenset({"dbXrefsLimit", "includeDbXrefs"})
_TYPES_FILTER_PARAM_NAMES = frozenset({"types"})
_FACETS_PARAM_NAMES = frozenset({"facets"})

_BIOPROJECT_EXTRA_PARAM_NAMES = frozenset({"objectTypes", "externalLinkLabel", "projectType"})
_BIOSAMPLE_EXTRA_PARAM_NAMES = frozenset(
    {
        "derivedFromId",
        "host",
        "strain",
        "isolate",
        "geoLocName",
        "collectionDate",
    }
)
_SRA_EXTRA_PARAM_NAMES = frozenset(
    {
        "libraryStrategy",
        "librarySource",
        "librarySelection",
        "platform",
        "instrumentModel",
        "libraryLayout",
        "analysisType",
        "derivedFromId",
        "libraryName",
        "libraryConstructionProtocol",
        "geoLocName",
        "collectionDate",
    }
)
_JGA_EXTRA_PARAM_NAMES = frozenset({"studyType", "datasetType", "externalLinkLabel", "vendor"})
_GEA_EXTRA_PARAM_NAMES = frozenset({"experimentType"})
_METABOBANK_EXTRA_PARAM_NAMES = frozenset({"studyType", "experimentType", "submissionType"})

# DbType → set of accepted type-specific parameter names. Members of the
# same type group share the same set (sra-* / jga-*).
TYPE_GROUP_PARAM_NAMES: dict[DbType, frozenset[str]] = {
    DbType.bioproject: _BIOPROJECT_EXTRA_PARAM_NAMES,
    DbType.biosample: _BIOSAMPLE_EXTRA_PARAM_NAMES,
    DbType.sra_submission: _SRA_EXTRA_PARAM_NAMES,
    DbType.sra_study: _SRA_EXTRA_PARAM_NAMES,
    DbType.sra_experiment: _SRA_EXTRA_PARAM_NAMES,
    DbType.sra_run: _SRA_EXTRA_PARAM_NAMES,
    DbType.sra_sample: _SRA_EXTRA_PARAM_NAMES,
    DbType.sra_analysis: _SRA_EXTRA_PARAM_NAMES,
    DbType.jga_study: _JGA_EXTRA_PARAM_NAMES,
    DbType.jga_dataset: _JGA_EXTRA_PARAM_NAMES,
    DbType.jga_dac: _JGA_EXTRA_PARAM_NAMES,
    DbType.jga_policy: _JGA_EXTRA_PARAM_NAMES,
    DbType.gea: _GEA_EXTRA_PARAM_NAMES,
    DbType.metabobank: _METABOBANK_EXTRA_PARAM_NAMES,
}


def entries_allowed_query_params(db_type: DbType | None) -> frozenset[str]:
    """Allowed query params for ``GET /entries/`` or ``GET /entries/{type}/``.

    ``db_type=None`` is the cross-type endpoint, which accepts ``types``
    instead of any type-specific filter.
    """
    base = (
        _PAGINATION_PARAM_NAMES
        | _SEARCH_FILTER_PARAM_NAMES
        | _RESPONSE_CONTROL_PARAM_NAMES
        | _DB_XREFS_LIMIT_PARAM_NAMES
        | _FACETS_PARAM_NAMES
    )
    if db_type is None:
        return base | _TYPES_FILTER_PARAM_NAMES
    return base | TYPE_GROUP_PARAM_NAMES[db_type]


def facets_allowed_query_params(db_type: DbType | None) -> frozenset[str]:
    """Allowed query params for ``GET /facets`` or ``GET /facets/{type}``.

    ``db_type=None`` is the cross-type endpoint.
    """
    base = _SEARCH_FILTER_PARAM_NAMES | _FACETS_PARAM_NAMES
    if db_type is None:
        return base | _TYPES_FILTER_PARAM_NAMES
    return base | TYPE_GROUP_PARAM_NAMES[db_type]


def reject_unknown_query_params(
    request: Request,
    allowed: frozenset[str],
) -> None:
    """Raise 422 when the request includes a query parameter the endpoint
    does not accept (e.g. a type-specific filter on the cross-type
    endpoint, or a parameter from a different type group)."""
    received = set(request.query_params.keys())
    extra = received - allowed
    if extra:
        raise HTTPException(
            status_code=422,
            detail=("Unknown or out-of-scope query parameter(s) for this endpoint: " + ", ".join(sorted(extra)) + "."),
        )


# All snake_case attribute names that ``TypeSpecificFilters`` exposes;
# precomputed once so :func:`extra_to_filters` does not pay the
# ``dataclasses.fields`` cost per request.
_TYPE_SPECIFIC_FIELD_NAMES: frozenset[str] = frozenset(f.name for f in dataclasses.fields(TypeSpecificFilters))


_SRA_FILTERS_DESC = (
    "Type-specific filters (shared across sra-*): "
    "libraryStrategy/librarySource/librarySelection/platform/instrumentModel/libraryLayout/analysisType (term); "
    "derivedFromId (nested); "
    "libraryName/libraryConstructionProtocol/geoLocName/collectionDate (text)."
)
_JGA_FILTERS_DESC = (
    "Type-specific filters (shared across jga-*): "
    "studyType/datasetType (term); externalLinkLabel (nested); vendor (text)."
)

# One-line summary of the type-specific filters available on each
# DbType, used in handler docstrings so SDK / Redoc descriptions hint
# at the parameter set without forcing the reader to scan the OpenAPI
# parameter list.  Members of the same type group share the same
# string (sra-* / jga-*).
TYPE_GROUP_FILTERS_DESC: dict[DbType, str] = {
    DbType.bioproject: ("Type-specific filters: objectTypes (term), externalLinkLabel (nested), projectType (text)."),
    DbType.biosample: (
        "Type-specific filters: derivedFromId (nested); host/strain/isolate/geoLocName/collectionDate (text)."
    ),
    DbType.sra_submission: _SRA_FILTERS_DESC,
    DbType.sra_study: _SRA_FILTERS_DESC,
    DbType.sra_experiment: _SRA_FILTERS_DESC,
    DbType.sra_run: _SRA_FILTERS_DESC,
    DbType.sra_sample: _SRA_FILTERS_DESC,
    DbType.sra_analysis: _SRA_FILTERS_DESC,
    DbType.jga_study: _JGA_FILTERS_DESC,
    DbType.jga_dataset: _JGA_FILTERS_DESC,
    DbType.jga_dac: _JGA_FILTERS_DESC,
    DbType.jga_policy: _JGA_FILTERS_DESC,
    DbType.gea: "Type-specific filter: experimentType (term).",
    DbType.metabobank: "Type-specific filters: studyType/experimentType/submissionType (term).",
}


def extra_to_filters(extra: object | None = None, **base: Any) -> TypeSpecificFilters:
    """Build a :class:`TypeSpecificFilters` from an ``*ExtraQuery``.

    ``extra`` is an instance of one of the type-group ``*ExtraQuery``
    classes (or ``None`` for the cross-type endpoint, which has no
    type-specific filters). Snake_case attributes whose name matches a
    ``TypeSpecificFilters`` field are copied verbatim; missing
    attributes default to ``None``. ``base`` lets callers pre-set
    fields that come from outside the ``*ExtraQuery`` (e.g. ``types``
    from :class:`TypesFilterQuery`); explicit ``base`` values take
    precedence over attributes on ``extra``.
    """
    kwargs: dict[str, Any] = dict(base)
    if extra is not None:
        for name in _TYPE_SPECIFIC_FIELD_NAMES:
            if name in kwargs:
                continue
            if hasattr(extra, name):
                kwargs[name] = getattr(extra, name)
    return TypeSpecificFilters(**kwargs)
