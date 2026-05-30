"""Integration tests for IT-DBPORTAL-13..20 (ES-only db-portal scenarios).

These scenarios exercise validation, error slugs, sort, hardLimitReached,
ES-backed cursor pagination, and Tier 3 uf allowlist completeness on
/db-portal/cross-search and /db-portal/search. Solr-dependent scenarios
live in test_db_portal.py (module-level ``staging_only``).

See ``tests/integration-scenarios.md § IT-DBPORTAL-*``.
"""

from __future__ import annotations

import itertools

import pytest
from fastapi.testclient import TestClient


class TestCrossSearchUnexpectedParameter:
    """IT-DBPORTAL-13: cross-search rejects DB-specific parameters."""

    @pytest.mark.parametrize(
        "extra",
        [
            {"db": "bioproject"},
            {"page": "1"},
            {"perPage": "20"},
            {"cursor": "any-token"},
            {"sort": "datePublished:desc"},
        ],
    )
    def test_extra_param_returns_400(self, app: TestClient, extra: dict[str, str]) -> None:
        """IT-DBPORTAL-13: any DB / pagination / sort param triggers 400."""
        params = {"q": "cancer", **extra}
        resp = app.get("/db-portal/cross-search", params=params)
        assert resp.status_code == 400, extra
        assert "unexpected-parameter" in resp.json().get("type", ""), extra


class TestSearchMissingDb:
    """IT-DBPORTAL-14: search without ``db`` returns ``missing-db``."""

    def test_missing_db_returns_400(self, app: TestClient) -> None:
        """IT-DBPORTAL-14: 400 with the slug when db is omitted."""
        resp = app.get("/db-portal/search", params={"q": "cancer"})
        assert resp.status_code == 400
        assert "missing-db" in resp.json().get("type", "")


class TestQueryAndJoin:
    """IT-DBPORTAL-15: ``q='X AND field:Y'`` → AND-joined result on both endpoints."""

    def test_cross_search_and_join_is_subset_of_free_text_only(self, app: TestClient) -> None:
        """IT-DBPORTAL-15: count(free_text AND field:value) <= count(free_text) per DB."""
        free_text_only = app.get(
            "/db-portal/cross-search",
            params={"q": "human", "topHits": 0},
        )
        assert free_text_only.status_code == 200
        combined = app.get(
            "/db-portal/cross-search",
            params={"q": "human AND title:human", "topHits": 0},
        )
        assert combined.status_code == 200
        baseline_counts = {d["db"]: d["count"] for d in free_text_only.json()["databases"]}
        for entry in combined.json()["databases"]:
            if entry["error"] is not None or entry["count"] is None:
                continue
            base = baseline_counts.get(entry["db"])
            if base is None:
                continue
            assert entry["count"] <= base, f"AND-joined count exceeds free-text-only baseline on db={entry['db']!r}"

    def test_search_and_join_is_subset_of_free_text_only_on_es_db(self, app: TestClient) -> None:
        """IT-DBPORTAL-15: ES-backed db-specific search で AND-join が free-text-only の subset."""
        free_text_only = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "human", "perPage": 20},
        )
        assert free_text_only.status_code == 200
        combined = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "q": "human AND title:human",
                "perPage": 20,
            },
        )
        assert combined.status_code == 200
        assert combined.json()["total"] <= free_text_only.json()["total"]

    def test_search_and_join_is_subset_of_field_only_on_es_db(self, app: TestClient) -> None:
        """AND-join は free-text-only / field-only 両方の subset (前後関係を両方で確認)."""
        field_only = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "title:human", "perPage": 20},
        )
        assert field_only.status_code == 200
        combined = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "q": "human AND title:human",
                "perPage": 20,
            },
        )
        assert combined.status_code == 200
        assert combined.json()["total"] <= field_only.json()["total"]


