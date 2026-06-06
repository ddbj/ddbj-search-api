"""DSL compiler for Solr edismax q string (Stage 3b).

SSOT: search-backends.md §バックエンド変換.

Dialect:
- ``arsa``: ARSA (Solr 4.4.0)。Tier 1 (``PrimaryAccessionNumber`` / ``Definition`` /
  ``AllText`` / ``Organism`` / ``Lineage`` / ``Date``) + Tier 2 ``publication``
  (``ReferenceTitle``) + Ddbj Tier 3 (``Division`` / ``MolecularType`` /
  ``SequenceLength`` / ``FeatureQualifier`` / ``ReferenceJournal``)。``organism_name``
  は ``Organism`` / ``Lineage`` の OR phrase。``organism_id`` (taxID) は ARSA に field が無いが、
  cross / single の ddbj arm が TXSearch で TaxID→学名解決し ``organism_name`` に rewrite してから
  compile する (``search.dsl.organism_rewrite``、この compiler には organism_id を直接渡さない)。
  ``submitter`` / date 系 / ES-only / Taxonomy 系 Tier 3 は ARSA に field が無く非対応。
- ``txsearch``: TXSearch (Solr 4.4.0)。Tier 1 (``tax_id`` / ``scientific_name`` / ``text``) +
  Taxonomy Tier 3 (``rank`` / ``lineage`` / ``kingdom`` / ... / ``synonym`` / ``blast_name`` /
  ``equivalent_name`` / ``domain`` / ``strain`` / ``isolate``)。TXSearch は Taxonomy DB
  そのものなので ``organism_id`` を ``tax_id`` に、``organism_name`` を ``scientific_name``
  にマップ。日付 + Tier 2 + Ddbj/ES-only Tier 3 は非対応。``japanese_name`` は staging
  TXSearch の schema に不在のため map しない。

非対応 / 固定値 field は cross / single の per-arm 簡約 (per_arm.reduce_ast_for_db) が
compile 前に AST から除く。コンパイラに非対応 field が来たら ``(-*:*)`` で潰さず
``RuntimeError`` を投げる (TXSearch では no-match が edismax qf で ~全件化するため)。
"""

from __future__ import annotations

import re
from typing import Literal, TypeAlias

from ddbj_search_api.search.dsl.ast import FieldClause, FreeText, Node, Range
from ddbj_search_api.search.dsl.validator import resolve_field_operator
from ddbj_search_api.search.phrase import (
    SOLR_AUTO_PHRASE_CHARS,
    escape_solr_phrase,
    parse_keywords_with_autophrase,
)

# Wildcard values flow into the edismax ``q`` string unquoted (Solr does not
# evaluate wildcards inside phrases).  The validator already rejects values
# containing characters outside this set, but we re-assert here so an
# accidentally hand-built AST that bypasses the validator can never produce
# a Solr query with Lucene metacharacters.
_SOLR_SAFE_WILDCARD_RE = re.compile(r"^[A-Za-z0-9_\-.*?]+$")

SolrDialect: TypeAlias = Literal["arsa", "txsearch"]

# シンプル検索 (q) がトークン化後に空となるとき edismax に投げる all-docs クエリ.
# ARSA / TXSearch どちらも同値、handler 側で q=None 正規化される想定だが安全側にここでも対応.
_FREE_TEXT_EMPTY_FALLBACK = "*:*"

# === ARSA (Ddbj) field map ===

_ARSA_FIELD_MAP: dict[str, tuple[str, ...]] = {
    # === Tier 1 ===
    "identifier": ("PrimaryAccessionNumber",),
    "title": ("Definition",),
    "description": ("AllText",),
    # organism_name は学名 (Organism) + 分類体系 (Lineage) の OR phrase。
    # organism_id (taxID exact) は ARSA に対応 field が無い。ddbj arm は organism_rewrite が
    # TaxID→学名解決して organism_name に変換してから compile するため map は追加しない
    # (直接 compile すると _compile_leaf が RuntimeError)。
    "organism_name": ("Organism", "Lineage"),
    "date_published": ("Date",),
    # === Tier 2 ===
    # publication (ES の publication.title) は ARSA の ReferenceTitle (GenBank REFERENCE
    # TITLE) にマップ。submitter は ARSA に organization 情報が無く非対応 (field_availability)。
    "publication": ("ReferenceTitle",),
    # === Tier 3 Ddbj only ===
    "division": ("Division",),
    "molecular_type": ("MolecularType",),
    "sequence_length": ("SequenceLength",),
    "feature_gene_name": ("FeatureQualifier",),
    "reference_journal": ("ReferenceJournal",),
}

