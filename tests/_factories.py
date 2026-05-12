"""Test data factories for the converter entry types.

The required *list* fields are sourced from :mod:`tests._required_list_fields`
(also consumed by other tests). The scalar / optional fields are defined
inline here so a single schema change touches one location only.
"""

from __future__ import annotations

from typing import Any

from tests._required_list_fields import (
    REQUIRED_LIST_FIELDS_BIOPROJECT,
    REQUIRED_LIST_FIELDS_BIOSAMPLE,
    REQUIRED_LIST_FIELDS_GEA,
    REQUIRED_LIST_FIELDS_JGA,
    REQUIRED_LIST_FIELDS_METABOBANK,
    REQUIRED_LIST_FIELDS_SRA,
)

# Optional common fields that surface in test dicts but accept ``None``.
_COMMON_OPTIONAL: dict[str, Any] = {
    "name": None,
    "organism": None,
    "title": None,
    "description": None,
    "dateCreated": None,
    "dateModified": None,
    "datePublished": None,
}


def _empty_lists(fields: list[str]) -> dict[str, Any]:
    """Return ``{field: []}`` for every required-list field name."""
    return {field: [] for field in fields}


def make_bioproject_dict(**overrides: Any) -> dict[str, Any]:
    """Minimal valid BioProject dict (override fields via kwargs)."""
    base: dict[str, Any] = {
        "identifier": "PRJDB1",
        "properties": {},
        "isPartOf": "bioproject",
        "type": "bioproject",
        "objectType": "BioProject",
        "url": "https://example.com/PRJDB1",
        "status": "public",
        "accessibility": "public-access",
        **_empty_lists(REQUIRED_LIST_FIELDS_BIOPROJECT),
        **_COMMON_OPTIONAL,
    }
    base.update(overrides)
    return base


def make_biosample_dict(**overrides: Any) -> dict[str, Any]:
    """Minimal valid BioSample dict (override fields via kwargs)."""
    base: dict[str, Any] = {
        "identifier": "SAMD00000001",
        "properties": {},
        "isPartOf": "biosample",
        "type": "biosample",
        "url": "https://example.com/SAMD00000001",
        "package": None,
        "status": "public",
        "accessibility": "public-access",
        **_empty_lists(REQUIRED_LIST_FIELDS_BIOSAMPLE),
        **_COMMON_OPTIONAL,
    }
    base.update(overrides)
    return base


def make_sra_dict(**overrides: Any) -> dict[str, Any]:
    """Minimal valid SRA dict (defaults to sra-run; override ``type``)."""
    base: dict[str, Any] = {
        "identifier": "DRR000001",
        "properties": {},
        "isPartOf": "sra",
        "type": "sra-run",
        "url": "https://example.com/DRR000001",
        "libraryLayout": None,
        "platform": None,
        "analysisType": None,
        "status": "public",
        "accessibility": "public-access",
        **_empty_lists(REQUIRED_LIST_FIELDS_SRA),
        **_COMMON_OPTIONAL,
    }
    base.update(overrides)
    return base


def make_jga_dict(**overrides: Any) -> dict[str, Any]:
    """Minimal valid JGA dict (defaults to jga-study; override ``type``)."""
    base: dict[str, Any] = {
        "identifier": "JGAS000001",
        "properties": {},
        "isPartOf": "jga",
        "type": "jga-study",
        "url": "https://example.com/JGAS000001",
        "status": "public",
        "accessibility": "controlled-access",
        **_empty_lists(REQUIRED_LIST_FIELDS_JGA),
        **_COMMON_OPTIONAL,
    }
    base.update(overrides)
    return base


def make_gea_dict(**overrides: Any) -> dict[str, Any]:
    """Minimal valid GEA dict (override fields via kwargs)."""
    base: dict[str, Any] = {
        "identifier": "E-GEAD-1",
        "properties": {},
        "isPartOf": "gea",
        "type": "gea",
        "url": "https://example.com/E-GEAD-1",
        "status": "public",
        "accessibility": "public-access",
        **_empty_lists(REQUIRED_LIST_FIELDS_GEA),
        **_COMMON_OPTIONAL,
    }
    base.update(overrides)
    return base


def make_metabobank_dict(**overrides: Any) -> dict[str, Any]:
    """Minimal valid MetaboBank dict (override fields via kwargs)."""
    base: dict[str, Any] = {
        "identifier": "MTBKS1",
        "properties": {},
        "isPartOf": "metabobank",
        "type": "metabobank",
        "url": "https://example.com/MTBKS1",
        "status": "public",
        "accessibility": "public-access",
        **_empty_lists(REQUIRED_LIST_FIELDS_METABOBANK),
        **_COMMON_OPTIONAL,
    }
    base.update(overrides)
    return base
