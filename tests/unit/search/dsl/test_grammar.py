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

from ddbj_search_api.search.dsl import DslError, ErrorType, parse, validate
from ddbj_search_api.search.dsl.ast import BoolOp, FieldClause, FreeText, Node, Range


class TestFieldClauseValueKinds:
    """value_kind は phrase / word / wildcard / date / range の 5 種。"""

    def test_word(self) -> None:
        ast = parse("title:cancer")
        assert isinstance(ast, FieldClause)
        assert ast.field == "title"
        assert ast.value_kind == "word"
        assert ast.value == "cancer"

    def test_phrase_simple(self) -> None:
        ast = parse('organism_name:"Homo sapiens"')
        assert isinstance(ast, FieldClause)
        assert ast.field == "organism_name"
        assert ast.value_kind == "phrase"
        assert ast.value == "Homo sapiens"

    def test_phrase_simple_single_quote(self) -> None:
        ast = parse("organism_name:'Homo sapiens'")
        assert isinstance(ast, FieldClause)
        assert ast.field == "organism_name"
        assert ast.value_kind == "phrase"
        assert ast.value == "Homo sapiens"

    def test_phrase_empty(self) -> None:
        # field:"" は phrase として parse される (validator が missing-value で弾く)
        ast = parse('title:""')
        assert isinstance(ast, FieldClause)
        assert ast.value_kind == "phrase"
        assert ast.value == ""

    def test_phrase_empty_single_quote(self) -> None:
        # field:'' も同様 phrase として parse される
        ast = parse("title:''")
        assert isinstance(ast, FieldClause)
        assert ast.value_kind == "phrase"
        assert ast.value == ""

    def test_phrase_single_quote_can_contain_double_quote(self) -> None:
        # single-quoted phrase 内では `"` を escape なしで埋め込める
        ast = parse("""title:'foo"bar'""")
        assert isinstance(ast, FieldClause)
        assert ast.value_kind == "phrase"
        assert ast.value == 'foo"bar'

    def test_phrase_double_quote_can_contain_single_quote(self) -> None:
        # double-quoted phrase 内では `'` を escape なしで埋め込める (対称性)
        ast = parse('title:"foo\'bar"')
        assert isinstance(ast, FieldClause)
        assert ast.value_kind == "phrase"
        assert ast.value == "foo'bar"

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

    def test_wildcard_with_hyphen(self) -> None:
        ast = parse("title:HIF-1*")
        assert isinstance(ast, FieldClause)
        assert ast.value_kind == "wildcard"
        assert ast.value == "HIF-1*"

    def test_wildcard_with_hyphen_and_digits(self) -> None:
        ast = parse("title:COVID-19*")
        assert isinstance(ast, FieldClause)
        assert ast.value_kind == "wildcard"
        assert ast.value == "COVID-19*"

    def test_wildcard_with_multiple_hyphens(self) -> None:
        ast = parse("title:SARS-CoV-2*")
        assert isinstance(ast, FieldClause)
        assert ast.value_kind == "wildcard"
        assert ast.value == "SARS-CoV-2*"

    def test_wildcard_with_dot(self) -> None:
        ast = parse("title:1.5*")
        assert isinstance(ast, FieldClause)
        assert ast.value_kind == "wildcard"
        assert ast.value == "1.5*"

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


def _parse_then_validate(dsl: str) -> None:
    """Run the full parse + validate pipeline.

    ``pytest.raises`` expects a single statement inside its ``with``
    block, so the parse-then-validate flow is factored into this helper.
    """
    ast = parse(dsl)
    # If the parser accepted the input (e.g. because the metachar split
    # the token into WORD + WILDCARD and somehow parsed anyway), the
    # validator must catch it as a backstop.
    validate(ast, mode="cross")


class TestWildcardCharacterClass:
    """Solr / Lucene metacharacters must not appear in a wildcard token.

    The grammar's WILDCARD class is narrowed to ``[A-Za-z0-9_\\-.*?]`` so a
    misuse like ``title:foo+bar*`` cannot escape to the Solr edismax ``q``
    string with operator-bearing characters. Whatever the exact failure
    mode (``unexpected-token`` from the grammar or ``invalid-operator-
    for-field`` from the validator fallback), the parse pipeline must
    reject the input.
    """

    @pytest.mark.parametrize(
        "dsl",
        [
            "title:foo+bar*",
            "title:foo|bar*",
            "title:foo&bar*",
            "title:foo!bar*",
            "title:foo<bar*",
            "title:foo>bar*",
            "title:foo=bar*",
            "title:foo\\bar*",
        ],
    )
    def test_lucene_metachars_in_wildcard_rejected(self, dsl: str) -> None:
        with pytest.raises(DslError):
            _parse_then_validate(dsl)


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


