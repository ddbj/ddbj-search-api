"""DSL AST レベルの accession 完全一致判定。

`/db-portal/*` の adv 経路で、AST が単一 ``identifier`` field の eq クエリで
value がアクセッション ID と完全一致する場合のみ status filter を解放する
ための判定ユーティリティ。判定ルールは ``docs/db-portal-api-spec.md § データ可視性
(status 制御)`` 節を参照。

simple 経路 (``q``) 用の文字列ベース判定は ``search/accession.py`` 側に
``detect_accession_exact_match`` として実装されており、両者で
``is_accession_like`` を共有する。
"""

from __future__ import annotations

from ddbj_search_api.search.accession import is_accession_like
from ddbj_search_api.search.dsl.allowlist import FIELD_TYPES, OPERATOR_BY_KIND
from ddbj_search_api.search.dsl.ast import FieldClause, Node


def detect_accession_exact_match_in_ast(ast: Node) -> str | None:
    """``ast`` が単一 ``identifier`` field の eq でアクセッション ID 完全一致を表すとき、その値を返す。

    判定ルール:

    - AST のトップが ``FieldClause`` (AND / OR / NOT でラップされていない)
    - ``field == "identifier"``
    - ``(field_type, value_kind)`` から導出される operator が ``"eq"``
      (= ``value_kind`` が ``"word"`` または ``"phrase"``、``"wildcard"`` は対象外)
    - ``value`` が ``str`` (= ``Range`` ではない)
    - ``value`` が ``is_accession_like`` (ddbj-search-converter の
      ``ID_PATTERN_MAP`` 完全一致、ワイルドカード非含有) を満たす

    上記いずれかを満たさないとき ``None`` を返す。
    """
    if not isinstance(ast, FieldClause):
        return None
    if ast.field != "identifier":
        return None
    field_type = FIELD_TYPES.get(ast.field)
    if field_type is None:
        return None
    operator = OPERATOR_BY_KIND.get((field_type, ast.value_kind))
    if operator != "eq":
        return None
    if not isinstance(ast.value, str):
        return None
    if not is_accession_like(ast.value):
        return None
    return ast.value
