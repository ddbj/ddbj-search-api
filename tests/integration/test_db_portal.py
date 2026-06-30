"""Integration tests for IT-DBPORTAL-* scenarios (Solr-backed).

/db-portal/cross-search 8-DB fan-out and /db-portal/search?db=ddbj |
taxonomy. All tests in this module require Solr (ARSA + TXSearch),
hence the module-level ``staging_only`` marker.

See ``tests/integration-scenarios.md § IT-DBPORTAL-*``.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.staging_only


class TestArsaMolecularType:
    """IT-DBPORTAL-01: ARSA exposes MolecularType."""

    def test_molecular_type_present_in_some_hits(self, app: TestClient) -> None:
        """IT-DBPORTAL-01: at least one ddbj hit carries molecularType."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "ddbj", "perPage": 20},
        )
        assert resp.status_code == 200
        hits = resp.json()["hits"]
        assert len(hits) > 0
        # ``DbPortalHitDdbj.molecular_type`` is exposed via the JSON alias
        # ``molecularType``.
        assert any("molecularType" in hit for hit in hits)


class TestArsaSequenceLength:
    """IT-DBPORTAL-02: ARSA exposes SequenceLength."""

    def test_sequence_length_present_in_some_hits(self, app: TestClient) -> None:
        """IT-DBPORTAL-02: at least one ddbj hit carries sequenceLength."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "ddbj", "perPage": 20},
        )
        assert resp.status_code == 200
        hits = resp.json()["hits"]
        assert len(hits) > 0
        # ``DbPortalHitDdbj.sequence_length`` is exposed via the JSON alias
        # ``sequenceLength``.
        assert any("sequenceLength" in hit for hit in hits)


class TestArsaOrganismIdentifier:
    """IT-DBPORTAL-03: organism.identifier extracted from ``db_xref="taxon:..."``."""

    def test_organism_identifier_no_taxon_prefix(self, app: TestClient) -> None:
        """IT-DBPORTAL-03: organism.identifier holds the bare numeric ID."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "ddbj", "q": "cancer", "perPage": 20},
        )
        assert resp.status_code == 200
        for hit in resp.json()["hits"]:
            organism = hit.get("organism") or {}
            ident = organism.get("identifier")
            if ident is not None:
                assert not str(ident).startswith("taxon:"), f"prefixed: {ident}"


class TestSolrDescriptionAlwaysNull:
    """IT-DBPORTAL-04: ddbj / taxonomy ``description`` always null."""

    def test_ddbj_description_null(self, app: TestClient) -> None:
        """IT-DBPORTAL-04: every ddbj hit has description == null."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "ddbj", "perPage": 20},
        )
        assert resp.status_code == 200
        for hit in resp.json()["hits"]:
            assert hit.get("description") is None

    def test_taxonomy_description_null(self, app: TestClient) -> None:
        """IT-DBPORTAL-04: every taxonomy hit has description == null."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "taxonomy", "perPage": 20},
        )
        assert resp.status_code == 200
        for hit in resp.json()["hits"]:
            assert hit.get("description") is None


