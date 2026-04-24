"""Tests for ddbj_search_api.search.dsl.errors."""

from __future__ import annotations

import pytest

from ddbj_search_api.search.dsl import DslError, ErrorType, parse, type_uri
from ddbj_search_api.search.dsl.errors import TYPE_URI_PREFIX
from ddbj_search_api.search.dsl.validator import validate


class TestErrorTypeEnum:
    @pytest.mark.parametrize(
        ("error_type", "slug"),
        [
            (ErrorType.unexpected_token, "unexpected-token"),
            (ErrorType.unknown_field, "unknown-field"),
            (ErrorType.field_not_available_in_cross_db, "field-not-available-in-cross-db"),
            (ErrorType.invalid_date_format, "invalid-date-format"),
            (ErrorType.invalid_operator_for_field, "invalid-operator-for-field"),
            (ErrorType.nest_depth_exceeded, "nest-depth-exceeded"),
            (ErrorType.missing_value, "missing-value"),
        ],
    )
    def test_slug_value(self, error_type: ErrorType, slug: str) -> None:
        assert error_type.value == slug

    def test_all_slugs_kebab_case(self) -> None:
        for error_type in ErrorType:
            assert "_" not in error_type.value
            assert error_type.value == error_type.value.lower()

    def test_all_seven_ap3_slugs_present(self) -> None:
        expected = {
            "unexpected-token",
            "unknown-field",
            "field-not-available-in-cross-db",
            "invalid-date-format",
            "invalid-operator-for-field",
            "nest-depth-exceeded",
            "missing-value",
        }
        actual = {e.value for e in ErrorType}
        assert actual == expected


class TestTypeUri:
    def test_prefix(self) -> None:
        assert TYPE_URI_PREFIX == "https://ddbj.nig.ac.jp/problems/"

    def test_type_uri_composes(self) -> None:
        assert type_uri(ErrorType.unknown_field) == "https://ddbj.nig.ac.jp/problems/unknown-field"

    def test_type_uri_for_all_slugs(self) -> None:
        for error_type in ErrorType:
            uri = type_uri(error_type)
            assert uri.startswith(TYPE_URI_PREFIX)
            assert uri.endswith(error_type.value)


class TestDslErrorConstruction:
    def test_basic_attrs(self) -> None:
        err = DslError(
            type=ErrorType.unknown_field,
            detail="unknown field 'foo'",
            column=5,
            length=3,
        )
        assert err.type == ErrorType.unknown_field
        assert err.detail == "unknown field 'foo'"
        assert err.column == 5
        assert err.length == 3
        assert err.type_uri == "https://ddbj.nig.ac.jp/problems/unknown-field"

    def test_is_raisable(self) -> None:
        with pytest.raises(DslError):
            raise DslError(
                type=ErrorType.unexpected_token,
                detail="oops",
                column=1,
                length=1,
            )

    def test_repr_contains_type_and_column(self) -> None:
        err = DslError(
            type=ErrorType.nest_depth_exceeded,
            detail="depth 6 > 5",
            column=10,
            length=2,
        )
        r = repr(err)
        assert "nest-depth-exceeded" in r
        assert "column=10" in r


class TestParserErrorPosition:
    def test_unexpected_token_column(self) -> None:
        with pytest.raises(DslError) as exc_info:
            parse("title:cancer^2")
        assert exc_info.value.type == ErrorType.unexpected_token
        assert exc_info.value.column == 13  # '^'

    def test_regex_syntax_column(self) -> None:
        with pytest.raises(DslError) as exc_info:
            parse("title:/regex/")
        assert exc_info.value.type == ErrorType.unexpected_token
        assert exc_info.value.column >= 7  # `/` の位置

    def test_empty_input(self) -> None:
        with pytest.raises(DslError) as exc_info:
            parse("")
        assert exc_info.value.type == ErrorType.unexpected_token
        assert exc_info.value.column == 1


class TestValidatorErrorPosition:
    def test_unknown_field_position(self) -> None:
        with pytest.raises(DslError) as exc_info:
            validate(parse("title:a AND foo:bar"), mode="cross")
        assert exc_info.value.type == ErrorType.unknown_field
        assert exc_info.value.column == 13

    def test_invalid_operator_position(self) -> None:
        with pytest.raises(DslError) as exc_info:
            validate(parse("identifier:[a TO b]"), mode="cross")
        assert exc_info.value.type == ErrorType.invalid_operator_for_field
        assert exc_info.value.column == 1

    def test_invalid_date_position(self) -> None:
        with pytest.raises(DslError) as exc_info:
            validate(parse("date_published:2024-99-99"), mode="cross")
        assert exc_info.value.type == ErrorType.invalid_date_format
        assert exc_info.value.column == 1

    def test_missing_value_position(self) -> None:
        with pytest.raises(DslError) as exc_info:
            validate(parse('title:""'), mode="cross")
        assert exc_info.value.type == ErrorType.missing_value
        assert exc_info.value.column == 1


class TestErrorDetailEmbeddings:
    def test_unknown_field_detail_includes_column_and_allowed(self) -> None:
        with pytest.raises(DslError) as exc_info:
            validate(parse("foo:bar"), mode="cross")
        assert "column 1" in exc_info.value.detail
        # allowlist の候補が埋め込まれる
        assert "identifier" in exc_info.value.detail

    def test_invalid_date_detail_includes_value(self) -> None:
        with pytest.raises(DslError) as exc_info:
            validate(parse("date_published:2024-99-99"), mode="cross")
        assert "2024-99-99" in exc_info.value.detail
        assert "YYYY-MM-DD" in exc_info.value.detail

    def test_invalid_operator_detail_includes_field(self) -> None:
        with pytest.raises(DslError) as exc_info:
            validate(parse("date:cancer*"), mode="cross")
        assert "'date'" in exc_info.value.detail

    def test_nest_depth_detail_includes_limit(self) -> None:
        dsl = "title:a"
        for i in range(6):
            dsl = f"({dsl} AND title:v{i})"
        with pytest.raises(DslError) as exc_info:
            validate(parse(dsl), mode="cross")
        assert "5" in exc_info.value.detail  # default max_depth