# === TXSearch (Taxonomy) field map ===

_TXSEARCH_FIELD_MAP: dict[str, tuple[str, ...]] = {
    # === Tier 1 ===
    "identifier": ("tax_id",),
    "title": ("scientific_name",),
    "description": ("text",),
    # TXSearch は Taxonomy DB なので entry の identifier=tax_id / title=scientific_name と
    # 生物種検索の organism_id / organism_name が同じ field を指す (organism そのものを引く DB).
    "organism_id": ("tax_id",),
    "organism_name": ("scientific_name",),
    # === Tier 3 Taxonomy ===
    "rank": ("rank",),
    "lineage": ("lineage",),
    "kingdom": ("kingdom",),
    "phylum": ("phylum",),
    "class": ("class",),
    "order": ("order",),
    "family": ("family",),
    "genus": ("genus",),
    "species": ("species",),
    "common_name": ("common_name",),
    "synonym": ("synonym",),
    "blast_name": ("blast_name",),
    "equivalent_name": ("equivalent_name",),
    "domain": ("domain",),
    # strain / isolate は biosample と同名の Tier3 (allowlist)。TXSearch では taxonomy の
    # 株 field を引く。_ex 系 (synonym_ex 等) は exact analyzer なので map せず qf 専用。
    "strain": ("strain",),
    "isolate": ("isolate",),
    # japanese_name は staging TXSearch の schema luke で field 不在のため map しない
}

# 前方一致を相乗りさせる末尾語の最小 literal 長。Solr 4.4.0 の prefix query は ES の
# ``max_expansions`` のような既定キャップを持たず、AllText (ARSA ~3 億件) / text (TXSearch)
# のような全文 field 上で 1 文字 prefix は term dictionary の広範囲をスキャンする。既存の
# wildcard ガード (``validator._MIN_WILDCARD_LITERAL_LEN`` = 2) と同基準で 2 文字以上を要求する。
_MIN_PREFIX_LITERAL_LEN = 2


def arsa_uf_fields() -> tuple[str, ...]:
    """All ARSA Solr fields reachable through ``compile_to_solr(dialect="arsa")``.

    edismax's ``uf`` parameter allowlists field names inside ``q``.  A field
    that compile_to_solr may emit but is absent from ``uf`` is silently
    demoted to a bare keyword and then matched against ``qf`` — producing
    wildly wrong counts (staging probe 2026-04-24: ``Division:"BCT"``
    returned 88.8M / all-docs without ``uf``, 753k with it).  Derive the
    allowlist from the field map so query.py cannot drift.
    """
    seen: set[str] = set()
    for mapped in _ARSA_FIELD_MAP.values():
        seen.update(mapped)
    return tuple(sorted(seen))


def txsearch_uf_fields() -> tuple[str, ...]:
    """All TXSearch Solr fields reachable through ``compile_to_solr(dialect="txsearch")``."""
    seen: set[str] = set()
    for mapped in _TXSEARCH_FIELD_MAP.values():
        seen.update(mapped)
    return tuple(sorted(seen))


def _solr_prefixable_words(token: str) -> list[str] | None:
    """前方一致を相乗りさせて安全な ASCII alnum word 列を返す (不可なら ``None``).

    edismax は bare token の Lucene メタ文字を解釈するため、unquoted で末尾 ``*`` を
    付けて安全なのは ``[A-Za-z0-9]`` のみで構成された語 (空白区切りで複数可) に限る。
    記号 (``-`` ``.`` ``:`` 等) を含む語や非 ASCII 語は ``None`` を返し、呼び出し側で
    quoted 完全一致に倒す (``HIF-1`` 等の auto-phrase 対象と挙動を揃える)。
    """
    words = token.split()
    if words and all(w.isascii() and w.isalnum() for w in words):
        return words
    return None


