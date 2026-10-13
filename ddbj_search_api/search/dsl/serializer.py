"""AST → DSL string (inverse of parser.py).

``POST /db-portal/serialize`` のレスポンスとして DSL 文字列を生成する.

SSOT: grammar.lark.  WORD / PHRASE / WILDCARD / RANGE の lexer 定義と、
``or_expr / and_expr / not_op`` の precedence 規則を直接反映する.

経路::

    JSON tree ─json_to_ast─> AST ─validate─> AST ─ast_to_dsl─> DSL string

quote / paren 判定:

- ``value_kind="word"``: ``is_bare_word`` (WORD として読み戻せる) なら bare、そうでなければ quote.
  日付で始まる値・演算子・``'`` で始まる値は WORD 以外の token として読まれるので quote する.
- ``value_kind="date"``: bare.
- ``value_kind="wildcard"``: bare. ``"..."`` で包むと ``*`` / ``?`` が literal 化する.
- ``value_kind="phrase"``: 常に ``"..."`` で包み、内部 ``\\`` → ``\\\\`` / ``"`` → ``\\"`` を
  エスケープ (parser の ``_PHRASE_UNESCAPE`` と対称).  空白か ``)`` が後ろに続く ``'`` も
  ``\\'`` にする (quote の外では単一引用符の phrase を閉じる文字になるため).
- ``value_kind="range"``: ``[from TO to]``.
- ``FreeText.value``: ``is_bare_safe_multiword`` (空白区切りの各語が keyword の語として
  読み戻せる) なら bare、記号・連続空白・演算子の語・語をまたぐ単一引用符を含めば quote.
  日付の形の語は keyword の位置では DATE にならないので bare のまま.
- ``BoolOp(NOT, [child])``: 子が ``BoolOp`` (AND/OR/NOT) なら必ず括弧.  grammar の
  ``not_op: NOT atom`` 制約 (連鎖 ``NOT NOT x`` を許容しない) のため.
- ``BoolOp(AND|OR, ...)``: precedence (AND=3, OR=2) を子のものと比較し、子 < 親なら括弧.
"""

from __future__ import annotations

import re

from ddbj_search_api.search.dsl.ast import (
    BoolOp,
    FieldClause,
    FreeText,
    Node,
    Range,
    ValueKind,
)
from ddbj_search_api.search.dsl.lex_patterns import is_bare_safe_multiword, is_bare_word

_AND_PRECEDENCE = 3
_OR_PRECEDENCE = 2
# ``parent_prec`` 初期値 (top-level / 括弧直下).  どの BoolOp も top では括弧不要にしたいので 0.
_TOP_PRECEDENCE = 0


def ast_to_dsl(ast: Node) -> str:
    """Serialize an AST node into a DSL string.

    Output is normalized: redundant parens are omitted, and ``BoolOp`` children
    are emitted with the same operator chained (``a AND b AND c``).  Feeding
    the result back into ``parse()`` yields a structurally equivalent AST
    (modulo ``Position``).
    """
    return _node_to_dsl(ast, _TOP_PRECEDENCE)


def _node_to_dsl(node: Node, parent_prec: int) -> str:
    if isinstance(node, FreeText):
        return _serialize_free_text(node.value, node.is_phrase)
    if isinstance(node, FieldClause):
        return f"{node.field}:{_serialize_field_value(node.value_kind, node.value)}"
    return _serialize_bool_op(node, parent_prec)


def _serialize_bool_op(node: BoolOp, parent_prec: int) -> str:
    if node.op == "NOT":
        if len(node.children) != 1:
            raise ValueError(f"NOT must have exactly one child, got {len(node.children)}")
        child = node.children[0]
        if isinstance(child, BoolOp):
            inner = _node_to_dsl(child, _TOP_PRECEDENCE)
            return f"NOT ({inner})"
        return f"NOT {_node_to_dsl(child, _TOP_PRECEDENCE)}"

    own_prec = _AND_PRECEDENCE if node.op == "AND" else _OR_PRECEDENCE
    rendered = f" {node.op} ".join(_node_to_dsl(child, own_prec) for child in node.children)
    if own_prec < parent_prec:
        return f"({rendered})"
    return rendered


def _serialize_field_value(value_kind: ValueKind, value: str | Range) -> str:
    if value_kind == "range":
        if not isinstance(value, Range):
            raise TypeError(f"range value_kind requires Range, got {type(value).__name__}")
        return f"[{value.from_} TO {value.to}]"
    if not isinstance(value, str):
        raise TypeError(f"non-range value_kind requires str, got {type(value).__name__}")
    if value_kind == "phrase" or (value_kind == "word" and not is_bare_word(value)):
        # word でも、WORD 以外の token (DATE / 演算子 / 単一引用符の phrase / WILDCARD) として
        # 読まれる値は quote しないと parser がドリフトする.
        return _quote_phrase(value)
    # ``date`` / ``wildcard`` は grammar token の文字集合に従う bare 出力.
    # validator が事前に通っている前提で、re-quote は行わない.
    return value


def _serialize_free_text(value: str, is_phrase: bool) -> str:
    # is_phrase=True: ユーザーが DSL で ``"..."`` / ``'...'`` で囲んだ事実を尊重し、
    # single word (e.g. ``"cancer"``) でも quote を保持する. 再 parse 時に
    # ``is_phrase=True`` の FreeText に復元され、phrase match (順序保持) を継続できる.
    if is_phrase:
        return _quote_phrase(value)
    # 空白区切りの各語が keyword の語として bare 出力可能なら bare のまま出す. parser の
    # ``free_text_atom: WORD+`` がこれを 1 つの FreeText (is_phrase=False) に復元するため
    # round-trip が安定する. 記号・連続空白・空文字や、演算子の語、語をまたいで単一引用符の
    # phrase ができる値は quote 必須 (bare だと re-parse で別 token に分解 / drift する).
    if is_bare_safe_multiword(value):
        return value
    return _quote_phrase(value)


# quote の外では、空白か ``)`` が後ろに続く ``'`` は単一引用符の phrase を閉じる.  phrase の
# 中でもこの ``'`` を escape しておくと、前にある ``'`` で始まる語がこの ``'`` まで phrase に
# ならない.
_CLOSING_SINGLE_QUOTE = re.compile(r"'(?=[\s)])")


def _quote_phrase(value: str) -> str:
    # parser の ``_PHRASE_UNESCAPE`` (\\X → X) と対称: \\ → \\\\、" → \\"、閉じる ' → \\'.
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + _CLOSING_SINGLE_QUOTE.sub("\\\\'", escaped) + '"'
