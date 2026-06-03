"""Hypothesis custom strategies for ddbj-search-api tests."""

from __future__ import annotations

import datetime
import string
from typing import Final

from hypothesis import strategies as st

from ddbj_search_api.schemas.common import DbType
from ddbj_search_api.schemas.dblink import AccessionType
from ddbj_search_api.search.dsl.allowlist import (
    FIELD_TYPES,
    OPERATOR_BY_KIND,
    TIER1_FIELDS,
    TIER2_FIELDS,
)
from ddbj_search_api.search.dsl.ast import (
    BoolOp,
    FieldClause,
    FreeText,
    Node,
    Position,
    Range,
    ValueKind,
)
from ddbj_search_api.search.dsl.lex_patterns import is_bare_safe_multiword
from ddbj_search_api.search.phrase import ES_AUTO_PHRASE_CHARS, parse_keywords_with_autophrase

# === DbType ===

db_type_values: list[str] = [e.value for e in DbType]
valid_db_types = st.sampled_from(db_type_values)

# === Pagination ===

valid_page = st.integers(min_value=1, max_value=10000)
invalid_page = st.integers(max_value=0)
valid_per_page = st.integers(min_value=1, max_value=100)
invalid_per_page_low = st.integers(max_value=0)
invalid_per_page_high = st.integers(min_value=101)

# === dbXrefsLimit ===

valid_db_xrefs_limit = st.integers(min_value=0, max_value=1000)
invalid_db_xrefs_limit_low = st.integers(max_value=-1)
invalid_db_xrefs_limit_high = st.integers(min_value=1001)

# === Bulk API ids ===

valid_id = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
    min_size=1,
    max_size=30,
)
short_id = st.from_regex(r"[A-Z]{2,6}[0-9]{1,6}", fullmatch=True)
valid_bulk_ids = st.lists(valid_id, min_size=1, max_size=100)
oversized_bulk_ids = st.lists(short_id, min_size=1001, max_size=1050)

# === FacetBucket ===

valid_facet_count = st.integers(min_value=0)
valid_facet_value = st.text(min_size=1, max_size=100)

# === Pagination response ===

valid_total = st.integers(min_value=0)

# === AccessionType (dblink) ===

accession_type_values: list[str] = [e.value for e in AccessionType]
valid_accession_types = st.sampled_from(accession_type_values)
valid_accession_id = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
    min_size=1,
    max_size=30,
)

# === BioProject accession (umbrella tree) ===

bioproject_accession = st.from_regex(r"PRJ(DB|NA|EB)[0-9]{1,7}", fullmatch=True)

__all__ = [
    "accession_type_values",
    "alphanumeric_no_trigger",
    "bioproject_accession",
    "db_type_values",
    "field_clause_strategy",
    "invalid_db_xrefs_limit_high",
    "invalid_db_xrefs_limit_low",
    "invalid_page",
    "invalid_per_page_high",
    "invalid_per_page_low",
    "oversized_bulk_ids",
    "short_id",
    "text_with_trigger",
    "valid_accession_id",
    "valid_accession_types",
    "valid_ast_strategy",
    "valid_bulk_ids",
    "valid_db_types",
    "valid_db_xrefs_limit",
    "valid_facet_count",
    "valid_facet_value",
    "valid_id",
    "valid_page",
    "valid_per_page",
    "valid_total",
]


def alphanumeric_no_trigger(trigger_chars: frozenset[str]) -> st.SearchStrategy[str]:
    """Alphanumeric text excluding trigger char, comma, quote, whitespace."""
    return st.text(
        alphabet=st.characters(
            whitelist_categories=("L", "N"),
            blacklist_characters='",' + "".join(sorted(trigger_chars)) + " \t\r\n",
        ),
        min_size=1,
        max_size=30,
    )