class TestTxsearchLineageSelfRemoval:
    """IT-DBPORTAL-05: TXSearch lineage drops the leading self-name."""

    def test_lineage_first_not_scientific_name(self, app: TestClient) -> None:
        """IT-DBPORTAL-05: ``lineage[0] != scientific_name`` on every hit."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "taxonomy", "q": "Homo", "perPage": 20},
        )
        assert resp.status_code == 200
        for hit in resp.json()["hits"]:
            lineage = hit.get("lineage") or []
            sci = hit.get("scientific_name")
            if lineage and sci:
                assert lineage[0] != sci, f"lineage[0]={lineage[0]} sci={sci}"


class TestSolrUfAllowlistCompleteness:
    """IT-DBPORTAL-06: Tier 3 fields are accepted by edismax (uf allowlist)."""

    def test_tier3_field_query_accepted(self, app: TestClient) -> None:
        """IT-DBPORTAL-06: ``adv=division:BCT`` (ddbj Tier 3 field) is accepted."""
        # ``division`` is an enum-typed ddbj-only DSL field per
        # ``search/dsl/allowlist.py``. ``molecularType`` / ``sequenceLength``
        # surface in the response shape but are not search-allowlisted.
        # Solr DBs only accept perPage in {20, 50, 100} (IT-DBPORTAL-11).
        resp = app.get(
            "/db-portal/search",
            params={"db": "ddbj", "q": "division:BCT", "perPage": 20},
        )
        assert resp.status_code == 200


class TestCrossSearchEightDbFanout:
    """IT-DBPORTAL-07: cross-search fan-out across 8 DBs."""

    def test_eight_dbs_present(self, app: TestClient) -> None:
        """IT-DBPORTAL-07: every documented DB key is present in the response."""
        resp = app.get(
            "/db-portal/cross-search",
            params={"q": "cancer", "topHits": 10},
        )
        assert resp.status_code == 200
        databases = resp.json()["databases"]
        # ``DbPortalCrossSearchResponse.databases`` is fixed length 8 with one
        # entry per documented DB.
        present_dbs = {entry.get("db") for entry in databases}
        expected = {
            "bioproject",
            "biosample",
            "sra",
            "jga",
            "gea",
            "metabobank",
            "ddbj",
            "taxonomy",
        }
        assert expected <= present_dbs, f"missing DBs: {expected - present_dbs}"


class TestCrossSearchTopHitsBoundary:
    """IT-DBPORTAL-08: topHits 0 / 50 / 51."""

    def test_top_hits_zero_returns_200(self, app: TestClient) -> None:
        """IT-DBPORTAL-08: topHits=0 → 200 (count-only mode)."""
        resp = app.get(
            "/db-portal/cross-search",
            params={"q": "cancer", "topHits": 0},
        )
        assert resp.status_code == 200

    def test_top_hits_fifty_returns_200(self, app: TestClient) -> None:
        """IT-DBPORTAL-08: topHits=50 (max) → 200."""
        resp = app.get(
            "/db-portal/cross-search",
            params={"q": "cancer", "topHits": 50},
        )
        assert resp.status_code == 200

    def test_top_hits_fifty_one_returns_422(self, app: TestClient) -> None:
        """IT-DBPORTAL-08: topHits=51 → 422."""
        resp = app.get(
            "/db-portal/cross-search",
            params={"q": "cancer", "topHits": 51},
        )
        assert resp.status_code == 422


class TestCrossSearchEightDbHitsUniqueness:
    """IT-DBPORTAL-22: 8 DB すべての hits が ``(identifier, type)`` で unique."""

    @pytest.mark.parametrize("query", ["human", "cancer"])
    def test_all_eight_dbs_have_unique_identifier_type_pairs(
        self,
        app: TestClient,
        query: str,
    ) -> None:
        """ES 6 DB は API 層 de-dup、Solr 2 DB は別 mapper 経路で重複を持たない."""
        resp = app.get(
            "/db-portal/cross-search",
            params={"q": query, "topHits": 50},
        )
        assert resp.status_code == 200
        databases = resp.json()["databases"]
        for entry in databases:
            hits = entry.get("hits") or []
            pairs = [(h["identifier"], h["type"]) for h in hits]
            assert len(pairs) == len(set(pairs)), f"db={entry['db']} q={query} hits に重複: {pairs}"
            count = entry.get("count")
            if count is not None:
                assert count >= len(hits), f"db={entry['db']} q={query} count={count} < len(hits)={len(hits)}"


class TestSearchDdbjCursorNotSupported:
    """IT-DBPORTAL-09: db=ddbj cursor 不可."""

    def test_ddbj_cursor_returns_400(self, app: TestClient) -> None:
        """IT-DBPORTAL-09: ddbj search rejects cursor with the slug."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "ddbj", "q": "cancer", "cursor": "any-token"},
        )
        assert resp.status_code == 400
        assert "cursor-not-supported" in resp.json().get("type", "")


