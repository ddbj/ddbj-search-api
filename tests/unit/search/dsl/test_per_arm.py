"""Tests for per-arm AST reduction (per_arm.reduce_ast_for_db).

availability の SSOT (allowlist.field_availability) と簡約ロジックの不変条件を固定する。
特に「簡約後 AST に non-applicable leaf が残らない」(= compile が RuntimeError にならない)
を property として担保し、TXSearch の no-match ~全件化バグの再発を防ぐ。
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ddbj_search_api.search.dsl import parse
from ddbj_search_api.search.dsl.allowlist import ALL_ALLOWED_FIELDS, field_availability
from ddbj_search_api.search.dsl.compiler_solr import (
    _ARSA_FIELD_MAP,
    _TXSEARCH_FIELD_MAP,
    compile_to_solr,
)
from ddbj_search_api.search.dsl.per_arm import reduce_ast_for_db


class TestAvailableField:
    def test_available_field_kept_taxonomy(self) -> None:
        r = reduce_ast_for_db(parse("organism_id:9606"), "taxonomy")
        assert r.applicable
        assert not r.always_zero
        assert r.ast is not None
        assert compile_to_solr(r.ast, dialect="txsearch") == 'tax_id:"9606"'

    def test_available_field_kept_es(self) -> None:
        r = reduce_ast_for_db(parse("title:cancer"), "biosample")
        assert r.applicable
        assert not r.always_zero
        assert r.ast is not None

    def test_freetext_kept(self) -> None:
        r = reduce_ast_for_db(parse("cancer"), "taxonomy")
        assert r.applicable
        assert r.ast is not None

    def test_none_ast_is_applicable_all(self) -> None:
        r = reduce_ast_for_db(None, "taxonomy")
        assert r.applicable
        assert r.ast is None
        assert not r.always_zero


class TestNonApplicable:
    def test_date_published_non_applicable_taxonomy(self) -> None:
        r = reduce_ast_for_db(parse("date_published:2024-01-01"), "taxonomy")
        assert not r.applicable

    def test_publication_non_applicable_biosample(self) -> None:
        r = reduce_ast_for_db(parse("publication:cancer"), "biosample")
        assert not r.applicable

    def test_name_non_applicable_trad(self) -> None:
        r = reduce_ast_for_db(parse("name:foo"), "trad")
        assert not r.applicable

    def test_and_with_non_applicable_marks_inapplicable(self) -> None:
        # date_published 非対応を含む AND は arm 全体を対象外にする (嘘件数回避)。
        r = reduce_ast_for_db(parse("date_published:2024-01-01 AND organism_id:9606"), "taxonomy")
        assert not r.applicable

    def test_or_with_non_applicable_marks_inapplicable(self) -> None:
        r = reduce_ast_for_db(parse("title:human OR date_published:2024-01-01"), "taxonomy")
        assert not r.applicable


class TestUnavailableFields:
    """対象外 arm が原因 field 名を出現順 (重複除去) で持つことを固定する。"""

    def test_single_na_leaf(self) -> None:
        r = reduce_ast_for_db(parse("publication:cancer"), "biosample")
        assert not r.applicable
        assert r.unavailable_fields == ("publication",)

    def test_and_lists_na_fields_in_order(self) -> None:
        r = reduce_ast_for_db(parse("date_modified:2024-01-01 AND publication:cancer"), "taxonomy")
        assert not r.applicable
        assert r.unavailable_fields == ("date_modified", "publication")

    def test_or_lists_na_fields_in_order(self) -> None:
        # AND と逆順の query で出現順がそのまま保たれることを固定する。
        r = reduce_ast_for_db(parse("publication:cancer OR date_modified:2024-01-01"), "taxonomy")
        assert not r.applicable
        assert r.unavailable_fields == ("publication", "date_modified")

    def test_duplicate_na_field_deduped(self) -> None:
        r = reduce_ast_for_db(parse("publication:cancer AND publication:lung"), "taxonomy")
        assert not r.applicable
        assert r.unavailable_fields == ("publication",)

    def test_nested_boolop_propagates_na_fields_in_order(self) -> None:
        # 内側 BoolOp の na_fields が外へ順序保持で伝播し、available な organism_id は含まれない。
        r = reduce_ast_for_db(
            parse("(publication:cancer OR date_modified:2024-01-01) AND organism_id:9606"),
            "taxonomy",
        )
        assert not r.applicable
        assert r.unavailable_fields == ("publication", "date_modified")

    def test_not_propagates_na_field(self) -> None:
        r = reduce_ast_for_db(parse("NOT publication:cancer"), "taxonomy")
        assert not r.applicable
        assert r.unavailable_fields == ("publication",)

    def test_available_only_has_empty_unavailable(self) -> None:
        r = reduce_ast_for_db(parse("organism_id:9606"), "taxonomy")
        assert r.applicable
        assert r.unavailable_fields == ()

    def test_always_zero_has_empty_unavailable(self) -> None:
        r = reduce_ast_for_db(parse("accessibility:controlled-access"), "taxonomy")
        assert r.always_zero
        assert r.unavailable_fields == ()

    def test_fixed_value_true_has_empty_unavailable(self) -> None:
        r = reduce_ast_for_db(parse("accessibility:public-access"), "taxonomy")
        assert r.applicable
        assert r.unavailable_fields == ()

    def test_none_ast_has_empty_unavailable(self) -> None:
        r = reduce_ast_for_db(None, "taxonomy")
        assert r.applicable
        assert r.unavailable_fields == ()


# taxonomy で非対応 / 対応な field の最小 query 断片 (value 型に合わせる)。PBT で組み合わせる。
_TAXONOMY_NA_CLAUSES = {
    "publication": "publication:cancer",
    "date_published": "date_published:2020-01-01",
    "date_modified": "date_modified:2021-06-15",
    "submitter": "submitter:smith",
    "name": "name:foo",
}
_TAXONOMY_AVAILABLE_CLAUSES = {
    "organism_id": "organism_id:9606",
    "title": "title:genome",
    "organism_name": "organism_name:human",
    "description": "description:bacteria",
}


class TestUnavailableFieldsInvariants:
    """``applicable=False`` ⟺ ``unavailable_fields`` 非空 の双条件を property で固定する。"""

    @given(
        na=st.lists(st.sampled_from(sorted(_TAXONOMY_NA_CLAUSES)), min_size=1, max_size=3),
        available=st.lists(st.sampled_from(sorted(_TAXONOMY_AVAILABLE_CLAUSES)), max_size=3),
        op=st.sampled_from([" AND ", " OR "]),
    )
    def test_pbt_na_present_is_inapplicable_with_exact_na_fields(
        self,
        na: list[str],
        available: list[str],
        op: str,
    ) -> None:
        clauses = [_TAXONOMY_NA_CLAUSES[f] for f in na] + [_TAXONOMY_AVAILABLE_CLAUSES[f] for f in available]
        r = reduce_ast_for_db(parse(op.join(clauses)), "taxonomy")
        assert not r.applicable
        # 原因は query に現れた na field のみ、重複なし
        assert set(r.unavailable_fields) == set(na)
        assert len(r.unavailable_fields) == len(set(r.unavailable_fields))

    @given(
        available=st.lists(st.sampled_from(sorted(_TAXONOMY_AVAILABLE_CLAUSES)), min_size=1, max_size=4),
        op=st.sampled_from([" AND ", " OR "]),
    )
    def test_pbt_available_only_is_applicable_with_empty_unavailable(
        self,
        available: list[str],
        op: str,
    ) -> None:
        clauses = [_TAXONOMY_AVAILABLE_CLAUSES[f] for f in available]
        r = reduce_ast_for_db(parse(op.join(clauses)), "taxonomy")
        assert r.applicable
        assert r.unavailable_fields == ()


class TestPublicationTradAvailable:
    def test_publication_maps_reference_title_on_trad(self) -> None:
        r = reduce_ast_for_db(parse("publication:cancer"), "trad")
        assert r.applicable
        assert r.ast is not None
        assert compile_to_solr(r.ast, dialect="arsa") == '(ReferenceTitle:"cancer" OR ReferenceTitle:cancer*)'


class TestFixedValue:
    def test_fixed_value_match_is_always_true(self) -> None:
        # taxonomy の accessibility は public-access 固定。一致 → 恒真 → 全件 (ast=None)。
        r = reduce_ast_for_db(parse("accessibility:public-access"), "taxonomy")
        assert r.applicable
        assert not r.always_zero
        assert r.ast is None

    def test_fixed_value_mismatch_is_always_zero(self) -> None:
        r = reduce_ast_for_db(parse("accessibility:controlled-access"), "taxonomy")
        assert r.applicable
        assert r.always_zero

    def test_and_drops_fixed_value_true_clause(self) -> None:
        r = reduce_ast_for_db(parse("accessibility:public-access AND organism_id:9606"), "taxonomy")
        assert r.applicable
        assert not r.always_zero
        assert r.ast is not None
        assert compile_to_solr(r.ast, dialect="txsearch") == 'tax_id:"9606"'

    def test_and_with_fixed_value_false_is_always_zero(self) -> None:
        r = reduce_ast_for_db(parse("accessibility:controlled-access AND organism_id:9606"), "taxonomy")
        assert r.always_zero

    def test_not_fixed_value_true_becomes_always_zero(self) -> None:
        # NOT (恒真) → 恒偽 → 0 件。
        r = reduce_ast_for_db(parse("NOT accessibility:public-access"), "taxonomy")
        assert r.applicable
        assert r.always_zero

    def test_not_fixed_value_false_is_all(self) -> None:
        # NOT (恒偽) → 恒真 → 全件。
        r = reduce_ast_for_db(parse("NOT accessibility:controlled-access"), "taxonomy")
        assert r.applicable
        assert not r.always_zero
        assert r.ast is None

    def test_fixed_value_field_is_available_on_es(self) -> None:
        # ES 6DB では accessibility は実 field。固定値ではなく available なので keep。
        r = reduce_ast_for_db(parse("accessibility:public-access"), "biosample")
        assert r.applicable
        assert not r.always_zero
        assert r.ast is not None


class TestAvailabilityMatchesSolrFieldMap:
    """field_availability (SSOT) と compiler_solr の field map の drift 防止.

    Solr arm で available とされる field は必ず field map にマップが存在し、その逆も成り立つ。
    これが崩れると簡約後 AST が compile で RuntimeError になる / 非対応 field が漏れる。

    唯一の例外は organism_id@trad: available だが ARSA に直接 field は無く、trad arm は
    organism_rewrite (TXSearch 解決) で organism_name に置換してから compile する。等価則の対象外。
    """

    @pytest.mark.parametrize("field", sorted(ALL_ALLOWED_FIELDS - {"organism_id"}))
    def test_trad_availability_matches_arsa_map(self, field: str) -> None:
        assert field_availability(field, "trad").available == (field in _ARSA_FIELD_MAP)

    def test_organism_id_trad_available_but_absent_from_arsa_map(self) -> None:
        # organism_id は trad で available だが ARSA field map には無い唯一の例外。
        # trad arm は organism_rewrite で organism_name に置換してから compile する。
        assert field_availability("organism_id", "trad").available is True
        assert "organism_id" not in _ARSA_FIELD_MAP

    @pytest.mark.parametrize("field", sorted(ALL_ALLOWED_FIELDS))
    def test_taxonomy_availability_matches_txsearch_map(self, field: str) -> None:
        assert field_availability(field, "taxonomy").available == (field in _TXSEARCH_FIELD_MAP)


class TestOrganismIdTradAvailable:
    """organism_id@trad は available (rewrite 前提)。per-arm は keep し na にしない.

    実際の compile は organism_rewrite (TXSearch 解決) で organism_name に置換した後に行う
    (rewrite 前の ast を直接 ARSA compile すると compiler_solr が RuntimeError)。ここでは
    per-arm 段が organism_id を落とさず arm を applicable に保つことだけを固定する。
    """

    def test_organism_id_kept_on_trad(self) -> None:
        r = reduce_ast_for_db(parse("organism_id:9606"), "trad")
        assert r.applicable
        assert not r.always_zero
        assert r.ast is not None
        assert r.unavailable_fields == ()

    def test_organism_id_with_other_field_applicable_on_trad(self) -> None:
        r = reduce_ast_for_db(parse("organism_id:9606 AND title:cancer"), "trad")
        assert r.applicable
        assert not r.always_zero
        assert r.ast is not None

    def test_organism_id_or_unavailable_field_still_propagates_na_on_trad(self) -> None:
        # organism_id は available になったが、submitter は trad 非対応のまま → arm 対象外。
        r = reduce_ast_for_db(parse("organism_id:9606 OR submitter:smith"), "trad")
        assert not r.applicable
        assert r.unavailable_fields == ("submitter",)


class TestReducedAstAlwaysCompiles:
    """簡約後 (applicable かつ ast あり) の AST は Solr compile で RuntimeError にならない.

    例外: trad の organism_id は per-arm では keep されるが ARSA に直接 field が無いため、
    compile 前に organism_rewrite (TXSearch 解決) で organism_name に置換する必要がある
    (rewrite 後の compile 可は test_organism_rewrite が担保)。
    """

    @given(value=st.text(alphabet="abcdefghijklmnop", min_size=2, max_size=8))
    def test_pbt_available_field_compiles(self, value: str) -> None:
        r = reduce_ast_for_db(parse(f"organism_name:{value}"), "taxonomy")
        assert r.applicable
        assert r.ast is not None
        compile_to_solr(r.ast, dialect="txsearch")

    @given(value=st.text(alphabet="abcdefghijklmnop", min_size=2, max_size=8))
    def test_pbt_mixed_fixed_and_available_compiles(self, value: str) -> None:
        # 固定値一致 + available の AND → 固定値節が落ち、残りは compile 可能。
        r = reduce_ast_for_db(parse(f"accessibility:public-access AND organism_name:{value}"), "taxonomy")
        assert r.applicable
        if r.ast is not None:
            compile_to_solr(r.ast, dialect="txsearch")