class TestSearchSortAllowlist:
    """IT-DBPORTAL-16: sort accepts only documented values."""

    @pytest.mark.parametrize("sort", ["datePublished:desc", "datePublished:asc"])
    def test_documented_sort_succeeds(self, app: TestClient, sort: str) -> None:
        """IT-DBPORTAL-16: documented sort form returns 200 (``q`` 省略 = match_all)."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "sort": sort, "perPage": 20},
        )
        assert resp.status_code == 200, sort

    def test_descending_sort_is_actually_descending(self, app: TestClient) -> None:
        """IT-DBPORTAL-16: ``datePublished:desc`` produces a non-increasing sequence."""
        resp = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "sort": "datePublished:desc",
                "perPage": 20,
            },
        )
        assert resp.status_code == 200
        dates = [hit.get("datePublished") for hit in resp.json()["hits"] if hit.get("datePublished")]
        for left, right in itertools.pairwise(dates):
            assert left >= right, f"sort broken: {left} < {right}"

    @pytest.mark.parametrize(
        "sort",
        ["identifier:asc", "datePublished:foo", "title:desc"],
    )
    def test_invalid_sort_returns_422(self, app: TestClient, sort: str) -> None:
        """IT-DBPORTAL-16: anything outside the allowlist yields 422."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "sort": sort, "perPage": 20},
        )
        assert resp.status_code == 422, sort


