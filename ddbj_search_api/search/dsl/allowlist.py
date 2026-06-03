"""Tier-based field allowlist, operator matrix, and per-DB availability.

3 段構成:
- Tier 1 (横断可): identifier / title / description / name / organism_id /
  organism_name / date 系の基本 field + accessibility。
- Tier 2 (横断可、converter 側正規化済の共通 field): submitter / publication。
- Tier 3 (単一 DB 指定必須): DB 特化 field。

Tier 1/2 は「横断可」だが全 DB が実 field を持つわけではない。各 (field, DB) の
実在性は :func:`field_availability` が SSOT として返す: 実 field で検索可能か、その
DB では値が固定 (例: Solr backed の accessibility="public-access") か、非対応か。
cross 検索の per-arm 簡約 (per_arm.py) と single 検索の db scope 検証 (validator.py)
は both この関数を参照する。

API 側 allowlist が field 構成の唯一の source of truth。
"""

from __future__ import annotations

from dataclasses import dataclass
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
        # 全 ES backed 6 DB 共通 (public-access / controlled-access)。Solr backed
        # (trad / taxonomy) は公開前提で "public-access" 固定 (field_availability)。
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
    # ES backed 6 DB は controlled vocab (public-access / controlled-access)。
    # Solr backed は "public-access" 固定 (field_availability で突き合わせ)。
    "accessibility": "enum",
    # === Tier 2 (cross, converter-normalized) ===
    "submitter": "text",
    # publication は ES nested の publication.title。trad (ARSA) は ReferenceTitle に
    # マップして検索可 (compiler_solr)、biosample / taxonomy は非対応 (field_availability)。
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
    # 別名・俗称・上位分類。strain / isolate は biosample と同名 (Tier 3 共有、上記参照)。
    "synonym": "text",
    "blast_name": "text",
    "equivalent_name": "text",
    "domain": "text",
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
    # strain / isolate は biosample (sample 属性) と taxonomy (TXSearch の株 field) の
    # 同名 Tier3。db 指定必須なので曖昧さはなく、compiler_es / compiler_solr が各々の
    # 物理 field に解決する (複数 DB 同名は grant_title / geo_loc_name 等と同様)。
    "strain": ("biosample", "taxonomy"),
    "isolate": ("biosample", "taxonomy"),
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
    # Taxonomy-only (TXSearch backend)
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
    "synonym": ("taxonomy",),
    "blast_name": ("taxonomy",),
    "equivalent_name": ("taxonomy",),
    "domain": ("taxonomy",),
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


@dataclass(frozen=True, slots=True)
class FieldAvailability:
    """ある (field, DB) の検索可否。

    - ``available=True``: その DB に実 field があり検索できる。
    - ``available=False`` かつ ``fixed_value`` あり: その DB では値が固定
      (例: Solr backed の ``accessibility="public-access"``)。cross / single の
      per-arm 簡約 (per_arm.py) で値と突き合わせ、一致なら恒真・不一致なら恒偽に畳む。
    - ``available=False`` かつ ``fixed_value=None``: 非対応。cross は arm を対象外
      (count=null)、single は 400 ``field-not-available-for-db``。
    """

    available: bool
    fixed_value: str | None = None


# Tier 1/2 field が「横断可」でも実 field を持たない DB (記載なし = available)。
# Solr backed (trad / taxonomy) の実 field 不在と、biosample の publication nested 不在。
# Solr の availability は compiler_solr の field map と一致する (drift は unit test で担保)。
_TIER12_UNAVAILABLE_DBS: dict[str, frozenset[str]] = {
    # organism_id (taxID exact) は ARSA に直接検索 field が無い (taxonomy は tax_id で available)。
    "organism_id": frozenset({"trad"}),
    "name": frozenset({"trad", "taxonomy"}),
    "date_published": frozenset({"taxonomy"}),
    "date_modified": frozenset({"trad", "taxonomy"}),
    "date_created": frozenset({"trad", "taxonomy"}),
    "date": frozenset({"trad", "taxonomy"}),
    "submitter": frozenset({"trad", "taxonomy"}),
    "publication": frozenset({"biosample", "taxonomy"}),
}

# Tier 1/2 field が、ある DB では実 field を持たず値が固定されているもの。
# accessibility は Solr backed (trad / taxonomy) が公開前提で "public-access" 固定
# (solr/mappers.py が response に詰める固定値が SSOT)。
_TIER12_FIXED_VALUES: dict[str, dict[str, str]] = {
    "accessibility": {"trad": "public-access", "taxonomy": "public-access"},
}


def field_availability(field: str, db: str) -> FieldAvailability:
    """``field`` が ``db`` で検索可能かを返す (allowlist 内の field を前提)。

    Tier 3 は :data:`TIER3_FIELD_DBS` の db scope、Tier 1/2 は
    :data:`_TIER12_FIXED_VALUES` / :data:`_TIER12_UNAVAILABLE_DBS` の例外を引き、
    いずれにも該当しなければ available とみなす。
    """
    if field in TIER3_FIELD_DBS:
        return FieldAvailability(available=db in TIER3_FIELD_DBS[field])
    fixed = _TIER12_FIXED_VALUES.get(field, {}).get(db)
    if fixed is not None:
        return FieldAvailability(available=False, fixed_value=fixed)
    if db in _TIER12_UNAVAILABLE_DBS.get(field, frozenset()):
        return FieldAvailability(available=False)

    return FieldAvailability(available=True)


# db-portal の 8 DB (schemas.db_portal.DbPortalDb と一致、drift は unit test で担保)。
_ALL_DBS: tuple[str, ...] = (
    "bioproject",
    "biosample",
    "sra",
    "jga",
    "gea",
    "metabobank",
    "trad",
    "taxonomy",
)


def available_dbs(field: str) -> tuple[str, ...]:
    """``field`` が single モードで使える (実 field or 固定値) db の列挙。

    cross 拒否 / single 非対応エラーの hint に使う。Tier 3 は :data:`TIER3_FIELD_DBS`、
    Tier 1/2 は :func:`field_availability` が非対応でない db を :data:`_ALL_DBS` 順に並べる。
    """
    if field in TIER3_FIELD_DBS:
        return TIER3_FIELD_DBS[field]

    return tuple(
        db for db in _ALL_DBS if (avail := field_availability(field, db)).available or avail.fixed_value is not None
    )
