"""Tests for GET /db-portal/parse.

``q`` クエリを SSOT query-tree JSON に変換する endpoint。共有 URL (``?q=...``)
から GUI の state (検索ボックス + フィルタツリー) を復元する用途。

既存の ``parse`` / ``validate`` / ``ast_to_json`` を wiring するだけで、コア処理は
完全再利用。validator mode は既存 ``/db-portal/search?q=...`` と同一
(``db`` 未指定 → ``mode='cross'`` / 指定 → ``mode='single'``)。

エラー契約: クエリ関連 9 slug を発火する (FreeText 位置制約系の
``invalid-freetext-position`` / ``duplicate-freetext`` を含む)。
"""

from __future__ import annotations

import json
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ddbj_search_api.schemas.db_portal import DbPortalErrorType

# === Routing ===


class TestDbPortalParseRouting:
    """GET /db-portal/parse: canonical path, required q, tag."""

    def test_route_exists_requires_q(self, app_with_db_portal: TestClient) -> None:
        # q 未指定は FastAPI 標準の 422 (about:blank)。
        resp = app_with_db_portal.get("/db-portal/parse")
        assert resp.status_code == 422

    def test_route_accepts_minimal_q(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"q": "title:cancer"})
        assert resp.status_code == 200

    def test_trailing_slash_not_canonical(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse/", params={"q": "title:cancer"})
        assert resp.status_code == 404

    def test_tag_is_db_portal(self, app_with_db_portal: TestClient) -> None:
        spec = app_with_db_portal.get("/openapi.json").json()
        operation = spec["paths"]["/db-portal/parse"]["get"]
        assert operation["tags"] == ["db-portal"]


# === Valid query → JSON tree ===


def _ast(resp_body: dict[str, Any]) -> dict[str, Any]:
    assert isinstance(resp_body, dict)
    ast = resp_body["ast"]
    assert isinstance(ast, dict)
    return ast


class TestDbPortalParseValidLeaf:
    """Leaf serialization — mirrors test_serde.py::TestLeafSerialization via HTTP."""

    def test_identifier_word_eq(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"q": "identifier:PRJDB1"})
        assert resp.status_code == 200
        assert _ast(resp.json()) == {"field": "identifier", "op": "eq", "value": "PRJDB1"}

    def test_identifier_wildcard(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"q": "identifier:PRJ*"})
        assert _ast(resp.json()) == {"field": "identifier", "op": "wildcard", "value": "PRJ*"}

    def test_title_word_contains(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"q": "title:cancer"})
        assert _ast(resp.json()) == {"field": "title", "op": "contains", "value": "cancer"}

    def test_title_phrase_contains(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/parse",
            params={"q": 'title:"cancer treatment"'},
        )
        assert _ast(resp.json()) == {"field": "title", "op": "contains", "value": "cancer treatment"}

    def test_title_wildcard(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"q": "title:canc*"})
        assert _ast(resp.json()) == {"field": "title", "op": "wildcard", "value": "canc*"}

    def test_organism_eq_word(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"q": "organism:human"})
        assert _ast(resp.json()) == {"field": "organism", "op": "eq", "value": "human"}

    def test_organism_eq_phrase_with_space(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/parse",
            params={"q": 'organism:"Homo sapiens"'},
        )
        assert _ast(resp.json()) == {"field": "organism", "op": "eq", "value": "Homo sapiens"}

    def test_date_published_eq(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/parse",
            params={"q": "date_published:2024-01-01"},
        )
        assert _ast(resp.json()) == {"field": "date_published", "op": "eq", "value": "2024-01-01"}

    def test_date_published_between(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/parse",
            params={"q": "date_published:[2020-01-01 TO 2024-12-31]"},
        )
        ast = _ast(resp.json())
        # range leaf は "from" / "to" で出力 (Pydantic alias、Python 予約語回避)。
        assert ast == {
            "field": "date_published",
            "op": "between",
            "from": "2020-01-01",
            "to": "2024-12-31",
        }
        assert "from_" not in ast  # alias serialization が効いていること

    def test_date_alias_between(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/parse",
            params={"q": "date:[2020-01-01 TO 2024-12-31]"},
        )
        assert _ast(resp.json()) == {
            "field": "date",
            "op": "between",
            "from": "2020-01-01",
            "to": "2024-12-31",
        }


