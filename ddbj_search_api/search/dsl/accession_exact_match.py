"""AST レベルの accession 完全一致判定。

`/db-portal/*` の handler で組み立てた AST を走査し、accession ID 完全一致を
表すノードが含まれていれば、その accession 文字列を返す。返り値が ``None`` 以外
のとき、対象 DB の ``suppressed`` ステータスを ``include_suppressed`` で許可する。

判定対象 (詳細は ``docs/db-portal-api-spec.md § データ可視性 (status 制御)``):

- AST のトップが ``FreeText(v)`` で ``is_accession_like(v.strip())`` を満たす
- AST のトップが ``FieldClause(identifier, eq, v)`` で ``is_accession_like(v)`` を満たす
- AST のトップが ``BoolOp(AND, children=...)`` で、**直下** の子のいずれかが上記 2 条件の
  どちらかを満たす (``q`` + ``adv`` 併用時の合成 BoolOp に相当)

それ以外 (``BoolOp(OR, ...)``、``BoolOp(NOT, ...)``、ネスト AND の更に下、
ワイルドカード、``identifier`` 以外のフィールド) はすべて ``None`` を返す。

simple 経路 (``q``) 用の文字列ベース判定 (`search/accession.py` の
``detect_accession_exact_match``) は ``/entries/*`` 系で残置されており、両者で
``is_accession_like`` を共有する。
"""

from __future__ import annotations

from ddbj_search_api.search.accession import is_accession_like
from ddbj_search_api.search.dsl.allowlist import FIELD_TYPES, OPERATOR_BY_KIND
from ddbj_search_api.search.dsl.ast import BoolOp, FieldClause, FreeText, Node


def detect_accession_exact_match_in_ast(ast: Node) -> str | None:
    """AST が accession ID 完全一致を表すとき、その値を返す。

    判定ルールは module docstring 参照。AND 直下の子のみ走査し、ネスト AND
    の更に下や OR / NOT 配下は無視する (誤検出回避: ``a AND (b OR identifier:PRJDB12345)``
    のようなクエリで PRJDB12345 が「ヒット 1 件のうちの 1 つの選択肢」に過ぎない
    ケースまで suppressed を解禁したくない)。
    """
    direct = _match_at(ast)
    if direct is not None:
        return direct
    if isinstance(ast, BoolOp) and ast.op == "AND":
        for child in ast.children:
            matched = _match_at(child)
            if matched is not None:
                return matched
    return None


def _normalize_free_text_for_accession(value: str) -> str | None:
    """``FreeText.value`` から accession 判定用の単一トークンを取り出す.

    文字列版 ``detect_accession_exact_match`` と同じルール:
    - カンマ含み (multi-token) は ``None``
    - 前後空白を strip
    - 外側 quote ペア (``"..."`` / ``'...'``) が一致すれば strip
    """
    if "," in value:
        return None
    token = value.strip()
    if not token:
        return None
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ('"', "'"):
        token = token[1:-1].strip()
    return token or None


def _match_at(node: Node) -> str | None:
    """単一ノードが accession 完全一致を表すかを判定する (走査はしない)。"""
    if isinstance(node, FreeText):
        normalized = _normalize_free_text_for_accession(node.value)
        return normalized if normalized is not None and is_accession_like(normalized) else None
    if isinstance(node, FieldClause):
        if node.field != "identifier":
            return None
        field_type = FIELD_TYPES.get(node.field)
        if field_type is None:
            return None
        operator = OPERATOR_BY_KIND.get((field_type, node.value_kind))
        if operator != "eq":
            return None
        if not isinstance(node.value, str):
            return None
        if not is_accession_like(node.value):
            return None
        return node.value
    return None
