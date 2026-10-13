"""Tests for ast_to_dsl serializer (AST → DSL string).

SSOT 1: db-portal/docs/api-requests/serialize-endpoint.md §4 (request の変換例).
SSOT 2: ddbj_search_api/search/dsl/grammar.lark (precedence / quote 判定).

quote / paren 判定は grammar.lark に依存する。WORD = ``[^\\s:()\\[\\]"{}^~*?\\/]+``、
PHRASE は ``"..."`` のエスケープ ``\\"`` / ``\\\\`` を受ける。AND > OR、NOT は ``not_op: NOT atom``
で BoolOp 子を直接置けないため NOT の子が BoolOp なら必ず括弧化する。
"""

from __future__ import annotations

import string

from hypothesis import given, settings
from hypothesis import strategies as st

from ddbj_search_api.search.dsl import parse
from ddbj_search_api.search.dsl.ast import BoolOp, FieldClause, FreeText, Node, Position, Range
from ddbj_search_api.search.dsl.lex_patterns import is_bare_safe_multiword, lower_reserved_words
from ddbj_search_api.search.dsl.serde import ast_to_json
from ddbj_search_api.search.dsl.serializer import ast_to_dsl
from ddbj_search_api.search.dsl.validator import validate
from tests.unit.strategies import valid_ast_strategy


def _pos() -> Position:
    return Position(column=1, length=0)


def _free(value: str) -> FreeText:
    return FreeText(value=value)


def _word(field: str, value: str) -> FieldClause:
    return FieldClause(field=field, value_kind="word", value=value, position=_pos())


def _phrase(field: str, value: str) -> FieldClause:
    return FieldClause(field=field, value_kind="phrase", value=value, position=_pos())


def _wildcard(field: str, value: str) -> FieldClause:
    return FieldClause(field=field, value_kind="wildcard", value=value, position=_pos())


def _date(field: str, value: str) -> FieldClause:
    return FieldClause(field=field, value_kind="date", value=value, position=_pos())


def _range(field: str, from_: str, to: str) -> FieldClause:
    return FieldClause(
        field=field,
        value_kind="range",
        value=Range(from_=from_, to=to),
        position=_pos(),
    )


def _and(*children: Node) -> BoolOp:
    return BoolOp(op="AND", children=tuple(children), position=_pos())


def _or(*children: Node) -> BoolOp:
    return BoolOp(op="OR", children=tuple(children), position=_pos())


def _not(child: Node) -> BoolOp:
    return BoolOp(op="NOT", children=(child,), position=_pos())


# === §4 の変換例 9 ケース ===


class TestSerializeSpecExamples:
    """db-portal/docs/api-requests/serialize-endpoint.md §4 の変換例."""

    def test_simple_keyword(self) -> None:
        assert ast_to_dsl(_free("cancer")) == "cancer"

    def test_multiple_keywords_and(self) -> None:
        assert ast_to_dsl(_and(_free("cancer"), _free("tumor"))) == "cancer AND tumor"

    def test_single_field_eq_phrase(self) -> None:
        assert ast_to_dsl(_phrase("organism_name", "Homo sapiens")) == 'organism_name:"Homo sapiens"'

    def test_free_text_plus_field(self) -> None:
        ast = _and(_free("cancer"), _phrase("organism_name", "Homo sapiens"))
        assert ast_to_dsl(ast) == 'cancer AND organism_name:"Homo sapiens"'

    def test_or_inside_and_requires_paren(self) -> None:
        ast = _and(
            _or(_free("cancer"), _free("tumor")),
            _phrase("organism_name", "Homo sapiens"),
        )
        assert ast_to_dsl(ast) == '(cancer OR tumor) AND organism_name:"Homo sapiens"'

    def test_not_with_and_parent(self) -> None:
        ast = _and(_free("cancer"), _not(_phrase("organism_name", "Mus musculus")))
        assert ast_to_dsl(ast) == 'cancer AND NOT organism_name:"Mus musculus"'

    def test_range(self) -> None:
        assert (
            ast_to_dsl(_range("date_published", "2020-01-01", "2024-12-31"))
            == "date_published:[2020-01-01 TO 2024-12-31]"
        )

    def test_wildcard_bare(self) -> None:
        # 依頼書 §4 wildcard 例 (gene field は allowlist 外だが、serializer 単体は field 名を見ない).
        assert ast_to_dsl(_wildcard("gene", "BRCA1*")) == "gene:BRCA1*"

    def test_high_precedence_and_inside_or_no_paren(self) -> None:
        # 依頼書 §3.2 表: BoolOp(OR, [BoolOp(AND, [a, b]), c]) は AND 子に括弧不要.
        a = _word("title", "a")
        b = _word("title", "b")
        c = _word("title", "c")
        assert ast_to_dsl(_or(_and(a, b), c)) == "title:a AND title:b OR title:c"


