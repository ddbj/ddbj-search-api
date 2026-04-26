"""Tests for ddbj_search_api.search.dsl.accession_exact_match."""

from __future__ import annotations

from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ddbj_search_api.search.accession import is_accession_like
from ddbj_search_api.search.dsl.accession_exact_match import (
    detect_accession_exact_match_in_ast,
)
from ddbj_search_api.search.dsl.ast import (
    BoolOp,
    FieldClause,
    Node,
    Position,
    Range,
    ValueKind,
)

_POS = Position(column=1, length=1)


def _identifier_clause(value: str, value_kind: ValueKind = "word") -> FieldClause:
    return FieldClause(
        field="identifier",
        value_kind=value_kind,
        value=value,
        position=_POS,
    )


class TestDetectAccessionExactMatchInAst:
    """単一 identifier field の eq + accession-shape value のみ value 返却、それ以外は None。"""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("PRJDB1234", "PRJDB1234"),
            ("DRA000001", "DRA000001"),
            ("JGAS000001", "JGAS000001"),
            ("SAMD00000001", "SAMD00000001"),
        ],
    )
    def test_single_identifier_eq_word_returns_value(
        self,
        value: str,
        expected: str,
    ) -> None:
        ast = _identifier_clause(value, value_kind="word")
        assert detect_accession_exact_match_in_ast(ast) == expected

    def test_single_identifier_eq_phrase_returns_value(self) -> None:
        """phrase value_kind も identifier では op=eq に分類される。"""
        ast = _identifier_clause("PRJDB1234", value_kind="phrase")
        assert detect_accession_exact_match_in_ast(ast) == "PRJDB1234"

    def test_identifier_wildcard_returns_none(self) -> None:
        """wildcard value_kind は op=wildcard なので解放対象外。"""
        ast = _identifier_clause("PRJDB*", value_kind="wildcard")
        assert detect_accession_exact_match_in_ast(ast) is None

    @pytest.mark.parametrize(
        "value",
        ["cancer", "abc", "GSE12345", "PRJDB", ""],
    )
    def test_non_accession_shape_value_returns_none(self, value: str) -> None:
        ast = _identifier_clause(value, value_kind="word")
        assert detect_accession_exact_match_in_ast(ast) is None

    def test_title_field_returns_none(self) -> None:
        """identifier 以外の field (例: title) は対象外。"""
        ast = FieldClause(
            field="title",
            value_kind="word",
            value="PRJDB1234",
            position=_POS,
        )
        assert detect_accession_exact_match_in_ast(ast) is None

    def test_publication_field_returns_none(self) -> None:
        """publication も identifier 型だが field 名が違うので対象外。"""
        ast = FieldClause(
            field="publication",
            value_kind="word",
            value="PRJDB1234",
            position=_POS,
        )
        assert detect_accession_exact_match_in_ast(ast) is None

    def test_date_field_returns_none(self) -> None:
        ast = FieldClause(
            field="date_published",
            value_kind="date",
            value="2024-01-01",
            position=_POS,
        )
        assert detect_accession_exact_match_in_ast(ast) is None

    def test_and_wrapper_returns_none(self) -> None:
        """AND でラップされた単一 child でも対象外 (AST top が BoolOp なので)。"""
        ast = BoolOp(
            op="AND",
            children=(_identifier_clause("PRJDB1234"),),
            position=_POS,
        )
        assert detect_accession_exact_match_in_ast(ast) is None

    def test_or_wrapper_returns_none(self) -> None:
        ast = BoolOp(
            op="OR",
            children=(
                _identifier_clause("PRJDB1234"),
                _identifier_clause("DRA000001"),
            ),
            position=_POS,
        )
        assert detect_accession_exact_match_in_ast(ast) is None

    def test_not_wrapper_returns_none(self) -> None:
        ast = BoolOp(
            op="NOT",
            children=(_identifier_clause("PRJDB1234"),),
            position=_POS,
        )
        assert detect_accession_exact_match_in_ast(ast) is None

    def test_range_value_returns_none(self) -> None:
        """value が Range のとき (date range など) は対象外。"""
        ast = FieldClause(
            field="date_published",
            value_kind="range",
            value=Range(from_="2024-01-01", to="2024-12-31"),
            position=_POS,
        )
        assert detect_accession_exact_match_in_ast(ast) is None


class TestDetectAccessionExactMatchInAstPBT:
    """単一 identifier word AST に任意 value を入れたとき、is_accession_like と挙動が一致する。"""

    @given(st.text(min_size=1, max_size=20).filter(lambda s: s.strip() != ""))
    def test_arbitrary_value_consistent_with_is_accession_like(
        self,
        value: str,
    ) -> None:
        ast: Node = _identifier_clause(value, value_kind="word")
        result = detect_accession_exact_match_in_ast(ast)
        if is_accession_like(value):
            assert result == value
        else:
            assert result is None

    @given(st.sampled_from(["PRJDB1234", "DRA000001", "JGAS000001", "SAMD00000001"]))
    def test_known_accession_returns_value(self, accession: str) -> None:
        ast = _identifier_clause(accession, value_kind="word")
        # cast: hypothesis strategy で literal 化された str が ast 内に流れる
        assert detect_accession_exact_match_in_ast(cast(Node, ast)) == accession
