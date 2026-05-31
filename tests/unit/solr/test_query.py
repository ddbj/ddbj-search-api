"""Tests for ddbj_search_api.solr.query.

Covers Solr-specific edismax param builders for ARSA and TXSearch. The
caller (router) builds the ``q`` string via :mod:`ddbj_search_api.search.dsl`
(``compile_to_solr`` / ``compile_free_text_solr``); these tests verify the
remaining qf / fl / uf / sort / start / rows wiring around it.
"""

from __future__ import annotations

from typing import Any

from ddbj_search_api.search.dsl import arsa_uf_fields, parse, txsearch_uf_fields, validate
from ddbj_search_api.search.dsl.allowlist import FIELD_TYPES
from ddbj_search_api.search.dsl.compiler_solr import _ARSA_FIELD_MAP, _TXSEARCH_FIELD_MAP
from ddbj_search_api.solr.query import (
    DB_PORTAL_SOLR_FACET_NAMES,
    arsa_facet_dsl_field_map,
    arsa_facet_field_map,
    build_arsa_request_params,
    build_solr_facet_plan,
    build_txsearch_request_params,
    txsearch_facet_dsl_field_map,
    txsearch_facet_field_map,
)


def _single_ast(q: str) -> Any:
    ast = parse(q)
    validate(ast, mode="single")
    return ast


# === Unified request params ===


class TestBuildArsaRequestParams:
    """compile_to_solr で生成した ``q`` を ARSA Solr params に組み立てる."""

    def test_q_passed_through(self) -> None:
        p = build_arsa_request_params(
            q='"cancer"',
            page=1,
            per_page=20,
            sort=None,
            shards=None,
            with_uf=False,
        )
        assert p["q"] == '"cancer"'

    def test_def_type_edismax(self) -> None:
        p = build_arsa_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort=None,
            shards=None,
            with_uf=False,
        )
        assert p["defType"] == "edismax"

    def test_qf_and_fl_constants(self) -> None:
        p = build_arsa_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort=None,
            shards=None,
            with_uf=False,
        )
        assert "AllText^0.1" in p["qf"]
        assert "PrimaryAccessionNumber" in p["fl"]

    def test_without_uf_omits_param(self) -> None:
        """FreeText 単独 (with_uf=False) では uf を付けない."""
        p = build_arsa_request_params(
            q='"cancer"',
            page=1,
            per_page=20,
            sort=None,
            shards=None,
            with_uf=False,
        )
        assert "uf" not in p

    def test_with_uf_adds_allowlist(self) -> None:
        """adv 含む AST (with_uf=True) では uf に allowlist を付与."""
        p = build_arsa_request_params(
            q='Division:"BCT"',
            page=1,
            per_page=20,
            sort=None,
            shards=None,
            with_uf=True,
        )
        assert "uf" in p
        uf_set = set(p["uf"].split())
        for field in arsa_uf_fields():
            assert field in uf_set

    def test_start_rows_pagination(self) -> None:
        p = build_arsa_request_params(
            q="*:*",
            page=3,
            per_page=20,
            sort=None,
            shards=None,
            with_uf=False,
        )
        assert p["start"] == "40"
        assert p["rows"] == "20"

    def test_sort_passes_through_allowlist(self) -> None:
        p = build_arsa_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort="datePublished:desc",
            shards=None,
            with_uf=False,
        )
        assert p["sort"] == "Date desc"

    def test_sort_outside_allowlist_omits(self) -> None:
        p = build_arsa_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort="bogus:desc",
            shards=None,
            with_uf=False,
        )
        assert "sort" not in p

    def test_shards_included_when_provided(self) -> None:
        p = build_arsa_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort=None,
            shards="a:1/solr/c,a:2/solr/c",
            with_uf=False,
        )
        assert p["shards"] == "a:1/solr/c,a:2/solr/c"

    def test_no_shards_when_blank(self) -> None:
        p = build_arsa_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort=None,
            shards="  ",
            with_uf=False,
        )
        assert "shards" not in p

    def test_wt_json(self) -> None:
        p = build_arsa_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort=None,
            shards=None,
            with_uf=False,
        )
        assert p["wt"] == "json"


