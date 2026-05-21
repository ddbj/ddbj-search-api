"""AST → DSL string (inverse of parser.py).

``POST /db-portal/serialize`` のレスポンスとして DSL 文字列を生成する.

SSOT: grammar.lark.  WORD / PHRASE / WILDCARD / RANGE の lexer 定義と、
``or_expr / and_expr / not_op`` の precedence 規則を直接反映する.

経路::

    JSON tree ─json_to_ast─> AST ─validate─> AST ─ast_to_dsl─> DSL string

quote / paren 判定:

- ``value_kind="word"|"date"``: WORD regex の subset なので bare 出力 (validator 通過前提).
- ``value_kind="wildcard"``: bare. ``"..."`` で包むと ``*`` / ``?`` が literal 化する.
- ``value_kind="phrase"``: 常に ``"..."`` で包み、内部 ``\\`` → ``\\\\`` / ``"`` → ``\\"`` を
  エスケープ (parser の ``_PHRASE_UNESCAPE`` と対称).
- ``value_kind="range"``: ``[from TO to]``.
- ``FreeText.value``: WORD regex に full-match すれば bare、空白・特殊文字を含めば quote.
- ``BoolOp(NOT, [child])``: 子が ``BoolOp`` (AND/OR/NOT) なら必ず括弧.  grammar の
  ``not_op: NOT atom`` 制約 (連鎖 ``NOT NOT x`` を許容しない) のため.
- ``BoolOp(AND|OR, ...)``: precedence (AND=3, OR=2) を子のものと比較し、子 < 親なら括弧.
"""

from __future__ import annotations

from ddbj_search_api.search.dsl.ast import (
    BoolOp,
    FieldClause,
    FreeText,
    Node,
    Range,
    ValueKind,
)
from ddbj_search_api.search.dsl.lex_patterns import WORD_RE, needs_quote_for_token_collision

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
        return _serialize_free_text(node.value)
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
    if value_kind == "phrase":
        return _quote_phrase(value)
    if value_kind == "word" and needs_quote_for_token_collision(value):
        # word は本来 bare 可だが、grammar の DATE / operator literal token priority が
        # WORD より高いため、形だけ match する値は quote しないと parser がドリフトする.
        return _quote_phrase(value)
    # ``word`` / ``date`` / ``wildcard`` はいずれも grammar token の文字集合に従う bare 出力.
    # validator が事前に通っている前提で、re-quote は行わない.
    return value


def _serialize_free_text(value: str) -> str:
    # WORD regex に full-match しない値 (空白・記号入り・空文字) は quote 必須.
    # quote しないと parser が複数 token に分解して duplicate-freetext を引き起こす.
    # 加えて DATE / operator literal token と衝突する値も quote する.
    if value and WORD_RE.match(value) and not needs_quote_for_token_collision(value):
        return value
    return _quote_phrase(value)


def _quote_phrase(value: str) -> str:
    # parser の ``_PHRASE_UNESCAPE`` (\\X → X) と対称: \\ → \\\\、" → \\".
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
