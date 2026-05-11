"""AST 走査ヘルパー.

handler が AST 全体を見て backend へのパラメータ (Solr ``uf`` 等) を切替えるときに
使うユーティリティ群。判定の単一実装を提供し、handler 側で同種の走査を二重実装させない。
"""

from __future__ import annotations

from ddbj_search_api.search.dsl.ast import FieldClause, FreeText, Node


def ast_has_field_clause(node: Node) -> bool:
    """AST に ``FieldClause`` (``field:value`` leaf) が 1 つでも含まれるかを判定する.

    Solr edismax の ``uf`` allowlist は ``FieldClause`` を含むクエリでのみ
    enable する (FreeText 単独なら uf 不要、FieldClause が含まれるなら uf 必要)。
    """
    if isinstance(node, FieldClause):
        return True
    if isinstance(node, FreeText):
        return False
    return any(ast_has_field_clause(c) for c in node.children)
