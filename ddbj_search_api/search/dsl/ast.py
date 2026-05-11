"""DSL AST ノード型 (Stage 1 output).

parser.py が生成し、validator.py / compiler_es.py / compiler_solr.py / serde.py が消費する。
operator は AST には持たず、(field_type, value_kind) から compiler/serde 段で導出する。

FreeText ノードは Lark パーサからは生成されず、handler が ``q`` を直接ラップして作る
(``docs/db-portal-api-spec.md § 内部モデル``)。
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
    """シンプル検索 ``q`` 由来の全文検索ノード.

    フィールド指定なし。compiler が backend に応じた全文検索クエリ
    (ES multi_match / Solr edismax quoted token 列) に変換する。
    Lark パーサからは生成されず、handler が ``q`` 文字列を直接ラップして作る。
    Position を持たない (DSL 文字列ではないので column 概念がない)。
    """

    value: str


@dataclass(frozen=True, slots=True)
class FieldClause:
    """`field:value` 形式の leaf ノード."""

    field: str
    value_kind: ValueKind
    value: str | Range
    position: Position


@dataclass(frozen=True, slots=True)
class BoolOp:
    """AND / OR / NOT ノード.

    children は FieldClause / BoolOp に加え、handler が組み立てる合成
    ``BoolOp(AND, [adv_ast, FreeText(q)])`` 経由で FreeText も保持しうる。
    position は Lark 由来 (adv 経路) では DSL 中の位置、合成 BoolOp では adv_ast の
    position を継承する (validator は parse 直後の adv_ast にのみ適用され、
    合成 BoolOp 経路で参照されることはない)。
    """

    op: BoolOpKind
    children: tuple[Node, ...]
    position: Position


Node: TypeAlias = FreeText | FieldClause | BoolOp
