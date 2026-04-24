"""Tests for ddbj_search_api.search.dsl.parser (Stage 1: DSL → AST).

SSOT:
- search-backends.md §パーサ実装 (L416-470) : Lark LALR(1), Lucene サブセット
- search.md §採用構文 (L670-689) : field:value, field:"phrase", field:[a TO b], field:value*, AND/OR/NOT, (...)

Parser stage の責務は「構文的に認識された value 種別 (value_kind)」を AST に載せること。
operator は compiler 側で (field_type, value_kind) から導出する。
validator は allowlist / 演算子互換性 / 日付フォーマット / ネスト深さ を見る。
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ddbj_search_api.search.dsl import DslError, ErrorType, parse
from ddbj_search_api.search.dsl.ast import BoolOp, FieldClause, Range


class TestFieldClauseValueKinds:
    """value_kind は phrase / word / wildcard / date / range の 5 種。"""

    def test_word(self) -> None:
        ast = parse("title:cancer")
        assert isinstance(ast, FieldClause)
        assert ast.field == "title"
        assert ast.value_kind == "word"
        assert ast.value == "cancer"

    def test_phrase_simple(self) -> None:
        ast = parse('organism:"Homo sapiens"')
        assert isinstance(ast, FieldClause)
        assert ast.field == "organism"
        assert ast.value_kind == "phrase"
        assert ast.value == "Homo sapiens"

    def test_phrase_empty(self) -> None:
        # field:"" は phrase として parse される (validator が missing-value で弾く)
        ast = parse('title:""')
        assert isinstance(ast, FieldClause)
        assert ast.value_kind == "phrase"
        assert ast.value == ""

    def test_wildcard_asterisk_trailing(self) -> None:
        ast = parse("title:cancer*")
        assert isinstance(ast, FieldClause)
        assert ast.value_kind == "wildcard"
        assert ast.value == "cancer*"

    def test_wildcard_question_trailing(self) -> None:
        ast = parse("title:cancer?")
        assert isinstance(ast, FieldClause)
        assert ast.value_kind == "wildcard"
        assert ast.value == "cancer?"

    def test_wildcard_middle(self) -> None:
        ast = parse("identifier:PRJ*123")
        assert isinstance(ast, FieldClause)
        assert ast.value_kind == "wildcard"
        assert ast.value == "PRJ*123"

    def test_date(self) -> None:
        ast = parse("date_published:2024-01-01")
        assert isinstance(ast, FieldClause)
        assert ast.value_kind == "date"
        assert ast.value == "2024-01-01"

    def test_range(self) -> None:
        ast = parse("date_published:[2020-01-01 TO 2024-12-31]")
        assert isinstance(ast, FieldClause)
        assert ast.value_kind == "range"
        assert isinstance(ast.value, Range)
        assert ast.value.from_ == "2020-01-01"
        assert ast.value.to == "2024-12-31"

    def test_range_with_non_date_values(self) -> None:
        # 数値や文字列 range も構文上は parse (validator が field 型で弾く)
        ast = parse("identifier:[AAA TO ZZZ]")
        assert isinstance(ast, FieldClause)
        assert ast.value_kind == "range"
        assert isinstance(ast.value, Range)
        assert ast.value.from_ == "AAA"
        assert ast.value.to == "ZZZ"


class TestBoolOperators:
    def test_and(self) -> None:
        ast = parse("title:a AND title:b")
        assert isinstance(ast, BoolOp)
        assert ast.op == "AND"
        assert len(ast.children) == 2

    def test_or(self) -> None:
        ast = parse("title:a OR title:b")
        assert isinstance(ast, BoolOp)
        assert ast.op == "OR"
        assert len(ast.children) == 2

    def test_not_prefix(self) -> None:
        ast = parse("NOT title:cancer")
        assert isinstance(ast, BoolOp)
        assert ast.op == "NOT"
        assert len(ast.children) == 1

    def test_and_has_higher_precedence_than_or(self) -> None:
        # a OR b AND c  → OR(a, AND(b, c))
        ast = parse("title:a OR title:b AND title:c")
        assert isinstance(ast, BoolOp)
        assert ast.op == "OR"
        right = ast.children[1]
        assert isinstance(right, BoolOp)
        assert right.op == "AND"

    def test_parentheses_override_precedence(self) -> None:
        # (a OR b) AND c  → AND(OR(a,b), c)
        ast = parse("(title:a OR title:b) AND title:c")
        assert isinstance(ast, BoolOp)
        assert ast.op == "AND"
        left = ast.children[0]
        assert isinstance(left, BoolOp)
        assert left.op == "OR"

    def test_and_left_associative_multiple_terms(self) -> None:
        # a AND b AND c  → 1 個の AND ノードに子が 3 つ (or left-leaning 2-ary、どちらでも OK)
        ast = parse("title:a AND title:b AND title:c")
        assert isinstance(ast, BoolOp)
        assert ast.op == "AND"
        # 子の平たんな数または構造をチェック
        leaves = _flatten_leaves(ast)
        assert [lf.field for lf in leaves] == ["title", "title", "title"]
        assert [lf.value for lf in leaves] == ["a", "b", "c"]

    def test_not_with_group(self) -> None:
        ast = parse("NOT (title:a OR title:b)")
        assert isinstance(ast, BoolOp)
        assert ast.op == "NOT"
        assert len(ast.children) == 1
        inner = ast.children[0]
        assert isinstance(inner, BoolOp)
        assert inner.op == "OR"


def _flatten_leaves(node: BoolOp | FieldClause) -> list[FieldClause]:
    if isinstance(node, FieldClause):
        return [node]
    out: list[FieldClause] = []
    for child in node.children:
        out.extend(_flatten_leaves(child))
    return out


class TestPositionMeta:
    def test_column_is_one_based_at_start(self) -> None:
        ast = parse("title:cancer")
        assert isinstance(ast, FieldClause)
        assert ast.position.column == 1

    def test_length_covers_token(self) -> None:
        ast = parse("title:cancer")
        assert isinstance(ast, FieldClause)
        # "title:cancer" は 12 文字
        assert ast.position.length == len("title:cancer")

    def test_nested_position_after_operator(self) -> None:
        # "title:a AND title:b" の右 FieldClause は column > 8
        ast = parse("title:a AND title:b")
        assert isinstance(ast, BoolOp)
        ast.children[-1]  # AND の最後の子
        # left-leaning か flat かに依存するため、位置として 8 以降にあることを確認
        leaves = _flatten_leaves(ast)
        assert leaves[-1].position.column > 8


class TestUnsupportedSyntax:
    @pytest.mark.parametrize(
        "dsl",
        [
            "title:cancer^2",  # boost
            "title:cancer~1",  # fuzzy
            "title:cancer~",  # fuzzy (edit distance 省略)
            "title:/regex/",  # regex
        ],
    )
    def test_unsupported_returns_unexpected_token(self, dsl: str) -> None:
        with pytest.raises(DslError) as exc_info:
            parse(dsl)
        assert exc_info.value.type == ErrorType.unexpected_token


class TestInvalidInput:
    def test_empty_string_rejected(self) -> None:
        with pytest.raises(DslError) as exc_info:
            parse("")
        assert exc_info.value.type == ErrorType.unexpected_token

    def test_whitespace_only_rejected(self) -> None:
        with pytest.raises(DslError):
            parse("   ")

    def test_dangling_and(self) -> None:
        with pytest.raises(DslError):
            parse("title:a AND")

    def test_dangling_or(self) -> None:
        with pytest.raises(DslError):
            parse("OR title:a")

    def test_unmatched_open_paren(self) -> None:
        with pytest.raises(DslError):
            parse("(title:a")

    def test_unmatched_close_paren(self) -> None:
        with pytest.raises(DslError):
            parse("title:a)")

    def test_missing_value(self) -> None:
        # `field:` は Lark 文法レベルで value が required なので unexpected-token
        with pytest.raises(DslError):
            parse("title:")

    def test_bare_not(self) -> None:
        # NOT 単独 (atom なし) は parse 失敗
        with pytest.raises(DslError):
            parse("NOT")

    def test_unknown_bool_operator_case_sensitive(self) -> None:
        # 小文字 and は keyword ではない → FIELD "and" と解釈 → parse error
        with pytest.raises(DslError):
            parse("title:a and title:b")


class TestMaxLength:
    def test_under_max_length_ok(self) -> None:
        dsl = " AND ".join([f"title:v{i}" for i in range(100)])
        assert len(dsl) < 4096
        ast = parse(dsl, max_length=4096)
        assert ast is not None

    def test_over_max_length_rejected_as_unexpected_token(self) -> None:
        dsl = "title:" + ("x" * 5000)
        with pytest.raises(DslError) as exc_info:
            parse(dsl, max_length=4096)
        assert exc_info.value.type == ErrorType.unexpected_token

    def test_default_max_length_is_4096(self) -> None:
        # max_length 未指定でも 4096 超は reject
        dsl = "title:" + ("x" * 5000)
        with pytest.raises(DslError):
            parse(dsl)


class TestPhraseEscaping:
    def test_phrase_with_escaped_quote(self) -> None:
        ast = parse(r'title:"foo\"bar"')
        assert isinstance(ast, FieldClause)
        assert ast.value_kind == "phrase"
        # escape は解除されて \" → "
        assert ast.value == 'foo"bar'

    def test_phrase_with_escaped_backslash(self) -> None:
        ast = parse(r'title:"foo\\bar"')
        assert isinstance(ast, FieldClause)
        assert ast.value_kind == "phrase"
        assert ast.value == r"foo\bar"

    def test_phrase_preserves_other_metachars_literally(self) -> None:
        # クォート内では + - ( ) [ ] などは literal (SSOT §エスケープ規則 L410-414)
        ast = parse(r'title:"HIF-1 alpha (ChIP-Seq)"')
        assert isinstance(ast, FieldClause)
        assert ast.value == "HIF-1 alpha (ChIP-Seq)"


class TestErrorPositionMeta:
    def test_unexpected_token_column_reported(self) -> None:
        with pytest.raises(DslError) as exc_info:
            parse("title:cancer^2")
        # `^` は 13 文字目 (1-based)
        assert exc_info.value.column == 13

    def test_unmatched_paren_column_reported(self) -> None:
        with pytest.raises(DslError) as exc_info:
            parse("(title:a")
        # EOF 相当 (末尾位置)。少なくとも 0 より大きい
        assert exc_info.value.column > 0


class TestDslParserPBT:
    @given(
        field=st.sampled_from(["identifier", "title", "description", "organism"]),
        word=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_"),
            min_size=1,
            max_size=20,
        ),
    )
    @settings(max_examples=50, deadline=None)
    def test_word_roundtrip(self, field: str, word: str) -> None:
        ast = parse(f"{field}:{word}")
        assert isinstance(ast, FieldClause)
        assert ast.field == field
        assert ast.value_kind == "word"
        assert ast.value == word

    @given(
        date_str=st.from_regex(r"\d{4}-\d{2}-\d{2}", fullmatch=True),
    )
    @settings(max_examples=30, deadline=None)
    def test_date_like_roundtrip(self, date_str: str) -> None:
        # 構文的に日付 shape なら value_kind=date (valid か否かは validator の仕事)
        ast = parse(f"date_published:{date_str}")
        assert isinstance(ast, FieldClause)
        assert ast.value_kind == "date"
        assert ast.value == date_str

    @given(
        left=st.sampled_from(["title:a", "description:b", "organism:c"]),
        right=st.sampled_from(["title:x", "description:y", "organism:z"]),
        op=st.sampled_from(["AND", "OR"]),
    )
    @settings(max_examples=30, deadline=None)
    def test_binary_roundtrip(self, left: str, right: str, op: str) -> None:
        ast = parse(f"{left} {op} {right}")
        assert isinstance(ast, BoolOp)
        assert ast.op == op
