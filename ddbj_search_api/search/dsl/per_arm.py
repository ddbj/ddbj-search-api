"""cross / single 検索の per-DB AST 簡約。

cross-search の fan-out (db_portal._cross_search_dispatch) と single 検索の dispatch は、
ある DB が query の field を意味的に持たない / 値が固定のとき、その arm を Solr/ES に
投げる前に AST を簡約する。これにより:

- 非対応 field (例: date_published@taxonomy) を含む arm は対象外 (applicable=False)。
  Solr/ES を叩かず count=null を返す。(-*:*) を edismax に投げると TXSearch で ~全件化
  する問題 (compiler_solr の no-match 撤廃の理由) を根本回避する。
- 固定値 field (例: Solr backed の accessibility="public-access") は値と突き合わせ、
  恒真なら節を除去、恒偽なら arm 全体を 0 件 (always_zero) にする。

availability の SSOT は :func:`ddbj_search_api.search.dsl.allowlist.field_availability`。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from ddbj_search_api.search.dsl.allowlist import field_availability
from ddbj_search_api.search.dsl.ast import BoolOp, FieldClause, FreeText, Node


@dataclass(frozen=True, slots=True)
class ArmReduction:
    """ある DB 向けに簡約した結果。

    - ``applicable=False``: その DB は query の field を持たず対象外。``ast`` は使わない。
      対象外の原因となった DSL field 名を出現順 (重複除去済み) で ``unavailable_fields``
      に持つ。
    - ``applicable=True`` かつ ``always_zero=True``: 固定値の不一致等で必ず 0 件。
      Solr/ES を叩かず count=0 を返す。``ast`` は使わない。
    - ``applicable=True`` かつ ``always_zero=False``: ``ast`` (簡約後、``None`` は全件) を
      compile して検索する。

    ``unavailable_fields`` は ``applicable=False`` のときのみ非空。
    """

    applicable: bool
    ast: Node | None
    always_zero: bool
    unavailable_fields: tuple[str, ...] = ()


# 簡約途中の 1 ノードの状態。
# - "keep": 検索条件として残す (``node`` を持つ)
# - "true": 恒真 (固定値一致)。親 AND からは除去、OR では全体恒真
# - "false": 恒偽 (固定値不一致)。親 OR からは除去、AND では全体恒偽
# - "na": 非対応 field を含む。伝播すると arm 対象外
_Kind = Literal["keep", "true", "false", "na"]


@dataclass(frozen=True, slots=True)
class _Reduced:
    kind: _Kind
    node: Node | None = None
    # kind=="na" のとき、対象外の原因 field 名を出現順 (重複除去済み) で持つ。他の kind では空。
    na_fields: tuple[str, ...] = ()


def reduce_ast_for_db(ast: Node | None, db: str) -> ArmReduction:
    """``ast`` を ``db`` 向けに簡約する。``ast=None`` (q 未指定) は全件扱い。"""
    if ast is None:
        return ArmReduction(applicable=True, ast=None, always_zero=False)
    reduced = _reduce(ast, db)
    if reduced.kind == "na":
        return ArmReduction(
            applicable=False,
            ast=None,
            always_zero=False,
            unavailable_fields=reduced.na_fields,
        )
    if reduced.kind == "true":
        return ArmReduction(applicable=True, ast=None, always_zero=False)
    if reduced.kind == "false":
        return ArmReduction(applicable=True, ast=None, always_zero=True)

    return ArmReduction(applicable=True, ast=reduced.node, always_zero=False)


def _reduce(node: Node, db: str) -> _Reduced:
    if isinstance(node, FreeText):
        # free text は field を持たず全 backend で検索可能。
        return _Reduced(kind="keep", node=node)
    if isinstance(node, FieldClause):
        return _reduce_leaf(node, db)

    return _reduce_boolop(node, db)


def _reduce_leaf(clause: FieldClause, db: str) -> _Reduced:
    avail = field_availability(clause.field, db)
    if avail.available:
        return _Reduced(kind="keep", node=clause)
    if avail.fixed_value is not None:
        # 固定値 field (enum)。clause.value は word / phrase の文字列 (validator が
        # この型に wildcard / range を許さない)。固定値と一致すれば恒真、否なら恒偽。
        if isinstance(clause.value, str) and clause.value == avail.fixed_value:
            return _Reduced(kind="true")

        return _Reduced(kind="false")

    return _Reduced(kind="na", na_fields=(clause.field,))


def _reduce_boolop(node: BoolOp, db: str) -> _Reduced:
    reduced_children = [_reduce(child, db) for child in node.children]
    if node.op == "NOT":
        return _reduce_not(node, reduced_children[0])
    if node.op == "AND":
        return _reduce_and(node, reduced_children)

    return _reduce_or(node, reduced_children)


def _reduce_not(node: BoolOp, child: _Reduced) -> _Reduced:
    if child.kind == "na":
        return _Reduced(kind="na", na_fields=child.na_fields)
    if child.kind == "true":
        return _Reduced(kind="false")
    if child.kind == "false":
        return _Reduced(kind="true")
    # child.kind == "keep": その node は必ず非 None (keep は常に node を伴う)。
    assert child.node is not None

    return _Reduced(kind="keep", node=replace(node, children=(child.node,)))


def _reduce_and(node: BoolOp, children: list[_Reduced]) -> _Reduced:
    # na は嘘件数を避けるため arm 全体を対象外に伝播する (恒偽より優先)。
    na_children = [child for child in children if child.kind == "na"]
    if na_children:
        return _Reduced(kind="na", na_fields=_merge_na_fields(na_children))
    if any(child.kind == "false" for child in children):
        return _Reduced(kind="false")
    kept = [child.node for child in children if child.kind == "keep"]

    return _combine(node, kept, identity="true")


def _reduce_or(node: BoolOp, children: list[_Reduced]) -> _Reduced:
    na_children = [child for child in children if child.kind == "na"]
    if na_children:
        return _Reduced(kind="na", na_fields=_merge_na_fields(na_children))
    if any(child.kind == "true" for child in children):
        return _Reduced(kind="true")
    kept = [child.node for child in children if child.kind == "keep"]

    return _combine(node, kept, identity="false")


def _merge_na_fields(na_children: list[_Reduced]) -> tuple[str, ...]:
    """na の子の na_fields を AST 出現順を保ったまま結合し、重複を除く。"""
    return tuple(dict.fromkeys(field for child in na_children for field in child.na_fields))


def _combine(node: BoolOp, kept: list[Node | None], *, identity: _Kind) -> _Reduced:
    """残った keep 子で AND / OR を再構成する。空なら単位元 (AND→true / OR→false)。"""
    real = [child for child in kept if child is not None]
    if not real:
        return _Reduced(kind=identity)
    if len(real) == 1:
        return _Reduced(kind="keep", node=real[0])

    return _Reduced(kind="keep", node=replace(node, children=tuple(real)))
