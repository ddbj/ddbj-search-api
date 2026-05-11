"""Tests for ddbj_search_api.search.dsl.inspect."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from ddbj_search_api.search.dsl.ast import (
    BoolOp,
    FieldClause,
    FreeText,
    Node,
    Position,
)
from ddbj_search_api.search.dsl.inspect import ast_has_field_clause

_POS = Position(column=1, length=1)


def _identifier_clause(value: str) -> FieldClause:
    return FieldClause(field="identifier", value_kind="word", value=value, position=_POS)


class TestAstHasFieldClause:
    def test_free_text_alone_returns_false(self) -> None:
        assert ast_has_field_clause(FreeText("cancer")) is False

    def test_empty_free_text_returns_false(self) -> None:
        """value が空でも FieldClause を含まない以上 False。"""
        assert ast_has_field_clause(FreeText("")) is False

    def test_field_clause_alone_returns_true(self) -> None:
        assert ast_has_field_clause(_identifier_clause("PRJDB1234")) is True

    def test_and_of_free_text_and_field_clause_returns_true(self) -> None:
        ast = BoolOp(
            op="AND",
            children=(_identifier_clause("PRJDB1234"), FreeText("cancer")),
            position=_POS,
        )
        assert ast_has_field_clause(ast) is True

    def test_and_of_two_free_texts_returns_false(self) -> None:
        """合成 BoolOp に FreeText しか含まない場合は uf 不要 = False。"""
        ast = BoolOp(
            op="AND",
            children=(FreeText("a"), FreeText("b")),
            position=_POS,
        )
        assert ast_has_field_clause(ast) is False

    def test_or_with_field_clause_returns_true(self) -> None:
        ast = BoolOp(
            op="OR",
            children=(_identifier_clause("PRJDB1234"), _identifier_clause("DRA000001")),
            position=_POS,
        )
        assert ast_has_field_clause(ast) is True

    def test_not_with_field_clause_returns_true(self) -> None:
        ast = BoolOp(
            op="NOT",
            children=(_identifier_clause("PRJDB1234"),),
            position=_POS,
        )
        assert ast_has_field_clause(ast) is True

    def test_nested_and_with_deep_field_clause_returns_true(self) -> None:
        """ネスト深い位置にある FieldClause も検出する (uf 必要判定)。"""
        inner = BoolOp(
            op="OR",
            children=(_identifier_clause("PRJDB1234"), FreeText("filler")),
            position=_POS,
        )
        ast = BoolOp(
            op="AND",
            children=(FreeText("cancer"), inner),
            position=_POS,
        )
        assert ast_has_field_clause(ast) is True

    def test_nested_only_free_texts_returns_false(self) -> None:
        inner = BoolOp(op="OR", children=(FreeText("a"), FreeText("b")), position=_POS)
        ast = BoolOp(op="AND", children=(FreeText("c"), inner), position=_POS)
        assert ast_has_field_clause(ast) is False


class TestAstHasFieldClausePBT:
    @given(st.text(min_size=0, max_size=20))
    def test_free_text_value_never_makes_it_true(self, value: str) -> None:
        """FreeText.value がどんな文字列でも、FieldClause がなければ False のまま。"""
        assert ast_has_field_clause(FreeText(value)) is False

    @given(st.lists(st.text(min_size=0, max_size=5), min_size=1, max_size=4))
    def test_and_of_only_free_texts_is_false(self, values: list[str]) -> None:
        children: tuple[Node, ...] = tuple(FreeText(v) for v in values)
        if len(children) == 1:
            assert ast_has_field_clause(children[0]) is False
            return
        ast = BoolOp(op="AND", children=children, position=_POS)
        assert ast_has_field_clause(ast) is False
