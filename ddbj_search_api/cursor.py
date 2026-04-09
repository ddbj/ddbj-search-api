"""Cursor token encode/decode for cursor-based pagination.

Pure functions with no I/O. The cursor token is a URL-safe Base64-encoded
JSON string containing the state needed to resume a search_after query.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from pydantic import BaseModel, Field


class CursorPayload(BaseModel):
    """Decoded cursor token data."""

    pit_id: str | None = Field(
        description="PIT ID. None for the first cursor generated from offset mode.",
    )
    search_after: list[Any] = Field(
        description="Sort values of the last hit (used as search_after).",
    )
    sort: list[dict[str, Any]] = Field(
        description="ES sort spec with _id tiebreaker.",
    )
    query: dict[str, Any] = Field(
        description="Original ES query body.",
    )


def encode_cursor(payload: CursorPayload) -> str:
    """Encode a cursor payload to a URL-safe Base64 string."""
    json_bytes = json.dumps(
        payload.model_dump(),
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    return base64.urlsafe_b64encode(json_bytes).decode("ascii")


def decode_cursor(token: str) -> CursorPayload:
    """Decode a cursor token string into a CursorPayload.

    Raises ValueError on invalid input (bad base64, malformed JSON,
    missing required fields).
    """
    try:
        json_bytes = base64.urlsafe_b64decode(token)
    except Exception as e:
        raise ValueError(f"Invalid cursor: base64 decode failed: {e}") from e

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