class TestDbPortalParseFreeText:
    """FreeText serialization (bare word / phrase からの生成)."""

    def test_bare_word_alone(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"q": "cancer"})
        assert resp.status_code == 200
        assert _ast(resp.json()) == {"op": "free_text", "value": "cancer"}

    def test_phrase_alone(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"q": '"Homo sapiens"'})
        assert resp.status_code == 200
        assert _ast(resp.json()) == {"op": "free_text", "value": "Homo sapiens"}

    def test_hyphenated_word_stays_bare(self, app_with_db_portal: TestClient) -> None:
        # auto-phrase 化は compiler 内、parser は bare WORD のまま FreeText に積む。
        resp = app_with_db_portal.get("/db-portal/parse", params={"q": "HIF-1"})
        assert resp.status_code == 200
        assert _ast(resp.json()) == {"op": "free_text", "value": "HIF-1"}

    def test_bare_with_field_clause_and(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/parse",
            params={"q": "cancer AND organism:9606"},
        )
        assert resp.status_code == 200
        assert _ast(resp.json()) == {
            "op": "AND",
            "rules": [
                {"op": "free_text", "value": "cancer"},
                {"field": "organism", "op": "eq", "value": "9606"},
            ],
        }


class TestDbPortalParseValidBool:
    """BoolOp serialization — mirrors test_serde.py::TestBoolSerialization via HTTP."""

    def test_and(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"q": "title:a AND title:b"})
        assert _ast(resp.json()) == {
            "op": "AND",
            "rules": [
                {"field": "title", "op": "contains", "value": "a"},
                {"field": "title", "op": "contains", "value": "b"},
            ],
        }

    def test_or(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"q": "title:a OR title:b"})
        assert _ast(resp.json()) == {
            "op": "OR",
            "rules": [
                {"field": "title", "op": "contains", "value": "a"},
                {"field": "title", "op": "contains", "value": "b"},
            ],
        }

    def test_not(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"q": "NOT title:a"})
        assert _ast(resp.json()) == {
            "op": "NOT",
            "rules": [{"field": "title", "op": "contains", "value": "a"}],
        }

    def test_nested(self, app_with_db_portal: TestClient) -> None:
        q = 'organism:"Homo sapiens" AND date:[2020-01-01 TO 2024-12-31] AND (title:cancer OR title:tumor)'
        resp = app_with_db_portal.get("/db-portal/parse", params={"q": q})
        assert _ast(resp.json()) == {
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


class TestDbPortalParseKeysShape:
    """discriminator key / key presence — mirrors test_serde.py::TestKeysShape."""

    def test_leaf_value_keys(self, app_with_db_portal: TestClient) -> None:
        ast = _ast(app_with_db_portal.get("/db-portal/parse", params={"q": "title:cancer"}).json())
        assert set(ast) == {"field", "op", "value"}

    def test_leaf_range_keys(self, app_with_db_portal: TestClient) -> None:
        ast = _ast(
            app_with_db_portal.get(
                "/db-portal/parse",
                params={"q": "date_published:[2020-01-01 TO 2024-12-31]"},
            ).json(),
        )
        assert set(ast) == {"field", "op", "from", "to"}

    def test_bool_keys(self, app_with_db_portal: TestClient) -> None:
        ast = _ast(
            app_with_db_portal.get("/db-portal/parse", params={"q": "title:a AND title:b"}).json(),
        )
        assert set(ast) == {"op", "rules"}

    def test_free_text_keys(self, app_with_db_portal: TestClient) -> None:
        ast = _ast(app_with_db_portal.get("/db-portal/parse", params={"q": "cancer"}).json())
        assert set(ast) == {"op", "value"}
        assert ast["op"] == "free_text"

    def test_full_tree_json_serializable(self, app_with_db_portal: TestClient) -> None:
        # ProblemDetails 形 / Pydantic alias も含めて JSON roundtrip が壊れないこと。
        resp = app_with_db_portal.get(
            "/db-portal/parse",
            params={"q": "cancer AND (title:cancer OR title:tumor) AND date:[2020-01-01 TO 2024-12-31]"},
        )
        body = resp.json()
        assert json.loads(json.dumps(body)) == body


# === Errors ===


class TestDbPortalParseErrorSlugs:
    """クエリ関連 9 slug の契約確認."""

    def test_unexpected_token(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"q": "title:cancer^2"})
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.unexpected_token.value
        assert body["title"] == "Bad Request"
        assert "column" in body["detail"].lower() or str(13) in body["detail"]

    def test_empty_q_unexpected_token(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"q": ""})
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.unexpected_token.value

    def test_unknown_field(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"q": "foo:bar"})
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.unknown_field.value
        assert "identifier" in body["detail"]

    def test_invalid_operator_for_field_range_on_text(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/parse",
            params={"q": "identifier:[a TO b]"},
        )
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.invalid_operator_for_field.value

    def test_invalid_operator_for_field_wildcard_on_date(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"q": "date:cancer*"})
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.invalid_operator_for_field.value

    def test_invalid_date_format(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/parse",
            params={"q": "date_published:2024-99-99"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.invalid_date_format.value
        assert "2024-99-99" in body["detail"]
        assert "YYYY-MM-DD" in body["detail"]

    def test_missing_value(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"q": 'title:""'})
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.missing_value.value

    def test_nest_depth_exceeded(self, app_with_db_portal: TestClient) -> None:
        # DEFAULT_MAX_DEPTH=5 の境界を 1 超える (wrap 6 回 → depth 6)。
        q = "title:a"
        for i in range(6):
            q = f"({q} AND title:v{i})"
        resp = app_with_db_portal.get("/db-portal/parse", params={"q": q})
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.nest_depth_exceeded.value
        assert "5" in body["detail"]  # default max_depth が detail に含まれる

    def test_invalid_freetext_position_under_or(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/parse",
            params={"q": "(cancer OR title:tumor)"},
        )
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.invalid_freetext_position.value

    def test_invalid_freetext_position_under_not(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"q": "NOT cancer"})
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.invalid_freetext_position.value

    def test_invalid_freetext_position_nested_and(self, app_with_db_portal: TestClient) -> None:
        # ネスト AND 配下 (top-level AND の直下子じゃない) も NG。
        resp = app_with_db_portal.get(
            "/db-portal/parse",
            params={"q": "(cancer AND title:tumor) AND organism:9606"},
        )
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.invalid_freetext_position.value

    def test_duplicate_freetext(self, app_with_db_portal: TestClient) -> None:
        # top-level AND の直下子に FreeText が 2 つ以上。
        resp = app_with_db_portal.get("/db-portal/parse", params={"q": "cancer AND tumor"})
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.duplicate_freetext.value

    def test_field_not_available_in_cross_db_enum_exists(self) -> None:
        # 実発火は TestModeValidation で cross-mode Tier 3 のケースを別途検証する。
        # ここでは enum member が期待 URI で存在することだけ保証する。
        assert DbPortalErrorType.field_not_available_in_cross_db.value == (
            "https://ddbj.nig.ac.jp/problems/field-not-available-in-cross-db"
        )


