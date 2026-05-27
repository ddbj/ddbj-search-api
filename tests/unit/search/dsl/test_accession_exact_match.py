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
    FreeText,
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

    def test_and_with_single_identifier_child_returns_value(self) -> None:
        """AND 直下に identifier accession があれば解禁 (q+adv 併用合成 BoolOp 相当)。"""
        ast = BoolOp(
            op="AND",
            children=(_identifier_clause("PRJDB1234"),),
            position=_POS,
        )
        assert detect_accession_exact_match_in_ast(ast) == "PRJDB1234"

    def test_and_with_identifier_and_other_field_returns_value(self) -> None:
        """``identifier:PRJDB1234 AND title:cancer`` で解禁。"""
        ast = BoolOp(
            op="AND",
            children=(
                _identifier_clause("PRJDB1234"),
                FieldClause(field="title", value_kind="word", value="cancer", position=_POS),
            ),
            position=_POS,
        )
        assert detect_accession_exact_match_in_ast(ast) == "PRJDB1234"

    def test_and_with_other_field_and_identifier_returns_value(self) -> None:
        """子の並び順に依存せず解禁する (q+adv の合成順序が変わっても同じ挙動)。"""
        ast = BoolOp(
            op="AND",
            children=(
                FieldClause(field="title", value_kind="word", value="cancer", position=_POS),
                _identifier_clause("PRJDB1234"),
            ),
            position=_POS,
        )
        assert detect_accession_exact_match_in_ast(ast) == "PRJDB1234"

    def test_and_without_accession_returns_none(self) -> None:
        """AND 直下に accession 完全一致 child が無ければ None。"""
        ast = BoolOp(
            op="AND",
            children=(
                FieldClause(field="title", value_kind="word", value="PRJDB1234", position=_POS),
                FieldClause(field="organism_id", value_kind="word", value="9606", position=_POS),
            ),
            position=_POS,
        )
        assert detect_accession_exact_match_in_ast(ast) is None

    def test_nested_and_does_not_unwrap(self) -> None:
        """ネスト AND の更に下にある accession は対象外 (誤検出回避)。"""
        inner = BoolOp(
            op="AND",
            children=(_identifier_clause("PRJDB1234"),),
            position=_POS,
        )
        ast = BoolOp(
            op="AND",
            children=(inner, FieldClause(field="title", value_kind="word", value="x", position=_POS)),
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


class TestDetectAccessionExactMatchInAstFreeText:
    """FreeText 単独 / AND 直下子としての挙動 (q-only / q+adv 併用に相当)。"""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("PRJDB1234", "PRJDB1234"),
            ("DRA000001", "DRA000001"),
            ("JGAS000001", "JGAS000001"),
            ("SAMD00000001", "SAMD00000001"),
        ],
    )
    def test_free_text_accession_returns_value(self, value: str, expected: str) -> None:
        assert detect_accession_exact_match_in_ast(FreeText(value)) == expected

    def test_free_text_accession_with_surrounding_whitespace(self) -> None:
        """前後空白付きでも解禁 (strip 後で is_accession_like 判定)。"""
        assert detect_accession_exact_match_in_ast(FreeText("  PRJDB1234  ")) == "PRJDB1234"

    @pytest.mark.parametrize(
        "value",
        ["cancer", "PRJDB1234 cancer", "PRJDB", "", "  "],
    )
    def test_free_text_non_accession_returns_none(self, value: str) -> None:
        """is_accession_like を満たさない (複数トークン / 空 / partial) は対象外。"""
        assert detect_accession_exact_match_in_ast(FreeText(value)) is None

    def test_and_of_field_clause_and_free_text_accession_returns_value(self) -> None:
        """q+adv 併用合成: FreeText(accession) AND adv_ast でも解禁。"""
        ast = BoolOp(
            op="AND",
            children=(
                FieldClause(field="title", value_kind="word", value="cancer", position=_POS),
                FreeText("PRJDB1234"),
            ),
            position=_POS,
        )
        assert detect_accession_exact_match_in_ast(ast) == "PRJDB1234"

    def test_and_of_free_text_accession_and_field_clause_accession(self) -> None:
        """q=PRJDB1234 + adv=identifier:DRA000001 のような併用は最初に見つかった方を返す。"""
        ast = BoolOp(
            op="AND",
            children=(
                _identifier_clause("DRA000001"),
                FreeText("PRJDB1234"),
            ),
            position=_POS,
        )
        # 走査順 (children 順) で最初にマッチしたものを返す: adv 先頭運用なので DRA が先
        assert detect_accession_exact_match_in_ast(ast) == "DRA000001"

    def test_or_with_free_text_accession_returns_none(self) -> None:
        """OR 配下に accession があっても解禁しない。"""
        ast = BoolOp(
            op="OR",
            children=(FreeText("PRJDB1234"), FreeText("cancer")),
            position=_POS,
        )
        assert detect_accession_exact_match_in_ast(ast) is None

    def test_not_with_free_text_accession_returns_none(self) -> None:
        ast = BoolOp(
            op="NOT",
            children=(FreeText("PRJDB1234"),),
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