def _solr_token_expr(token: str, token_is_phrase: bool) -> str:
    """1 つの free-text トークンを edismax 句に変換する.

    quoted / 記号含み (auto-phrase) トークン、または安全な alnum 語でないものは
    ``"<escaped>"`` の完全一致 phrase に倒す。安全な alnum word トークンは
    ``("<exact>" OR <prefix 展開>)`` で前方一致を相乗りさせる
    (単一語 ``w`` → ``("w" OR w*)``、複数語 ``w1 w2`` → ``("w1 w2" OR (w1 AND w2*))``)。
    ``"<exact>"`` を残すのは完全一致をスコア上位に置くため (ES の should[match_phrase,
    match_phrase_prefix] と同じ意図)。
    """
    exact = f'"{escape_solr_phrase(token)}"'
    if token_is_phrase:
        return exact
    words = _solr_prefixable_words(token)
    if words is None or len(words[-1]) < _MIN_PREFIX_LITERAL_LEN:
        return exact
    if len(words) == 1:
        prefix_expr = f"{words[0]}*"
    else:
        prefix_expr = "(" + " AND ".join([*words[:-1], f"{words[-1]}*"]) + ")"
    return f"({exact} OR {prefix_expr})"


def compile_free_text_solr(
    value: str,
    *,
    operator: Literal["AND", "OR"] = "AND",
    is_phrase: bool = False,
) -> str:
    """シンプル検索 (``q``) を edismax ``q`` 文字列に変換する.

    トークン化して各トークンを ``_solr_token_expr`` で edismax 句に展開し、
    ``operator`` ("AND" または "OR") で連結する。``ARSA`` / ``TXSearch`` どちらも
    同形式で、dialect 依存しない。記号なし・クオートなしの alnum word トークンは
    ``("<exact>" OR <prefix>)`` で前方一致を相乗りさせる (打ちかけ対応、ES keyword box と
    挙動を揃える)。quoted / 記号含みトークンは ``"<exact>"`` 完全一致のまま。

    ``is_phrase=True`` (FreeText 全体が明示クオート) のときはコンマ分割・前方一致を
    bypass し、``value`` 全体を 1 つの完全一致 phrase ``"<escaped>"`` として返す
    (ES 側 ``compile_free_text(is_phrase=True)`` と対称)。

    トークンが 1 つだけのときは外側括弧を省略する。複数トークンの時は
    ``"(<e1> AND <e2> ...)"`` または ``"(<e1> OR <e2> ...)"`` で外側括弧を付ける。
    Solr edismax の ``q.op`` には依存せず、token 間の演算子を明示することで DSL の
    ``AND`` / ``OR`` BoolOp と挙動を干渉させない。

    入力がトークン化後に空 (None / "" / 空白のみ / カンマ区切り全部空) の場合は
    edismax all-docs ``*:*`` を返す。
    """
    if is_phrase:
        if not value:
            return _FREE_TEXT_EMPTY_FALLBACK
        return f'"{escape_solr_phrase(value)}"'
    parsed = parse_keywords_with_autophrase(value, SOLR_AUTO_PHRASE_CHARS)
    if not parsed:
        return _FREE_TEXT_EMPTY_FALLBACK
    exprs = [_solr_token_expr(token, token_is_phrase) for token, token_is_phrase in parsed]
    if len(exprs) == 1:
        return exprs[0]
    joiner = f" {operator} "
    return "(" + joiner.join(exprs) + ")"


def compile_to_solr(
    ast: Node,
    *,
    dialect: SolrDialect,
    free_text_operator: Literal["AND", "OR"] = "AND",
) -> str:
    """Convert a validated AST to an edismax ``q`` string for the given Solr dialect.

    ``FreeText`` ノードは dialect 非依存の ``compile_free_text_solr`` で展開する.
    ``free_text_operator`` は FreeText 内部のトークン連結に使う演算子を指定する
    (``AND`` / ``OR``)。DSL の明示 ``AND`` / ``OR`` / ``NOT`` BoolOp は影響を受けない。

    トップレベル AND 直下に FreeText が混じる AST では、AND が既存ロジック
    ``"(" + " AND ".join(children_q) + ")"`` で結合するため
    ``(<field_compiled> AND "<freetext_token>" ...)`` 形式の単一外側括弧クエリに
    なる。
    """
    return _compile_node(ast, dialect=dialect, free_text_operator=free_text_operator)


