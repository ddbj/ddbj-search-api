"""Accession ID 完全一致判定。

`/entries/` 系のキーワード検索で ``keywords`` が単一の accession ID と
完全一致する場合のみ、`suppressed` ステータスも検索対象に含めるための
判定ユーティリティ。判定ルールの詳細は
``docs/api-spec.md`` の「データ可視性 (status 制御)」節を参照。
"""

from __future__ import annotations

from re import Pattern

from ddbj_search_converter.id_patterns import ID_PATTERN_MAP

from ddbj_search_api.schemas.common import DbType

_ACCESSION_PATTERNS: list[Pattern[str]] = [ID_PATTERN_MAP[db_type.value] for db_type in DbType]


def is_accession_like(token: str) -> bool:
    """指定トークンが DbType のいずれかの ID パターンに完全一致するか判定する。

    呼び出し側は前後空白と外側クオートを事前に除去しておく必要がある。
    """
    if not token:
        return False
    if "*" in token or "?" in token:
        return False
    return any(pattern.fullmatch(token) for pattern in _ACCESSION_PATTERNS)


def detect_accession_exact_match(keywords: str | None) -> str | None:
    """``keywords`` が単一 accession ID と完全一致するとき、その ID を返す。

    判定ルールは ``docs/api-spec.md`` の「データ可視性 (status 制御)」節を参照。
    一致しないときは ``None`` を返す。
    """
    if keywords is None:
        return None
    if "," in keywords:
        return None

    token = keywords.strip()
    if not token:
        return None

    if len(token) >= 2 and token[0] == token[-1] and token[0] in ('"', "'"):
        token = token[1:-1].strip()

    if not token:
        return None

    if is_accession_like(token):
        return token
    return None
