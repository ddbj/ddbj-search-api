"""AST 変換ユーティリティ。

facet 集計の self-exclusion (``docs/db-portal-api-spec.md § 集計母集団と
self-exclusion``) で、母集団から「ある facet 自身に対応する DSL フィルタだけ」を
外すために AST を組み替える。元の AST (parser/validator 出力) は frozen dataclass
なので、変更は ``dataclasses.replace`` で新規ノードを生成して行う (in-place 変更は
しない)。

2 つの経路を提供する:

- ES (filter aggregation): :func:`exclude_field_from_ast` で対象 field の clause を
  出現位置に関わらず全除外した AST を作り、compile して filter に被せる。
- Solr (``{!tag}`` / ``{!ex}``): :func:`split_top_level_field` でトップレベル AND
  直下 (または root 単独) の対象 field clause だけを分離し、残りを ``q``、分離分を
  tagged ``fq`` に回す。OR / NOT 配下やネスト深部の clause は分離せず残す。
"""

from __future__ import annotations

from dataclasses import replace

from ddbj_search_api.search.dsl.ast import BoolOp, FieldClause, FreeText, Node


def exclude_field_from_ast(ast: Node | None, dsl_field: str) -> Node | None:
    """``dsl_field`` に対応する ``FieldClause`` を AST 全体から除外した AST を返す。

    AND / OR / NOT / ネストのどこに現れても対象 field の leaf を取り除く。除外で子が
    1 つも残らない BoolOp はそのノードごと消える。残りが 1 つだけになった AND / OR は
    その唯一の子に畳む (NOT は単項のままにする)。すべて除外されて空になった場合は
    ``None`` を返す (呼び出し側で ES ``match_all`` 相当に扱う)。

    ``FreeText`` は field 概念を持たないため常にそのまま残す。
    """
    if ast is None:
        return None
    if isinstance(ast, FreeText):
        return ast
    if isinstance(ast, FieldClause):
        return None if ast.field == dsl_field else ast

    kept: list[Node] = []
    for child in ast.children:
        pruned = exclude_field_from_ast(child, dsl_field)
        if pruned is not None:
            kept.append(pruned)
    if not kept:
        return None
    if ast.op in ("AND", "OR") and len(kept) == 1:
        return kept[0]

    return replace(ast, children=tuple(kept))


def split_top_level_field(
    ast: Node | None,
    dsl_fields: set[str],
) -> tuple[Node | None, dict[str, list[FieldClause]]]:
    """トップレベル AND 直下の対象 field clause を分離する (Solr self-exclusion 用)。

    AST root が単独 ``FieldClause`` か ``BoolOp(AND, ...)`` のときだけ、直下にある
    ``dsl_fields`` 該当の ``FieldClause`` を取り出す。それ以外 (``FreeText`` 単独、
    root が OR / NOT) や、OR / NOT 配下・ネスト AND の更に下にある clause は分離せず
    残す (Solr の ``{!tag}`` / ``{!ex}`` はトップレベル AND の項にしか安全に適用
    できないため)。

    Returns:
        ``(remaining_ast, extracted)``。``remaining_ast`` は分離後に ``q`` へ回す
        AST (空になれば ``None`` → Solr ``*:*`` 相当)。``extracted`` は ``{dsl_field:
        [分離した FieldClause, ...]}`` で、tagged ``fq`` の生成に使う (該当が無ければ
        空 dict)。
    """
    extracted: dict[str, list[FieldClause]] = {}
    if ast is None:
        return None, extracted
    if isinstance(ast, FieldClause):
        if ast.field in dsl_fields:
            extracted.setdefault(ast.field, []).append(ast)

            return None, extracted

        return ast, extracted
    if isinstance(ast, BoolOp) and ast.op == "AND":
        remaining: list[Node] = []
        for child in ast.children:
            if isinstance(child, FieldClause) and child.field in dsl_fields:
                extracted.setdefault(child.field, []).append(child)
            else:
                remaining.append(child)
        if not remaining:
            return None, extracted
        if len(remaining) == 1:
            return remaining[0], extracted

        return replace(ast, children=tuple(remaining)), extracted

    return ast, extracted