# === 境界 / quote 判定 ===


class TestFreeTextQuoting:
    def test_value_with_space_bare_multiword(self) -> None:
        # 空白区切りの各 token が bare 出力可能なら bare のまま. parser の
        # ``free_text_atom: WORD+`` が 1 つの FreeText (is_phrase=False) に復元するので
        # quote 不要 (round-trip 安定).
        assert ast_to_dsl(_free("cancer tumor")) == "cancer tumor"

    def test_value_with_operator_literal_must_quote(self) -> None:
        # 空白区切りでも token に operator literal が混じると bare 化できず quote.
        assert ast_to_dsl(_free("cancer AND")) == '"cancer AND"'

    def test_value_with_date_shape_stays_bare(self) -> None:
        # keyword の位置では parser が DATE を読まないので、日付の形の語も bare のまま.
        assert ast_to_dsl(_free("cancer 2024-01-01")) == "cancer 2024-01-01"
        reparsed = parse("cancer 2024-01-01")
        assert isinstance(reparsed, FreeText)
        assert reparsed.value == "cancer 2024-01-01"
        assert reparsed.is_phrase is False

    def test_value_with_consecutive_spaces_must_quote(self) -> None:
        # 連続空白は単一空白正規形でないため quote (bare だと re-parse で空白が畳まれ drift).
        assert ast_to_dsl(_free("cancer  tumor")) == '"cancer  tumor"'

    def test_value_with_colon_must_quote(self) -> None:
        assert ast_to_dsl(_free("a:b")) == '"a:b"'

    def test_value_with_double_quote_escaped(self) -> None:
        assert ast_to_dsl(_free('a"b c')) == '"a\\"b c"'


