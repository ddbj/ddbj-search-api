"""Auto-phrase helpers shared by ES and Solr query builders.

ES uses a narrow trigger set (``-/.+:``) to promote tokens to
``multi_match(type=phrase)`` so that the standard analyzer does not split
them and inflate hit counts (staging-measured: ``HIF-1`` 5.86M → 615 hits).

Solr uses an extended set that also covers edismax meta chars
(``*?()[]{}^~!|&\\``) because edismax interprets bare tokens like ``HIF-1``
as NOT expressions (staging-measured: numFound 15050 quoted → 295M unquoted).

The trigger set is passed in by callers so this module does not depend
on backend identity.
"""

from __future__ import annotations

ES_AUTO_PHRASE_CHARS: frozenset[str] = frozenset("-/.+:")
SOLR_AUTO_PHRASE_CHARS: frozenset[str] = frozenset("-/.+:*?()[]{}^~!|&\\")


def has_auto_phrase_trigger(text: str, trigger_chars: frozenset[str]) -> bool:
    """Return True if ``text`` contains any character in ``trigger_chars``."""
    return any(c in trigger_chars for c in text)


def _split_raw_tokens(keywords: str) -> list[str]:
    # 引用符内のカンマは split 対象外、トリム済みの raw token (引用符そのまま) を返す
    tokens: list[str] = []
    current: list[str] = []
    quote_ch: str | None = None
    for ch in keywords:
        if ch in ('"', "'"):
            if quote_ch is None:
                quote_ch = ch
            elif quote_ch == ch:
                quote_ch = None
            current.append(ch)
        elif ch == "," and quote_ch is None:
            tokens.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    tokens.append("".join(current).strip())
    return tokens


def _strip_outer_quotes(token: str) -> tuple[str, bool]:
    """Strip a matching pair of outer ``"`` or ``'`` quotes.

    Returns ``(inner, was_quoted)``. ``was_quoted`` is True only when an
    outer quote pair was actually removed; the ES query builder uses it
    to force phrase mode.
    """
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ('"', "'"):
        return token[1:-1], True
    return token, False


def tokenize_keywords(keywords: str | None) -> list[str]:
    """Split comma-separated keywords, preserving quoted content.

    Returns cleaned token strings: surrounding quotes (``"..."`` /
    ``'...'``) stripped, stray double quotes removed, whitespace trimmed.
    Empty / all-whitespace input yields ``[]``.
    """
    if not keywords:
        return []

    result: list[str] = []
    for token in _split_raw_tokens(keywords):
        if not token:
            continue
        inner, was_quoted = _strip_outer_quotes(token)
        if was_quoted:
            if inner:
                result.append(inner)
        else:
            cleaned = token.replace('"', "")
            if cleaned:
                result.append(cleaned)
    return result


def parse_keywords_with_autophrase(
    keywords: str | None,
    trigger_chars: frozenset[str],
) -> list[tuple[str, bool]]:
    """Tokenize ``keywords`` and tag each token with a phrase flag.

    A token is flagged as phrase when it was originally quoted (either
    ``"..."`` or ``'...'``) *or* when it contains any character in
    ``trigger_chars``.  Used by the ES query builder; Solr quotes
    everything unconditionally so it should call
    :func:`tokenize_keywords` instead.
    """
    if not keywords:
        return []

    result: list[tuple[str, bool]] = []
    for token in _split_raw_tokens(keywords):
        if not token:
            continue
        inner, was_quoted = _strip_outer_quotes(token)
        if was_quoted:
            if inner:
                result.append((inner, True))
        else:
            cleaned = token.replace('"', "")
            if cleaned:
                result.append((cleaned, has_auto_phrase_trigger(cleaned, trigger_chars)))
    return result


def escape_solr_phrase(text: str) -> str:
    """Escape ``\\`` and ``"`` for embedding inside a Solr phrase.

    Backslash must be doubled first; otherwise the backslashes inserted
    by quote escaping would themselves get doubled.
    """
    return text.replace("\\", "\\\\").replace('"', '\\"')