def text_with_trigger(trigger_chars: frozenset[str]) -> st.SearchStrategy[str]:
    """Text guaranteed to contain at least one trigger char (sandwiched)."""
    inner = alphanumeric_no_trigger(trigger_chars)
    return st.builds(
        lambda prefix, trigger, suffix: prefix + trigger + suffix,
        inner,
        st.sampled_from(sorted(trigger_chars)),
        inner,
    )


# === DSL AST (used by serializer / parser round-trip PBT) =================
#
# Tier 1/2 のみ使い、``validate(mode="cross")`` が通る AST を生成する.
# FreeText は配置制約 (root 単独 or top-level AND 直下に最大 1 個) が複雑なので
# まず除外する.  FreeText 経路は serializer / parser の unit test で個別に
# カバー済み.
#
# AND/OR は flat children (parser の左結合 fold と同形) を生成し、子に同じ op
# の BoolOp を直接置かない (parse → serialize の chain 正規化と整合).
# value_kind は OPERATOR_BY_KIND の (field_type, value_kind) → operator が
# 定義されているもののみから抽選する.

_DSL_DUMMY_POSITION: Final[Position] = Position(column=1, length=0)

# WORD regex (``[^\s:()\[\]"{}^~*?\/]+``) の安全な subset.  英数 + ``_-.``.
_DSL_WORD_ALPHABET: Final[str] = string.ascii_letters + string.digits + "_-."

# Phrase 用の文字集合.  改行と Tab を除き、ASCII 印字可能 + 一部記号.  PHRASE
# regex の ``\\.`` エスケープが正常動作することを担保するため backslash と quote
# を一部含めるが、頻度を低めに抑える.
_DSL_PHRASE_ALPHABET: Final[str] = string.ascii_letters + string.digits + " _-.,:/()[]{}"

_DSL_FIELDS_BY_TYPE: Final[dict[str, tuple[str, ...]]] = {
    field_type: tuple(sorted(f for f in (TIER1_FIELDS | TIER2_FIELDS) if FIELD_TYPES[f] == field_type))
    for field_type in ("identifier", "text", "date", "enum")
}

_DSL_KINDS_BY_FIELD: Final[dict[str, tuple[ValueKind, ...]]] = {
    field: tuple(sorted(vk for (ft, vk) in OPERATOR_BY_KIND if ft == FIELD_TYPES[field]))
    for field in (TIER1_FIELDS | TIER2_FIELDS)
}

_DSL_ALL_FIELDS: Final[tuple[str, ...]] = tuple(sorted(TIER1_FIELDS | TIER2_FIELDS))


# grammar の DATE / AND / OR / NOT は WORD より token priority が高いため、形だけ
# 一致する word value は serializer 側で quote 必須 (``_needs_quote_for_token_collision``).
# strategy はそれらの shape を意図的に混ぜ、quote 漏れの回帰を PBT が検出できるようにする.
_DSL_RESERVED_OPERATOR_LITERALS: Final[tuple[str, ...]] = ("AND", "OR", "NOT")


@st.composite
def _dsl_word_value(draw: st.DrawFn) -> str:
    return draw(
        st.one_of(
            st.text(alphabet=_DSL_WORD_ALPHABET, min_size=1, max_size=10),
            _dsl_date_value(),
            st.sampled_from(_DSL_RESERVED_OPERATOR_LITERALS),
        ),
    )


@st.composite
def _dsl_phrase_value(draw: st.DrawFn) -> str:
    # 必ず WORD regex に match しない (= phrase 化される) 値を生成.
    head = draw(st.text(alphabet=_DSL_PHRASE_ALPHABET, min_size=1, max_size=8))
    tail = draw(st.text(alphabet=_DSL_PHRASE_ALPHABET, min_size=1, max_size=8))
    return f"{head} {tail}"


