"""DSL AST ノード型 (Stage 1 output).

parser.py が生成し、validator.py / compiler_es.py / compiler_solr.py / serde.py が消費する。
operator は AST には持たず、(field_type, value_kind) から compiler/serde 段で導出する。
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
class FreeText:
    """フィールド指定なしの全文検索ノード.

    Lark grammar の bare word / quoted phrase から生成される。compiler が
    backend に応じた全文検索クエリ (ES multi_match / Solr edismax quoted token 列)
    に変換する。

    ``is_phrase`` は元の DSL で値が ``"..."`` / ``'...'`` でクオートされていたかを表す。
    True なら compiler は順序保持の phrase match (ES の ``multi_match.type=phrase``) を
    出力する必要があり、bare word (False) の場合は ``operator=and`` の AND match
    (auto-phrase trigger 文字含みは内部で phrase 化) として展開される。
    """

    value: str
    is_phrase: bool = False
    position: Position | None = None


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


Node: TypeAlias = FreeText | FieldClause | BoolOp