class TestHardLimitReachedFlag:
    """IT-DBPORTAL-17: ``hardLimitReached`` flips at the 10000-hit boundary."""

    def test_large_total_sets_flag_true(self, app: TestClient) -> None:
        """IT-DBPORTAL-17: a broad keyword (``cancer``) breaches 10000 hits."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "cancer", "perPage": 20},
        )
        assert resp.status_code == 200
        body = resp.json()
        # ``cancer`` covers tens of thousands of bioproject docs; the flag
        # must be true once total >= 10000 (api-spec.md § hardLimitReached).
        assert body["total"] >= 10000
        assert body["hardLimitReached"] is True

    def test_small_total_sets_flag_false(self, app: TestClient) -> None:
        """IT-DBPORTAL-17: a narrow query stays below 10000."""
        resp = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "q": "identifier:PRJDB42131",
                "perPage": 20,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        # Single-accession exact-match cannot reach 10000.
        assert body["total"] < 10000
        assert body["hardLimitReached"] is False


class TestSearchEsCursor:
    """IT-DBPORTAL-18: cursor pagination on ES-backed search."""

    def test_cursor_continuation_returns_distinct_hits(self, app: TestClient) -> None:
        """IT-DBPORTAL-18: page 1 ``nextCursor`` drives a disjoint page 2."""
        first = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "cancer", "perPage": 20},
        )
        assert first.status_code == 200
        first_body = first.json()
        cursor = first_body["nextCursor"]
        # Broad keyword guarantees > 1 page so a cursor is always issued.
        assert cursor

        second = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "cursor": cursor, "perPage": 20},
        )
        assert second.status_code == 200
        second_body = second.json()
        assert len(second_body["hits"]) <= 20

        first_ids = {hit["identifier"] for hit in first_body["hits"]}
        second_ids = {hit["identifier"] for hit in second_body["hits"]}
        # Cursor pagination must not repeat identifiers.
        assert first_ids.isdisjoint(second_ids)

    def test_cursor_with_search_condition_returns_400(self, app: TestClient) -> None:
        """IT-DBPORTAL-18: cursor + q on the same request is mutually exclusive."""
        first = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "cancer", "perPage": 20},
        ).json()
        cursor = first["nextCursor"]
        assert cursor

        resp = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "cursor": cursor,
                "q": "brain",
                "perPage": 20,
            },
        )
        assert resp.status_code == 400


class TestUfAllowlistCompletenessBioSample:
    """IT-DBPORTAL-19: BioSample Tier 3 (geo_loc_name) is reachable via the ES allowlist."""

    def test_geo_loc_name_filters_actually_apply(self, app: TestClient) -> None:
        """IT-DBPORTAL-19: ``adv=geo_loc_name:Japan`` shrinks ``total`` vs a broad baseline."""
        adv_resp = app.get(
            "/db-portal/search",
            params={"db": "biosample", "q": "geo_loc_name:Japan", "perPage": 20},
        )
        assert adv_resp.status_code == 200
        adv_total = adv_resp.json()["total"]
        assert adv_total > 0

        # ``q=*`` would search the literal ``*`` token (auto-phrase) and yield 0
        # hits, so a broad keyword baseline (``cancer``) is used to detect a
        # silent wrong-field fallback that would inflate adv toward baseline.
        baseline_resp = app.get(
            "/db-portal/search",
            params={"db": "biosample", "q": "cancer", "perPage": 20},
        )
        assert baseline_resp.status_code == 200
        baseline_total = baseline_resp.json()["total"]
        assert adv_total < baseline_total


class TestUfAllowlistCompletenessSraAnalysis:
    """IT-DBPORTAL-20: SRA Tier 3 (analysis_type) is reachable via the ES allowlist."""

    def test_analysis_type_filters_actually_apply(self, app: TestClient) -> None:
        """IT-DBPORTAL-20: ``adv=analysis_type:reference_alignment`` shrinks ``total`` vs a broad baseline."""
        adv_resp = app.get(
            "/db-portal/search",
            params={"db": "sra", "q": "analysis_type:reference_alignment", "perPage": 20},
        )
        assert adv_resp.status_code == 200
        adv_total = adv_resp.json()["total"]
        assert adv_total > 0

        # See IT-DBPORTAL-19 for why ``q=cancer`` is used as the broad baseline.
        baseline_resp = app.get(
            "/db-portal/search",
            params={"db": "sra", "q": "cancer", "perPage": 20},
        )
        assert baseline_resp.status_code == 200
        baseline_total = baseline_resp.json()["total"]
        assert adv_total < baseline_total


_ES_DBS_FOR_UNIQUENESS: tuple[str, ...] = (
    "sra",
    "bioproject",
    "biosample",
    "jga",
    "gea",
    "metabobank",
)


class TestCrossSearchHitsUniqueness:
    """IT-DBPORTAL-21: cross-search per-DB hits は ``(identifier, type)`` で unique.

    ddbj-search-converter の sameAs alias 投入 (同一 ``_source`` を別 ``_id`` で
    複数件投入) により ES raw hits に同 ``(identifier, type)`` が複数現れることが
    あるが、API 層で de-dup している (docs/db-portal-api-spec.md § hits 仕様)。
    JGA / SRA など subtype を持つ DB で特に効果がある。
    """

    @pytest.mark.parametrize("query", ["human", "cancer"])
    def test_es_dbs_have_unique_identifier_type_pairs(
        self,
        app: TestClient,
        query: str,
    ) -> None:
        """全 ES DB で ``(identifier, type)`` が unique、かつ ``count >= len(hits)``."""
        resp = app.get(
            "/db-portal/cross-search",
            params={"q": query, "topHits": 50},
        )
        assert resp.status_code == 200
        databases = resp.json()["databases"]
        for entry in databases:
            if entry["db"] not in _ES_DBS_FOR_UNIQUENESS:
                continue
            hits = entry.get("hits") or []
            pairs = [(h["identifier"], h["type"]) for h in hits]
            assert len(pairs) == len(set(pairs)), f"db={entry['db']} q={query} hits に重複: {pairs}"
            count = entry.get("count")
            if count is not None:
                assert count >= len(hits), f"db={entry['db']} q={query} count={count} < len(hits)={len(hits)}"

    def test_jga_top_hits_for_human_are_unique(self, app: TestClient) -> None:
        """報告再現: ``q=human topHits=5`` の JGA hits は重複なし (regression 防止)."""
        resp = app.get(
            "/db-portal/cross-search",
            params={"q": "human", "topHits": 5},
        )
        assert resp.status_code == 200
        databases = resp.json()["databases"]
        jga = next(d for d in databases if d["db"] == "jga")
        hits = jga.get("hits") or []
        # JGA は staging に「human」を含む public dataset が複数ある前提。
        # hits=0 になると uniqueness 不変条件が trivially 成立してしまい
        # regression を検出できないため、明示的に最低 1 件を要求する。
        assert len(hits) >= 1, "jga hits が空: テスト前提のデータ不足"
        pairs = [(h["identifier"], h["type"]) for h in hits]
        assert len(pairs) == len(set(pairs)), f"jga hits に重複: {pairs}"


class TestDbPortalEsFacets:
    """IT-DBPORTAL-21: ES 単一 DB / 横断の facet 集計と母集団一致."""

    def test_search_facets_param_returns_lists(self, app: TestClient) -> None:
        """IT-DBPORTAL-21: facets 指定で要求 facet が list (非 null) で返る."""
        resp = app.get("/db-portal/search", params={"db": "bioproject", "facets": "organism,objectType"})
        assert resp.status_code == 200
        facets = resp.json()["facets"]
        assert isinstance(facets, dict)
        assert isinstance(facets["organism"], list)
        assert isinstance(facets["objectType"], list)
        # Solr-only facet stays null on an ES DB.
        assert facets["division"] is None

    def test_search_without_facets_is_null(self, app: TestClient) -> None:
        """IT-DBPORTAL-21: facets 未指定では facets フィールドが null."""
        resp = app.get("/db-portal/search", params={"db": "bioproject"})
        assert resp.status_code == 200
        assert resp.json()["facets"] is None

    def test_facet_count_not_greater_than_total(self, app: TestClient) -> None:
        """IT-DBPORTAL-21: 各 facet bucket の count は total を超えない."""
        resp = app.get("/db-portal/search", params={"db": "bioproject", "facets": "organism", "perPage": 20})
        assert resp.status_code == 200
        body = resp.json()
        total = body["total"]
        for bucket in body["facets"]["organism"]:
            assert bucket["count"] <= total

    def test_organism_facet_reinjection_reproduces_count(self, app: TestClient) -> None:
        """IT-DBPORTAL-21 母集団一致: organism bucket の value を
        ``organism_id:<value>`` で再注入すると total が bucket.count と一致する
        (facet が hits と同じ status filter / public_only で集計されている)。
        """
        resp = app.get("/db-portal/search", params={"db": "bioproject", "facets": "organism"})
        assert resp.status_code == 200
        buckets = resp.json()["facets"]["organism"]
        if not buckets:
            pytest.skip("bioproject organism facet が空: テスト前提のデータ不足")
        bucket = buckets[0]
        reinjected = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": f"organism_id:{bucket['value']}"},
        )
        assert reinjected.status_code == 200
        assert reinjected.json()["total"] == bucket["count"]

    def test_cross_search_facets(self, app: TestClient) -> None:
        """IT-DBPORTAL-21: 横断は organism / type の facet を返す (ES 6 DB union)."""
        resp = app.get("/db-portal/cross-search", params={"facets": "organism,type", "topHits": 0})
        assert resp.status_code == 200
        facets = resp.json()["facets"]
        assert isinstance(facets, dict)
        assert isinstance(facets["organism"], list)
        assert isinstance(facets["type"], list)

    def test_cross_search_rejects_type_specific_facet(self, app: TestClient) -> None:
        """IT-DBPORTAL-21: 横断で type-specific facet は 400 facet-not-applicable."""
        resp = app.get("/db-portal/cross-search", params={"facets": "libraryStrategy"})
        assert resp.status_code == 400
        assert "facet-not-applicable" in resp.json().get("type", "")

    def test_single_db_out_of_scope_facet_400(self, app: TestClient) -> None:
        """IT-DBPORTAL-21: db=bioproject で package (biosample 専属) は 400."""
        resp = app.get("/db-portal/search", params={"db": "bioproject", "facets": "package"})
        assert resp.status_code == 400
        assert "facet-not-applicable" in resp.json().get("type", "")

    def test_unknown_facet_name_422(self, app: TestClient) -> None:
        """IT-DBPORTAL-21: allowlist 外 facet 名は 422."""
        resp = app.get("/db-portal/search", params={"db": "bioproject", "facets": "__not_a_facet__"})
        assert resp.status_code == 422