class TestFreeTextPhraseSerialization:
    """``FreeText.is_phrase=True`` の AST を AST → DSL シリアライズしたとき、
    無条件で quote を保持する.

    意図: ``/db-portal/parse`` のレスポンス JSON tree から DSL を組み立て直したとき
    (e.g. /db-portal/serialize)、ユーザーがクオートで囲んだ事実を保つ。
    single word でも is_phrase=True なら ``"cancer"`` で出力し、再 parse 時に
    is_phrase=True の FreeText に戻る (GUI 復元の round-trip 安定性のため)。
    """

    def test_is_phrase_true_single_word_keeps_quote(self) -> None:
        # bare word (cancer は WORD regex full-match) でも is_phrase=True なら quote.
        ast = FreeText(value="cancer", is_phrase=True)
        assert ast_to_dsl(ast) == '"cancer"'

    def test_is_phrase_false_single_word_bare(self) -> None:
        # 回帰: is_phrase=False は従来通り bare 出力.
        ast = FreeText(value="cancer", is_phrase=False)
        assert ast_to_dsl(ast) == "cancer"

    def test_is_phrase_true_with_space_keeps_quote(self) -> None:
        ast = FreeText(value="Homo sapiens", is_phrase=True)
        assert ast_to_dsl(ast) == '"Homo sapiens"'

    def test_is_phrase_true_with_inner_double_quote_escaped(self) -> None:
        # is_phrase=True 出力でも quote/backslash の escape は維持.
        ast = FreeText(value='a"b', is_phrase=True)
        assert ast_to_dsl(ast) == '"a\\"b"'

    def test_is_phrase_true_parse_reparse_round_trip(self) -> None:
        # FreeText(is_phrase=True) → DSL → parse 後も is_phrase=True に戻る.
        ast = FreeText(value="cancer", is_phrase=True)
        dsl = ast_to_dsl(ast)
        reparsed = parse(dsl)
        assert isinstance(reparsed, FreeText)
        assert reparsed.value == "cancer"
        assert reparsed.is_phrase is True

    def test_is_phrase_false_parse_reparse_round_trip(self) -> None:
        # FreeText(is_phrase=False) → DSL → parse でも is_phrase=False のまま.
        # (value が WORD full-match の bare 出力ケース.)
        ast = FreeText(value="cancer", is_phrase=False)
        dsl = ast_to_dsl(ast)
        reparsed = parse(dsl)
        assert isinstance(reparsed, FreeText)
        assert reparsed.value == "cancer"
        assert reparsed.is_phrase is False

    def test_is_phrase_false_multiword_parse_reparse_round_trip(self) -> None:
        # 空白区切りの複数 bare word は bare 出力 → parse で is_phrase=False の
        # 単一 FreeText に戻る (``free_text_atom: WORD+``).
        ast = FreeText(value="cancer tumor mouse", is_phrase=False)
        dsl = ast_to_dsl(ast)
        assert dsl == "cancer tumor mouse"
        reparsed = parse(dsl)
        assert isinstance(reparsed, FreeText)
        assert reparsed.value == "cancer tumor mouse"
        assert reparsed.is_phrase is False

    def test_is_phrase_true_inside_and_serialized(self) -> None:
        # AND 子に is_phrase=True の FreeText が混ざってもシリアライズで quote 保持.
        ast = _and(
            FreeText(value="cancer", is_phrase=True),
            _word("title", "tumor"),
        )
        assert ast_to_dsl(ast) == '"cancer" AND title:tumor'


class TestFieldClauseQuoting:
    def test_word_bare(self) -> None:
        assert ast_to_dsl(_word("title", "cancer")) == "title:cancer"

    def test_phrase_always_quoted(self) -> None:
        # value_kind=phrase は WORD match しても常に quote.
        assert ast_to_dsl(_phrase("title", "cancer")) == 'title:"cancer"'

    def test_phrase_inner_double_quote_escaped(self) -> None:
        assert ast_to_dsl(_phrase("title", 'a"b')) == 'title:"a\\"b"'

    def test_phrase_inner_backslash_escaped(self) -> None:
        # _PHRASE_UNESCAPE の逆向き: \ を \\ に.  parser は \\X → X に戻すので
        # 入力 "a\b" を一意に表現するために \\b と書く必要がある.
        assert ast_to_dsl(_phrase("title", "a\\b")) == 'title:"a\\\\b"'

    def test_phrase_with_space(self) -> None:
        assert ast_to_dsl(_phrase("title", "cancer treatment")) == 'title:"cancer treatment"'

    def test_wildcard_with_hyphen_bare(self) -> None:
        # WILDCARD regex は hyphen を許容する (HIF-1*, COVID-19* など).
        assert ast_to_dsl(_wildcard("gene", "HIF-1*")) == "gene:HIF-1*"

    def test_wildcard_question_mark(self) -> None:
        assert ast_to_dsl(_wildcard("identifier", "PRJDB?")) == "identifier:PRJDB?"

    def test_date_value(self) -> None:
        assert ast_to_dsl(_date("date_published", "2024-01-01")) == "date_published:2024-01-01"


