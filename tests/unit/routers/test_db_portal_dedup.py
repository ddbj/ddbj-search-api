"""Tests for cross-search per-DB hits de-duplication.

Covers:
- ``_dedup_lightweight_hits`` pure-function invariants (Hypothesis properties)
- ``/db-portal/cross-search`` endpoint behaviour with mocked ES responses
  containing duplicate ``(identifier, type)`` raw hits
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st

from ddbj_search_api.routers.db_portal import (
    _CROSS_SEARCH_DEDUP_OVERSHOOT,
    _dedup_lightweight_hits,
)
from ddbj_search_api.schemas.db_portal import DbPortalLightweightHit
from tests.unit.conftest import make_es_search_response

# ``DbPortalLightweightHit.type`` Literal の 16 値 (schemas/db_portal.py L562-579)
_HIT_TYPES: list[str] = [
    "bioproject",
    "biosample",
    "sra-submission",
    "sra-study",
    "sra-experiment",
    "sra-run",
    "sra-sample",
    "sra-analysis",
    "jga-study",
    "jga-dataset",
    "jga-dac",
    "jga-policy",
    "gea",
    "metabobank",
    "trad",
    "taxonomy",
]

# 鳩の巣で衝突を出すため identifier 値域を小さく絞る
_lightweight_hit_strategy = st.builds(
    DbPortalLightweightHit,
    identifier=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N")),
        min_size=1,
        max_size=4,
    ),
    type=st.sampled_from(_HIT_TYPES),
)
_hit_list_strategy = st.lists(_lightweight_hit_strategy, max_size=200)
_limit_strategy = st.integers(min_value=0, max_value=50)


# === Pure-function invariants ===


class TestDedupLightweightHitsProperties:
    """Hypothesis property tests for the de-dup helper."""

    @given(hits=_hit_list_strategy, limit=_limit_strategy)
    @settings(max_examples=200)
    def test_dedup_lightweight_hits_returns_unique_identifier_type_pairs(
        self,
        hits: list[DbPortalLightweightHit],
        limit: int,
    ) -> None:
        result = _dedup_lightweight_hits(hits, limit)
        keys = [(h.identifier, h.type) for h in result]
        assert len(keys) == len(set(keys))

    @given(hits=_hit_list_strategy, limit=_limit_strategy)
    @settings(max_examples=200)
    def test_dedup_lightweight_hits_truncates_to_limit(
        self,
        hits: list[DbPortalLightweightHit],
        limit: int,
    ) -> None:
        result = _dedup_lightweight_hits(hits, limit)
        assert len(result) <= limit

    @given(hits=_hit_list_strategy, limit=_limit_strategy)
    @settings(max_examples=200)
    def test_dedup_lightweight_hits_preserves_first_wins_order(
        self,
        hits: list[DbPortalLightweightHit],
        limit: int,
    ) -> None:
        """De-dup keeps the first occurrence of each ``(identifier, type)`` and preserves order."""
        seen: set[tuple[str, str]] = set()
        expected: list[tuple[str, str]] = []
        for hit in hits:
            key = (hit.identifier, hit.type)
            if key in seen:
                continue
            seen.add(key)
            expected.append(key)
        expected = expected[:limit]
        result = _dedup_lightweight_hits(hits, limit)
        assert [(h.identifier, h.type) for h in result] == expected


# === Targeted examples ===


class TestDedupLightweightHitsExamples:
    """Direct examples covering documented invariants."""

    def test_dedup_lightweight_hits_with_zero_limit_returns_empty_list(self) -> None:
        hit = DbPortalLightweightHit(identifier="JGAD000001", type="jga-dataset")
        assert _dedup_lightweight_hits([hit, hit], 0) == []

    def test_dedup_lightweight_hits_keeps_subtype_variants_as_distinct(self) -> None:
        """同 identifier でも type が違えば別 hit として保持 (spec L265 subtype 分散)."""
        study = DbPortalLightweightHit(identifier="JGAS000001", type="jga-study")
        dataset = DbPortalLightweightHit(identifier="JGAS000001", type="jga-dataset")
        result = _dedup_lightweight_hits([study, dataset], 5)
        assert [(h.identifier, h.type) for h in result] == [
            ("JGAS000001", "jga-study"),
            ("JGAS000001", "jga-dataset"),
        ]

    def test_dedup_lightweight_hits_collapses_same_as_alias_duplicates(self) -> None:
        """ddbj-search-converter sameAs alias 由来の完全一致重複は 1 件に集約."""
        a = DbPortalLightweightHit(identifier="JGAD000026", type="jga-dataset")
        b = DbPortalLightweightHit(identifier="JGAD000026", type="jga-dataset")
        c = DbPortalLightweightHit(identifier="JGAD000027", type="jga-dataset")
        result = _dedup_lightweight_hits([a, b, c], 5)
        assert [(h.identifier, h.type) for h in result] == [
            ("JGAD000026", "jga-dataset"),
            ("JGAD000027", "jga-dataset"),
        ]

    def test_dedup_lightweight_hits_truncates_after_dedup(self) -> None:
        """先に de-dup、後に limit で切ることを確認 (重複で容量を消費しない)."""
        hits = [
            DbPortalLightweightHit(identifier="A", type="jga-dataset"),
            DbPortalLightweightHit(identifier="A", type="jga-dataset"),
            DbPortalLightweightHit(identifier="B", type="jga-dataset"),
            DbPortalLightweightHit(identifier="C", type="jga-dataset"),
        ]
        result = _dedup_lightweight_hits(hits, 2)
        assert [h.identifier for h in result] == ["A", "B"]


# === Endpoint-level integration with mocked ES ===


def _raw_hit(identifier: str, type_: str) -> dict[str, Any]:
    return {
        "_source": {"identifier": identifier, "type": type_},
        "_id": identifier,
        "_score": 1.0,
    }


class TestCrossSearchDedupEndpoint:
    """``/db-portal/cross-search`` integrates the de-dup helper into the JGA path."""

    def test_cross_search_deduplicates_same_identifier_type_for_jga(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        """sameAs alias 投入で raw_hits に 5 件中 3 unique が来ても hits は 3 件に縮む."""
        mock_es_search_db_portal.return_value = make_es_search_response(
            hits=[
                _raw_hit("JGAD000026", "jga-dataset"),
                _raw_hit("JGAD000026", "jga-dataset"),
                _raw_hit("JGAD000027", "jga-dataset"),
                _raw_hit("JGAD000027", "jga-dataset"),
                _raw_hit("JGAD000028", "jga-dataset"),
            ],
            total=12345,
        )
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "human", "topHits": 5})
        assert resp.status_code == 200
        body = resp.json()
        jga = next(d for d in body["databases"] if d["db"] == "jga")
        pairs = [(h["identifier"], h["type"]) for h in jga["hits"]]
        assert pairs == [
            ("JGAD000026", "jga-dataset"),
            ("JGAD000027", "jga-dataset"),
            ("JGAD000028", "jga-dataset"),
        ]
        # ``count`` は raw ES total_hits をそのまま返す (de-dup 前)
        assert jga["count"] == 12345

    def test_cross_search_overshoots_es_size_to_compensate_dedup(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        """ES に投げる ``size`` は ``top_hits * _CROSS_SEARCH_DEDUP_OVERSHOOT``."""
        mock_es_search_db_portal.return_value = make_es_search_response(total=0)
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "human", "topHits": 5})
        assert resp.status_code == 200
        sizes = {call.args[2]["size"] for call in mock_es_search_db_portal.call_args_list}
        assert sizes == {5 * _CROSS_SEARCH_DEDUP_OVERSHOOT}

    def test_cross_search_with_top_hits_zero_skips_overshoot(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        """count-only モード (``topHits=0``) は ``size=0`` のまま (overshoot 不要)."""
        mock_es_search_db_portal.return_value = make_es_search_response(total=42)
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "human", "topHits": 0})
        assert resp.status_code == 200
        sizes = {call.args[2]["size"] for call in mock_es_search_db_portal.call_args_list}
        assert sizes == {0}

    def test_cross_search_keeps_subtype_variants_for_jga(
        self,
        app_with_db_portal: TestClient,
        mock_es_search_db_portal: AsyncMock,
    ) -> None:
        """subtype 分散 (jga-study + jga-dataset) は両方残る (key=(identifier, type))."""
        mock_es_search_db_portal.return_value = make_es_search_response(
            hits=[
                _raw_hit("JGAS000001", "jga-study"),
                _raw_hit("JGAS000001", "jga-dataset"),
                _raw_hit("JGAS000001", "jga-study"),
            ],
            total=3,
        )
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "human", "topHits": 5})
        assert resp.status_code == 200
        body = resp.json()
        jga = next(d for d in body["databases"] if d["db"] == "jga")
        pairs = [(h["identifier"], h["type"]) for h in jga["hits"]]
        assert pairs == [
            ("JGAS000001", "jga-study"),
            ("JGAS000001", "jga-dataset"),
        ]
