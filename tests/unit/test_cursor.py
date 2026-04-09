"""Tests for cursor token encode/decode."""

from __future__ import annotations

import base64
import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ddbj_search_api.cursor import CursorPayload, decode_cursor, encode_cursor

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

    def test_token_is_url_safe_base64(self) -> None:
        payload = _make_payload()
        token = encode_cursor(payload)
        # URL-safe base64 should not contain +, /, or newlines
        assert "+" not in token
        assert "/" not in token
        assert "\n" not in token

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


# === Decode error tests ===


class TestDecodeCursorErrors:
    def test_invalid_base64_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid cursor"):
            decode_cursor("not-valid-base64!!!")

    def test_invalid_json_raises_value_error(self) -> None:
        token = base64.urlsafe_b64encode(b"not json").decode("ascii")
        with pytest.raises(ValueError, match="JSON decode failed"):
            decode_cursor(token)

    def test_non_object_json_raises_value_error(self) -> None:
        token = base64.urlsafe_b64encode(b"[1,2,3]").decode("ascii")
        with pytest.raises(ValueError, match="expected a JSON object"):
            decode_cursor(token)

    def test_missing_required_fields_raises_value_error(self) -> None:
        data: dict[str, object] = {"pit_id": None}  # missing search_after, sort, query
        token = base64.urlsafe_b64encode(json.dumps(data).encode()).decode("ascii")
        with pytest.raises(ValueError, match="Invalid cursor"):
            decode_cursor(token)

    def test_empty_string_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            decode_cursor("")

    def test_wrong_type_search_after_raises_value_error(self) -> None:
        data: dict[str, object] = {
            "pit_id": None,
            "search_after": "not a list",
            "sort": [{"identifier": {"order": "asc"}}],
            "query": {"match_all": {}},
        }
        token = base64.urlsafe_b64encode(json.dumps(data).encode()).decode("ascii")
        with pytest.raises(ValueError, match="Invalid cursor"):
            decode_cursor(token)
