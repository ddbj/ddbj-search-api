"""Tests for ddbj_search_api.search.dsl.validator (AP3 Stage 2).

SSOT:
- search.md §演算子とフィールドの組み合わせ (L225-236)
- search-backends.md §値のバリデーション (L400-414)
- source.md §AP3 (L99-102)
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ddbj_search_api.schemas.db_portal import DbPortalDb
from ddbj_search_api.search.dsl import DslError, ErrorType, parse
from ddbj_search_api.search.dsl.allowlist import TIER1_FIELDS
from ddbj_search_api.search.dsl.validator import validate


class TestAllowedFields:
    @pytest.mark.parametrize(
        "field",
        [
            "identifier",
            "title",
            "description",
            "organism",
            "date_published",
            "date_modified",
            "date_created",
            "date",
        ],
    )
    def test_tier1_fields_accepted_in_cross_mode(self, field: str) -> None:
        if field == "identifier":
            dsl = f"{field}:PRJDB1"
        elif field == "organism":
            dsl = f"{field}:human"
        elif field.startswith("date"):
            dsl = f"{field}:2024-01-01"
        else:
            dsl = f"{field}:cancer"
        validate(parse(dsl), mode="cross")

    def test_unknown_field_rejected(self) -> None:
        ast = parse("foo:bar")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        assert exc_info.value.type == ErrorType.unknown_field
        assert exc_info.value.column == 1
        # detail に候補一覧が含まれる
        assert "identifier" in exc_info.value.detail

    def test_unknown_field_position_reported_mid_expr(self) -> None:
        ast = parse("title:a AND foo:bar")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        assert exc_info.value.type == ErrorType.unknown_field
        # "foo:bar" は column 13
        assert exc_info.value.column == 13


class TestValueKindOperatorCompat:
    @pytest.mark.parametrize(
        "dsl",
        [
            "identifier:[a TO b]",  # identifier は range 不可
            "title:2024-01-01",  # text は date 不可
            "date:cancer*",  # date は wildcard 不可
            "date_published:cancer",  # date は word 不可
            "organism:cancer*",  # organism は wildcard 不可
            "organism:2024-01-01",  # organism は date 不可
            "description:[a TO b]",  # text は range 不可
        ],
    )
    def test_invalid_operator_rejected(self, dsl: str) -> None:
        ast = parse(dsl)
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        assert exc_info.value.type == ErrorType.invalid_operator_for_field

    @pytest.mark.parametrize(
        "dsl",
        [
            "identifier:PRJDB1",
            "identifier:PRJ*",
            'identifier:"PRJDB1"',
            "title:cancer",
            'title:"cancer treatment"',
            "title:canc*",
            "organism:human",
            'organism:"Homo sapiens"',
            "date_published:2024-01-01",
            "date_published:[2020-01-01 TO 2024-12-31]",
            "date:[2020-01-01 TO 2024-12-31]",
            "date:2024-01-01",
        ],
    )
    def test_valid_combinations_accepted(self, dsl: str) -> None:
        ast = parse(dsl)
        validate(ast, mode="cross")


class TestDateFormat:
    def test_non_leap_year_feb_29_rejected(self) -> None:
        ast = parse("date_published:2023-02-29")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        assert exc_info.value.type == ErrorType.invalid_date_format

    def test_leap_year_feb_29_accepted(self) -> None:
        ast = parse("date_published:2024-02-29")
        validate(ast, mode="cross")

    def test_invalid_month_rejected(self) -> None:
        ast = parse("date_published:2024-99-99")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        assert exc_info.value.type == ErrorType.invalid_date_format

    def test_range_invalid_from_rejected(self) -> None:
        ast = parse("date_published:[2023-02-29 TO 2024-12-31]")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        assert exc_info.value.type == ErrorType.invalid_date_format

    def test_range_invalid_to_rejected(self) -> None:
        ast = parse("date_published:[2024-01-01 TO 2024-99-99]")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        assert exc_info.value.type == ErrorType.invalid_date_format

    def test_range_from_greater_than_to_accepted(self) -> None:
        # SSOT 未明記、Lucene 挙動に合わせ 0 件扱いとして通す
        ast = parse("date_published:[2024-12-31 TO 2020-01-01]")
        validate(ast, mode="cross")


class TestMissingValue:
    def test_empty_phrase_raises_missing_value(self) -> None:
        ast = parse('title:""')
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        assert exc_info.value.type == ErrorType.missing_value


class TestNestDepth:
    def test_depth_5_accepted(self) -> None:
        # 5 iteration で 5 BoolOp ネスト → max_depth=5 の境界で accept
        dsl = "title:a"
        for i in range(5):
            dsl = f"({dsl} AND title:v{i})"
        validate(parse(dsl), mode="cross")

    def test_depth_6_rejected(self) -> None:
        # 6 iteration で 6 BoolOp ネスト → max_depth=5 を超過で reject
        dsl = "title:a"
        for i in range(6):
            dsl = f"({dsl} AND title:v{i})"
        ast = parse(dsl)
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        assert exc_info.value.type == ErrorType.nest_depth_exceeded

    def test_custom_max_depth(self) -> None:
        ast = parse("title:a AND title:b")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross", max_depth=0)
        assert exc_info.value.type == ErrorType.nest_depth_exceeded


class TestMode:
    def test_cross_mode_with_tier1_accepted(self) -> None:
        validate(parse("title:cancer"), mode="cross")

    def test_single_mode_with_tier1_accepted(self) -> None:
        validate(parse("title:cancer"), mode="single", db=DbPortalDb.bioproject)

    def test_single_mode_unknown_field_still_rejected(self) -> None:
        ast = parse("foo:bar")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="single", db=DbPortalDb.bioproject)
        assert exc_info.value.type == ErrorType.unknown_field


class TestBoolCombinations:
    def test_and_with_valid_leaves_accepted(self) -> None:
        validate(parse("title:cancer AND organism:human"), mode="cross")

    def test_or_with_invalid_leaf_rejected(self) -> None:
        ast = parse("title:cancer OR date:cancer*")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        assert exc_info.value.type == ErrorType.invalid_operator_for_field

    def test_not_with_valid_leaf_accepted(self) -> None:
        validate(parse("NOT title:cancer"), mode="cross")


class TestValidatorPBT:
    @given(
        field=st.sampled_from(["title", "description", "organism"]),
        word=st.text(
            alphabet=st.characters(
                min_codepoint=ord("0"),
                max_codepoint=ord("z"),
                whitelist_categories=("Ll", "Lu", "Nd"),
                whitelist_characters="_",
            ),
            min_size=1,
            max_size=20,
        ),
    )
    @settings(max_examples=50, deadline=None)
    def test_word_values_for_text_organism_accepted(self, field: str, word: str) -> None:
        validate(parse(f"{field}:{word}"), mode="cross")

    @given(
        unknown=st.text(
            alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z"), whitelist_characters="_"),
            min_size=3,
            max_size=20,
        ).filter(lambda s: s not in TIER1_FIELDS),
    )
    @settings(max_examples=30, deadline=None)
    def test_random_unknown_field_rejected(self, unknown: str) -> None:
        ast = parse(f"{unknown}:value")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        assert exc_info.value.type == ErrorType.unknown_field