@st.composite
def _dsl_wildcard_value(draw: st.DrawFn) -> str:
    # validator は first ``*``/``?`` の前に最低 ``_MIN_WILDCARD_LITERAL_LEN``
    # (=2) 文字の literal を要求する.  leading wildcard も reject される.
    head = draw(st.text(alphabet=_DSL_WORD_ALPHABET, min_size=2, max_size=5))
    marker = draw(st.sampled_from("*?"))
    tail = draw(st.text(alphabet=_DSL_WORD_ALPHABET, min_size=0, max_size=5))
    return f"{head}{marker}{tail}"


@st.composite
def _dsl_date_value(draw: st.DrawFn) -> str:
    d = draw(
        st.dates(
            min_value=datetime.date(1900, 1, 1),
            max_value=datetime.date(2099, 12, 31),
        ),
    )
    return d.isoformat()


@st.composite
def _dsl_range_value(draw: st.DrawFn) -> Range:
    a = draw(_dsl_date_value())
    b = draw(_dsl_date_value())
    if a > b:
        a, b = b, a
    return Range(from_=a, to=b)


def _dsl_value_for_kind(value_kind: ValueKind) -> st.SearchStrategy[str | Range]:
    if value_kind == "word":
        return _dsl_word_value()
    if value_kind == "phrase":
        return _dsl_phrase_value()
    if value_kind == "wildcard":
        return _dsl_wildcard_value()
    if value_kind == "date":
        return _dsl_date_value()
    if value_kind == "range":
        return _dsl_range_value()
    raise AssertionError(f"unknown value_kind: {value_kind!r}")


@st.composite
def field_clause_strategy(draw: st.DrawFn) -> FieldClause:
    """Generate a valid ``FieldClause`` (Tier 1/2 field, allowed value_kind)."""
    field = draw(st.sampled_from(_DSL_ALL_FIELDS))
    value_kind = draw(st.sampled_from(_DSL_KINDS_BY_FIELD[field]))
    value = draw(_dsl_value_for_kind(value_kind))
    return FieldClause(
        field=field,
        value_kind=value_kind,
        value=value,
        position=_DSL_DUMMY_POSITION,
    )


def _dsl_subtree_strategy(*, depth: int, parent_op: str | None) -> st.SearchStrategy[Node]:
    """Recursive subtree.  ``parent_op`` を渡すと同 op の BoolOp を直接子に置かない."""
    if depth <= 0:
        return field_clause_strategy()

    @st.composite
    def _composite(draw: st.DrawFn) -> Node:
        candidate_ops = ["leaf", "NOT"]
        for op in ("AND", "OR"):
            if op != parent_op:
                candidate_ops.append(op)
        choice = draw(st.sampled_from(candidate_ops))
        if choice == "leaf":
            return draw(field_clause_strategy())
        if choice == "NOT":
            inner = draw(_dsl_subtree_strategy(depth=depth - 1, parent_op=None))
            return BoolOp(op="NOT", children=(inner,), position=_DSL_DUMMY_POSITION)
        n = draw(st.integers(min_value=2, max_value=3))
        children = [draw(_dsl_subtree_strategy(depth=depth - 1, parent_op=choice)) for _ in range(n)]
        return BoolOp(op=choice, children=tuple(children), position=_DSL_DUMMY_POSITION)  # type: ignore[arg-type]

    return _composite()


@st.composite
def _dsl_multiword_value(draw: st.DrawFn) -> str:
    """空白区切りの複数 bare word.  各 token は WORD alphabet だが DATE / operator
    literal shape も混ざり得るので、bare-safe (1 FreeText の値内 AND) と phrase 退避の
    両経路を round-trip が踏むようにする."""
    words = draw(
        st.lists(
            st.text(alphabet=_DSL_WORD_ALPHABET, min_size=1, max_size=8),
            min_size=2,
            max_size=3,
        ),
    )
    return " ".join(words)