class TestParserTokenCollisions:
    """grammar の DATE / AND / OR / NOT は WORD よりも token priority が高いため、
    word value / FreeText が形だけ一致すると parser が別 token として lex する.
    serializer はこの衝突を quote で防がなければ round-trip が破綻する.
    """

    def test_date_shape_word_value_for_text_field(self) -> None:
        # title:1234-56-78 を bare で出すと parser が DATE token として読み、
        # (text, date) は OPERATOR_BY_KIND に無いので validator が reject する.
        dsl = ast_to_dsl(_word("title", "1234-56-78"))
        parsed = parse(dsl)
        validate(parsed, mode="cross")
        assert isinstance(parsed, FieldClause)
        assert parsed.field == "title"
        assert parsed.value_kind == "phrase"
        assert parsed.value == "1234-56-78"

    def test_date_shape_word_value_for_identifier_field(self) -> None:
        # identifier も値型 word.  bare 出力すると DATE drift.
        dsl = ast_to_dsl(_word("identifier", "2024-01-01"))
        parsed = parse(dsl)
        validate(parsed, mode="cross")
        assert isinstance(parsed, FieldClause)
        assert parsed.field == "identifier"
        # quote 経由なので phrase に変わるが、(identifier, phrase) → eq は valid.
        assert parsed.value_kind == "phrase"
        assert parsed.value == "2024-01-01"

    def test_free_text_not_literal_quoted(self) -> None:
        # FreeText("NOT") を bare で出すと grammar の NOT operator として lex され parse fail.
        dsl = ast_to_dsl(_free("NOT"))
        parsed = parse(dsl)
        assert isinstance(parsed, FreeText)
        assert parsed.value == "NOT"

    def test_free_text_and_literal_quoted(self) -> None:
        dsl = ast_to_dsl(_free("AND"))
        parsed = parse(dsl)
        assert isinstance(parsed, FreeText)
        assert parsed.value == "AND"

    def test_free_text_or_literal_quoted(self) -> None:
        dsl = ast_to_dsl(_free("OR"))
        parsed = parse(dsl)
        assert isinstance(parsed, FreeText)
        assert parsed.value == "OR"

    def test_word_value_and_literal_for_text_field(self) -> None:
        # title:AND が parser で operator として誤吸収されないよう quote する.
        dsl = ast_to_dsl(_word("title", "AND"))
        parsed = parse(dsl)
        validate(parsed, mode="cross")
        assert isinstance(parsed, FieldClause)
        assert parsed.field == "title"
        assert parsed.value == "AND"

    def test_free_text_date_shape_quoted(self) -> None:
        # FreeText("2024-01-01") を bare で出すと DATE token として lex され
        # field-less DATE は grammar 構造上 ambiguous (free_text_atom: PHRASE|WORD のみ).
        dsl = ast_to_dsl(_free("2024-01-01"))
        parsed = parse(dsl)
        assert isinstance(parsed, FreeText)
        assert parsed.value == "2024-01-01"