def _compile_node(
    node: Node,
    *,
    dialect: SolrDialect,
    free_text_operator: Literal["AND", "OR"] = "AND",
) -> str:
    if isinstance(node, FreeText):
        return compile_free_text_solr(node.value, operator=free_text_operator, is_phrase=node.is_phrase)
    if isinstance(node, FieldClause):
        return _compile_leaf(node, dialect=dialect)
    children_q = [_compile_node(c, dialect=dialect, free_text_operator=free_text_operator) for c in node.children]
    if node.op == "AND":
        return "(" + " AND ".join(children_q) + ")"
    if node.op == "OR":
        return "(" + " OR ".join(children_q) + ")"
    # Top-level `(NOT x)` is pure-negative.  Solr 4.4.0 rewrites this to
    # `MatchAllDocsQuery AND NOT x` automatically (staging probe 2026-04-24:
    # ARSA `(NOT Definition:"human")` = total - matches).  Wrapping in
    # `(*:* AND NOT x)` is NOT safe — edismax expands `*:*` via `qf` and
    # scores differently (TXSearch probe: `(NOT sn:"Homo")` = 2,737,968 but
    # `(*:* AND NOT sn:"Homo")` = 173,055).
    return f"(NOT {children_q[0]})"


def _compile_leaf(clause: FieldClause, *, dialect: SolrDialect) -> str:
    field_map = _ARSA_FIELD_MAP if dialect == "arsa" else _TXSEARCH_FIELD_MAP
    solr_fields = field_map.get(clause.field)
    if not solr_fields:
        # per-arm 簡約 (per_arm.reduce_ast_for_db) が非対応 / 固定値 field を compile 前に
        # 除くため、ここに到達するのは簡約をバイパスしたバグ。silent な no-match
        # (TXSearch では edismax qf 展開で ~全件化する) を避けて明示的に落とす。
        raise RuntimeError(
            f"field {clause.field!r} has no Solr mapping for dialect {dialect!r}; "
            "per-arm reduction must drop unavailable / fixed-value fields before compilation.",
        )
    if len(solr_fields) == 1:
        return _basic_leaf(solr_fields[0], clause)
    return "(" + " OR ".join(_basic_leaf(f, clause) for f in solr_fields) + ")"


def _basic_leaf(solr_field: str, clause: FieldClause) -> str:
    field_type, op = resolve_field_operator(clause)
    value = clause.value
    if op == "between" and isinstance(value, Range):
        from_v = _format_date_for_solr(value.from_) if field_type == "date" else value.from_
        to_v = _format_date_for_solr(value.to) if field_type == "date" else value.to
        return f"{solr_field}:[{from_v} TO {to_v}]"
    if not isinstance(value, str):
        raise TypeError(f"expected str value for field {clause.field!r}")
    if clause.value_kind == "date":
        formatted = _format_date_for_solr(value) if field_type == "date" else value
        return f"{solr_field}:{formatted}"
    if clause.value_kind == "wildcard":
        if not _SOLR_SAFE_WILDCARD_RE.match(value):
            raise RuntimeError(
                f"wildcard value {value!r} for field {clause.field!r} reached the Solr compiler "
                "with unsafe characters; this means the validator was bypassed.",
            )
        return f"{solr_field}:{value}"
    # word / phrase は両方 quote (Solr edismax metachar 解釈回避)
    escaped = escape_solr_phrase(value)
    exact = f'{solr_field}:"{escaped}"'
    # text 型 contains の simple word (記号なし ASCII alnum) は前方一致を相乗り
    # (keyword box と同じ「simple word のみ前方一致」原則)。記号含み語・クオート値・
    # 非 ASCII 語は完全一致のまま (edismax メタ文字 escape を漏らさない)。grammar 上
    # field 値の word は単一語なので複数語展開は不要。
    if (
        op == "contains"
        and clause.value_kind == "word"
        and value.isascii()
        and value.isalnum()
        and len(value) >= _MIN_PREFIX_LITERAL_LEN
    ):
        return f"({exact} OR {solr_field}:{value}*)"
    return exact


def _format_date_for_solr(iso: str) -> str:
    """YYYY-MM-DD → YYYYMMDD (ARSA ``Date`` field format)."""
    return iso.replace("-", "")
