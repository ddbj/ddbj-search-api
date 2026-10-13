"""Shared lexer pattern constants mirroring ``grammar.lark``.

SSOT: ``ddbj_search_api/search/dsl/grammar.lark``.

parser は Lark grammar の terminal 定義を使うが、parser を経由しないコード
(serializer / serde / strategies) も「ある文字列が WORD token として読まれるか」
「DATE や演算子の token と衝突するか」を判定したい.  そこで grammar の正規表現を Python
regex として複製して提供する.

field の値と keyword の語では、parser が試す terminal が違う (contextual lexer).
field の値の位置では DATE / WILDCARD / PHRASE / WORD を試すが、keyword の語の位置では
DATE を試さない.  そのため判定を ``needs_quote_for_token_collision`` (field の値) と
``is_keyword_word`` (keyword の語) に分ける.
"""

from __future__ import annotations

import re
from typing import Final

# grammar.lark の PHRASE の単一引用符の側.  空白・``)``・終端が後ろに続く ``'`` で閉じる.
_SINGLE_QUOTED: Final[str] = r"'(?:[^'\\]|\\.|'(?=[^\s)]))*'(?![^\s)])"

# grammar.lark ``WORD.1`` の full-match 版.  閉じた単一引用符の phrase が始まる位置では
# WORD にならない.
WORD_RE: Final[re.Pattern[str]] = re.compile(rf"^(?!{_SINGLE_QUOTED})[^\s:()\[\]\"{{}}^~*?\/]+\Z")

# 語の先頭 (文字列の先頭、空白か ``(`` の後) から始まる、閉じた単一引用符の phrase.
# keyword の語を空白で並べたときに、語をまたいで phrase ができるかを見る.
SINGLE_QUOTED_PHRASE_RE: Final[re.Pattern[str]] = re.compile(rf"(?<![^\s(]){_SINGLE_QUOTED}")

# grammar.lark ``DATE.4: /\d{4}-\d{2}-\d{2}/``.  WORD より priority が高く、値の先頭だけが
# 合っても DATE token を切り出すので、``match`` (先頭一致) で判定する.
DATE_RE: Final[re.Pattern[str]] = re.compile(r"\d{4}-\d{2}-\d{2}")

# grammar.lark ``WILDCARD.3`` の full-match 版.
WILDCARD_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_\-.]*[*?][A-Za-z0-9_\-.]*\Z")

# grammar.lark ``AND.10`` / ``OR.10`` / ``NOT.10``.  priority 10 で WORD を勝つ.
# 大文字 case-sensitive (grammar 側も同様).
RESERVED_OPERATOR_LITERALS: Final[frozenset[str]] = frozenset({"AND", "OR", "NOT"})

# 演算子の直後の ``'`` は演算子を終える文字なので、``AND'x`` は演算子と語に分かれる.
_OPERATOR_QUOTE: Final[re.Pattern[str]] = re.compile(r"(?:AND|OR|NOT)'")


def needs_quote_for_token_collision(value: str) -> bool:
    """field の値を bare で出すと WORD 以外の token として読まれるとき True.

    ``'`` で始まる値は単一引用符の phrase を開き、後ろの条件をまたいで次の ``'`` まで
    続きうる.  日付で始まる値は DATE token が先頭を切り出す.  演算子そのもの、演算子に
    ``'`` が続く値、wildcard の形の値も WORD にならない.
    """
    return (
        value.startswith("'")
        or DATE_RE.match(value) is not None
        or value in RESERVED_OPERATOR_LITERALS
        or _OPERATOR_QUOTE.match(value) is not None
        or WILDCARD_RE.match(value) is not None
    )


def is_bare_word(value: str) -> bool:
    """field の値を quote なしで出し、同じ word として読み戻せるとき True."""
    return WORD_RE.match(value) is not None and not needs_quote_for_token_collision(value)


def is_keyword_word(value: str) -> bool:
    """keyword の 1 語を quote なしで出し、同じ語として読み戻せるとき True.

    keyword の位置では parser が DATE を試さないので、日付の形の語はそのまま出せる.
    演算子そのもの、演算子に ``'`` が続く語、閉じた単一引用符の phrase は語にならない.
    語を空白で並べたときに語をまたいで phrase ができないことは ``is_bare_safe_multiword`` が見る.
    """
    return (
        WORD_RE.match(value) is not None
        and value not in RESERVED_OPERATOR_LITERALS
        and _OPERATOR_QUOTE.match(value) is None
    )


def is_bare_safe_multiword(value: str) -> bool:
    """keyword の値を空白区切りの bare word 列として出せるとき True.

    parser は ``free_text_atom: WORD+`` で空白区切りの bare word 列を 1 つの FreeText 値に
    畳む (``%ignore /\\s+/`` で空白は単一に正規化される).  連続空白・先頭末尾空白を含む値、
    ``is_keyword_word`` でない語を含む値、語をまたいで単一引用符の phrase ができる値
    (``'s y'``) は bare にできない.
    """
    if " ".join(value.split()) != value:
        return False
    return all(is_keyword_word(tok) for tok in value.split(" ")) and SINGLE_QUOTED_PHRASE_RE.search(value) is None


def lower_reserved_words(value: str) -> str:
    """keyword の中の ``AND`` / ``OR`` / ``NOT`` の語を小文字にする.

    演算子として読めない位置の予約語は keyword の語になる.  keyword は大文字小文字を区別
    しないので、小文字にしても意味は変わらず、serialize した文字列を parse しても同じ語に戻る.
    """
    return " ".join(word.lower() if word in RESERVED_OPERATOR_LITERALS else word for word in value.split(" "))
