"""DSL compiler for Solr edismax q string (Stage 3b).

SSOT: search-backends.md §バックエンド変換.

Dialect:
- ``arsa``: ARSA (Solr 4.4.0)。Tier 1 (``PrimaryAccessionNumber`` / ``Definition`` /
  ``AllText`` / ``Organism`` / ``Lineage`` / ``Date``) + Trad Tier 3 (``Division`` /
  ``MolecularType`` / ``SequenceLength`` / ``FeatureQualifier`` / ``ReferenceJournal``)。
  ``organism_name`` は ``Organism`` / ``Lineage`` の OR phrase にマップ、``organism_id``
  は taxID 直接検索 field がないため degenerate。``date_modified`` / ``date_created`` /
  ``date`` alias、submitter / publication (organization も publication.title 相当の field
  も ARSA にない)、ES-only / Taxonomy 系 Tier 3 は degenerate。
- ``txsearch``: TXSearch (Solr 4.4.0)。Tier 1 (``tax_id`` / ``scientific_name`` / ``text``) +
  Taxonomy Tier 3 (``rank`` / ``lineage`` / ``kingdom`` / ... / ``common_name``; 10 field)。
  TXSearch は Taxonomy DB そのものなので ``organism_id`` を ``tax_id`` に、``organism_name``
  を ``scientific_name`` にマップ (entry の identifier / title と同じ field を別名で叩く形)。
  日付 + Tier 2 + Trad/ES-only Tier 3 は degenerate。``japanese_name`` は staging TXSearch
  の schema に不在のため allowlist 外。

degenerate は leaf を ``(-*:*)`` (no-match リテラル) に置換。ツリー構造は維持する。
"""

from __future__ import annotations

import re
from typing import Literal, TypeAlias

from ddbj_search_api.search.dsl.allowlist import ALL_ALLOWED_FIELDS, FIELD_TYPES, OPERATOR_BY_KIND
from ddbj_search_api.search.dsl.ast import FieldClause, FreeText, Node, Range
from ddbj_search_api.search.phrase import escape_solr_phrase, tokenize_keywords

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

# === ARSA (Trad) field map ===

_ARSA_FIELD_MAP: dict[str, tuple[str, ...]] = {
    # === Tier 1 ===
    "identifier": ("PrimaryAccessionNumber",),
    "title": ("Definition",),
    "description": ("AllText",),
    # organism_name は学名 (Organism) + 分類体系 (Lineage) の OR phrase。
    # organism_id (taxID exact) は ARSA に対応 field 不在のため _ARSA_UNAVAILABLE 行き。
    "organism_name": ("Organism", "Lineage"),
    "date_published": ("Date",),
    # === Tier 2 ===
    # publication は publication.title (text) に正規化したため ARSA に対応 field なし
    # (旧 publication.id → ReferencePubmedID マップは廃止)。
    # submitter / publication は _ARSA_UNAVAILABLE で degenerate される。
    # === Tier 3 Trad only ===
    "division": ("Division",),
    "molecular_type": ("MolecularType",),
    "sequence_length": ("SequenceLength",),
    "feature_gene_name": ("FeatureQualifier",),
    "reference_journal": ("ReferenceJournal",),
}

_ARSA_UNAVAILABLE: frozenset[str] = ALL_ALLOWED_FIELDS - frozenset(_ARSA_FIELD_MAP)

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
    # japanese_name は staging TXSearch の schema luke で field 不在のため allowlist 外
}

_TXSEARCH_UNAVAILABLE: frozenset[str] = ALL_ALLOWED_FIELDS - frozenset(_TXSEARCH_FIELD_MAP)

_NO_MATCH_LITERAL = "(-*:*)"


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


def compile_free_text_solr(
    value: str,
    *,
    operator: Literal["AND", "OR"] = "AND",
) -> str:
    """シンプル検索 (``q``) を edismax ``q`` 文字列に変換する.

    各トークンを ``escape_solr_phrase`` で escape して double-quote wrap し、
    ``operator`` ("AND" または "OR") で連結する。``ARSA`` / ``TXSearch`` どちらも
    同形式で、dialect 依存しない。

    トークンが 1 つだけのときは括弧を省略する (escape されたフレーズそのまま)。
    複数トークンの時は ``"(<t1> AND <t2> ...)"`` または ``"(<t1> OR <t2> ...)"`` の
    形で外側括弧を付ける。Solr edismax の ``q.op`` には依存せず、token 間の演算子を
    明示することで DSL の ``AND`` / ``OR`` BoolOp と挙動を干渉させない。

    入力がトークン化後に空 (None / "" / 空白のみ / カンマ区切り全部空) の場合は
    edismax all-docs ``*:*`` を返す。
    """
    tokens = tokenize_keywords(value)
    if not tokens:
        return _FREE_TEXT_EMPTY_FALLBACK
    quoted = [f'"{escape_solr_phrase(t)}"' for t in tokens]
    if len(quoted) == 1:
        return quoted[0]
    joiner = f" {operator} "
    return "(" + joiner.join(quoted) + ")"


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
        return compile_free_text_solr(node.value, operator=free_text_operator)
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
    if dialect == "arsa":
        if clause.field in _ARSA_UNAVAILABLE:
            return _NO_MATCH_LITERAL
        solr_fields = _ARSA_FIELD_MAP.get(clause.field)
    else:
        if clause.field in _TXSEARCH_UNAVAILABLE:
            return _NO_MATCH_LITERAL
        solr_fields = _TXSEARCH_FIELD_MAP.get(clause.field)
    if not solr_fields:
        return _NO_MATCH_LITERAL
    if len(solr_fields) == 1:
        return _basic_leaf(solr_fields[0], clause)
    return "(" + " OR ".join(_basic_leaf(f, clause) for f in solr_fields) + ")"


def _basic_leaf(solr_field: str, clause: FieldClause) -> str:
    field_type = FIELD_TYPES[clause.field]
    op = OPERATOR_BY_KIND[(field_type, clause.value_kind)]
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
    return f'{solr_field}:"{escaped}"'


def _format_date_for_solr(iso: str) -> str:
    """YYYY-MM-DD → YYYYMMDD (ARSA ``Date`` field format)."""
    return iso.replace("-", "")
