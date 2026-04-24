"""Tier-based field allowlist and operator matrix (AP3 / AP6 SSOT).

- AP3 は Tier 1 (8 フィールド) のみ有効化。Tier 2 / Tier 3 は AP6 で追加する。
- SSOT: db-portal/docs/search.md §フィールド構成 (3 層) / §演算子マトリクス。
- API 側が allowlist の唯一の source of truth (decisions.md A2-5)。
"""

from __future__ import annotations

from typing import Literal, TypeAlias

from ddbj_search_api.search.dsl.ast import ValueKind

FieldType: TypeAlias = Literal["identifier", "text", "organism", "date"]
Operator: TypeAlias = Literal["eq", "contains", "starts_with", "wildcard", "between", "gte", "lte"]
Tier: TypeAlias = Literal["tier1", "tier2", "tier3"]

TIER1_FIELDS: frozenset[str] = frozenset(
    {
        "identifier",
        "title",
        "description",
        "organism",
        "date_published",
        "date_modified",
        "date_created",
        "date",
    },
)
TIER2_FIELDS: frozenset[str] = frozenset()  # AP6: {"submitter", "publication"}
TIER3_FIELDS: frozenset[str] = frozenset()  # AP6: DB ごとに追加

ALL_ALLOWED_FIELDS: frozenset[str] = TIER1_FIELDS | TIER2_FIELDS | TIER3_FIELDS

FIELD_TYPES: dict[str, FieldType] = {
    "identifier": "identifier",
    "title": "text",
    "description": "text",
    "organism": "organism",
    "date_published": "date",
    "date_modified": "date",
    "date_created": "date",
    "date": "date",
}

# (field_type, value_kind) → 導出される operator。
# 含まれない組み合わせは invalid-operator-for-field となる。
OPERATOR_BY_KIND: dict[tuple[FieldType, ValueKind], Operator] = {
    ("identifier", "word"): "eq",
    ("identifier", "phrase"): "eq",
    ("identifier", "wildcard"): "wildcard",
    ("text", "word"): "contains",
    ("text", "phrase"): "contains",
    ("text", "wildcard"): "wildcard",
    ("organism", "word"): "eq",
    ("organism", "phrase"): "eq",
    ("date", "date"): "eq",
    ("date", "range"): "between",
}


def field_tier(field: str) -> Tier | None:
    if field in TIER1_FIELDS:
        return "tier1"
    if field in TIER2_FIELDS:
        return "tier2"
    if field in TIER3_FIELDS:
        return "tier3"
    return None
