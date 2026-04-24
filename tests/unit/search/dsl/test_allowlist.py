"""Tests for ddbj_search_api.search.dsl.allowlist (AP3 + AP6).

AP6 で追加した Tier 2/3 allowlist (30 field per DB 集計 = 27 unique) と
`TIER3_FIELD_DBS` 候補 DB 表の整合性を検証する。SSOT は
db-portal/docs/search.md §フィールド構成 + search-backends.md §バックエンド変換。
"""

from __future__ import annotations

from typing import get_args

import pytest

from ddbj_search_api.schemas.db_portal import DbPortalDb
from ddbj_search_api.search.dsl.allowlist import (
    ALL_ALLOWED_FIELDS,
    FIELD_TYPES,
    OPERATOR_BY_KIND,
    TIER1_FIELDS,
    TIER2_FIELDS,
    TIER3_FIELD_DBS,
    TIER3_FIELDS,
    FieldType,
    Operator,
    field_tier,
)
from ddbj_search_api.search.dsl.ast import ValueKind


class TestTierFrozensets:
    def test_tier1_has_8_fields(self) -> None:
        assert len(TIER1_FIELDS) == 8

    def test_tier2_has_2_fields(self) -> None:
        assert frozenset({"submitter", "publication"}) == TIER2_FIELDS

    def test_tier3_contains_expected_per_db_fields(self) -> None:
        # BioProject 2 + SRA 5 + JGA-exclusive 0 (share grant_agency/study_type)
        # + MetaboBank-exclusive 1 (submission_type) + GEA/MetaboBank shared 1 (experiment_type)
        # + Trad 5 + Taxonomy 10 = 24 unique, plus shared 3 (grant_agency/study_type共有、experiment_type 既算入)
        # → shared は実質 grant_agency (BP+JGA), study_type (JGA+MetaboBank), experiment_type (GEA+MetaboBank)
        expected = {
            # BioProject
            "project_type",
            "grant_agency",  # BioProject と JGA 共通
            # SRA
            "library_strategy",
            "library_source",
            "library_layout",
            "platform",
            "instrument_model",
            # JGA (grant_agency は BP と共通)
            "study_type",  # JGA と MetaboBank 共通
            # GEA
            "experiment_type",  # GEA と MetaboBank 共通
            # MetaboBank
            "submission_type",
            # Trad
            "division",
            "molecular_type",
            "sequence_length",
            "feature_gene_name",
            "reference_journal",
            # Taxonomy
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
        }
        assert expected == TIER3_FIELDS

    def test_tier3_unique_count_is_25(self) -> None:
        assert len(TIER3_FIELDS) == 25

    def test_tiers_are_disjoint(self) -> None:
        assert frozenset() == TIER1_FIELDS & TIER2_FIELDS
        assert frozenset() == TIER1_FIELDS & TIER3_FIELDS
        assert frozenset() == TIER2_FIELDS & TIER3_FIELDS

    def test_all_allowed_is_union(self) -> None:
        assert ALL_ALLOWED_FIELDS == TIER1_FIELDS | TIER2_FIELDS | TIER3_FIELDS


class TestFieldTypesMapping:
    def test_every_allowed_field_has_type(self) -> None:
        missing = ALL_ALLOWED_FIELDS - FIELD_TYPES.keys()
        assert missing == set(), f"fields without FIELD_TYPES: {missing}"

    def test_field_types_has_no_stale_entries(self) -> None:
        extra = FIELD_TYPES.keys() - ALL_ALLOWED_FIELDS
        assert extra == set(), f"stale FIELD_TYPES keys: {extra}"

    def test_field_type_values_are_literals(self) -> None:
        valid = set(get_args(FieldType))
        assert valid == {"identifier", "text", "organism", "date", "enum", "number"}
        for field, ftype in FIELD_TYPES.items():
            assert ftype in valid, f"{field}: invalid type {ftype!r}"

    @pytest.mark.parametrize(
        ("field", "expected_type"),
        [
            # Tier 2
            ("submitter", "text"),
            ("publication", "identifier"),
            # Tier 3 enum
            ("project_type", "enum"),
            ("library_strategy", "enum"),
            ("library_source", "enum"),
            ("library_layout", "enum"),
            ("platform", "enum"),
            ("study_type", "enum"),
            ("division", "enum"),
            ("molecular_type", "enum"),
            ("rank", "enum"),
            # Tier 3 number
            ("sequence_length", "number"),
            # Tier 3 text
            ("instrument_model", "text"),
            ("grant_agency", "text"),
            ("experiment_type", "text"),
            ("submission_type", "text"),
            ("feature_gene_name", "text"),
            ("reference_journal", "text"),
            ("lineage", "text"),
            ("kingdom", "text"),
            ("phylum", "text"),
            ("class", "text"),
            ("order", "text"),
            ("family", "text"),
            ("genus", "text"),
            ("species", "text"),
            ("common_name", "text"),
        ],
    )
    def test_tier2_tier3_field_types(self, field: str, expected_type: str) -> None:
        assert FIELD_TYPES[field] == expected_type