class TestBuildTxsearchRequestParams:
    def test_q_passed_through(self) -> None:
        p = build_txsearch_request_params(
            q='"Homo"',
            page=1,
            per_page=20,
            sort=None,
            with_uf=False,
        )
        assert p["q"] == '"Homo"'

    def test_def_type_edismax(self) -> None:
        p = build_txsearch_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort=None,
            with_uf=False,
        )
        assert p["defType"] == "edismax"

    def test_without_uf_omits_param(self) -> None:
        p = build_txsearch_request_params(
            q='"x"',
            page=1,
            per_page=20,
            sort=None,
            with_uf=False,
        )
        assert "uf" not in p

    def test_with_uf_adds_allowlist(self) -> None:
        p = build_txsearch_request_params(
            q='rank:"species"',
            page=1,
            per_page=20,
            sort=None,
            with_uf=True,
        )
        assert "uf" in p
        uf_set = set(p["uf"].split())
        for field in txsearch_uf_fields():
            assert field in uf_set

    def test_sort_silently_ignored(self) -> None:
        """Taxonomy に日付なし → sort は無視."""
        p = build_txsearch_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort="datePublished:desc",
            with_uf=False,
        )
        assert "sort" not in p

    def test_no_shards_param(self) -> None:
        p = build_txsearch_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort=None,
            with_uf=False,
        )
        assert "shards" not in p

    def test_start_rows_pagination(self) -> None:
        p = build_txsearch_request_params(
            q="*:*",
            page=2,
            per_page=50,
            sort=None,
            with_uf=False,
        )
        assert p["start"] == "50"
        assert p["rows"] == "50"


# === db-portal facet 集計 (terms faceting params + field maps) ===


class TestArsaFacetParams:
    """``facet_fields`` 指定で terms faceting params を相乗りさせる."""

    def test_no_facet_params_when_empty(self) -> None:
        """facet_fields 未指定 (既定) では facet 系 key を一切付けない."""
        p = build_arsa_request_params(q="*:*", page=1, per_page=20, sort=None, shards=None, with_uf=False)
        assert "facet" not in p
        assert "facet.field" not in p
        assert "facet.mincount" not in p
        assert "facet.limit" not in p

    def test_facet_params_present(self) -> None:
        p = build_arsa_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort=None,
            shards=None,
            with_uf=False,
            facet_fields=["Division", "MolecularType"],
            facet_limit=50,
        )
        assert p["facet"] == "true"
        assert p["facet.mincount"] == "1"
        assert p["facet.limit"] == "50"

    def test_facet_field_is_list_for_multivalue(self) -> None:
        """facet.field は list で保持する (httpx が repeated param に展開する).

        str に collapse すると Solr が単一フィールド名として誤解釈し、
        2 つ目以降の facet が黙って欠落する。
        """
        p = build_arsa_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort=None,
            shards=None,
            with_uf=False,
            facet_fields=["Division", "MolecularType"],
            facet_limit=100,
        )
        assert p["facet.field"] == ["Division", "MolecularType"]
        assert isinstance(p["facet.field"], list)

    def test_facet_limit_default_is_100(self) -> None:
        p = build_arsa_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort=None,
            shards=None,
            with_uf=False,
            facet_fields=["Division"],
        )
        assert p["facet.limit"] == "100"

    def test_facets_coexist_with_shards(self) -> None:
        """8 shard fan-out (shards) と facet params が両立する (分散集計)."""
        p = build_arsa_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort=None,
            shards="a:1/solr/c,a:2/solr/c",
            with_uf=False,
            facet_fields=["Division"],
            facet_limit=100,
        )
        assert p["shards"] == "a:1/solr/c,a:2/solr/c"
        assert p["facet"] == "true"
        assert p["facet.field"] == ["Division"]


class TestTxsearchFacetParams:
    def test_no_facet_params_when_empty(self) -> None:
        p = build_txsearch_request_params(q="*:*", page=1, per_page=20, sort=None, with_uf=False)
        assert "facet" not in p
        assert "facet.field" not in p

    def test_facet_params_present(self) -> None:
        p = build_txsearch_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort=None,
            with_uf=False,
            facet_fields=["rank", "kingdom"],
            facet_limit=25,
        )
        assert p["facet"] == "true"
        assert p["facet.field"] == ["rank", "kingdom"]
        assert p["facet.mincount"] == "1"
        assert p["facet.limit"] == "25"