class TestPrecedenceAndParens:
    def test_not_around_atom_no_paren(self) -> None:
        assert ast_to_dsl(_not(_word("title", "a"))) == "NOT title:a"

    def test_not_around_free_text_no_paren(self) -> None:
        assert ast_to_dsl(_not(_free("cancer"))) == "NOT cancer"

    def test_not_around_and_requires_paren(self) -> None:
        ast = _not(_and(_word("title", "a"), _word("title", "b")))
        assert ast_to_dsl(ast) == "NOT (title:a AND title:b)"

    def test_not_around_or_requires_paren(self) -> None:
        ast = _not(_or(_word("title", "a"), _word("title", "b")))
        assert ast_to_dsl(ast) == "NOT (title:a OR title:b)"

    def test_not_not_requires_paren(self) -> None:
        # grammar の not_op: NOT atom は連鎖 NOT を許容しないので括弧必須.
        assert ast_to_dsl(_not(_not(_word("title", "x")))) == "NOT (NOT title:x)"

    def test_or_inside_and_requires_paren(self) -> None:
        ast = _and(_or(_word("title", "a"), _word("title", "b")), _word("title", "c"))
        assert ast_to_dsl(ast) == "(title:a OR title:b) AND title:c"

    def test_and_inside_or_no_paren(self) -> None:
        # AND > OR.  OR 配下の AND は括弧不要.
        ast = _or(_and(_word("title", "a"), _word("title", "b")), _word("title", "c"))
        assert ast_to_dsl(ast) == "title:a AND title:b OR title:c"

    def test_chain_and_three_children(self) -> None:
        ast = _and(_word("title", "a"), _word("title", "b"), _word("title", "c"))
        assert ast_to_dsl(ast) == "title:a AND title:b AND title:c"

    def test_chain_or_three_children(self) -> None:
        ast = _or(_word("title", "a"), _word("title", "b"), _word("title", "c"))
        assert ast_to_dsl(ast) == "title:a OR title:b OR title:c"

    def test_deep_nesting(self) -> None:
        # 5 段ネスト.  最外殻が AND で、内部の OR は括弧化される.
        x = _word("title", "x")
        ast = _and(_or(_and(_or(_and(x, x), x), x), x), x)
        # 評価:
        # inner1: _and(x, x)                 → "title:x AND title:x"
        # inner2: _or(inner1, x)             → "title:x AND title:x OR title:x" (AND は OR より高)
        # inner3: _and(inner2, x)            → "(title:x AND title:x OR title:x) AND title:x" (OR は AND より低 → 括弧)
        # inner4: _or(inner3, x)             → "(title:x AND title:x OR title:x) AND title:x OR title:x"
        # outer:  _and(inner4, x)            → "((title:x AND title:x OR title:x) AND title:x OR title:x) AND title:x"
        expected = "((title:x AND title:x OR title:x) AND title:x OR title:x) AND title:x"
        assert ast_to_dsl(ast) == expected


# === Round-trip 不変量 (PBT) ===


def _equals_ignoring_position(a: Node, b: Node) -> bool:
    """Equality on the JSON-tree projection (SSOT form for ``DbPortalParseResponse``).

    Position は JSON 表現に含まれないため自動的に無視される.  word/phrase の
    ``value_kind`` 差は ``OPERATOR_BY_KIND`` の逆引きで同じ operator に collapse する
    場合があるが、それは依頼書 §3.2 の正規化 (DATE-shape / operator literal を quote 化
    すると word → phrase に流れる) と整合する設計上の挙動である.  client から見える
    観測量は JSON tree なのでこの粒度で比較する.
    """
    return ast_to_json(a) == ast_to_json(b)


class TestRoundTrip:
    """``validate(parse(ast_to_dsl(ast))) == ast`` を任意の valid AST について検証.

    依頼書 §6.1 round-trip 不変量.  Position は parser が新規付与するので比較から除外.
    """

    @given(ast=valid_ast_strategy())
    @settings(max_examples=200, deadline=None)
    def test_serialize_parse_round_trip(self, ast: Node) -> None:
        # 前提: 元 AST が cross-mode で valid.
        validate(ast, mode="cross")
        dsl = ast_to_dsl(ast)
        reparsed = parse(dsl)
        validate(reparsed, mode="cross")
        assert _equals_ignoring_position(reparsed, ast), (
            f"round-trip mismatch:\n  dsl={dsl!r}\n  original={ast!r}\n  reparsed={reparsed!r}"
        )


