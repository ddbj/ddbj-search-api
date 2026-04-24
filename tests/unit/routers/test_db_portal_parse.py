"""Tests for GET /db-portal/parse (AP7).

AP7: ``adv`` DSL を SSOT query-tree JSON (search-backends.md §L363-381) に変換する
endpoint。共有 URL (``?adv=...``) から Advanced Search GUI の state を復元する用途。

AP3 の ``parse`` / ``validate`` / ``ast_to_json`` を wiring するだけで、コア DSL
処理は完全再利用。validator mode は既存 ``/db-portal/search?adv=...`` と同一
(``db`` 未指定 → ``mode='cross'`` / 指定 → ``mode='single'``)。

エラー契約: AP3 で確定した 7 slug をそのまま発火 (新 slug 追加なし)。
``advanced-search-not-implemented`` は AP3 完了後 never emitted (AP7 でも同様)。
``field-not-available-in-cross-db`` は AP3 時点で ``TIER3_FIELDS`` が空のため実際には
発火しないが、enum member としては存在する (AP6 で Tier 3 追加時に enable)。
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
    """GET /db-portal/parse: canonical path, required adv, tag."""

    def test_route_exists_requires_adv(self, app_with_db_portal: TestClient) -> None:
        # adv 未指定は FastAPI 標準の 422 (about:blank)。
        resp = app_with_db_portal.get("/db-portal/parse")
        assert resp.status_code == 422

    def test_route_accepts_minimal_adv(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"adv": "title:cancer"})
        assert resp.status_code == 200

    def test_trailing_slash_not_canonical(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse/", params={"adv": "title:cancer"})
        assert resp.status_code == 404

    def test_tag_is_db_portal(self, app_with_db_portal: TestClient) -> None:
        spec = app_with_db_portal.get("/openapi.json").json()
        operation = spec["paths"]["/db-portal/parse"]["get"]
        assert operation["tags"] == ["db-portal"]


# === Valid DSL → JSON tree ===


def _ast(resp_body: dict[str, Any]) -> dict[str, Any]:
    assert isinstance(resp_body, dict)
    ast = resp_body["ast"]
    assert isinstance(ast, dict)
    return ast


class TestDbPortalParseValidLeaf:
    """Leaf serialization — mirrors test_serde.py::TestLeafSerialization via HTTP."""

    def test_identifier_word_eq(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"adv": "identifier:PRJDB1"})
        assert resp.status_code == 200
        assert _ast(resp.json()) == {"field": "identifier", "op": "eq", "value": "PRJDB1"}

    def test_identifier_wildcard(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"adv": "identifier:PRJ*"})
        assert _ast(resp.json()) == {"field": "identifier", "op": "wildcard", "value": "PRJ*"}

    def test_title_word_contains(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"adv": "title:cancer"})
        assert _ast(resp.json()) == {"field": "title", "op": "contains", "value": "cancer"}

    def test_title_phrase_contains(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/parse",
            params={"adv": 'title:"cancer treatment"'},
        )
        assert _ast(resp.json()) == {"field": "title", "op": "contains", "value": "cancer treatment"}

    def test_title_wildcard(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"adv": "title:canc*"})
        assert _ast(resp.json()) == {"field": "title", "op": "wildcard", "value": "canc*"}

    def test_organism_eq_word(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"adv": "organism:human"})
        assert _ast(resp.json()) == {"field": "organism", "op": "eq", "value": "human"}

    def test_organism_eq_phrase_with_space(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/parse",
            params={"adv": 'organism:"Homo sapiens"'},
        )
        assert _ast(resp.json()) == {"field": "organism", "op": "eq", "value": "Homo sapiens"}

    def test_date_published_eq(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/parse",
            params={"adv": "date_published:2024-01-01"},
        )
        assert _ast(resp.json()) == {"field": "date_published", "op": "eq", "value": "2024-01-01"}

    def test_date_published_between(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/parse",
            params={"adv": "date_published:[2020-01-01 TO 2024-12-31]"},
        )
        ast = _ast(resp.json())
        # SSOT contract: range leaf は "from" / "to" で出力 (predefined key、Python 予約語回避のため alias)。
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
            params={"adv": "date:[2020-01-01 TO 2024-12-31]"},
        )
        assert _ast(resp.json()) == {
            "field": "date",
            "op": "between",
            "from": "2020-01-01",
            "to": "2024-12-31",
        }


class TestDbPortalParseValidBool:
    """BoolOp serialization — mirrors test_serde.py::TestBoolSerialization via HTTP."""

    def test_and(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"adv": "title:a AND title:b"})
        assert _ast(resp.json()) == {
            "op": "AND",
            "rules": [
                {"field": "title", "op": "contains", "value": "a"},
                {"field": "title", "op": "contains", "value": "b"},
            ],
        }

    def test_or(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"adv": "title:a OR title:b"})
        assert _ast(resp.json()) == {
            "op": "OR",
            "rules": [
                {"field": "title", "op": "contains", "value": "a"},
                {"field": "title", "op": "contains", "value": "b"},
            ],
        }

    def test_not(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"adv": "NOT title:a"})
        assert _ast(resp.json()) == {
            "op": "NOT",
            "rules": [{"field": "title", "op": "contains", "value": "a"}],
        }

    def test_ssot_sample_nested(self, app_with_db_portal: TestClient) -> None:
        # SSOT search-backends.md L363-381 の完全サンプル。
        dsl = 'organism:"Homo sapiens" AND date:[2020-01-01 TO 2024-12-31] AND (title:cancer OR title:tumor)'
        resp = app_with_db_portal.get("/db-portal/parse", params={"adv": dsl})
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
        ast = _ast(app_with_db_portal.get("/db-portal/parse", params={"adv": "title:cancer"}).json())
        assert set(ast) == {"field", "op", "value"}

    def test_leaf_range_keys(self, app_with_db_portal: TestClient) -> None:
        ast = _ast(
            app_with_db_portal.get(
                "/db-portal/parse",
                params={"adv": "date_published:[2020-01-01 TO 2024-12-31]"},
            ).json(),
        )
        assert set(ast) == {"field", "op", "from", "to"}

    def test_bool_keys(self, app_with_db_portal: TestClient) -> None:
        ast = _ast(
            app_with_db_portal.get("/db-portal/parse", params={"adv": "title:a AND title:b"}).json(),
        )
        assert set(ast) == {"op", "rules"}

    def test_full_tree_json_serializable(self, app_with_db_portal: TestClient) -> None:
        # ProblemDetails 形 / Pydantic alias も含めて JSON roundtrip が壊れないこと。
        resp = app_with_db_portal.get(
            "/db-portal/parse",
            params={"adv": "(title:cancer OR title:tumor) AND date:[2020-01-01 TO 2024-12-31]"},
        )
        body = resp.json()
        assert json.loads(json.dumps(body)) == body


# === Errors ===


class TestDbPortalParseErrorSlugs:
    """7 DSL slug (AP3 確定) + AP3 直前の never-emitted slug の契約確認.

    トリガ DSL は tests/unit/search/dsl/test_errors.py の
    TestParserErrorPosition / TestValidatorErrorPosition / TestErrorDetailEmbeddings
    と一致させてある。"""

    def test_unexpected_token(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"adv": "title:cancer^2"})
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.unexpected_token.value
        assert body["title"] == "Bad Request"
        # column 情報が detail に埋め込まれる (test_errors.py の契約)。
        assert "column" in body["detail"].lower() or str(13) in body["detail"]

    def test_empty_adv_unexpected_token(self, app_with_db_portal: TestClient) -> None:
        # Query(..., required=True) でも空文字は通るので、parser が unexpected-token で落とす。
        resp = app_with_db_portal.get("/db-portal/parse", params={"adv": ""})
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.unexpected_token.value

    def test_unknown_field(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"adv": "foo:bar"})
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.unknown_field.value
        # allowlist の候補が detail に embed されている (test_errors.py::TestErrorDetailEmbeddings)。
        assert "identifier" in body["detail"]

    def test_invalid_operator_for_field_range_on_text(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/parse",
            params={"adv": "identifier:[a TO b]"},
        )
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.invalid_operator_for_field.value

    def test_invalid_operator_for_field_wildcard_on_date(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"adv": "date:cancer*"})
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.invalid_operator_for_field.value

    def test_invalid_date_format(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get(
            "/db-portal/parse",
            params={"adv": "date_published:2024-99-99"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.invalid_date_format.value
        assert "2024-99-99" in body["detail"]
        assert "YYYY-MM-DD" in body["detail"]

    def test_missing_value(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"adv": 'title:""'})
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.missing_value.value

    def test_nest_depth_exceeded(self, app_with_db_portal: TestClient) -> None:
        # DEFAULT_MAX_DEPTH=5 の境界を 1 超える (wrap 6 回 → depth 6)。
        dsl = "title:a"
        for i in range(6):
            dsl = f"({dsl} AND title:v{i})"
        resp = app_with_db_portal.get("/db-portal/parse", params={"adv": dsl})
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.nest_depth_exceeded.value
        assert "5" in body["detail"]  # default max_depth が detail に含まれる

    def test_field_not_available_in_cross_db_enum_exists(self) -> None:
        # AP3 時点で TIER3_FIELDS は空なので runtime では発火しない。
        # AP6 で Tier 3 追加時に enable する placeholder。enum 存在だけ保証。
        assert DbPortalErrorType.field_not_available_in_cross_db.value == (
            "https://ddbj.nig.ac.jp/problems/field-not-available-in-cross-db"
        )

    def test_advanced_search_not_implemented_never_emitted(
        self,
        app_with_db_portal: TestClient,
    ) -> None:
        # AP3 完了で 501 legacy slug は never emitted (schemas/db_portal.py L48-49 コメント)。
        # parse endpoint でも同様。複数の典型的な error DSL で確認。
        for bad in ["foo:bar", "date_published:2024-99-99", 'title:""', ""]:
            resp = app_with_db_portal.get("/db-portal/parse", params={"adv": bad})
            assert resp.json().get("type") != DbPortalErrorType.advanced_search_not_implemented.value


# === Mode (db 有無) ===


class TestDbPortalParseMode:
    """db 指定有無で validator の mode が切り替わる挙動の確認."""

    def test_tier1_same_result_both_modes(self, app_with_db_portal: TestClient) -> None:
        # Tier 1 は mode を問わず同じ AST を返す。
        adv = "title:cancer AND organism:human"
        r_cross = app_with_db_portal.get("/db-portal/parse", params={"adv": adv})
        r_single = app_with_db_portal.get("/db-portal/parse", params={"adv": adv, "db": "bioproject"})
        assert r_cross.status_code == 200
        assert r_single.status_code == 200
        assert r_cross.json() == r_single.json()

    def test_single_mode_unknown_field_still_rejected(self, app_with_db_portal: TestClient) -> None:
        # test_validator.py::TestMode::test_single_mode_unknown_field_still_rejected と対応。
        resp = app_with_db_portal.get(
            "/db-portal/parse",
            params={"adv": "foo:bar", "db": "bioproject"},
        )
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.unknown_field.value

    def test_invalid_db_enum_returns_422(self, app_with_db_portal: TestClient) -> None:
        # FastAPI 標準の enum validation で 422 (about:blank)。
        resp = app_with_db_portal.get(
            "/db-portal/parse",
            params={"adv": "title:cancer", "db": "nosuch"},
        )
        assert resp.status_code == 422


# === Boundaries ===


class TestDbPortalParseBoundaries:
    """max_length / max_depth / empty の境界動作."""

    def test_max_length_within_accepted(self, app_with_db_portal: TestClient) -> None:
        # 4096 以下の DSL は parse される (title:<3000文字> 相当)。
        long_value = "a" * 3000
        resp = app_with_db_portal.get("/db-portal/parse", params={"adv": f"title:{long_value}"})
        assert resp.status_code == 200
        assert _ast(resp.json()) == {"field": "title", "op": "contains", "value": long_value}

    def test_max_length_exceeded_returns_400(self, app_with_db_portal: TestClient) -> None:
        # DEFAULT_MAX_LENGTH=4096 を超える入力は parser が unexpected-token で reject。
        resp = app_with_db_portal.get("/db-portal/parse", params={"adv": "title:" + "a" * 4200})
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.unexpected_token.value

    def test_depth_5_within_accepted(self, app_with_db_portal: TestClient) -> None:
        # DEFAULT_MAX_DEPTH=5 の境界内 (wrap 4 回 → depth 5)。
        dsl = "title:a"
        for _ in range(4):
            dsl = f"({dsl} AND title:b)"
        resp = app_with_db_portal.get("/db-portal/parse", params={"adv": dsl})
        assert resp.status_code == 200

    def test_depth_6_exceeds_boundary(self, app_with_db_portal: TestClient) -> None:
        # DEFAULT_MAX_DEPTH=5 を超える (wrap 6 回 → depth 6)。
        # test_validator.py::TestNestDepth::test_depth_6_rejected と同一境界。
        dsl = "title:a"
        for _ in range(6):
            dsl = f"({dsl} AND title:b)"
        resp = app_with_db_portal.get("/db-portal/parse", params={"adv": dsl})
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.nest_depth_exceeded.value

    def test_single_leaf_minimum_ast(self, app_with_db_portal: TestClient) -> None:
        # 最小の valid DSL は BoolOp を含まない純 leaf (top-level は FieldClause)。
        resp = app_with_db_portal.get("/db-portal/parse", params={"adv": "identifier:X"})
        assert resp.status_code == 200
        ast = _ast(resp.json())
        # top-level が leaf shape であって {"op": "AND", "rules": [...]} で wrap されていない。
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
        ],
    )

    @settings(
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        max_examples=30,
        deadline=None,
    )
    @given(adv=_TIER1_SAMPLES)
    def test_tier1_valid_dsl_always_200_and_json_roundtrip(
        self,
        app_with_db_portal: TestClient,
        adv: str,
    ) -> None:
        resp = app_with_db_portal.get("/db-portal/parse", params={"adv": adv})
        assert resp.status_code == 200
        body = resp.json()
        # JSON として re-serialize できる (Pydantic discriminated union が stable)。
        assert json.loads(json.dumps(body)) == body
        # ast field は必ず含まれる。
        assert "ast" in body

    @settings(
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        max_examples=40,
        deadline=None,
    )
    @given(
        adv=st.text(
            alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")),
            min_size=1,
            max_size=30,
        ),
    )
    def test_bare_lowercase_text_without_colon_always_400(
        self,
        app_with_db_portal: TestClient,
        adv: str,
    ) -> None:
        # ":" が含まれない純粋な小文字テキストは field:value の形にならないので必ず parser エラー。
        resp = app_with_db_portal.get("/db-portal/parse", params={"adv": adv})
        assert resp.status_code == 400
        # ProblemDetails の contract。
        body = resp.json()
        assert body["type"].startswith("https://ddbj.nig.ac.jp/problems/")


# === OpenAPI spec shape ===


class TestDbPortalParseOpenAPI:
    """OpenAPI spec 上の endpoint + response schema の構造契約."""

    def test_adv_is_required_query_param(self, app_with_db_portal: TestClient) -> None:
        spec = app_with_db_portal.get("/openapi.json").json()
        op = spec["paths"]["/db-portal/parse"]["get"]
        params = {p["name"]: p for p in op.get("parameters", [])}
        assert params["adv"]["required"] is True
        assert params["adv"]["in"] == "query"

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
        # FastAPI は $ref で Pydantic model を参照する。
        ref = schema.get("$ref", "")
        assert ref.endswith("/DbPortalParseResponse")

    def test_ast_property_is_discriminated_union(self, app_with_db_portal: TestClient) -> None:
        spec = app_with_db_portal.get("/openapi.json").json()
        components = spec["components"]["schemas"]
        assert "DbPortalParseResponse" in components
        ast_prop = components["DbPortalParseResponse"]["properties"]["ast"]
        # FastAPI + Pydantic v2 の discriminated union は oneOf + discriminator で出る。
        # version 揺れ対策で両対応。
        assert "oneOf" in ast_prop or "discriminator" in ast_prop or "anyOf" in ast_prop

    def test_400_declared_on_route(self, app_with_db_portal: TestClient) -> None:
        # FastAPI (0.128.0) の include_router 経由の responses merge で handler-level の
        # 400 が OpenAPI output に現れないことがある (router-level PROBLEM_RESPONSES の
        # 404/422/500 が出るが 400 は drop される quirk)。そこで route 定義時の
        # ``responses`` attribute を直接確認することで 400 の契約を保証する。
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
        # discriminated union の leaf range は "from" alias を使う (Python 予約語回避)。
        spec = app_with_db_portal.get("/openapi.json").json()
        components = spec["components"]["schemas"]
        # Pydantic v2 は model 名をそのまま schema 名に使う。
        assert "DbPortalParseLeafRange" in components
        props = components["DbPortalParseLeafRange"]["properties"]
        assert "from" in props
        assert "from_" not in props
