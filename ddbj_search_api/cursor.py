"""Cursor token encode/decode for cursor-based pagination.

Pure functions with no I/O. The cursor token is a URL-safe Base64-encoded
JSON string containing the state needed to resume a search_after query.
An HMAC-SHA256 signature prevents clients from forging arbitrary queries.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
from typing import Any

from pydantic import BaseModel, Field

# Signing key for cursor tokens.  Read from ``DDBJ_SEARCH_API_CURSOR_SECRET``
# at first use; if unset, a per-process random key is generated.  Multi-worker
# / multi-instance deployments must set the env so every process signs with
# the same key, otherwise a cursor issued by one worker fails verification on
# another (see docs/deployment.md).
_SECRET: bytes | None = None
_SECRET_LOCK = threading.Lock()


def _get_secret() -> bytes:
    """Lazily resolve the signing secret (env override, else per-process random)."""
    global _SECRET  # noqa: PLW0603
    if _SECRET is not None:
        return _SECRET
    with _SECRET_LOCK:
        if _SECRET is not None:
            return _SECRET
        env_value = os.environ.get("DDBJ_SEARCH_API_CURSOR_SECRET")
        if env_value:
            _SECRET = env_value.encode("utf-8")
        else:
            _SECRET = secrets.token_bytes(32)
        return _SECRET


def _reset_secret_for_tests() -> None:
    """Reset the cached secret so the next call re-reads the env. Test-only."""
    global _SECRET  # noqa: PLW0603
    with _SECRET_LOCK:
        _SECRET = None


class CursorPayload(BaseModel):
    """Decoded cursor token data."""

    pit_id: str | None = Field(
        description="PIT ID. None for the first cursor generated from offset mode.",
    )
    search_after: list[Any] = Field(
        description="Sort values of the last hit (used as search_after).",
    )
    sort: list[dict[str, Any]] = Field(
        description="ES sort spec with identifier tiebreaker.",
    )
    query: dict[str, Any] = Field(
        description="Original ES query body.",
    )


def _sign(payload_bytes: bytes) -> str:
    """Compute URL-safe Base64 HMAC-SHA256 signature."""
    sig = hmac.new(_get_secret(), payload_bytes, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")


def encode_cursor(payload: CursorPayload) -> str:
    """Encode a cursor payload to a signed, URL-safe token."""
    json_bytes = json.dumps(
        payload.model_dump(),
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    b64_payload = base64.urlsafe_b64encode(json_bytes).decode("ascii")
    signature = _sign(json_bytes)
    return f"{signature}.{b64_payload}"


def compute_next_cursor(
    raw_hits: list[dict[str, Any]],
    size: int,
    total: int,
    offset: int,
    sort_with_tiebreaker: list[dict[str, Any]],
    query: dict[str, Any],
    pit_id: str | None,
) -> tuple[str | None, bool]:
    """Build (nextCursor, hasNext) from ES hits.

    - ``pit_id`` non-None: cursor mode. The total-reached guard is skipped
      because search_after does not rely on offsets.
    - ``pit_id`` None: offset mode. ``offset + size >= total`` terminates.
    - A short final page or a hit without ``sort`` also terminates.
    """
    if not raw_hits or len(raw_hits) < size:
        return (None, False)
    if pit_id is None and offset + size >= total:
        return (None, False)
    last_sort = raw_hits[-1].get("sort")
    if last_sort is None:
        return (None, False)
    payload = CursorPayload(
        pit_id=pit_id,
        search_after=last_sort,
        sort=sort_with_tiebreaker,
        query=query,
    )
    return (encode_cursor(payload), True)


def decode_cursor(token: str) -> CursorPayload:
    """Decode and verify a signed cursor token.

    Raises ValueError on invalid input (bad base64, malformed JSON,
    missing required fields, or signature mismatch).
    """
    parts = token.split(".", 1)
    if len(parts) != 2:
        raise ValueError("Invalid cursor: expected signed token format")

    signature, b64_payload = parts

    try:
        json_bytes = base64.urlsafe_b64decode(b64_payload)
    except Exception as e:
        raise ValueError(f"Invalid cursor: base64 decode failed: {e}") from e

    expected_sig = _sign(json_bytes)
    if not hmac.compare_digest(signature, expected_sig):
        raise ValueError("Invalid cursor: signature mismatch")

    try:
        data = json.loads(json_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"Invalid cursor: JSON decode failed: {e}") from e

    if not isinstance(data, dict):
        raise ValueError("Invalid cursor: expected a JSON object")  # noqa: TRY004

    try:
        return CursorPayload(**data)
    except Exception as e:
        raise ValueError(f"Invalid cursor: {e}") from e
