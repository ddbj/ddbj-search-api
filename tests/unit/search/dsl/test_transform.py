"""Tests for ddbj_search_api.search.dsl.transform."""

from __future__ import annotations

from collections import Counter

from hypothesis import given

from ddbj_search_api.search.dsl.ast import (
    BoolOp,
    FieldClause,
    FreeText,
    Node,
    Position,
)
from ddbj_search_api.search.dsl.compiler_es import compile_to_es
from ddbj_search_api.search.dsl.transform import (
    exclude_field_from_ast,
    split_top_level_field,
)
from tests.unit.strategies import valid_ast_strategy

_POS = Position(column=1, length=1)


def _clause(field: str, value: str = "x", value_kind: str = "word") -> FieldClause:
    return FieldClause(field=field, value_kind=value_kind, value=value, position=_POS)  # type: ignore[arg-type]


def _collect_field_clauses(node: Node | None) -> list[FieldClause]:
    """AST 中の FieldClause を出現順 (DFS) に集める。"""
    if node is None:
        return []
    if isinstance(node, FieldClause):
        return [node]
    if isinstance(node, FreeText):
        return []
    out: list[FieldClause] = []
    for child in node.children:
        out.extend(_collect_field_clauses(child))

    return out


def _top_level_field_clauses(node: Node | None) -> list[FieldClause]:
    """root 単独 / top-level AND 直下に現れる FieldClause だけを集める。"""
    if node is None:
        return []
    if isinstance(node, FieldClause):
        return [node]
    if isinstance(node, BoolOp) and node.op == "AND":
        return [c for c in node.children if isinstance(c, FieldClause)]

    return []


class TestExcludeFieldFromAst:
    def test_none_input_returns_none(self) -> None:
        assert exclude_field_from_ast(None, "organism_id") is None

    def test_single_clause_matching_field_returns_none(self) -> None:
        assert exclude_field_from_ast(_clause("organism_id", "9606"), "organism_id") is None

    def test_single_clause_other_field_unchanged(self) -> None:
        clause = _clause("title", "cancer")
        assert exclude_field_from_ast(clause, "organism_id") == clause

    def test_free_text_unchanged(self) -> None:
        ft = FreeText("cancer")
        assert exclude_field_from_ast(ft, "organism_id") == ft

    def test_and_drops_target_keeps_others(self) -> None:
        ast = BoolOp(
            op="AND",
            children=(_clause("organism_id", "9606"), _clause("title", "cancer")),
            position=_POS,
        )
        # organism_id を除くと title clause 1 つだけ残り、AND は unwrap される。
        assert exclude_field_from_ast(ast, "organism_id") == _clause("title", "cancer")

    def test_and_keeps_target_when_excluding_unrelated_field(self) -> None:
        ast = BoolOp(
            op="AND",
            children=(_clause("organism_id", "9606"), _clause("title", "cancer")),
            position=_POS,
        )
        assert exclude_field_from_ast(ast, "package") == ast

    def test_or_of_same_field_fully_removed(self) -> None:
        """organism_id:9606 OR organism_id:10090 を organism で除くと全消し (None)。"""
        ast = BoolOp(
            op="OR",
            children=(_clause("organism_id", "9606"), _clause("organism_id", "10090")),
            position=_POS,
        )
        assert exclude_field_from_ast(ast, "organism_id") is None

    def test_or_drops_only_target_field(self) -> None:
        ast = BoolOp(
            op="OR",
            children=(_clause("organism_id", "9606"), _clause("title", "cancer")),
            position=_POS,
        )
        # OR で 1 子だけ残ると unwrap される。
        assert exclude_field_from_ast(ast, "organism_id") == _clause("title", "cancer")

    def test_not_of_target_field_removed(self) -> None:
        """NOT organism_id:9606 を organism で除くと NOT ごと消える。"""
        ast = BoolOp(op="NOT", children=(_clause("organism_id", "9606"),), position=_POS)
        assert exclude_field_from_ast(ast, "organism_id") is None

    def test_not_of_other_field_unchanged(self) -> None:
        ast = BoolOp(op="NOT", children=(_clause("title", "cancer"),), position=_POS)
        assert exclude_field_from_ast(ast, "organism_id") == ast

    def test_nested_and_excludes_deep_clause(self) -> None:
        """(organism_id:9606 OR organism_id:10090) AND title:cancer で organism を除くと
        OR が丸ごと消え title だけ残る。"""
        or_node = BoolOp(
            op="OR",
            children=(_clause("organism_id", "9606"), _clause("organism_id", "10090")),
            position=_POS,
        )
        ast = BoolOp(op="AND", children=(or_node, _clause("title", "cancer")), position=_POS)
        assert exclude_field_from_ast(ast, "organism_id") == _clause("title", "cancer")

    def test_and_with_three_targets_keeps_remaining_bool(self) -> None:
        """対象除外後に 2 つ以上残れば BoolOp を維持する。"""
        ast = BoolOp(
            op="AND",
            children=(
                _clause("organism_id", "9606"),
                _clause("title", "cancer"),
                _clause("package", "y"),
            ),
            position=_POS,
        )
        expected = BoolOp(
            op="AND",
            children=(_clause("title", "cancer"), _clause("package", "y")),
            position=_POS,
        )
        assert exclude_field_from_ast(ast, "organism_id") == expected


