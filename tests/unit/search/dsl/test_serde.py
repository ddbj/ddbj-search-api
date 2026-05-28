"""Tests for ddbj_search_api.search.dsl.serde.

SSOT: search-backends.md §スキーマ仕様 (L363-381).

``ast_to_json`` (Stage 1) と、逆方向 ``json_to_ast`` (POST /db-portal/serialize が使う)
の両方を検証する.  逆方向は ``(field_type, op) → value_kind`` の逆引きを行うため、
``word`` / ``phrase`` の曖昧ケースは WORD regex full-match で決定する.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from ddbj_search_api.search.dsl import parse
from ddbj_search_api.search.dsl.ast import BoolOp, FieldClause, FreeText
from ddbj_search_api.search.dsl.serde import ast_to_json, json_to_ast
from ddbj_search_api.search.dsl.validator import validate


def _j(dsl: str) -> dict[str, Any]:
    ast = parse(dsl)
    validate(ast, mode="cross")
    return ast_to_json(ast)


class TestLeafSerialization:
    def test_identifier_word_eq(self) -> None:
        assert _j("identifier:PRJDB1") == {"field": "identifier", "op": "eq", "value": "PRJDB1"}

    def test_identifier_wildcard(self) -> None:
        assert _j("identifier:PRJ*") == {"field": "identifier", "op": "wildcard", "value": "PRJ*"}

    def test_title_word_contains(self) -> None:
        assert _j("title:cancer") == {"field": "title", "op": "contains", "value": "cancer"}

    def test_title_phrase_contains(self) -> None:
        assert _j('title:"cancer treatment"') == {
            "field": "title",
            "op": "contains",
            "value": "cancer treatment",
        }

    def test_title_wildcard(self) -> None:
        assert _j("title:canc*") == {"field": "title", "op": "wildcard", "value": "canc*"}

    def test_organism_name_contains_word(self) -> None:
        # organism_name は text 型 → contains
        assert _j("organism_name:human") == {
            "field": "organism_name",
            "op": "contains",
            "value": "human",
        }

    def test_organism_name_contains_phrase(self) -> None:
        assert _j('organism_name:"Homo sapiens"') == {
            "field": "organism_name",
            "op": "contains",
            "value": "Homo sapiens",
        }

    def test_organism_id_eq_word(self) -> None:
        # organism_id は identifier 型 → eq
        assert _j("organism_id:9606") == {"field": "organism_id", "op": "eq", "value": "9606"}

    def test_date_published_eq(self) -> None:
        assert _j("date_published:2024-01-01") == {
            "field": "date_published",
            "op": "eq",
            "value": "2024-01-01",
        }

    def test_date_published_between(self) -> None:
        assert _j("date_published:[2020-01-01 TO 2024-12-31]") == {
            "field": "date_published",
            "op": "between",
            "from": "2020-01-01",
            "to": "2024-12-31",
        }

    def test_date_alias_between(self) -> None:
        assert _j("date:[2020-01-01 TO 2024-12-31]") == {
            "field": "date",
            "op": "between",
            "from": "2020-01-01",
            "to": "2024-12-31",
        }


class TestBoolSerialization:
    def test_and(self) -> None:
        assert _j("title:a AND title:b") == {
            "op": "AND",
            "rules": [
                {"field": "title", "op": "contains", "value": "a"},
                {"field": "title", "op": "contains", "value": "b"},
            ],
        }

    def test_or(self) -> None:
        assert _j("title:a OR title:b") == {
            "op": "OR",
            "rules": [
                {"field": "title", "op": "contains", "value": "a"},
                {"field": "title", "op": "contains", "value": "b"},
            ],
        }

    def test_not(self) -> None:
        assert _j("NOT title:a") == {
            "op": "NOT",
            "rules": [{"field": "title", "op": "contains", "value": "a"}],
        }

    def test_ssot_sample_nested(self) -> None:
        # SSOT search-backends.md §AST フォーマット のサンプル相当
        dsl = 'organism_name:"Homo sapiens" AND date:[2020-01-01 TO 2024-12-31] AND (title:cancer OR title:tumor)'
        assert _j(dsl) == {
            "op": "AND",
            "rules": [
                {"field": "organism_name", "op": "contains", "value": "Homo sapiens"},
                {
                    "field": "date",
                    "op": "between",
                    "from": "2020-01-01",
                    "to": "2024-12-31",
                },
                {
                    "op": "OR",
                    "rules": [
                        {"field": "title", "op": "contains", "value": "cancer"},
                        {"field": "title", "op": "contains", "value": "tumor"},
                    ],
                },
            ],
        }


class TestKeysShape:
    def test_leaf_value_keys(self) -> None:
        assert set(_j("title:cancer")) == {"field", "op", "value"}

    def test_leaf_range_keys(self) -> None:
        assert set(_j("date_published:[2020-01-01 TO 2024-12-31]")) == {
            "field",
            "op",
            "from",
            "to",
        }

    def test_bool_keys(self) -> None:
        assert set(_j("title:a AND title:b")) == {"op", "rules"}


class TestJsonSerializable:
    def test_full_tree_round_trip(self) -> None:
        result = _j("(title:cancer OR title:tumor) AND date:[2020-01-01 TO 2024-12-31]")
        serialized = json.dumps(result)
        parsed = json.loads(serialized)
        assert parsed == result


class TestJsonToAstLeaves:
    """Inverse direction (dict → AST).  JSON dict round-trip via ast_to_json."""

    def test_free_text(self) -> None:
        # 受け入れ: is_phrase 不在 dict (legacy) は default False で復元.
        # 出力: ast_to_json は常に is_phrase key を出力する (False 明示).
        d_in = {"op": "free_text", "value": "cancer"}
        node = json_to_ast(d_in)
        assert isinstance(node, FreeText)
        assert node.value == "cancer"
        assert node.is_phrase is False
        assert ast_to_json(node) == {"op": "free_text", "value": "cancer", "is_phrase": False}

    def test_identifier_eq_word(self) -> None:
        d = {"field": "identifier", "op": "eq", "value": "PRJDB1"}
        assert ast_to_json(json_to_ast(d)) == d

    def test_identifier_wildcard(self) -> None:
        d = {"field": "identifier", "op": "wildcard", "value": "PRJ*"}
        assert ast_to_json(json_to_ast(d)) == d

    def test_text_contains_word(self) -> None:
        d = {"field": "title", "op": "contains", "value": "cancer"}
        assert ast_to_json(json_to_ast(d)) == d

    def test_text_contains_phrase(self) -> None:
        d = {"field": "title", "op": "contains", "value": "cancer treatment"}
        assert ast_to_json(json_to_ast(d)) == d

    def test_organism_name_contains_word(self) -> None:
        d = {"field": "organism_name", "op": "contains", "value": "human"}
        assert ast_to_json(json_to_ast(d)) == d

    def test_organism_name_contains_phrase(self) -> None:
        d = {"field": "organism_name", "op": "contains", "value": "Homo sapiens"}
        assert ast_to_json(json_to_ast(d)) == d

    def test_organism_id_eq_word(self) -> None:
        d = {"field": "organism_id", "op": "eq", "value": "9606"}
        assert ast_to_json(json_to_ast(d)) == d

    def test_date_eq(self) -> None:
        d = {"field": "date_published", "op": "eq", "value": "2024-01-01"}
        assert ast_to_json(json_to_ast(d)) == d

    def test_date_between(self) -> None:
        d = {"field": "date_published", "op": "between", "from": "2020-01-01", "to": "2024-12-31"}
        assert ast_to_json(json_to_ast(d)) == d

    def test_enum_eq(self) -> None:
        d = {"field": "library_strategy", "op": "eq", "value": "WGS"}
        assert ast_to_json(json_to_ast(d)) == d

    def test_number_eq(self) -> None:
        d = {"field": "sequence_length", "op": "eq", "value": "100"}
        assert ast_to_json(json_to_ast(d)) == d

    def test_number_between(self) -> None:
        d = {"field": "sequence_length", "op": "between", "from": "100", "to": "200"}
        assert ast_to_json(json_to_ast(d)) == d


class TestJsonToAstValueKindInference:
    """word vs phrase 推定 (text/identifier/enum + eq/contains の曖昧ケース)."""

    def test_no_special_chars_yields_word(self) -> None:
        node = json_to_ast({"field": "organism_name", "op": "contains", "value": "human"})
        assert isinstance(node, FieldClause)
        assert node.value_kind == "word"

    def test_space_yields_phrase(self) -> None:
        node = json_to_ast({"field": "organism_name", "op": "contains", "value": "Homo sapiens"})
        assert isinstance(node, FieldClause)
        assert node.value_kind == "phrase"

    def test_colon_yields_phrase(self) -> None:
        # WORD regex は ``:`` を除外する.
        node = json_to_ast({"field": "title", "op": "contains", "value": "a:b"})
        assert isinstance(node, FieldClause)
        assert node.value_kind == "phrase"

    def test_empty_string_yields_phrase(self) -> None:
        # 空文字列は WORD regex match しないので phrase.  後段 validator が missing-value で reject.
        node = json_to_ast({"field": "title", "op": "contains", "value": ""})
        assert isinstance(node, FieldClause)
        assert node.value_kind == "phrase"


class TestJsonToAstBool:
    def test_and(self) -> None:
        d = {
            "op": "AND",
            "rules": [
                {"field": "title", "op": "contains", "value": "a"},
                {"field": "title", "op": "contains", "value": "b"},
            ],
        }
        node = json_to_ast(d)
        assert isinstance(node, BoolOp)
        assert node.op == "AND"
        assert len(node.children) == 2
        assert ast_to_json(node) == d

    def test_or(self) -> None:
        d = {
            "op": "OR",
            "rules": [
                {"field": "title", "op": "contains", "value": "a"},
                {"field": "title", "op": "contains", "value": "b"},
            ],
        }
        assert ast_to_json(json_to_ast(d)) == d

    def test_not_single_child(self) -> None:
        d = {"op": "NOT", "rules": [{"field": "title", "op": "contains", "value": "a"}]}
        assert ast_to_json(json_to_ast(d)) == d

    def test_nested_bool(self) -> None:
        # legacy input (is_phrase 不在) は default False で受入れ、output は常に is_phrase 出力.
        d_in = {
            "op": "AND",
            "rules": [
                {"op": "free_text", "value": "cancer"},
                {
                    "op": "OR",
                    "rules": [
                        {"field": "title", "op": "contains", "value": "tumor"},
                        {"field": "organism_name", "op": "contains", "value": "Homo sapiens"},
                    ],
                },
            ],
        }
        d_out = {
            "op": "AND",
            "rules": [
                {"op": "free_text", "value": "cancer", "is_phrase": False},
                {
                    "op": "OR",
                    "rules": [
                        {"field": "title", "op": "contains", "value": "tumor"},
                        {"field": "organism_name", "op": "contains", "value": "Homo sapiens"},
                    ],
                },
            ],
        }
        assert ast_to_json(json_to_ast(d_in)) == d_out


class TestJsonToAstUnknownField:
    """Unknown field は json_to_ast では reject せず、validator に委譲する."""

    def test_unknown_field_passes_through(self) -> None:
        # value_kind は WORD/phrase 判定の dummy で OK.  validator が unknown-field で reject.
        node = json_to_ast({"field": "nonexistent", "op": "eq", "value": "x"})
        assert isinstance(node, FieldClause)
        assert node.field == "nonexistent"


class TestJsonToAstAstToJsonRoundTrip:
    """parse 由来の AST → ast_to_json → json_to_ast → ast_to_json が初手と一致することを確認."""

    @pytest.mark.parametrize(
        "dsl",
        [
            "cancer",
            "identifier:PRJDB1",
            'organism_name:"Homo sapiens"',
            "title:cancer AND title:tumor",
            'title:cancer AND organism_name:"Homo sapiens"',
            '(title:cancer OR title:tumor) AND organism_name:"Homo sapiens"',
            "date_published:[2020-01-01 TO 2024-12-31]",
            "NOT title:cancer",
            "identifier:PRJ*",
        ],
    )
    def test_round_trip(self, dsl: str) -> None:
        original = _j(dsl)
        recovered = ast_to_json(json_to_ast(original))
        assert recovered == original


class TestFreeTextPhraseRoundtrip:
    """FreeText.is_phrase が JSON tree (``/db-portal/parse`` レスポンス形式) と
    AST 間で往復しても保持されることを確認.

    出力は常に ``is_phrase`` key を明示する (True / False).  受入れは legacy
    形式 (key 不在) も default False で復元する (json_to_ast の後方互換).
    """

    def test_is_phrase_true_emitted_in_json(self) -> None:
        ast = FreeText(value="Homo sapiens", is_phrase=True)
        assert ast_to_json(ast) == {
            "op": "free_text",
            "value": "Homo sapiens",
            "is_phrase": True,
        }

    def test_is_phrase_false_always_present_in_json(self) -> None:
        # is_phrase=False でも常に key 出力 (response shape の安定性のため).
        ast = FreeText(value="cancer", is_phrase=False)
        assert ast_to_json(ast) == {
            "op": "free_text",
            "value": "cancer",
            "is_phrase": False,
        }

    def test_legacy_json_without_is_phrase_defaults_to_false(self) -> None:
        # 旧形式 JSON tree (is_phrase key 無し) は is_phrase=False の AST に復元 (受入れ後方互換).
        node = json_to_ast({"op": "free_text", "value": "cancer"})
        assert isinstance(node, FreeText)
        assert node.value == "cancer"
        assert node.is_phrase is False

    def test_json_with_is_phrase_true_round_trip(self) -> None:
        d = {"op": "free_text", "value": "Homo sapiens", "is_phrase": True}
        node = json_to_ast(d)
        assert isinstance(node, FreeText)
        assert node.is_phrase is True
        assert ast_to_json(node) == d

    def test_json_with_is_phrase_false_explicit_round_trip(self) -> None:
        d = {"op": "free_text", "value": "cancer", "is_phrase": False}
        node = json_to_ast(d)
        assert isinstance(node, FreeText)
        assert node.is_phrase is False
        assert ast_to_json(node) == d

    def test_parse_quoted_emits_is_phrase_in_json(self) -> None:
        ast = parse('"Homo sapiens"')
        assert ast_to_json(ast) == {
            "op": "free_text",
            "value": "Homo sapiens",
            "is_phrase": True,
        }

    def test_parse_bare_emits_is_phrase_false_in_json(self) -> None:
        ast = parse("cancer")
        assert ast_to_json(ast) == {
            "op": "free_text",
            "value": "cancer",
            "is_phrase": False,
        }

    def test_nested_bool_preserves_is_phrase(self) -> None:
        ast = parse('"whole genome" AND title:cancer')
        result = ast_to_json(ast)
        assert result == {
            "op": "AND",
            "rules": [
                {"op": "free_text", "value": "whole genome", "is_phrase": True},
                {"field": "title", "op": "contains", "value": "cancer"},
            ],
        }
        # 逆方向 round-trip でも保持される.
        recovered = ast_to_json(json_to_ast(result))
        assert recovered == result
