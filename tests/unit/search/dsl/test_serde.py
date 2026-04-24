"""Tests for ddbj_search_api.search.dsl.serde.

SSOT: search-backends.md §スキーマ仕様 (L363-381).
"""

from __future__ import annotations

import json
from typing import Any

from ddbj_search_api.search.dsl import parse
from ddbj_search_api.search.dsl.serde import ast_to_json
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

    def test_organism_eq_word(self) -> None:
        assert _j("organism:human") == {"field": "organism", "op": "eq", "value": "human"}

    def test_organism_eq_phrase(self) -> None:
        assert _j('organism:"Homo sapiens"') == {
            "field": "organism",
            "op": "eq",
            "value": "Homo sapiens",
        }

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
        # SSOT search-backends.md L363-381 のサンプル相当
        dsl = 'organism:"Homo sapiens" AND date:[2020-01-01 TO 2024-12-31] AND (title:cancer OR title:tumor)'
        assert _j(dsl) == {
            "op": "AND",
            "rules": [
                {"field": "organism", "op": "eq", "value": "Homo sapiens"},
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
