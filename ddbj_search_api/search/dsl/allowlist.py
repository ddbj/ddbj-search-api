"""Tier-based field allowlist and operator matrix.

3 段構成:
- Tier 1 (横断可、11 field): identifier / title / description / name / organism_id /
  organism_name / date 系の基本 field + accessibility。
- Tier 2 (横断可、converter 側正規化済の共通 field、2 field): submitter / publication。
- Tier 3 (単一 DB 指定必須、44 unique / per-DB 集計 53 field): DB 特化 field。

SSOT: db-portal/docs/search.md §フィールド構成 (3 層) / §演算子マトリクス、
db-portal/docs/search-backends.md §バックエンド変換 (Tier 1/2/3 x ES/ARSA/TXSearch)。
API 側が allowlist の唯一の source of truth。
"""

from __future__ import annotations

from typing import Literal, TypeAlias

from ddbj_search_api.search.dsl.ast import ValueKind

FieldType: TypeAlias = Literal["identifier", "text", "date", "enum", "number"]
Operator: TypeAlias = Literal["eq", "contains", "starts_with", "wildcard", "between", "gte", "lte"]
Tier: TypeAlias = Literal["tier1", "tier2", "tier3"]

TIER1_FIELDS: frozenset[str] = frozenset(
    {
        "identifier",
        "title",
        # 全 DB 共通の name (ES common text+keyword)。free-text 既定 5 field の 1 つでもあるが、
        # field-scoped DSL アクセス用に Tier1 text として開放する (学名の organism_name とは別 field)。
        "name",
        "description",
        # 生物種は identifier (taxID exact) / name (学名 match) で 2 field に分ける.
        # REST API の `?organism=<taxId>` は organism_id 側と同じ field を叩く.
        "organism_id",
        "organism_name",
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
    # ES common の name (text+keyword) を text 型 (contains = match_phrase) で開放する。
    "name": "text",
    "description": "text",
    # 生物種 (taxID): keyword `organism.identifier` に term。REST API の `?organism=<taxId>` と同じ.
    "organism_id": "identifier",
    # 生物種 (学名): text `organism.name` に match_phrase (standard analyzer 経由で大文字小文字寛容).
    "organism_name": "text",
    "date_published": "date",
    "date_modified": "date",
    "date_created": "date",
    "date": "date",
    # 全 ES backed 6 DB 共通 controlled vocab (public-access / controlled-access)
    "accessibility": "enum",
    # === Tier 2 (cross, converter-normalized) ===
    "submitter": "text",
    "publication": "text",
    # === Tier 3 BioProject ===
    # ES `objectType` (keyword) に term。BioProject / UmbrellaBioProject の Umbrella 区分。
    # REST API の `?objectTypes=` と同じ field。
    "object_type": "enum",
    # ES `projectType` (text+keyword) を match_phrase。INSDC controlled vocab
    # (genome / metagenome / 等)。REST API の `?projectType=` と同じ field。
    # `object_type` (ES `objectType`、Umbrella 区分) とは別 field。
    "project_type": "text",
    # BioProject + JGA 共通: grant nested の title。REST API の `?grant=` と同じ field。
    "grant_title": "text",
    "grant_agency": "text",
    "relevance": "enum",
    # BioProject + JGA 共通: externalLink nested の label。converter mapping は text
    # (converter common.py の externalLink.label)。ラベル値 ("GEO" / "dbGaP" / "GEO Sample"
    # など) をそのまま検索する用途に合わせ text 型 (contains = match_phrase) で開放する。
    "external_link_label": "text",
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
    # instrument_model は sra-experiment の controlled vocab。facet bucket (.keyword の exact 値)
    # を op=eq で再注入するため enum (term は instrumentModel.keyword に当てる: compiler_es.py)。
    # 値域 validation は ES 側に委譲し、未知値は 0 件で返る。
    "instrument_model": "enum",
    "library_name": "text",
    "library_construction_protocol": "text",
    # BioSample + SRA (sra-sample) 共通: derivedFrom nested の identifier (例: SAMD00012345)。
    # converter mapping は keyword なので identifier 型 (eq / wildcard) で開放する。
    "derived_from_id": "identifier",
    # analysis_type / dataset_type は controlled vocab。facet bucket (.keyword の exact 値) を
    # op=eq で再注入するため enum (term は analysisType.keyword / datasetType.keyword に当てる:
    # compiler_es.py)。値域 validation は ES 側に委譲し、未知値は 0 件で返る (既存 type / model と同方針)。
    "analysis_type": "enum",
    # === Tier 3 JGA ===
    "study_type": "enum",
    "vendor": "text",
    # dataset_type は jga-dataset の controlled vocab。enum (term は datasetType.keyword)。
    "dataset_type": "enum",
    # === Tier 3 SRA / JGA 共通 ===
    # type は subtype 識別子 (SRA: sra-submission..sra-analysis、JGA: jga-study..jga-policy)。
    # 値域 validation は ES 側に委譲、未知値は 0 件で返る。
    "type": "enum",
    # === Tier 3 GEA / MetaboBank ===
    # experiment_type / submission_type は controlled vocab。facet bucket (.keyword の exact 値) を
    # op=eq で再注入するため enum (term は experimentType.keyword / submissionType.keyword に当てる:
    # compiler_es.py)。converter 実装は list[str] だが ES は array/scalar を同じ mapping で扱うので
    # .keyword term が効く。値域 validation は ES 側に委譲し、未知値は 0 件で返る。
    "experiment_type": "enum",
    "submission_type": "enum",
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
    "object_type": ("bioproject",),
    "project_type": ("bioproject",),
    "relevance": ("bioproject",),
    # BioProject + JGA (jga-study のみ)
    "grant_title": ("bioproject", "jga"),
    "grant_agency": ("bioproject", "jga"),
    # BioProject + JGA: externalLink nested (label)
    "external_link_label": ("bioproject", "jga"),
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
    # BioSample + SRA: derivedFrom nested (identifier)
    "derived_from_id": ("biosample", "sra"),
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