class TestDbPortalSolrFacetMaps:
    """facet wire-name → Solr field map と DSL compiler との整合."""

    def test_arsa_map_content(self) -> None:
        assert arsa_facet_field_map() == {"division": "Division", "molecularType": "MolecularType"}

    def test_txsearch_map_content(self) -> None:
        assert txsearch_facet_field_map() == {"rank": "rank", "kingdom": "kingdom"}

    def test_db_portal_solr_facet_names(self) -> None:
        assert frozenset({"division", "molecularType", "rank", "kingdom"}) == DB_PORTAL_SOLR_FACET_NAMES

    def test_maps_return_independent_copies(self) -> None:
        m = arsa_facet_field_map()
        m["injected"] = "Bogus"
        assert "injected" not in arsa_facet_field_map()

    def test_arsa_facet_field_matches_compiler_field(self) -> None:
        """facet.field は同 DSL field の compiler マップ先と一致しなければならない.

        ズレると facet bucket の value を ``molecular_type:<value>`` 等で
        再注入したとき別 Solr field を叩き、母集団が facet と食い違う。
        """
        facet_to_dsl = arsa_facet_dsl_field_map()
        for facet_name, solr_field in arsa_facet_field_map().items():
            dsl_field = facet_to_dsl[facet_name]
            assert solr_field in _ARSA_FIELD_MAP[dsl_field]

    def test_txsearch_facet_field_matches_compiler_field(self) -> None:
        facet_to_dsl = txsearch_facet_dsl_field_map()
        for facet_name, solr_field in txsearch_facet_field_map().items():
            dsl_field = facet_to_dsl[facet_name]
            assert solr_field in _TXSEARCH_FIELD_MAP[dsl_field]

    def test_dsl_field_maps_align_with_facet_field_maps(self) -> None:
        """逆引き表のキーは facet field map と 1:1、値は DSL allowlist に存在 (self-exclusion)."""
        assert set(arsa_facet_dsl_field_map()) == set(arsa_facet_field_map())
        assert set(txsearch_facet_dsl_field_map()) == set(txsearch_facet_field_map())
        dsl_fields = set(arsa_facet_dsl_field_map().values()) | set(txsearch_facet_dsl_field_map().values())
        assert dsl_fields <= set(FIELD_TYPES)

    def test_dsl_field_maps_return_independent_copies(self) -> None:
        m = arsa_facet_dsl_field_map()
        m["injected"] = "bogus"
        assert "injected" not in arsa_facet_dsl_field_map()


# === fq passthrough (self-exclusion tagged filters) ===


class TestRequestParamsFq:
    def test_arsa_fq_default_absent(self) -> None:
        p = build_arsa_request_params(q="*:*", page=1, per_page=20, sort=None, shards=None, with_uf=False)
        assert "fq" not in p

    def test_arsa_fq_passed_as_list(self) -> None:
        p = build_arsa_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort=None,
            shards=None,
            with_uf=False,
            fq=['{!tag=selfex_division}Division:"BCT"'],
        )
        assert p["fq"] == ['{!tag=selfex_division}Division:"BCT"']
        assert isinstance(p["fq"], list)

    def test_txsearch_fq_passed_as_list(self) -> None:
        p = build_txsearch_request_params(
            q="*:*",
            page=1,
            per_page=20,
            sort=None,
            with_uf=False,
            fq=['{!tag=selfex_rank}rank:"species"'],
        )
        assert p["fq"] == ['{!tag=selfex_rank}rank:"species"']


# === build_solr_facet_plan (self-exclusion) ===


class TestBuildSolrFacetPlanBaseline:
    """self_exclude=False / no facets: q = compile(full ast)、fq 空、facet.field は素の Solr field."""

    def test_no_facets_compiles_full_q_no_fq(self) -> None:
        ast = _single_ast('division:BCT AND molecular_type:"genomic DNA"')
        plan = build_solr_facet_plan(ast, None, dialect="arsa")
        assert "Division" in plan.q
        assert "MolecularType" in plan.q
        assert plan.fq == []
        assert plan.facet_fields == []
        assert plan.name_to_field == {}

    def test_none_ast_yields_star_q(self) -> None:
        plan = build_solr_facet_plan(None, ["division"], dialect="arsa")
        assert plan.q == "*:*"
        assert plan.facet_fields == ["Division"]
        assert plan.fq == []

    def test_self_exclude_off_keeps_clause_in_q_plain_facet(self) -> None:
        ast = _single_ast("division:BCT")
        plan = build_solr_facet_plan(ast, ["division"], dialect="arsa", self_exclude=False)
        assert 'Division:"BCT"' in plan.q
        assert plan.facet_fields == ["Division"]
        assert plan.fq == []


