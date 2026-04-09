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
import secrets
from typing import Any

from pydantic import BaseModel, Field

# Per-process secret; cursor tokens become invalid on restart, which is
# acceptable because PIT IDs expire in 5 minutes anyway.
_SECRET = secrets.token_bytes(32)


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
    sig = hmac.new(_SECRET, payload_bytes, hashlib.sha256).digest()
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
