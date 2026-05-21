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
