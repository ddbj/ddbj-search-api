"""Tests for organism_id → organism_name rewrite (organism_rewrite, Stage 3a).

TXSearch 解決 (async I/O) は呼び出し側の責務で、ここでは解決済み ``{TaxID: 学名}`` を注入して
純粋な AST 組み替えと bool 畳み込みだけを検証する。不変条件「rewrite 後 AST に organism_id が
残らない (= ARSA compile が RuntimeError にならない)」を property で固定し、解決失敗 / wildcard /
AND・OR・NOT の畳み込みを境界ごとに突く。
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from ddbj_search_api.search.dsl import parse
from ddbj_search_api.search.dsl.ast import FieldClause, FreeText, Node
from ddbj_search_api.search.dsl.compiler_solr import compile_to_solr
from ddbj_search_api.search.dsl.organism_rewrite import (
    collect_organism_ids,
    rewrite_organism_ids,
)

_HOMO = {"9606": "Homo sapiens"}


def _has_organism_id(node: Node | None) -> bool:
    """AST に organism_id の FieldClause が (wildcard 含め) 残っているか。"""
    if node is None or isinstance(node, FreeText):
        return False
    if isinstance(node, FieldClause):
        return node.field == "organism_id"

    return any(_has_organism_id(child) for child in node.children)


class TestCollectOrganismIds:
    def test_single(self) -> None:
        assert collect_organism_ids(parse("organism_id:9606")) == ("9606",)

    def test_phrase_value_collected(self) -> None:
        assert collect_organism_ids(parse('organism_id:"9606"')) == ("9606",)

    def test_and(self) -> None:
        assert collect_organism_ids(parse("organism_id:9606 AND organism_id:10090")) == ("9606", "10090")

    def test_or_ignores_non_organism_id(self) -> None:
        assert collect_organism_ids(parse("organism_id:9606 OR title:foo")) == ("9606",)

    def test_not(self) -> None:
        assert collect_organism_ids(parse("NOT organism_id:9606")) == ("9606",)

    def test_nested(self) -> None:
        ast = parse("(organism_id:9606 OR title:x) AND organism_id:10090")
        assert collect_organism_ids(ast) == ("9606", "10090")

    def test_wildcard_excluded(self) -> None:
        # wildcard TaxID は学名解決できないので resolver には渡さない。
        assert collect_organism_ids(parse("organism_id:96*")) == ()

    def test_wildcard_mixed_with_exact(self) -> None:
        assert collect_organism_ids(parse("organism_id:96* OR organism_id:9606")) == ("9606",)

    def test_dedup_preserves_first_occurrence_order(self) -> None:
        ast = parse("organism_id:10090 OR organism_id:9606 OR organism_id:10090")
        assert collect_organism_ids(ast) == ("10090", "9606")

    def test_no_organism_id(self) -> None:
        assert collect_organism_ids(parse("title:foo AND date_published:2024-01-01")) == ()

    def test_none_ast(self) -> None:
        assert collect_organism_ids(None) == ()

    def test_freetext_only(self) -> None:
        assert collect_organism_ids(parse("cancer")) == ()


class TestRewriteOrganismIds:
    def test_resolved_exact_becomes_organism_name_phrase(self) -> None:
        result = rewrite_organism_ids(parse("organism_id:9606"), _HOMO)
        assert not result.always_zero
        assert result.ast is not None
        assert compile_to_solr(result.ast, dialect="arsa") == '(Organism:"Homo sapiens" OR Lineage:"Homo sapiens")'

    def test_unresolved_exact_is_always_zero(self) -> None:
        result = rewrite_organism_ids(parse("organism_id:99999999"), {})
        assert result.always_zero
        assert result.ast is None

    def test_wildcard_is_always_zero(self) -> None:
        # wildcard は collect で集めず resolved にも載らないが、rewrite 単体でも false に畳む。
        result = rewrite_organism_ids(parse("organism_id:96*"), {})
        assert result.always_zero

    def test_and_with_unresolved_is_always_zero(self) -> None:
        result = rewrite_organism_ids(parse("organism_id:99999999 AND title:foo"), {})
        assert result.always_zero

    def test_or_drops_unresolved_keeps_other(self) -> None:
        result = rewrite_organism_ids(parse("organism_id:99999999 OR title:foo"), {})
        assert not result.always_zero
        assert result.ast is not None
        assert compile_to_solr(result.ast, dialect="arsa") == '(Definition:"foo" OR Definition:foo*)'

    def test_or_all_unresolved_is_always_zero(self) -> None:
        result = rewrite_organism_ids(parse("organism_id:1 OR organism_id:2"), {})
        assert result.always_zero

    def test_not_unresolved_is_all_docs(self) -> None:
        # NOT (恒偽) → 恒真 → 全件 (ast=None)。ES の NOT organism.identifier:<unknown> ≈ 全件 と整合。
        result = rewrite_organism_ids(parse("NOT organism_id:99999999"), {})
        assert not result.always_zero
        assert result.ast is None

    def test_not_resolved_keeps_negated_phrase(self) -> None:
        result = rewrite_organism_ids(parse("NOT organism_id:9606"), _HOMO)
        assert not result.always_zero
        assert result.ast is not None
        compiled = compile_to_solr(result.ast, dialect="arsa")
        assert compiled == '(NOT (Organism:"Homo sapiens" OR Lineage:"Homo sapiens"))'

    def test_and_resolved_with_other_field(self) -> None:
        result = rewrite_organism_ids(parse("organism_id:9606 AND title:foo"), _HOMO)
        assert not result.always_zero
        assert result.ast is not None
        assert compile_to_solr(result.ast, dialect="arsa") == (
            '((Organism:"Homo sapiens" OR Lineage:"Homo sapiens") AND (Definition:"foo" OR Definition:foo*))'
        )

    def test_and_mixed_resolved_and_unresolved_is_always_zero(self) -> None:
        result = rewrite_organism_ids(parse("organism_id:9606 AND organism_id:99999999"), _HOMO)
        assert result.always_zero

    def test_or_mixed_keeps_resolved_only(self) -> None:
        result = rewrite_organism_ids(parse("organism_id:9606 OR organism_id:99999999"), _HOMO)
        assert not result.always_zero
        assert result.ast is not None
        assert compile_to_solr(result.ast, dialect="arsa") == '(Organism:"Homo sapiens" OR Lineage:"Homo sapiens")'

    def test_non_organism_field_unchanged(self) -> None:
        result = rewrite_organism_ids(parse("title:foo"), {})
        assert not result.always_zero
        assert result.ast is not None
        assert compile_to_solr(result.ast, dialect="arsa") == '(Definition:"foo" OR Definition:foo*)'

    def test_freetext_unchanged(self) -> None:
        result = rewrite_organism_ids(parse("cancer"), {})
        assert not result.always_zero
        assert result.ast is not None

    def test_none_ast(self) -> None:
        result = rewrite_organism_ids(None, {})
        assert not result.always_zero
        assert result.ast is None

    def test_empty_resolved_map_with_no_organism_id(self) -> None:
        # organism_id を含まない AST は resolved が空でも不変 (resolver skip の正常系)。
        result = rewrite_organism_ids(parse("title:foo OR description:bar"), {})
        assert not result.always_zero
        assert result.ast is not None
        assert not _has_organism_id(result.ast)


class TestRewriteOrganismIdsInvariants:
    """rewrite 後 AST に organism_id は残らず ARSA compile が RuntimeError にならない (property)."""

    @given(
        items=st.lists(
            st.tuples(st.from_regex(r"[1-9][0-9]{0,5}", fullmatch=True), st.booleans()),
            min_size=1,
            max_size=4,
            unique_by=lambda pair: pair[0],
        ),
        op=st.sampled_from([" AND ", " OR "]),
    )
    def test_pbt_rewrite_leaves_no_organism_id_and_compiles(
        self,
        items: list[tuple[str, bool]],
        op: str,
    ) -> None:
        dsl = op.join(f"organism_id:{taxid}" for taxid, _ in items)
        resolved = {taxid: "Homo sapiens" for taxid, ok in items if ok}
        result = rewrite_organism_ids(parse(dsl), resolved)
        if result.ast is not None:
            # organism_id leaf が一切残っていない (解決成功は organism_name、失敗は畳み込みで消去)。
            assert not _has_organism_id(result.ast)
            # よって safety net (compiler_solr の no-mapping RuntimeError) に到達しない。
            compile_to_solr(result.ast, dialect="arsa")