class TestOperatorByKind:
    def test_ap3_tier1_operators_present(self) -> None:
        for pair in [
            ("identifier", "word"),
            ("identifier", "phrase"),
            ("identifier", "wildcard"),
            ("text", "word"),
            ("text", "phrase"),
            ("text", "wildcard"),
            ("organism", "word"),
            ("organism", "phrase"),
            ("date", "date"),
            ("date", "range"),
        ]:
            assert pair in OPERATOR_BY_KIND

    def test_ap6_enum_operators(self) -> None:
        assert OPERATOR_BY_KIND[("enum", "word")] == "eq"
        assert OPERATOR_BY_KIND[("enum", "phrase")] == "eq"
        # enum は wildcard / range / date 非対応
        assert ("enum", "wildcard") not in OPERATOR_BY_KIND
        assert ("enum", "range") not in OPERATOR_BY_KIND
        assert ("enum", "date") not in OPERATOR_BY_KIND

    def test_ap6_number_operators(self) -> None:
        assert OPERATOR_BY_KIND[("number", "word")] == "eq"
        assert OPERATOR_BY_KIND[("number", "range")] == "between"
        # number は phrase / wildcard / date 非対応
        assert ("number", "phrase") not in OPERATOR_BY_KIND
        assert ("number", "wildcard") not in OPERATOR_BY_KIND
        assert ("number", "date") not in OPERATOR_BY_KIND

    def test_all_operators_are_valid_literals(self) -> None:
        valid_ops = set(get_args(Operator))
        valid_types = set(get_args(FieldType))
        valid_kinds = set(get_args(ValueKind))
        for (ftype, kind), op in OPERATOR_BY_KIND.items():
            assert ftype in valid_types, f"bad FieldType: {ftype!r}"
            assert kind in valid_kinds, f"bad ValueKind: {kind!r}"
            assert op in valid_ops, f"bad Operator: {op!r}"


class TestTier3FieldDbs:
    def test_keys_match_tier3_fields(self) -> None:
        assert set(TIER3_FIELD_DBS.keys()) == TIER3_FIELDS

    def test_all_dbs_are_valid_enum_values(self) -> None:
        valid_dbs = {e.value for e in DbPortalDb}
        for field, dbs in TIER3_FIELD_DBS.items():
            assert len(dbs) >= 1, f"{field}: empty tuple"
            for db in dbs:
                assert db in valid_dbs, f"{field}: unknown db {db!r}"

    @pytest.mark.parametrize(
        ("field", "expected_dbs"),
        [
            # BioProject-only
            ("project_type", ("bioproject",)),
            # BioProject + JGA shared
            ("grant_agency", ("bioproject", "jga")),
            # SRA-only
            ("library_strategy", ("sra",)),
            ("library_source", ("sra",)),
            ("library_layout", ("sra",)),
            ("platform", ("sra",)),
            ("instrument_model", ("sra",)),
            # JGA + MetaboBank shared
            ("study_type", ("jga", "metabobank")),
            # GEA + MetaboBank shared
            ("experiment_type", ("gea", "metabobank")),
            # MetaboBank-only
            ("submission_type", ("metabobank",)),
            # Trad-only
            ("division", ("trad",)),
            ("molecular_type", ("trad",)),
            ("sequence_length", ("trad",)),
            ("feature_gene_name", ("trad",)),
            ("reference_journal", ("trad",)),
            # Taxonomy-only (10 field)
            ("rank", ("taxonomy",)),
            ("lineage", ("taxonomy",)),
            ("kingdom", ("taxonomy",)),
            ("phylum", ("taxonomy",)),
            ("class", ("taxonomy",)),
            ("order", ("taxonomy",)),
            ("family", ("taxonomy",)),
            ("genus", ("taxonomy",)),
            ("species", ("taxonomy",)),
            ("common_name", ("taxonomy",)),
        ],
    )
    def test_each_field_has_expected_dbs(self, field: str, expected_dbs: tuple[str, ...]) -> None:
        assert TIER3_FIELD_DBS[field] == expected_dbs


class TestFieldTier:
    @pytest.mark.parametrize("field", sorted(TIER1_FIELDS))
    def test_tier1_returns_tier1(self, field: str) -> None:
        assert field_tier(field) == "tier1"

    @pytest.mark.parametrize("field", sorted(TIER2_FIELDS))
    def test_tier2_returns_tier2(self, field: str) -> None:
        assert field_tier(field) == "tier2"

    @pytest.mark.parametrize("field", sorted(TIER3_FIELDS))
    def test_tier3_returns_tier3(self, field: str) -> None:
        assert field_tier(field) == "tier3"

    def test_unknown_returns_none(self) -> None:
        assert field_tier("foo") is None
        assert field_tier("") is None
        assert field_tier("identifier_nope") is None
