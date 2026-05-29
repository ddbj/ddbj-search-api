"""Shared lexer pattern constants mirroring ``grammar.lark``.

SSOT: ``ddbj_search_api/search/dsl/grammar.lark``.

parser は Lark grammar の terminal 定義を使うが、parser を経由しないコード
(serializer / serde / strategies) も「ある文字列が WORD token として読まれるか」
「DATE token と衝突するか」を判定したい.  そこで grammar の正規表現を Python regex
として複製して提供する.

Lark の terminal priority (DATE.4 > AND.10 等) と一致させるため、ここでは「どの
token として lex されるか」の判定をシングルファイルに集約し、grammar 変更時の
同期忘れを起きにくくする.
"""

from __future__ import annotations

import re
from typing import Final

# grammar.lark ``WORD.1: /[^\s:()\[\]"{}^~*?\/]+/`` の full-match 版.
# field_clause の value、free_text_atom、識別子等の bare 出力可否判定に使う.
WORD_RE: Final[re.Pattern[str]] = re.compile(r"^[^\s:()\[\]\"{}^~*?\/]+$")

# grammar.lark ``DATE.4: /\d{4}-\d{2}-\d{2}/``.  WORD より priority が高いため、
# word value が DATE shape に full-match すると parser が DATE token として lex する.
DATE_RE: Final[re.Pattern[str]] = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# grammar.lark ``AND.10`` / ``OR.10`` / ``NOT.10``.  priority 10 で WORD を勝つ.
# 大文字 case-sensitive (grammar 側も同様).
RESERVED_OPERATOR_LITERALS: Final[frozenset[str]] = frozenset({"AND", "OR", "NOT"})


def needs_quote_for_token_collision(value: str) -> bool:
    """Return True if ``value`` collides with a higher-priority grammar token.

    bare 出力すると parser が WORD ではなく DATE / operator literal として lex
    してしまうため、serializer 側で強制 quote する必要がある.
    """
    return DATE_RE.match(value) is not None or value in RESERVED_OPERATOR_LITERALS


def is_bare_safe_multiword(value: str) -> bool:
    """Return True if ``value`` can be emitted as space-separated bare words.

    parser は ``free_text_atom: WORD+`` で空白区切りの bare word 列を 1 つの
    FreeText 値に畳む (``%ignore /\\s+/`` で空白は単一に正規化される).  serializer は
    この形の値を quote せず bare 出力してよいかをこの関数で判定する.  連続空白・先頭末尾
    空白を含む値や、DATE / operator literal token と衝突する token を含む値は bare 化
    できない (quote しないと re-parse で別 token に分解 / drift する).
    """
    if " ".join(value.split()) != value:
        return False
    return all(WORD_RE.match(tok) is not None and not needs_quote_for_token_collision(tok) for tok in value.split(" "))
