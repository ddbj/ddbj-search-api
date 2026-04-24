"""Tier-based field allowlist and operator matrix.

3 段構成:
- Tier 1 (横断可、8 field): identifier / text / organism / date 系の基本 field。
- Tier 2 (横断可、converter 側正規化済の共通 field、2 field): submitter / publication。
- Tier 3 (単一 DB 指定必須、25 unique / per-DB 集計 28 field): DB 特化 field。

SSOT: db-portal/docs/search.md §フィールド構成 (3 層) / §演算子マトリクス、
db-portal/docs/search-backends.md §バックエンド変換 (Tier 1/2/3 x ES/ARSA/TXSearch)。
API 側が allowlist の唯一の source of truth。
"""

from __future__ import annotations

from typing import Literal, TypeAlias

from ddbj_search_api.search.dsl.ast import ValueKind

FieldType: TypeAlias = Literal["identifier", "text", "organism", "date", "enum", "number"]
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

# 横断検索で使える Tier 2 (converter 側で正規化済の field)。
# SSOT: search-backends.md L546-554 Tier 2 共通フィールド。
TIER2_FIELDS: frozenset[str] = frozenset(
    {
        "submitter",
        "publication",
    },
)

# 単一 DB 選択時のみ使える Tier 3 (DB 特化 field)。
# unique 25 field、ただし per-DB 集計は 28 (grant_agency / study_type / experiment_type が 2 DB 間で shared)。
# SSOT: search-backends.md L560-575 Tier 3 DB 別フィールド。
# 未 allowlist 化で保留中の候補 field: BioSample attributes 系 6 field / JGA principal_investigator /
# submitting_organization / BioProject project_type の INSDC 値域 / Taxonomy japanese_name
# (staging TXSearch に field 不在)。
TIER3_FIELDS: frozenset[str] = frozenset(
    {
        # BioProject (2): grant_agency は JGA と共通
        "project_type",
        "grant_agency",
        # SRA 5 fields
        "library_strategy",
        "library_source",
        "library_layout",
        "platform",
        "instrument_model",
        # JGA (2): study_type は MetaboBank と共通。grant_agency は BioProject と共通
        "study_type",
        # GEA (1) + MetaboBank (3): experiment_type は両方で共通
        "experiment_type",
        # MetaboBank exclusive
        "submission_type",
        # Trad (5) — ARSA
        "division",
        "molecular_type",
        "sequence_length",
        "feature_gene_name",
        "reference_journal",
        # Taxonomy (10) — TXSearch。japanese_name は staging TXSearch に field 不在のため allowlist 外
        "rank",
        "lineage",
        "kingdom",
        "phylum",
        "class",
        "order",
        "family",
        "genus",
        "species",
        "common_name",
    },
)

ALL_ALLOWED_FIELDS: frozenset[str] = TIER1_FIELDS | TIER2_FIELDS | TIER3_FIELDS

FIELD_TYPES: dict[str, FieldType] = {
    # === Tier 1 (cross) ===
    "identifier": "identifier",
    "title": "text",
    "description": "text",
    "organism": "organism",
    "date_published": "date",
    "date_modified": "date",
    "date_created": "date",
    "date": "date",
    # === Tier 2 (cross, converter-normalized) ===
    "submitter": "text",
    "publication": "identifier",
    # === Tier 3 BioProject ===
    "project_type": "enum",
    "grant_agency": "text",
    # === Tier 3 SRA ===
    "library_strategy": "enum",
    "library_source": "enum",
    "library_layout": "enum",
    "platform": "enum",
    "instrument_model": "text",
    # === Tier 3 JGA ===
    "study_type": "enum",
    # === Tier 3 GEA / MetaboBank ===
    # experiment_type は spec L562 で SRA 同等の enum 想定だが、converter 実装は list[str]。
    # ここでは text 型として開放し、enum 値域検証は converter 側での正規化完了後に導入する。
    "experiment_type": "text",
    "submission_type": "text",
    # === Tier 3 Trad (ARSA) ===
    "division": "enum",
    "molecular_type": "enum",
    "sequence_length": "number",
    "feature_gene_name": "text",
    "reference_journal": "text",
    # === Tier 3 Taxonomy (TXSearch) ===
    "rank": "enum",
    "lineage": "text",
    "kingdom": "text",
    "phylum": "text",
    "class": "text",
    "order": "text",
    "family": "text",
    "genus": "text",
    "species": "text",
    "common_name": "text",
}

# (field_type, value_kind) → 導出される operator。
# 含まれない組み合わせは invalid-operator-for-field となる。
OPERATOR_BY_KIND: dict[tuple[FieldType, ValueKind], Operator] = {
    # === Tier 1 ===
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
    # === enum / number ===
    # enum: 列挙値は equals / not_equals のみ。DSL では not_equals は NOT FieldClause で表現。
    # 空白含みの値 (例: "VIRAL RNA") を扱えるよう phrase も許可。
    ("enum", "word"): "eq",
    ("enum", "phrase"): "eq",
    # number: sequence_length などの整数値。range (between) と equals をサポート。
    ("number", "word"): "eq",
    ("number", "range"): "between",
}

# Tier 3 field → 使用可能な DbPortalDb 値の tuple (cross-mode 拒否時の detail 文字列用)。
# SSOT: search-backends.md L530-575 のバックエンドマッピング表。
# 複数 DB 共通の field (grant_agency / study_type / experiment_type) は候補 DB を列挙して提示する。
TIER3_FIELD_DBS: dict[str, tuple[str, ...]] = {
    # BioProject-only
    "project_type": ("bioproject",),
    # BioProject + JGA (jga-study のみ)
    "grant_agency": ("bioproject", "jga"),
    # SRA-only (sra-experiment のみ実ヒット、他 subtype は ES mapping に field 不在)
    "library_strategy": ("sra",),
    "library_source": ("sra",),
    "library_layout": ("sra",),
    "platform": ("sra",),
    "instrument_model": ("sra",),
    # JGA + MetaboBank
    "study_type": ("jga", "metabobank"),
    # GEA + MetaboBank
    "experiment_type": ("gea", "metabobank"),
    # MetaboBank-only
    "submission_type": ("metabobank",),
    # Trad-only (Solr ARSA backend)
    "division": ("trad",),
    "molecular_type": ("trad",),
    "sequence_length": ("trad",),
    "feature_gene_name": ("trad",),
    "reference_journal": ("trad",),
    # Taxonomy-only (TXSearch) 10 field
    "rank": ("taxonomy",),
    "lineage": ("taxonomy",),
    "kingdom": ("taxonomy",),
    "phylum": ("taxonomy",),
    "class": ("taxonomy",),
    "order": ("taxonomy",),
    "family": ("taxonomy",),
    "genus": ("taxonomy",),
    "species": ("taxonomy",),
    "common_name": ("taxonomy",),
}


def field_tier(field: str) -> Tier | None:
    if field in TIER1_FIELDS:
        return "tier1"
    if field in TIER2_FIELDS:
        return "tier2"
    if field in TIER3_FIELDS:
        return "tier3"
    return None