class TestDatePrefixedValues:
    """grammar の DATE は値の先頭だけが合っても切り出すので、日付で始まる word 値は quote する.
    keyword の位置では parser が DATE を読まないので、keyword の語は日付で始まっても bare のまま.
    """

    def test_word_value_starting_with_a_date_is_quoted(self) -> None:
        assert ast_to_dsl(_word("title", "2024-01-01x")) == 'title:"2024-01-01x"'

    def test_word_value_starting_with_a_date_round_trips(self) -> None:
        reparsed = parse(ast_to_dsl(_word("title", "2024-01-01x")))
        assert isinstance(reparsed, FieldClause)
        assert reparsed.field == "title"
        assert reparsed.value == "2024-01-01x"

    def test_word_value_that_is_a_date_is_quoted(self) -> None:
        assert ast_to_dsl(_word("title", "2024-01-01")) == 'title:"2024-01-01"'

    def test_keyword_word_starting_with_a_date_stays_bare(self) -> None:
        assert ast_to_dsl(_free("2024-01-01x cancer")) == "2024-01-01x cancer"
        reparsed = parse("2024-01-01x cancer")
        assert isinstance(reparsed, FreeText)
        assert reparsed.value == "2024-01-01x cancer"
        assert reparsed.is_phrase is False

    def test_keyword_that_is_a_date_stays_bare(self) -> None:
        assert ast_to_dsl(_free("2024-01-01")) == "2024-01-01"
        reparsed = parse("2024-01-01")
        assert isinstance(reparsed, FreeText)
        assert reparsed.value == "2024-01-01"
        assert reparsed.is_phrase is False


class TestReservedWordsInKeywords:
    """keyword の語の ``AND`` / ``OR`` / ``NOT`` は quote すると phrase になり意味が変わるので、
    parser と serde が小文字にした語をそのまま bare で出す. 大文字のままの語は quote する.
    """

    def test_lower_case_reserved_word_stays_bare(self) -> None:
        assert ast_to_dsl(_free("cancer not mouse")) == "cancer not mouse"

    def test_upper_case_reserved_word_is_quoted(self) -> None:
        assert ast_to_dsl(_free("cancer NOT mouse")) == '"cancer NOT mouse"'

    def test_parsed_keyword_with_not_round_trips_as_words(self) -> None:
        dsl = ast_to_dsl(parse("cancer NOT mouse"))
        assert dsl == "cancer not mouse"
        reparsed = parse(dsl)
        assert isinstance(reparsed, FreeText)
        assert reparsed.value == "cancer not mouse"
        assert reparsed.is_phrase is False

    def test_parsed_keyword_with_and_at_the_start_round_trips_as_words(self) -> None:
        assert ast_to_dsl(parse("AND cancer")) == "and cancer"

    def test_word_starting_with_operator_letters_stays_bare(self) -> None:
        assert ast_to_dsl(_free("NOTCH1 signaling")) == "NOTCH1 signaling"
        assert ast_to_dsl(_free("breast ORGANOID")) == "breast ORGANOID"

    def test_word_that_is_an_operator_followed_by_a_quote_is_quoted(self) -> None:
        # ``x AND'y`` は AND を演算子として読むので、この語を含む keyword は bare にできない.
        assert ast_to_dsl(_free("x AND'y")) == '"x AND\'y"'