# === Mode (db 有無) ===


class TestDbPortalParseMode:
    """db 指定有無で validator の mode が切り替わる挙動の確認."""

    def test_tier1_same_result_both_modes(self, app_with_db_portal: TestClient) -> None:
        # Tier 1 は mode を問わず同じ AST を返す。
        q = "title:cancer AND organism:human"
        r_cross = app_with_db_portal.get("/db-portal/parse", params={"q": q})
        r_single = app_with_db_portal.get("/db-portal/parse", params={"q": q, "db": "bioproject"})
        assert r_cross.status_code == 200
        assert r_single.status_code == 200
        assert r_cross.json() == r_single.json()

    def test_single_mode_unknown_field_still_rejected(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/parse",
            params={"q": "foo:bar", "db": "bioproject"},
        )
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.unknown_field.value

    def test_invalid_db_enum_returns_422(self, app_with_db_portal: TestClient) -> None:
        # FastAPI 標準の enum validation で 422 (about:blank)。
        resp = app_with_db_portal.get(
            "/db-portal/parse",
            params={"q": "title:cancer", "db": "nosuch"},
        )
        assert resp.status_code == 422


# === Boundaries ===


class TestDbPortalParseBoundaries:
    """max_length / max_depth / empty の境界動作."""

    def test_max_length_within_accepted(self, app_with_db_portal: TestClient) -> None:
        # 4096 以下のクエリは parse される (title:<3000文字> 相当)。
        long_value = "a" * 3000
        resp = app_with_db_portal.get("/db-portal/parse", params={"q": f"title:{long_value}"})
        assert resp.status_code == 200
        assert _ast(resp.json()) == {"field": "title", "op": "contains", "value": long_value}

    def test_max_length_exceeded_returns_400(self, app_with_db_portal: TestClient) -> None:
        # DEFAULT_MAX_LENGTH=4096 を超える入力は parser が unexpected-token で reject。
        resp = app_with_db_portal.get("/db-portal/parse", params={"q": "title:" + "a" * 4200})
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.unexpected_token.value

    def test_depth_5_within_accepted(self, app_with_db_portal: TestClient) -> None:
        # DEFAULT_MAX_DEPTH=5 の境界内 (wrap 4 回 → depth 5)。
        q = "title:a"
        for _ in range(4):
            q = f"({q} AND title:b)"
        resp = app_with_db_portal.get("/db-portal/parse", params={"q": q})
        assert resp.status_code == 200

    def test_depth_6_exceeds_boundary(self, app_with_db_portal: TestClient) -> None:
        # DEFAULT_MAX_DEPTH=5 を超える (wrap 6 回 → depth 6)。
        q = "title:a"
        for _ in range(6):
            q = f"({q} AND title:b)"
        resp = app_with_db_portal.get("/db-portal/parse", params={"q": q})
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.nest_depth_exceeded.value

    def test_single_leaf_minimum_ast(self, app_with_db_portal: TestClient) -> None:
        # 最小の valid query は単一 leaf (top-level は FieldClause)。
        resp = app_with_db_portal.get("/db-portal/parse", params={"q": "identifier:X"})
        assert resp.status_code == 200
        ast = _ast(resp.json())
        assert "field" in ast
        assert "rules" not in ast