class TestBuildSolrFacetPlanSelfExclude:
    """self_exclude=True: top-level AND 直下の facet 句を tagged fq に分離し
    facet.field を {!ex} で外す (hits 母集団 = q ∧ fq は不変)."""

    def test_single_select_splits_clause_to_tagged_fq(self) -> None:
        ast = _single_ast('division:BCT AND molecular_type:"genomic DNA"')
        plan = build_solr_facet_plan(ast, ["division", "molecularType"], dialect="arsa", self_exclude=True)
        # 両 field とも facet なので q は全クローズが分離されて *:* になる。
        assert plan.q == "*:*"
        assert plan.fq == [
            '{!tag=selfex_division}Division:"BCT"',
            '{!tag=selfex_molecular_type}MolecularType:"genomic DNA"',
        ]
        assert plan.facet_fields == [
            "{!ex=selfex_division key=Division}Division",
            "{!ex=selfex_molecular_type key=MolecularType}MolecularType",
        ]

    def test_excluded_facet_uses_ex_others_plain(self) -> None:
        """facet にしない field (title) は q に残り、facet 句だけ fq に出る."""
        ast = _single_ast("division:BCT AND title:cancer")
        plan = build_solr_facet_plan(ast, ["division"], dialect="arsa", self_exclude=True)
        assert 'Definition:"cancer"' in plan.q
        assert 'Division:"BCT"' not in plan.q
        assert plan.fq == ['{!tag=selfex_division}Division:"BCT"']
        assert plan.facet_fields == ["{!ex=selfex_division key=Division}Division"]

    def test_facet_without_matching_clause_stays_plain(self) -> None:
        """q に該当句が無い facet (molecularType) は {!ex} を付けず素の field のまま."""
        ast = _single_ast("division:BCT")
        plan = build_solr_facet_plan(ast, ["division", "molecularType"], dialect="arsa", self_exclude=True)
        assert plan.fq == ['{!tag=selfex_division}Division:"BCT"']
        assert plan.facet_fields == [
            "{!ex=selfex_division key=Division}Division",
            "MolecularType",
        ]

    def test_or_multiselect_not_split_degrades(self) -> None:
        """OR multi-select は top-level AND 直下の FieldClause ではないので分離されず、
        その facet は {!ex} 無し (degrade)。他 facet の self-exclusion は効く."""
        ast = _single_ast('(division:BCT OR division:GSS) AND molecular_type:"genomic DNA"')
        plan = build_solr_facet_plan(ast, ["division", "molecularType"], dialect="arsa", self_exclude=True)
        assert "Division" in plan.q  # OR 群は q に残る
        assert plan.facet_fields[0] == "Division"  # division は degrade
        assert plan.facet_fields[1] == "{!ex=selfex_molecular_type key=MolecularType}MolecularType"
        assert plan.fq == ['{!tag=selfex_molecular_type}MolecularType:"genomic DNA"']

    def test_population_invariant_q_and_fq_recover_full_query(self) -> None:
        """分離した fq を q に AND し戻すと self_exclude=False の q と論理的に一致する
        (hits 母集団は不変): 構造比較ではなく field/value の集合で確認."""
        ast = _single_ast('division:BCT AND molecular_type:"genomic DNA"')
        on = build_solr_facet_plan(ast, ["division", "molecularType"], dialect="arsa", self_exclude=True)
        off = build_solr_facet_plan(ast, ["division", "molecularType"], dialect="arsa", self_exclude=False)
        # off の q に含まれる各 Solr 句が、on では q または fq のどこかに現れる。
        for token in ('Division:"BCT"', 'MolecularType:"genomic DNA"'):
            assert token in off.q
            assert token in on.q or any(token in f for f in on.fq)

    def test_txsearch_dialect_uses_txsearch_fields(self) -> None:
        ast = _single_ast("rank:species AND kingdom:Bacteria")
        plan = build_solr_facet_plan(ast, ["rank", "kingdom"], dialect="txsearch", self_exclude=True)
        assert plan.facet_fields == [
            "{!ex=selfex_rank key=rank}rank",
            "{!ex=selfex_kingdom key=kingdom}kingdom",
        ]
        assert plan.name_to_field == {"rank": "rank", "kingdom": "kingdom"}

    def test_self_exclude_with_none_ast_is_noop(self) -> None:
        plan = build_solr_facet_plan(None, ["division"], dialect="arsa", self_exclude=True)
        assert plan.q == "*:*"
        assert plan.fq == []
        assert plan.facet_fields == ["Division"]
