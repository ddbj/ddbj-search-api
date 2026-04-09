"""Tests for cursor token encode/decode with HMAC signing."""

from __future__ import annotations

import base64
import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ddbj_search_api.cursor import CursorPayload, _sign, decode_cursor, encode_cursor

# === Fixtures ===


def _make_payload(
    pit_id: str | None = None,
    search_after: list[object] | None = None,
    sort: list[dict[str, object]] | None = None,
    query: dict[str, object] | None = None,
) -> CursorPayload:
    return CursorPayload(
        pit_id=pit_id,
        search_after=search_after or ["2026-01-15", "SAMN12345678"],
        sort=sort or [{"datePublished": {"order": "desc"}}, {"identifier": {"order": "asc"}}],
        query=query or {"match_all": {}},
    )


# === Hypothesis strategies ===


@st.composite
def es_sort_entry(draw: st.DrawFn) -> dict[str, dict[str, str]]:
    field = draw(st.sampled_from(["_score", "identifier", "datePublished", "dateModified"]))
    order = draw(st.sampled_from(["asc", "desc"]))

    return {field: {"order": order}}


# search_after values can be strings, ints, floats, or None
search_after_value = st.one_of(
    st.text(min_size=0, max_size=50),
    st.integers(min_value=-1000000, max_value=1000000),
    st.floats(allow_nan=False, allow_infinity=False),
    st.none(),
)

cursor_payloads = st.builds(
    CursorPayload,
    pit_id=st.one_of(st.none(), st.text(min_size=1, max_size=100)),
    search_after=st.lists(search_after_value, min_size=1, max_size=5),
    sort=st.lists(es_sort_entry(), min_size=1, max_size=3),
    query=st.just({"match_all": {}}),
)


# === Round-trip tests ===


class TestEncodeDecode:
    def test_round_trip_with_pit_id(self) -> None:
        payload = _make_payload(pit_id="abc123")
        token = encode_cursor(payload)
        decoded = decode_cursor(token)
        assert decoded == payload

    def test_round_trip_without_pit_id(self) -> None:
        payload = _make_payload(pit_id=None)
        token = encode_cursor(payload)
        decoded = decode_cursor(token)
        assert decoded.pit_id is None
        assert decoded.search_after == payload.search_after
        assert decoded.sort == payload.sort
        assert decoded.query == payload.query

    def test_token_contains_signature_dot_payload(self) -> None:
        payload = _make_payload()
        token = encode_cursor(payload)
        assert "." in token
        parts = token.split(".", 1)
        assert len(parts) == 2
        # Signature part should not contain dots
        assert "." not in parts[0]

    def test_round_trip_with_float_score(self) -> None:
        payload = _make_payload(search_after=[1.5, "doc_id_123"])
        token = encode_cursor(payload)
        decoded = decode_cursor(token)
        assert decoded.search_after[0] == pytest.approx(1.5)
        assert decoded.search_after[1] == "doc_id_123"

    @given(payload=cursor_payloads)
    @settings(max_examples=50)
    def test_round_trip_property(self, payload: CursorPayload) -> None:
        token = encode_cursor(payload)
        decoded = decode_cursor(token)
        assert decoded.pit_id == payload.pit_id
        assert decoded.sort == payload.sort
        assert decoded.query == payload.query
        assert len(decoded.search_after) == len(payload.search_after)


# === Signature verification tests ===


class TestCursorSignature:
    def test_tampered_payload_raises(self) -> None:
        """Modifying the payload portion invalidates the signature."""
        payload = _make_payload(pit_id="original")
        token = encode_cursor(payload)
        sig, b64_payload = token.split(".", 1)

        # Tamper with the payload
        json_bytes = base64.urlsafe_b64decode(b64_payload)
        data = json.loads(json_bytes)
        data["query"] = {"match_all": {}, "injected": True}
        tampered_bytes = json.dumps(data, separators=(",", ":")).encode()
        tampered_b64 = base64.urlsafe_b64encode(tampered_bytes).decode("ascii")
        tampered_token = f"{sig}.{tampered_b64}"

        with pytest.raises(ValueError, match="signature mismatch"):
            decode_cursor(tampered_token)

    def test_forged_token_raises(self) -> None:
        """A completely forged token fails signature verification."""
        forged_data: dict[str, object] = {
            "pit_id": None,
            "search_after": ["2026-01-01", "FAKE"],
            "sort": [{"_score": {"order": "desc"}}],
            "query": {"match_all": {}},
        }
        forged_bytes = json.dumps(forged_data).encode()
        forged_b64 = base64.urlsafe_b64encode(forged_bytes).decode("ascii")
        fake_sig = base64.urlsafe_b64encode(b"fakesignature").decode("ascii").rstrip("=")
        forged_token = f"{fake_sig}.{forged_b64}"

        with pytest.raises(ValueError, match="signature mismatch"):
            decode_cursor(forged_token)

    def test_tampered_signature_raises(self) -> None:
        """Replacing the signature with a different one fails."""
        payload = _make_payload()
        token = encode_cursor(payload)
        _, b64_payload = token.split(".", 1)

        bad_sig = base64.urlsafe_b64encode(b"x" * 32).decode("ascii").rstrip("=")
        bad_token = f"{bad_sig}.{b64_payload}"

        with pytest.raises(ValueError, match="signature mismatch"):
            decode_cursor(bad_token)


# === Decode error tests ===


class TestDecodeCursorErrors:
    def test_no_dot_separator_raises(self) -> None:
        with pytest.raises(ValueError, match="signed token format"):
            decode_cursor("nodothere")

    def test_invalid_base64_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid cursor"):
            decode_cursor("sig.not-valid-base64!!!")

    def test_invalid_json_raises_value_error(self) -> None:
        b64 = base64.urlsafe_b64encode(b"not json").decode("ascii")
        sig = _sign(b"not json")
        with pytest.raises(ValueError, match="JSON decode failed"):
            decode_cursor(f"{sig}.{b64}")

    def test_missing_required_fields_raises_value_error(self) -> None:
        data: dict[str, object] = {"pit_id": None}
        payload_bytes = json.dumps(data).encode()
        b64 = base64.urlsafe_b64encode(payload_bytes).decode("ascii")
        sig = _sign(payload_bytes)
        with pytest.raises(ValueError, match="Invalid cursor"):
            decode_cursor(f"{sig}.{b64}")

    def test_empty_string_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            decode_cursor("")
