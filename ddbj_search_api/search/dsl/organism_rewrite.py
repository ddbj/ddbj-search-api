"""trad (ARSA) 向けの organism_id → organism_name rewrite (Stage 3a)。

ARSA (Solr 4.4.0) には NCBI TaxID で引ける queryable field が無い (compiler_solr の
``_ARSA_FIELD_MAP`` に ``organism_id`` は無く、``organism_name`` だけが ``("Organism",
"Lineage")`` の OR phrase にマップされている)。そこで cross / single の trad arm は、
per-arm 簡約後の AST 中の ``organism_id`` を TXSearch で学名 (scientific_name) に解決し、
ここで ``organism_name`` の ``FieldClause`` に置換してから ARSA に compile する。

TXSearch への I/O (resolver) は呼び出し側 (``routers.db_portal``) が持ち、この module は
解決済みの ``{TaxID: 学名}`` を受け取って純粋に AST を組み替える (sync)。AST は frozen な
dataclass なので :func:`dataclasses.replace` で新規ノードを生成する (``transform`` と同流儀)。

bool 畳み込みは :func:`ddbj_search_api.search.dsl.per_arm.reduce_ast_for_db` と同型。ただし
``organism_id`` は trad で available になっている (allowlist) ため per-arm 段で ``na``
(非対応) には畳まれず、ここに届く AST には ``na`` 概念が無い。よって leaf は keep / true /
false の 3 値だけを扱う:

- 学名に解決できた exact TaxID → ``organism_name`` phrase に置換 (keep)
- 解決できない exact TaxID / wildcard TaxID (``organism_id:96*``、学名展開不能) → false
- ``organism_id`` 以外の ``FieldClause`` / ``FreeText`` → keep (不変)

学名 phrase は ``Organism`` OR ``Lineage`` にマップされるため、上位分類の TaxID では Lineage
経由で子孫レコードもヒットする (ES arm の ``organism.identifier`` exact より広い)。この非対称は
docs/db-portal-api-spec.md の方針どおり許容する。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from ddbj_search_api.search.dsl.ast import BoolOp, FieldClause, FreeText, Node


@dataclass(frozen=True, slots=True)
class OrganismRewrite:
    """trad arm の organism_id を解決・rewrite した結果。

    - ``always_zero=True``: 全 organism_id が解決失敗 / wildcard 等で arm 全体が恒偽。
      ARSA を叩かず count=0 / 空 hits を返す。``ast`` は使わない。
    - ``always_zero=False``: ``ast`` (rewrite 後、``None`` は全件) を ARSA に compile する。
    """

    ast: Node | None
    always_zero: bool


def collect_organism_ids(ast: Node | None) -> tuple[str, ...]:
    """AST 中の exact な ``organism_id`` 値 (TaxID) を出現順・重複除去で返す。

    resolver に渡す TaxID 集合を作る。``value_kind`` が ``word`` / ``phrase`` の値だけを
    集める。``wildcard`` (``organism_id:96*``) は学名に解決できないため集めない (rewrite 側で
    false に畳む)。``organism_id`` を含まない AST は空 tuple を返す (呼び出し側は resolver を
    叩かずに済む)。
    """
    acc: list[str] = []
    _collect(ast, acc)
    return tuple(dict.fromkeys(acc))


def _collect(node: Node | None, acc: list[str]) -> None:
    if node is None or isinstance(node, FreeText):
        return
    if isinstance(node, FieldClause):
        if node.field == "organism_id" and node.value_kind in ("word", "phrase"):
            # identifier 型の word / phrase は str (validator が Range を許さない)。
            assert isinstance(node.value, str)
            acc.append(node.value)
        return
    for child in node.children:
        _collect(child, acc)


def rewrite_organism_ids(ast: Node | None, resolved: dict[str, str]) -> OrganismRewrite:
    """``organism_id`` を ``resolved`` (``{TaxID: 学名}``) で ``organism_name`` に置換する。

    解決できない TaxID / wildcard は false に畳み、AND / OR / NOT を per_arm と同型に簡約する。
    """
    if ast is None:
        return OrganismRewrite(ast=None, always_zero=False)
    reduced = _rewrite(ast, resolved)
    if reduced.kind == "true":
        # 恒真 (例: NOT <解決不能 TaxID>) → 全件。ast=None は compile 側で *:* になる。
        return OrganismRewrite(ast=None, always_zero=False)
    if reduced.kind == "false":
        return OrganismRewrite(ast=None, always_zero=True)

    return OrganismRewrite(ast=reduced.node, always_zero=False)


# 簡約途中の 1 ノードの状態 (per_arm の _Reduced と同型、na 抜き)。
_Kind = Literal["keep", "true", "false"]


@dataclass(frozen=True, slots=True)
class _Reduced:
    kind: _Kind
    node: Node | None = None


def _rewrite(node: Node, resolved: dict[str, str]) -> _Reduced:
    if isinstance(node, FreeText):
        return _Reduced(kind="keep", node=node)
    if isinstance(node, FieldClause):
        return _rewrite_leaf(node, resolved)

    return _rewrite_boolop(node, resolved)


def _rewrite_leaf(clause: FieldClause, resolved: dict[str, str]) -> _Reduced:
    if clause.field != "organism_id":
        return _Reduced(kind="keep", node=clause)
    if clause.value_kind == "wildcard":
        # TaxID の前方一致 (organism_id:96*) は学名に展開できない → 恒偽。
        return _Reduced(kind="false")
    assert isinstance(clause.value, str)
    name = resolved.get(clause.value)
    if name is None:
        # 存在しない / 解決できなかった TaxID は何にもマッチしない (ES の
        # organism.identifier:<unknown> = 0 件 と整合)。
        return _Reduced(kind="false")
    replaced = replace(clause, field="organism_name", value=name, value_kind="phrase")

    return _Reduced(kind="keep", node=replaced)


def _rewrite_boolop(node: BoolOp, resolved: dict[str, str]) -> _Reduced:
    reduced_children = [_rewrite(child, resolved) for child in node.children]
    if node.op == "NOT":
        return _rewrite_not(node, reduced_children[0])
    if node.op == "AND":
        return _rewrite_and(node, reduced_children)

    return _rewrite_or(node, reduced_children)


def _rewrite_not(node: BoolOp, child: _Reduced) -> _Reduced:
    if child.kind == "true":
        return _Reduced(kind="false")
    if child.kind == "false":
        return _Reduced(kind="true")
    # child.kind == "keep": keep は常に node を伴う。
    assert child.node is not None

    return _Reduced(kind="keep", node=replace(node, children=(child.node,)))


def _rewrite_and(node: BoolOp, children: list[_Reduced]) -> _Reduced:
    if any(child.kind == "false" for child in children):
        return _Reduced(kind="false")
    kept = [child.node for child in children if child.kind == "keep"]

    return _combine(node, kept, identity="true")


def _rewrite_or(node: BoolOp, children: list[_Reduced]) -> _Reduced:
    if any(child.kind == "true" for child in children):
        return _Reduced(kind="true")
    kept = [child.node for child in children if child.kind == "keep"]

    return _combine(node, kept, identity="false")


def _combine(node: BoolOp, kept: list[Node | None], *, identity: _Kind) -> _Reduced:
    """残った keep 子で AND / OR を再構成する。空なら単位元 (AND→true / OR→false)。"""
    real = [child for child in kept if child is not None]
    if not real:
        return _Reduced(kind=identity)
    if len(real) == 1:
        return _Reduced(kind="keep", node=real[0])

    return _Reduced(kind="keep", node=replace(node, children=tuple(real)))
