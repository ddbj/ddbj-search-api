"""Query parser (Stage 1: query text → AST).

Lark LALR(1) で ``grammar.lark`` を適用し、クエリ文字列を AST (``ast.Node``) に変換する。
Lark の ``propagate_positions=True`` + ``@v_args(meta=True)`` で ``Position`` を全ノードに付与。
Lark 例外は ``DslError(unexpected_token)`` に統一変換する。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from lark import Lark, Token, Transformer, v_args
from lark.exceptions import (
    LarkError,
    UnexpectedCharacters,
    UnexpectedEOF,
    UnexpectedInput,
    UnexpectedToken,
)

from ddbj_search_api.search.dsl.ast import BoolOp, FieldClause, FreeText, Node, Position, Range
from ddbj_search_api.search.dsl.errors import DslError, ErrorType

DEFAULT_MAX_LENGTH = 4096

_GRAMMAR_PATH = Path(__file__).with_name("grammar.lark")

_LARK: Lark = Lark(
    _GRAMMAR_PATH.read_text(encoding="utf-8"),
    parser="lalr",
    lexer="contextual",
    propagate_positions=True,
    maybe_placeholders=False,
)

_RANGE_SPLIT = re.compile(r"\s+TO\s+")
_PHRASE_UNESCAPE = re.compile(r"\\(.)", flags=re.DOTALL)


@v_args(meta=True, inline=True)
class _AstTransformer(Transformer):  # type: ignore[type-arg]
    def start(self, meta: Any, expr: Node) -> Node:
        return expr

    def or_expr(self, meta: Any, *items: Any) -> Node:
        operands = [item for item in items if not isinstance(item, Token)]
        if len(operands) == 1:
            return operands[0]  # type: ignore[no-any-return]
        return BoolOp(op="OR", children=tuple(operands), position=_position(meta))

    def and_expr(self, meta: Any, *items: Any) -> Node:
        operands = [item for item in items if not isinstance(item, Token)]
        if len(operands) == 1:
            return operands[0]  # type: ignore[no-any-return]
        return BoolOp(op="AND", children=tuple(operands), position=_position(meta))

    def not_op(self, meta: Any, _not_tok: Token, inner: Node) -> BoolOp:
        return BoolOp(op="NOT", children=(inner,), position=_position(meta))

    def atom_passthrough(self, meta: Any, inner: Node) -> Node:
        return inner

    def field_clause(
        self,
        meta: Any,
        field_tok: Token,
        value_payload: tuple[str, str | Range],
    ) -> FieldClause:
        kind, value = value_payload
        return FieldClause(
            field=str(field_tok),
            value_kind=kind,  # type: ignore[arg-type]
            value=value,
            position=_position(meta),
        )

    def v_phrase(self, meta: Any, tok: Token) -> tuple[str, str]:
        raw = str(tok)
        inner = raw[1:-1]
        unescaped = _PHRASE_UNESCAPE.sub(lambda m: m.group(1), inner)
        return ("phrase", unescaped)

    def v_range(self, meta: Any, tok: Token) -> tuple[str, Range]:
        raw = str(tok)
        inner = raw[1:-1]
        parts = _RANGE_SPLIT.split(inner, maxsplit=1)
        if len(parts) != 2:
            return ("range", Range(from_=inner, to=inner))
        return ("range", Range(from_=parts[0], to=parts[1]))

    def v_wildcard(self, meta: Any, tok: Token) -> tuple[str, str]:
        return ("wildcard", str(tok))

    def v_date(self, meta: Any, tok: Token) -> tuple[str, str]:
        return ("date", str(tok))

    def v_word(self, meta: Any, tok: Token) -> tuple[str, str]:
        return ("word", str(tok))

    def ft_phrase(self, meta: Any, tok: Token) -> FreeText:
        raw = str(tok)
        unescaped = _PHRASE_UNESCAPE.sub(lambda m: m.group(1), raw[1:-1])
        return FreeText(value=unescaped, is_phrase=True, position=_position(meta))

    def ft_word(self, meta: Any, tok: Token) -> FreeText:
        return FreeText(value=str(tok), is_phrase=False, position=_position(meta))


def _position(meta: Any) -> Position:
    column = getattr(meta, "column", None) or 1
    end_column = getattr(meta, "end_column", None) or (column + 1)
    length = max(end_column - column, 1)
    return Position(column=column, length=length)


def parse(dsl: str, *, max_length: int = DEFAULT_MAX_LENGTH) -> Node:
    """Parse a query string into an AST.

    Raises:
        DslError(unexpected_token): on syntax errors, over-length input, or empty input.
    """
    if len(dsl) > max_length:
        raise DslError(
            type=ErrorType.unexpected_token,
            detail=(
                f"query string too long: {len(dsl)} characters (max {max_length}). "
                "Shorten the query or split into multiple requests."
            ),
            column=max_length + 1,
            length=1,
        )
    if not dsl.strip():
        raise DslError(
            type=ErrorType.unexpected_token,
            detail="empty query string",
            column=1,
            length=1,
        )
    try:
        tree = _LARK.parse(dsl)
    except UnexpectedToken as e:
        col = getattr(e, "column", 1) or 1
        tok_obj = getattr(e, "token", None)
        tok_str = str(tok_obj) if tok_obj is not None else ""
        length = max(len(tok_str), 1)
        raise DslError(
            type=ErrorType.unexpected_token,
            detail=f"unexpected token {tok_str!r} at column {col}",
            column=col,
            length=length,
        ) from e
    except UnexpectedCharacters as e:
        col = getattr(e, "column", 1) or 1
        raise DslError(
            type=ErrorType.unexpected_token,
            detail=f"unexpected character at column {col}",
            column=col,
            length=1,
        ) from e
    except UnexpectedEOF as e:
        col = getattr(e, "column", len(dsl) + 1) or (len(dsl) + 1)
        raise DslError(
            type=ErrorType.unexpected_token,
            detail=f"unexpected end of query at column {col}",
            column=col,
            length=1,
        ) from e
    except UnexpectedInput as e:
        col = getattr(e, "column", 1) or 1
        raise DslError(
            type=ErrorType.unexpected_token,
            detail=f"query parse error at column {col}",
            column=col,
            length=1,
        ) from e
    except LarkError as e:
        raise DslError(
            type=ErrorType.unexpected_token,
            detail=f"query parse error: {e}",
            column=1,
            length=1,
        ) from e

    transformer = _AstTransformer()
    ast: Any = transformer.transform(tree)
    if not isinstance(ast, (FreeText, FieldClause, BoolOp)):
        raise DslError(
            type=ErrorType.unexpected_token,
            detail="query did not produce a valid AST",
            column=1,
            length=1,
        )
    return ast