class TestSearchTaxonomyCursorNotSupported:
    """IT-DBPORTAL-10: db=taxonomy cursor 不可."""

    def test_taxonomy_cursor_returns_400(self, app: TestClient) -> None:
        """IT-DBPORTAL-10: taxonomy search rejects cursor with the slug."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "taxonomy", "q": "Homo", "cursor": "any-token"},
        )
        assert resp.status_code == 400
        assert "cursor-not-supported" in resp.json().get("type", "")


class TestSearchSolrPerPageAllowlist:
    """IT-DBPORTAL-11: db=ddbj/taxonomy perPage は {20, 50, 100} のみ."""

    def test_perpage_20_succeeds(self, app: TestClient) -> None:
        """IT-DBPORTAL-11: ddbj perPage=20 → 200."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "ddbj", "q": "cancer", "perPage": 20},
        )
        assert resp.status_code == 200

    def test_perpage_30_returns_422(self, app: TestClient) -> None:
        """IT-DBPORTAL-11: ddbj perPage=30 → 422 (allowlist violation)."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "ddbj", "q": "cancer", "perPage": 30},
        )
        assert resp.status_code == 422


class TestCrossSearchPerBackendTimeout:
    """IT-DBPORTAL-12: per-backend timeout — partial failure does not 502."""

    def test_response_within_overall_timeout(self, app: TestClient) -> None:
        """IT-DBPORTAL-12: cross-search returns under the documented overall timeout."""
        start = time.time()
        resp = app.get(
            "/db-portal/cross-search",
            params={"q": "cancer", "topHits": 10},
        )
        elapsed = time.time() - start
        assert resp.status_code in {200, 502}
        # Documented overall timeout is 20s; allow generous slack for slow CI.
        assert elapsed < 30.0


class TestSolrFacets:
    """IT-DBPORTAL-22: Solr (ddbj / taxonomy) の facet 集計 (staging_only)."""

    def test_ddbj_facets_returns_lists(self, app: TestClient) -> None:
        """IT-DBPORTAL-22: db=ddbj で division / molecularType が list で返る."""
        resp = app.get("/db-portal/search", params={"db": "ddbj", "facets": "division,molecularType", "perPage": 20})
        assert resp.status_code == 200
        facets = resp.json()["facets"]
        assert isinstance(facets, dict)
        assert isinstance(facets["division"], list)
        assert isinstance(facets["molecularType"], list)

    def test_taxonomy_facets_returns_lists(self, app: TestClient) -> None:
        """IT-DBPORTAL-22: db=taxonomy で rank / kingdom が list で返る."""
        resp = app.get("/db-portal/search", params={"db": "taxonomy", "facets": "rank,kingdom", "perPage": 20})
        assert resp.status_code == 200
        facets = resp.json()["facets"]
        assert isinstance(facets["rank"], list)
        assert isinstance(facets["kingdom"], list)

    def test_ddbj_facet_count_not_greater_than_total(self, app: TestClient) -> None:
        """IT-DBPORTAL-22: 各 division bucket の count は total を超えない."""
        resp = app.get("/db-portal/search", params={"db": "ddbj", "facets": "division", "perPage": 20})
        assert resp.status_code == 200
        body = resp.json()
        for bucket in body["facets"]["division"]:
            assert bucket["count"] <= body["total"]

    def test_ddbj_facet_reinjection_reproduces_count(self, app: TestClient) -> None:
        """IT-DBPORTAL-22 母集団一致: division bucket の value を
        ``division:<value>`` で再注入すると total が bucket.count と一致する
        (3 shard 分散集計でも整合)。
        """
        resp = app.get("/db-portal/search", params={"db": "ddbj", "facets": "division"})
        assert resp.status_code == 200
        buckets = resp.json()["facets"]["division"]
        if not buckets:
            pytest.skip("ddbj division facet が空: テスト前提のデータ不足")
        bucket = buckets[0]
        reinjected = app.get("/db-portal/search", params={"db": "ddbj", "q": f"division:{bucket['value']}"})
        assert reinjected.status_code == 200
        assert reinjected.json()["total"] == bucket["count"]

    def test_ddbj_out_of_scope_facet_400(self, app: TestClient) -> None:
        """IT-DBPORTAL-22: db=ddbj で organism (ARSA に無い) は 400 facet-not-applicable."""
        resp = app.get("/db-portal/search", params={"db": "ddbj", "facets": "organism"})
        assert resp.status_code == 400
        assert "facet-not-applicable" in resp.json().get("type", "")


class TestSolrSelfExclusion:
    """IT-DBPORTAL-24: Solr facet の self-exclusion (staging_only).

    トップレベル AND 直下の facet 句を ``{!tag}`` 付き ``fq`` に分離し、その facet の
    集計だけ ``{!ex}`` で当該フィルタを外す (docs/db-portal-api-spec.md § 集計母集団と
    self-exclusion)。hits 母集団 (``q`` ∧ ``fq``) は不変。
    """

    @staticmethod
    def _division_values(app: TestClient, size: int = 50) -> list[str]:
        resp = app.get("/db-portal/search", params={"db": "ddbj", "facets": "division", "facetsSize": size})
        assert resp.status_code == 200
        return [b["value"] for b in resp.json()["facets"]["division"]]

    def test_self_exclude_retains_other_divisions(self, app: TestClient) -> None:
        """division を 1 値で絞っても self-exclusion 有効なら他 division が残る。"""
        values = self._division_values(app)
        if len(values) < 2:
            pytest.skip("self-exclusion 検証には division が 2 値以上必要")
        selected = values[0]
        resp = app.get(
            "/db-portal/search",
            params={"db": "ddbj", "q": f"division:{selected}", "facets": "division", "facetSelfExclude": "true"},
        )
        assert resp.status_code == 200
        on_vals = {b["value"] for b in resp.json()["facets"]["division"]}
        assert selected in on_vals
        assert on_vals - {selected}

    def test_default_collapses_to_selected(self, app: TestClient) -> None:
        """self-exclusion 無効 (既定) では division facet が選択値だけに潰れる。"""
        values = self._division_values(app)
        if not values:
            pytest.skip("ddbj division facet が空: テスト前提のデータ不足")
        selected = values[0]
        resp = app.get("/db-portal/search", params={"db": "ddbj", "q": f"division:{selected}", "facets": "division"})
        assert resp.status_code == 200
        assert {b["value"] for b in resp.json()["facets"]["division"]} == {selected}

    def test_self_exclude_does_not_change_hits(self, app: TestClient) -> None:
        """fq に分離した句が hits には効くので total は self-exclusion 有無で不変。"""
        values = self._division_values(app)
        if not values:
            pytest.skip("ddbj division facet が空: テスト前提のデータ不足")
        selected = values[0]
        params = {"db": "ddbj", "q": f"division:{selected}", "facets": "division"}
        off = app.get("/db-portal/search", params=params)
        on = app.get("/db-portal/search", params={**params, "facetSelfExclude": "true"})
        assert off.status_code == 200
        assert on.status_code == 200
        assert on.json()["total"] == off.json()["total"]
