"""AP3 DSL AST ノード型 (Stage 1 output).

parser.py が生成し、validator.py / compiler_es.py / compiler_solr.py / serde.py が消費する。
operator は AST には持たず、(field_type, value_kind) から compiler/serde 段で導出する
(AP3 設計 plan 参照)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

ValueKind: TypeAlias = Literal["phrase", "word", "wildcard", "date", "range"]
BoolOpKind: TypeAlias = Literal["AND", "OR", "NOT"]


@dataclass(frozen=True, slots=True)
class Position:
    """DSL 文字列中の位置 (1-based column, inclusive length)."""

    column: int
    length: int


@dataclass(frozen=True, slots=True)
class Range:
    """`[from TO to]` 形式の範囲値."""

    from_: str
    to: str


@dataclass(frozen=True, slots=True)
class FieldClause:
    """`field:value` 形式の leaf ノード."""

    field: str
    value_kind: ValueKind
    value: str | Range
    position: Position


@dataclass(frozen=True, slots=True)
class BoolOp:
    """AND / OR / NOT ノード."""

    op: BoolOpKind
    children: tuple[Node, ...]
    position: Position


Node: TypeAlias = FieldClause | BoolOp