def _flatten_leaves(node: Node) -> list[FieldClause]:
    if isinstance(node, FreeText):
        # この helper は FieldClause leaf のみ収集する用途で、FreeText を含む AST は対象外.
        # FreeText 入りの AST を扱うテストは個別に AST を navigate する.
        raise TypeError("FreeText nodes are not handled by this helper")
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

    def test_phrase_with_escaped_single_quote(self) -> None:
        ast = parse(r"title:'foo\'bar'")
        assert isinstance(ast, FieldClause)
        assert ast.value_kind == "phrase"
        # escape は解除されて \' → '
        assert ast.value == "foo'bar"

    def test_phrase_with_escaped_backslash(self) -> None:
        ast = parse(r'title:"foo\\bar"')
        assert isinstance(ast, FieldClause)
        assert ast.value_kind == "phrase"
        assert ast.value == r"foo\bar"

    def test_phrase_with_escaped_backslash_single_quote(self) -> None:
        ast = parse(r"title:'foo\\bar'")
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
        field=st.sampled_from(["identifier", "title", "description", "organism_name"]),
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
        field=st.sampled_from(["identifier", "title", "description", "organism_name"]),
        quote=st.sampled_from(['"', "'"]),
        inner=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters=" _-"),
            min_size=1,
            max_size=20,
        ),
    )
    @settings(max_examples=50, deadline=None)
    def test_phrase_roundtrip(self, field: str, quote: str, inner: str) -> None:
        """phrase は double / single 両 quote で対称に parse される。

        inner には quote / backslash を含めない (escape は別 case で確認済み)。
        """
        ast = parse(f"{field}:{quote}{inner}{quote}")
        assert isinstance(ast, FieldClause)
        assert ast.field == field
        assert ast.value_kind == "phrase"
        assert ast.value == inner

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
        left=st.sampled_from(["title:a", "description:b", "organism_name:c"]),
        right=st.sampled_from(["title:x", "description:y", "organism_name:z"]),
        op=st.sampled_from(["AND", "OR"]),
    )
    @settings(max_examples=30, deadline=None)
    def test_binary_roundtrip(self, left: str, right: str, op: str) -> None:
        ast = parse(f"{left} {op} {right}")
        assert isinstance(ast, BoolOp)
        assert ast.op == op

    @given(
        word=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_"),
            min_size=1,
            max_size=20,
        ),
    )
    @settings(max_examples=30, deadline=None)
    def test_free_text_bare_roundtrip(self, word: str) -> None:
        """bare word (root) は FreeText(is_phrase=False) として parse される."""
        ast = parse(word)
        assert isinstance(ast, FreeText)
        assert ast.value == word
        assert ast.is_phrase is False

    @given(
        quote=st.sampled_from(['"', "'"]),
        inner=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters=" _-"),
            min_size=1,
            max_size=20,
        ),
    )
    @settings(max_examples=30, deadline=None)
    def test_free_text_quoted_phrase_roundtrip(self, quote: str, inner: str) -> None:
        """quoted FreeText (root) は FreeText(is_phrase=True) として parse される.

        ``inner`` には quote / backslash を含めない (escape は TestPhraseEscaping で別途確認).
        """
        ast = parse(f"{quote}{inner}{quote}")
        assert isinstance(ast, FreeText)
        assert ast.value == inner
        assert ast.is_phrase is True


class TestFreeTextPhraseFlag:
    """FreeText が quoted phrase 由来 (`"..."` / `'...'`) か bare word 由来かを ``is_phrase`` で区別する.

    parser 段で flag を立てておかないと compile_es が ``multi_match.type=phrase``
    (順序保持) を出せず、``q='"Homo sapiens"'`` が ``operator=and`` の AND match
    (順序非保持) に退化する (db-portal-api-spec.md § FreeText auto-phrase 処理)。
    """

    def test_double_quoted_phrase_sets_is_phrase_true(self) -> None:
        ast = parse('"Homo sapiens"')
        assert isinstance(ast, FreeText)
        assert ast.value == "Homo sapiens"
        assert ast.is_phrase is True

    def test_single_quoted_phrase_sets_is_phrase_true(self) -> None:
        ast = parse("'Homo sapiens'")
        assert isinstance(ast, FreeText)
        assert ast.value == "Homo sapiens"
        assert ast.is_phrase is True

    def test_bare_word_sets_is_phrase_false(self) -> None:
        ast = parse("cancer")
        assert isinstance(ast, FreeText)
        assert ast.value == "cancer"
        assert ast.is_phrase is False

    def test_bare_word_with_symbol_sets_is_phrase_false(self) -> None:
        # auto-phrase 化は compile 段の責務で AST 上は bare のまま (is_phrase=False).
        ast = parse("HIF-1")
        assert isinstance(ast, FreeText)
        assert ast.value == "HIF-1"
        assert ast.is_phrase is False

    def test_escaped_phrase_preserves_is_phrase_true(self) -> None:
        ast = parse(r'"foo\"bar"')
        assert isinstance(ast, FreeText)
        assert ast.value == 'foo"bar'
        assert ast.is_phrase is True

    def test_quoted_phrase_with_comma_inside_kept_as_single_freetext(self) -> None:
        # 引用符内のコンマは phrase の一部として保持される (AST 段では unquote 済み value のみ持つ).
        ast = parse('"cancer, tumor"')
        assert isinstance(ast, FreeText)
        assert ast.value == "cancer, tumor"
        assert ast.is_phrase is True

    def test_quoted_phrase_inside_and_carries_flag(self) -> None:
        ast = parse('"Homo sapiens" AND title:cancer')
        assert isinstance(ast, BoolOp)
        assert ast.op == "AND"
        free_text_children = [c for c in ast.children if isinstance(c, FreeText)]
        assert len(free_text_children) == 1
        assert free_text_children[0].is_phrase is True
        assert free_text_children[0].value == "Homo sapiens"

    def test_bare_word_inside_and_carries_flag(self) -> None:
        ast = parse("cancer AND title:tumor")
        assert isinstance(ast, BoolOp)
        assert ast.op == "AND"
        free_text_children = [c for c in ast.children if isinstance(c, FreeText)]
        assert len(free_text_children) == 1
        assert free_text_children[0].is_phrase is False
        assert free_text_children[0].value == "cancer"