# === Property-based ===


class TestDbPortalParsePBT:
    """Property-based tests — hypothesis で broad range + 健全性確認."""

    _TIER1_SAMPLES = st.sampled_from(
        [
            "title:cancer",
            "title:canc*",
            'title:"breast cancer"',
            "identifier:PRJDB1",
            "identifier:PRJ*",
            "organism:human",
            'organism:"Homo sapiens"',
            "date_published:2024-01-01",
            "date_published:[2020-01-01 TO 2024-12-31]",
            "date:[2020-01-01 TO 2024-12-31]",
            "description:trial",
            "date_modified:2024-06-15",
            "date_created:2022-03-01",
            "cancer",
            'cancer AND organism:"Homo sapiens"',
        ],
    )

    @settings(
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        max_examples=30,
        deadline=None,
    )
    @given(q=_TIER1_SAMPLES)
    def test_tier1_valid_query_always_200_and_json_roundtrip(
        self,
        app_with_db_portal: TestClient,
        q: str,
    ) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"q": q})
        assert resp.status_code == 200
        body = resp.json()
        # JSON として re-serialize できる (Pydantic discriminated union が stable)。
        assert json.loads(json.dumps(body)) == body
        # ast field は必ず含まれる。
        assert "ast" in body


# === OpenAPI spec shape ===


class TestDbPortalParseOpenAPI:
    """OpenAPI spec 上の endpoint + response schema の構造契約."""

    def test_q_is_required_query_param(self, app_with_db_portal: TestClient) -> None:
        spec = app_with_db_portal.get("/openapi.json").json()
        op = spec["paths"]["/db-portal/parse"]["get"]
        params = {p["name"]: p for p in op.get("parameters", [])}
        assert params["q"]["required"] is True
        assert params["q"]["in"] == "query"

    def test_db_is_optional_enum_query_param(self, app_with_db_portal: TestClient) -> None:
        spec = app_with_db_portal.get("/openapi.json").json()
        op = spec["paths"]["/db-portal/parse"]["get"]
        params = {p["name"]: p for p in op.get("parameters", [])}
        assert params["db"]["required"] is False
        assert params["db"]["in"] == "query"

    def test_200_response_model_is_db_portal_parse_response(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        spec = app_with_db_portal.get("/openapi.json").json()
        op = spec["paths"]["/db-portal/parse"]["get"]
        schema = op["responses"]["200"]["content"]["application/json"]["schema"]
        ref = schema.get("$ref", "")
        assert ref.endswith("/DbPortalParseResponse")

    def test_ast_property_is_discriminated_union(self, app_with_db_portal: TestClient) -> None:
        spec = app_with_db_portal.get("/openapi.json").json()
        components = spec["components"]["schemas"]
        assert "DbPortalParseResponse" in components
        ast_prop = components["DbPortalParseResponse"]["properties"]["ast"]
        # FastAPI + Pydantic v2 の discriminated union は oneOf + discriminator で出る。
        assert "oneOf" in ast_prop or "discriminator" in ast_prop or "anyOf" in ast_prop

    def test_free_text_variant_present(self, app_with_db_portal: TestClient) -> None:
        # FreeText variant が discriminated union に含まれている。
        spec = app_with_db_portal.get("/openapi.json").json()
        components = spec["components"]["schemas"]
        assert "DbPortalParseFreeText" in components
        free_text = components["DbPortalParseFreeText"]
        assert set(free_text["properties"]) >= {"op", "value"}

    def test_400_declared_on_route(self, app_with_db_portal: TestClient) -> None:
        app = cast(FastAPI, app_with_db_portal.app)
        for route in app.routes:
            if getattr(route, "path", None) == "/db-portal/parse":
                responses = getattr(route, "responses", {}) or {}
                assert 400 in responses or "400" in responses, (
                    f"Expected 400 in route.responses, got keys: {list(responses.keys())}"
                )
                return
        msg = "/db-portal/parse route not found"
        raise AssertionError(msg)

    def test_leaf_range_uses_from_alias(self, app_with_db_portal: TestClient) -> None:
        spec = app_with_db_portal.get("/openapi.json").json()
        components = spec["components"]["schemas"]
        assert "DbPortalParseLeafRange" in components
        props = components["DbPortalParseLeafRange"]["properties"]
        assert "from" in props
        assert "from_" not in props
