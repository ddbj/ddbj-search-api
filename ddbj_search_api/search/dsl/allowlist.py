"""Tier-based field allowlist and operator matrix.

3 段構成:
- Tier 1 (横断可、9 field): identifier / text / organism / date 系の基本 field + accessibility。
- Tier 2 (横断可、converter 側正規化済の共通 field、2 field): submitter / publication。
- Tier 3 (単一 DB 指定必須、40 unique / per-DB 集計 46 field): DB 特化 field。

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
        # 全 ES backed 6 DB 共通 (public-access / controlled-access)。
        # Solr backed (Trad / Taxonomy) には field 不在のため cross-mode で degenerate される。
        "accessibility",
    },
)

# 横断検索で使える Tier 2 (converter 側で正規化済の field)。
TIER2_FIELDS: frozenset[str] = frozenset(
    {
        "submitter",
        "publication",
    },
)

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
    # 全 ES backed 6 DB 共通 controlled vocab (public-access / controlled-access)
    "accessibility": "enum",
    # === Tier 2 (cross, converter-normalized) ===
    "submitter": "text",
    "publication": "identifier",
    # === Tier 3 BioProject ===
    "project_type": "enum",
    "grant_agency": "text",
    "relevance": "enum",
    # === Tier 3 BioSample (converter 0.3.0 で top-level 化) ===
    # converter mapping は text + keyword:256 (host/strain/isolate) または text 単独 (geo_loc_name/
    # collection_date)。値域が free text 寄り (Homo sapiens / liver / Tokyo, Japan / 2020-04 等) なので text 型。
    "host": "text",
    "strain": "text",
    "isolate": "text",
    "geo_loc_name": "text",
    "collection_date": "text",
    # package は object{name:keyword, displayName:keyword}、DSL 経由では package.name.keyword に
    # 解決 (compiler_es.py)。model は keyword 単独。両者とも controlled vocab 寄り。
    "package": "enum",
    "model": "enum",
    # === Tier 3 SRA ===
    "library_strategy": "enum",
    "library_source": "enum",
    "library_layout": "enum",
    # library_selection は sra-experiment のみ field 存在、INSDC controlled vocab (RANDOM / PCR 等)
    "library_selection": "enum",
    "platform": "enum",
    "instrument_model": "text",
    "library_name": "text",
    "library_construction_protocol": "text",
    # analysis_type / dataset_type は controlled vocab に近い使われ方をするが、converter 側で
    # free string として受けるため、API 側でも text 型で開放する。
    "analysis_type": "text",
    # === Tier 3 JGA ===
    "study_type": "enum",
    "vendor": "text",
    "dataset_type": "text",
    # === Tier 3 SRA / JGA 共通 ===
    # type は subtype 識別子 (SRA: sra-submission..sra-analysis、JGA: jga-study..jga-policy)。
    # 値域 validation は ES 側に委譲、未知値は 0 件で返る。
    "type": "enum",
    # === Tier 3 GEA / MetaboBank ===
    # experiment_type は spec 上 SRA 同等の enum 想定だが、converter 実装は list[str]。
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
# 複数 DB 共通の field (grant_agency / study_type / experiment_type / geo_loc_name / collection_date)
# は候補 DB を列挙して提示する。Tier 3 のフィールド集合 (``TIER3_FIELDS``) はこの dict のキーから導出する。
TIER3_FIELD_DBS: dict[str, tuple[str, ...]] = {
    # BioProject-only
    "project_type": ("bioproject",),
    "relevance": ("bioproject",),
    # BioProject + JGA (jga-study のみ)
    "grant_agency": ("bioproject", "jga"),
    # BioSample-only
    "host": ("biosample",),
    "strain": ("biosample",),
    "isolate": ("biosample",),
    "package": ("biosample",),
    "model": ("biosample",),
    # BioSample + SRA (SRA-sample のみ field を持つ)
    "geo_loc_name": ("biosample", "sra"),
    "collection_date": ("biosample", "sra"),
    # SRA-only (subtype 別: library_* / platform / instrument_model は sra-experiment、
    # analysis_type は sra-analysis、他 subtype は ES mapping に field 不在)
    "library_strategy": ("sra",),
    "library_source": ("sra",),
    "library_layout": ("sra",),
    "library_selection": ("sra",),
    "platform": ("sra",),
    "instrument_model": ("sra",),
    "library_name": ("sra",),
    "library_construction_protocol": ("sra",),
    "analysis_type": ("sra",),
    # JGA + MetaboBank
    "study_type": ("jga", "metabobank"),
    # JGA-only (vendor=jga-study、dataset_type=jga-dataset)
    "vendor": ("jga",),
    "dataset_type": ("jga",),
    # SRA + JGA 共通 (subtype 識別子)
    "type": ("sra", "jga"),
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

TIER3_FIELDS: frozenset[str] = frozenset(TIER3_FIELD_DBS)

ALL_ALLOWED_FIELDS: frozenset[str] = TIER1_FIELDS | TIER2_FIELDS | TIER3_FIELDS


def field_tier(field: str) -> Tier | None:
    if field in TIER1_FIELDS:
        return "tier1"
    if field in TIER2_FIELDS:
        return "tier2"
    if field in TIER3_FIELDS:
        return "tier3"
    return None