class TestExcludeFieldFromAstPBT:
    @given(valid_ast_strategy())
    def test_excluded_field_never_remains(self, ast: Node) -> None:
        for field in {c.field for c in _collect_field_clauses(ast)}:
            result = exclude_field_from_ast(ast, field)
            assert all(c.field != field for c in _collect_field_clauses(result))

    @given(valid_ast_strategy())
    def test_exclude_preserves_other_clauses_in_order(self, ast: Node) -> None:
        all_clauses = _collect_field_clauses(ast)
        for field in {c.field for c in all_clauses}:
            result = exclude_field_from_ast(ast, field)
            expected = [c for c in all_clauses if c.field != field]
            assert _collect_field_clauses(result) == expected

    @given(valid_ast_strategy())
    def test_exclude_is_idempotent(self, ast: Node) -> None:
        for field in {c.field for c in _collect_field_clauses(ast)}:
            once = exclude_field_from_ast(ast, field)
            assert exclude_field_from_ast(once, field) == once

    @given(valid_ast_strategy())
    def test_exclude_then_compile_es_does_not_raise(self, ast: Node) -> None:
        """除外後の AST は validate を通さず compile しても例外を出さない。"""
        for field in {c.field for c in _collect_field_clauses(ast)}:
            result = exclude_field_from_ast(ast, field)
            if result is not None:
                compile_to_es(result)

    @given(valid_ast_strategy())
    def test_exclude_unrelated_field_is_noop(self, ast: Node) -> None:
        """AST に存在しない field を除外しても AST は変わらない。"""
        present = {c.field for c in _collect_field_clauses(ast)}
        if "no_such_field" not in present:
            assert exclude_field_from_ast(ast, "no_such_field") == ast