@st.composite
def _dsl_free_text_value(draw: st.DrawFn) -> str:
    """FreeText.value 用 strategy.  単一/複数 WORD / phrase / DATE / operator literal を網羅生成."""
    return draw(
        st.one_of(
            st.text(alphabet=_DSL_WORD_ALPHABET, min_size=1, max_size=10),
            _dsl_multiword_value(),
            _dsl_phrase_value(),
            _dsl_date_value(),
            st.sampled_from(_DSL_RESERVED_OPERATOR_LITERALS),
        ),
    )


def _value_implies_phrase(value: str) -> bool:
    """parser/serializer の quote 規則から ``is_phrase`` を導出.

    serializer は空白区切りの各 token が bare 出力可能 (WORD full-match かつ DATE /
    operator literal と衝突しない) な値だけを bare のまま出し、parser はそれを
    ``free_text_atom: WORD+`` 経由で ``is_phrase=False`` の単一 FreeText に読み戻す.
    それ以外 (記号・連続空白・operator literal・DATE 形を含む値) は serializer が quote
    を付け、parser はそれを quoted phrase として ``is_phrase=True`` に復元する.
    """
    return not is_bare_safe_multiword(value)


def _free_text_is_compilable(ft: FreeText) -> bool:
    """compiler (compile_free_text) が ValueError を出さない FreeText か。

    記号・空白のみの値 (例: ``", ,"``) は is_phrase=False のときトークン化後に空となり、
    compiler が ValueError を出す (parser は ``WORD+`` 経由でこうした値を生成しない)。
    valid_ast_strategy は「validate を通さず compile しても安全な AST」を生成するため、
    こうした parser 由来でない値を除外する。
    """
    if ft.is_phrase:
        return bool(ft.value)
    return bool(parse_keywords_with_autophrase(ft.value, ES_AUTO_PHRASE_CHARS))


def _free_text_strategy() -> st.SearchStrategy[FreeText]:
    # is_phrase は value の性質から確定的に導出 (parser → AST → serialize → parser の
    # round-trip が常に成り立つ AST のみ生成する).
    return (
        _dsl_free_text_value()
        .map(lambda v: FreeText(value=v, is_phrase=_value_implies_phrase(v)))
        .filter(_free_text_is_compilable)
    )


@st.composite
def _top_level_and_with_free_text(draw: st.DrawFn, *, max_depth: int) -> BoolOp:
    """``BoolOp(AND, [FreeText, non_freetext_subtree, ...])`` を生成.

    validator の FreeText 位置制約 (top-level AND 直下に最大 1 個) を構造的に満たす.
    """
    free = draw(_free_text_strategy())
    n = draw(st.integers(min_value=1, max_value=3))
    rest = [draw(_dsl_subtree_strategy(depth=max(max_depth - 1, 0), parent_op="AND")) for _ in range(n)]
    return BoolOp(op="AND", children=(free, *rest), position=_DSL_DUMMY_POSITION)


def valid_ast_strategy(*, max_depth: int = 3) -> st.SearchStrategy[Node]:
    """Generate an AST satisfying ``validate(mode="cross")``.

    Constraints:

    - Tier 1/2 fields only (cross-mode allowlist).
    - FreeText の配置: root 単独 または top-level AND 直下に最大 1 個 (validator の
      ``invalid-freetext-position`` / ``duplicate-freetext`` を構造的に満たす).
    - AND/OR の子に同じ op の BoolOp を直接置かない (parser が flat children
      に正規化するため、生成側も flat に揃える).
    - ``max_depth`` で再帰深さを制限 (default 3、Lark の ``DEFAULT_MAX_DEPTH=5``
      に余裕を持たせる).
    - word value / FreeText value に DATE-shape / operator literal も混ぜ、
      serializer の ``_needs_quote_for_token_collision`` 漏れを PBT が検出可能にする.
    """
    return st.one_of(
        _free_text_strategy(),
        field_clause_strategy(),
        _dsl_subtree_strategy(depth=max_depth, parent_op=None),
        _top_level_and_with_free_text(max_depth=max_depth),
    )