class TestSingleQuoteQuoting:
    """``'`` は語の文字でもあり、単一引用符の phrase の区切りでもある. 語を読み直したときに
    phrase にならない形だけ bare で出し、それ以外は二重引用符で quote する.
    """

    def test_value_starting_with_a_single_quote_is_quoted(self) -> None:
        assert ast_to_dsl(_word("title", "'x")) == 'title:"\'x"'
        reparsed = parse(ast_to_dsl(_word("title", "'x")))
        assert isinstance(reparsed, FieldClause)
        assert reparsed.value == "'x"

    def test_keyword_words_with_an_unclosed_quote_stay_bare(self) -> None:
        assert ast_to_dsl(_free("x 's")) == "x 's"
        assert ast_to_dsl(_free("'x y")) == "'x y"

    def test_keyword_words_that_close_a_single_quote_together_are_quoted(self) -> None:
        assert ast_to_dsl(_free("'s y'")) == "\"'s y'\""
        reparsed = parse(ast_to_dsl(_free("'s y'")))
        assert isinstance(reparsed, FreeText)
        assert reparsed.value == "'s y'"

    def test_closing_single_quote_inside_a_phrase_is_escaped(self) -> None:
        assert ast_to_dsl(FreeText(value="a' b", is_phrase=True)) == '"a\\\' b"'
        reparsed = parse(ast_to_dsl(FreeText(value="a' b", is_phrase=True)))
        assert isinstance(reparsed, FreeText)
        assert reparsed.value == "a' b"
        assert reparsed.is_phrase is True

    def test_single_quote_inside_or_at_the_end_of_a_word_stays_bare(self) -> None:
        assert ast_to_dsl(_free("5'-UTR 3' Alzheimer's")) == "5'-UTR 3' Alzheimer's"
        assert ast_to_dsl(_word("title", "b'")) == "title:b'"

    def test_clauses_that_together_hold_a_pair_of_single_quotes_round_trip(self) -> None:
        ast = _and(
            _word("title", "'a"), _word("description", "b'"), FreeText(value="'s", is_phrase=True), _free("Crohn's")
        )
        dsl = ast_to_dsl(ast)
        assert dsl == "title:\"'a\" AND description:b' AND \"'s\" AND Crohn's"
        assert _equals_ignoring_position(parse(dsl), ast)

    def test_word_before_a_phrase_with_a_closing_quote_round_trips(self) -> None:
        ast = _and(_free("'x"), FreeText(value="a' b", is_phrase=True))
        assert _equals_ignoring_position(parse(ast_to_dsl(ast)), ast)


_KEYWORD_WORDS = st.one_of(
    st.text(alphabet="abcxyz019'-", min_size=1, max_size=6),
    st.sampled_from(
        [
            "AND",
            "OR",
            "NOT",
            "and",
            "or",
            "not",
            "2024-01-01",
            "2024-01-01x",
            "NOTCH1",
            "ORGANOID",
            "'s",
            "'a",
            "b'",
            "'",
        ]
    ),
)
_PHRASE_TEXT = st.text(alphabet=string.ascii_letters + string.digits + " '\"\\()[]:-", max_size=12)


class TestKeywordRoundTripPBT:
    @given(words=st.lists(_KEYWORD_WORDS, min_size=1, max_size=4))
    @settings(max_examples=300, deadline=None)
    def test_keyword_of_words_serializes_and_parses_back(self, words: list[str]) -> None:
        value = lower_reserved_words(" ".join(words))
        node = FreeText(value=value, is_phrase=not is_bare_safe_multiword(value))
        reparsed = parse(ast_to_dsl(node))
        assert isinstance(reparsed, FreeText)
        assert _equals_ignoring_position(reparsed, node)

    @given(value=_PHRASE_TEXT)
    @settings(max_examples=200, deadline=None)
    def test_phrase_serializes_and_parses_back(self, value: str) -> None:
        node = FreeText(value=value, is_phrase=True)
        reparsed = parse(ast_to_dsl(node))
        assert isinstance(reparsed, FreeText)
        assert _equals_ignoring_position(reparsed, node)

    @given(words=st.lists(_KEYWORD_WORDS, min_size=1, max_size=3), value=_PHRASE_TEXT)
    @settings(max_examples=200, deadline=None)
    def test_keyword_before_a_phrase_serializes_and_parses_back(self, words: list[str], value: str) -> None:
        keyword = lower_reserved_words(" ".join(words))
        node = _and(
            FreeText(value=keyword, is_phrase=not is_bare_safe_multiword(keyword)),
            FreeText(value=value, is_phrase=True),
        )
        assert _equals_ignoring_position(parse(ast_to_dsl(node)), node)