class TestSplitTopLevelField:
    def test_none_input_returns_empty(self) -> None:
        remaining, extracted = split_top_level_field(None, {"organism_id"})
        assert remaining is None
        assert extracted == {}

    def test_single_target_clause_extracted(self) -> None:
        clause = _clause("organism_id", "9606")
        remaining, extracted = split_top_level_field(clause, {"organism_id"})
        assert remaining is None
        assert extracted == {"organism_id": [clause]}

    def test_single_non_target_clause_stays(self) -> None:
        clause = _clause("title", "cancer")
        remaining, extracted = split_top_level_field(clause, {"organism_id"})
        assert remaining == clause
        assert extracted == {}

    def test_top_level_and_extracts_target_unwraps_remainder(self) -> None:
        org = _clause("organism_id", "9606")
        title = _clause("title", "cancer")
        ast = BoolOp(op="AND", children=(org, title), position=_POS)
        remaining, extracted = split_top_level_field(ast, {"organism_id"})
        assert extracted == {"organism_id": [org]}
        assert remaining == title

    def test_top_level_and_keeps_bool_when_multiple_remain(self) -> None:
        org = _clause("organism_id", "9606")
        title = _clause("title", "cancer")
        package = _clause("package", "y")
        ast = BoolOp(op="AND", children=(org, title, package), position=_POS)
        remaining, extracted = split_top_level_field(ast, {"organism_id"})
        assert extracted == {"organism_id": [org]}
        assert remaining == BoolOp(op="AND", children=(title, package), position=_POS)

    def test_or_root_is_not_split(self) -> None:
        """root が OR のときは self-exclusion 対象外 (degrade)、丸ごと remaining に残す。"""
        ast = BoolOp(
            op="OR",
            children=(_clause("organism_id", "9606"), _clause("organism_id", "10090")),
            position=_POS,
        )
        remaining, extracted = split_top_level_field(ast, {"organism_id"})
        assert remaining == ast
        assert extracted == {}

    def test_or_under_top_level_and_is_not_split(self) -> None:
        """top-level AND 直下の OR ノードは FieldClause ではないので分離しない。"""
        or_node = BoolOp(
            op="OR",
            children=(_clause("organism_id", "9606"), _clause("organism_id", "10090")),
            position=_POS,
        )
        title = _clause("title", "cancer")
        ast = BoolOp(op="AND", children=(or_node, title), position=_POS)
        remaining, extracted = split_top_level_field(ast, {"organism_id"})
        assert extracted == {}
        assert remaining == ast

    def test_multiple_target_fields_extracted_separately(self) -> None:
        div = _clause("division", "BCT")
        mol = _clause("molecular_type", "genomic DNA", "phrase")
        title = _clause("title", "cancer")
        ast = BoolOp(op="AND", children=(div, mol, title), position=_POS)
        remaining, extracted = split_top_level_field(ast, {"division", "molecular_type"})
        assert extracted == {"division": [div], "molecular_type": [mol]}
        assert remaining == title

    def test_only_target_fields_yields_none_remaining(self) -> None:
        div = _clause("division", "BCT")
        mol = _clause("molecular_type", "genomic DNA", "phrase")
        ast = BoolOp(op="AND", children=(div, mol), position=_POS)
        remaining, extracted = split_top_level_field(ast, {"division", "molecular_type"})
        assert remaining is None
        assert extracted == {"division": [div], "molecular_type": [mol]}


class TestSplitTopLevelFieldPBT:
    @given(valid_ast_strategy())
    def test_split_preserves_all_clauses(self, ast: Node) -> None:
        """分離しても clause の multiset は保存される (q ∧ fq = 元の AND)。"""
        all_clauses = _collect_field_clauses(ast)
        fields = {c.field for c in all_clauses}
        remaining, extracted = split_top_level_field(ast, fields)
        extracted_clauses = [c for clauses in extracted.values() for c in clauses]
        combined = _collect_field_clauses(remaining) + extracted_clauses
        assert Counter(combined) == Counter(all_clauses)

    @given(valid_ast_strategy())
    def test_extracted_only_contains_requested_fields(self, ast: Node) -> None:
        fields = {c.field for c in _collect_field_clauses(ast)}
        _remaining, extracted = split_top_level_field(ast, fields)
        for field, clauses in extracted.items():
            assert field in fields
            assert all(c.field == field for c in clauses)

    @given(valid_ast_strategy())
    def test_remaining_has_no_target_field_at_top_level(self, ast: Node) -> None:
        fields = {c.field for c in _collect_field_clauses(ast)}
        remaining, _extracted = split_top_level_field(ast, fields)
        assert all(c.field not in fields for c in _top_level_field_clauses(remaining))

    @given(valid_ast_strategy())
    def test_split_then_compile_es_does_not_raise(self, ast: Node) -> None:
        fields = {c.field for c in _collect_field_clauses(ast)}
        remaining, _extracted = split_top_level_field(ast, fields)
        if remaining is not None:
            compile_to_es(remaining)
