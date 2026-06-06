"""Tests for POST /db-portal/cross-search and POST /db-portal/search (AST in body).

GET (``q`` DSL 文字列) と同じ検索を AST JSON body で受ける POST 経路.  入口で
``parse(q)`` を ``json_to_ast(body.ast)`` に差し替えるだけで、validate / per-arm
reduce / compile / facet 集計の pipeline は GET と共有する.  検証の焦点は POST
固有の差分:

- ``dsl`` エコーが ``POST /serialize`` と一致する (db-portal の共有 URL 同期用).
- ``ast`` 省略 / ``null`` は match_all + ``dsl=""``.
- cross POST の query param 制約 (``q`` も含めて拒否) と per-DB の ``missing-db``.
- scope (cross=Tier3 拒否 / single=許可) と cursor 経路の ``dsl`` エコー.
- GET(q=dsl) と POST(ast) の parity (PBT): databases / dsl が一致する.

docs/db-portal-api-spec.md § AST 入力の POST 検索 が SSOT.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, assume, given, settings

from ddbj_search_api.cursor import CursorPayload, encode_cursor
from ddbj_search_api.schemas.db_portal import DbPortalErrorType
from ddbj_search_api.search.dsl.serializer import ast_to_dsl
from tests.unit.conftest import make_es_search_response
from tests.unit.strategies import valid_ast_strategy

_SOLR_DBS = ("ddbj", "taxonomy")
_ES_DBS = ("sra", "bioproject", "biosample", "jga", "gea", "metabobank")
_DB_ORDER = ("ddbj", "sra", "bioproject", "biosample", "jga", "gea", "metabobank", "taxonomy")

# ``ast`` キーを省略した body ({}) と ``"ast": null`` を区別するための sentinel.
_OMIT = object()


def _post_cross(client: TestClient, ast: Any = _OMIT, **params: Any) -> Any:
    body = {} if ast is _OMIT else {"ast": ast}
    return client.post("/db-portal/cross-search", json=body, params=params or None)


def _post_search(client: TestClient, ast: Any = _OMIT, **params: Any) -> Any:
    body = {} if ast is _OMIT else {"ast": ast}
    return client.post("/db-portal/search", json=body, params=params or None)


def _cursor_token() -> str:
    """Default-shape cursor token (排他チェックに引っかからない sort/query)."""
    payload = CursorPayload(
        pit_id=None,
        search_after=["2024-01-15", "PRJDB1"],
        sort=[
            {"datePublished": {"order": "desc"}},
            {"identifier": {"order": "asc"}},
        ],
        query={"match_all": {}},
    )
    return encode_cursor(payload)


# AST / DSL の対 (serialize は mode 非依存なので cross/single 共通).
_AST_TITLE = {"field": "title", "op": "contains", "value": "cancer"}
_DSL_TITLE = "title:cancer"
_AST_AND = {
    "op": "AND",
    "rules": [
        {"op": "free_text", "value": "cancer"},
        {"field": "organism_name", "op": "contains", "value": "Homo sapiens"},
    ],
}
_DSL_AND = 'cancer AND organism_name:"Homo sapiens"'
# Tier 3 (SRA 専用) field. cross では reject, single(db=sra) では許可.
_AST_TIER3 = {"field": "library_strategy", "op": "eq", "value": "WGS"}


# === Routing ===


class TestRouting:
    def test_cross_search_post_accepts_minimal_ast(self, app_with_db_portal: TestClient) -> None:
        resp = _post_cross(app_with_db_portal, {"op": "free_text", "value": "cancer"})
        assert resp.status_code == 200, resp.text

    def test_search_post_accepts_minimal_ast(self, app_with_db_portal: TestClient) -> None:
        resp = _post_search(app_with_db_portal, {"op": "free_text", "value": "cancer"}, db="bioproject")
        assert resp.status_code == 200, resp.text

    def test_cross_search_get_still_works(self, app_with_db_portal: TestClient) -> None:
        # POST 追加で GET 経路が壊れていないこと (両 method 共存).
        resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "cancer"})
        assert resp.status_code == 200

    def test_cross_search_post_trailing_slash_not_canonical(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.post("/db-portal/cross-search/", json={"ast": _AST_TITLE})
        assert resp.status_code == 404

    def test_search_post_trailing_slash_not_canonical(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.post("/db-portal/search/", json={"ast": _AST_TITLE}, params={"db": "bioproject"})
        assert resp.status_code == 404

    def test_tags_are_db_portal(self, app_with_db_portal: TestClient) -> None:
        spec = app_with_db_portal.get("/openapi.json").json()
        assert spec["paths"]["/db-portal/cross-search"]["post"]["tags"] == ["db-portal"]
        assert spec["paths"]["/db-portal/search"]["post"]["tags"] == ["db-portal"]


# === dsl エコー ===


class TestDslEcho:
    """POST response の ``dsl`` が ``POST /serialize`` と同一の正規化 DSL であること."""

    def test_cross_dsl_echo_simple(self, app_with_db_portal: TestClient) -> None:
        resp = _post_cross(app_with_db_portal, _AST_AND)
        assert resp.status_code == 200, resp.text
        assert resp.json()["dsl"] == _DSL_AND

    def test_search_dsl_echo_simple(self, app_with_db_portal: TestClient) -> None:
        resp = _post_search(app_with_db_portal, _AST_AND, db="bioproject")
        assert resp.status_code == 200, resp.text
        assert resp.json()["dsl"] == _DSL_AND

    def test_cross_dsl_echo_matches_serialize_endpoint(self, app_with_db_portal: TestClient) -> None:
        post = _post_cross(app_with_db_portal, _AST_AND)
        serialized = app_with_db_portal.post("/db-portal/serialize", json={"ast": _AST_AND})
        assert post.json()["dsl"] == serialized.json()["dsl"]

    def test_search_dsl_echo_tier3_field(self, app_with_db_portal: TestClient) -> None:
        # single mode では Tier 3 field も dsl エコーされる.
        resp = _post_search(app_with_db_portal, _AST_TIER3, db="sra")
        assert resp.status_code == 200, resp.text
        assert resp.json()["dsl"] == "library_strategy:WGS"


# === ast 省略 / null → match_all + dsl="" ===


class TestAstOmittedMatchAll:
    def test_cross_ast_omitted_returns_empty_dsl_and_all_dbs(self, app_with_db_portal: TestClient) -> None:
        resp = _post_cross(app_with_db_portal)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["dsl"] == ""
        assert [e["db"] for e in body["databases"]] == list(_DB_ORDER)

    def test_cross_ast_null_returns_empty_dsl(self, app_with_db_portal: TestClient) -> None:
        resp = _post_cross(app_with_db_portal, None)
        assert resp.status_code == 200, resp.text
        assert resp.json()["dsl"] == ""

    def test_search_ast_omitted_returns_empty_dsl(self, app_with_db_portal: TestClient) -> None:
        resp = _post_search(app_with_db_portal, db="bioproject")
        assert resp.status_code == 200, resp.text
        assert resp.json()["dsl"] == ""


# === invalid-ast (body schema 違反) ===


class TestInvalidAst:
    def test_cross_unknown_op_returns_400_invalid_ast(self, app_with_db_portal: TestClient) -> None:
        resp = _post_cross(app_with_db_portal, {"op": "XOR", "rules": []})
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.invalid_ast.value

    def test_search_unknown_op_returns_400_invalid_ast(self, app_with_db_portal: TestClient) -> None:
        resp = _post_search(app_with_db_portal, {"op": "XOR", "rules": []}, db="bioproject")
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.invalid_ast.value

    def test_cross_ast_not_object_returns_400_invalid_ast(self, app_with_db_portal: TestClient) -> None:
        resp = _post_cross(app_with_db_portal, "not-an-object")
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.invalid_ast.value


# === cross の query param 制約 (AST は body にあるため q も拒否) ===


class TestCrossQueryParamConstraints:
    @pytest.mark.parametrize(
        "param,value",
        [
            ("q", "cancer"),
            ("db", "sra"),
            ("cursor", "tok"),
            ("page", "2"),
            ("perPage", "50"),
            ("sort", "datePublished:desc"),
        ],
    )
    def test_cross_post_rejects_forbidden_param(self, app_with_db_portal: TestClient, param: str, value: str) -> None:
        resp = _post_cross(app_with_db_portal, _AST_TITLE, **{param: value})
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.unexpected_parameter.value
        assert param in body["detail"]

    @pytest.mark.parametrize("param", ["topHits", "keywordOperator", "facets", "facetsSize", "facetSelfExclude"])
    def test_cross_post_accepts_allowed_param(self, app_with_db_portal: TestClient, param: str) -> None:
        value = {
            "topHits": "5",
            "keywordOperator": "AND",
            "facets": "organism",
            "facetsSize": "10",
            "facetSelfExclude": "true",
        }[param]
        resp = _post_cross(app_with_db_portal, _AST_TITLE, **{param: value})
        assert resp.status_code == 200, resp.text


# === per-DB の missing-db ===


class TestSearchMissingDb:
    def test_search_post_without_db_returns_400_missing_db(self, app_with_db_portal: TestClient) -> None:
        resp = _post_search(app_with_db_portal, _AST_TITLE)
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.missing_db.value


# === scope バリデーション (cross=Tier3 拒否 / single=許可) ===


class TestScopeValidation:
    def test_cross_tier3_field_returns_400(self, app_with_db_portal: TestClient) -> None:
        resp = _post_cross(app_with_db_portal, _AST_TIER3)
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.field_not_available_in_cross_db.value

    def test_search_tier3_field_accepted_for_owning_db(self, app_with_db_portal: TestClient) -> None:
        resp = _post_search(app_with_db_portal, _AST_TIER3, db="sra")
        assert resp.status_code == 200, resp.text

    def test_search_tier3_field_rejected_for_other_db(self, app_with_db_portal: TestClient) -> None:
        # library_strategy は SRA 専用. db=bioproject では当該 DB に無い → 400.
        resp = _post_search(app_with_db_portal, _AST_TIER3, db="bioproject")
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.field_not_available_for_db.value


# === cursor との相互作用 (per-DB) ===


class TestCursorInteraction:
    def test_cursor_with_solr_db_returns_400_cursor_not_supported(self, app_with_db_portal: TestClient) -> None:
        resp = _post_search(app_with_db_portal, _AST_TITLE, db="ddbj", cursor=_cursor_token())
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.cursor_not_supported.value

    def test_cursor_with_page_returns_400_exclusivity(self, app_with_db_portal: TestClient) -> None:
        resp = _post_search(app_with_db_portal, _AST_TITLE, db="bioproject", cursor=_cursor_token(), page="2")
        assert resp.status_code == 400

    def test_cursor_echoes_dsl_from_body_ast(
        self,
        app_with_db_portal: TestClient,
        mock_es_open_pit_db_portal: Any,
        mock_es_search_with_pit_db_portal: Any,
    ) -> None:
        # cursor 経路は token の query で検索するが、dsl は body AST から生成して返す
        # (「もっと見る」全ページで共有 URL を正しく保つ).
        mock_es_search_with_pit_db_portal.return_value = make_es_search_response(total=0)
        resp = _post_search(app_with_db_portal, _AST_TITLE, db="bioproject", cursor=_cursor_token())
        assert resp.status_code == 200, resp.text
        assert resp.json()["dsl"] == _DSL_TITLE


# === facets 同梱 (GET と同じ仕組み) ===


class TestFacetsCarried:
    def test_cross_facets_param_parity_with_get(self, app_with_db_portal: TestClient) -> None:
        get_resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": "cancer", "facets": "organism"})
        post_resp = _post_cross(app_with_db_portal, {"op": "free_text", "value": "cancer"}, facets="organism")
        assert post_resp.status_code == get_resp.status_code == 200
        assert post_resp.json()["facets"] == get_resp.json()["facets"]


# === GET ↔ POST parity (PBT) ===


class TestGetPostParity:
    """任意の有効 AST で GET(q=dsl) と POST(ast) が同結果を返すこと.

    valid_ast_strategy は Tier 1/2 のみ・``validate(mode="cross")`` を満たす AST を
    生成する.  ``ast_to_dsl`` で DSL 化 → ``GET /parse`` で JSON AST に戻し、GET / POST
    に同じ条件を流す.  GET と POST はパイプラインを共有するので、出力が割れたら
    POST 側の wiring 回帰.
    """

    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=40, deadline=None)
    @given(node=valid_ast_strategy())
    def test_cross_get_post_parity(self, app_with_db_portal: TestClient, node: Any) -> None:
        dsl = ast_to_dsl(node)
        parsed = app_with_db_portal.get("/db-portal/parse", params={"q": dsl})
        assume(parsed.status_code == 200)
        json_ast = parsed.json()["ast"]
        get_resp = app_with_db_portal.get("/db-portal/cross-search", params={"q": dsl})
        post_resp = _post_cross(app_with_db_portal, json_ast)
        assert post_resp.status_code == get_resp.status_code, (dsl, post_resp.json())
        if get_resp.status_code == 200:
            assert post_resp.json()["databases"] == get_resp.json()["databases"]

    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=40, deadline=None)
    @given(node=valid_ast_strategy())
    def test_cross_dsl_echo_equals_serialize(self, app_with_db_portal: TestClient, node: Any) -> None:
        dsl = ast_to_dsl(node)
        parsed = app_with_db_portal.get("/db-portal/parse", params={"q": dsl})
        assume(parsed.status_code == 200)
        json_ast = parsed.json()["ast"]
        post_resp = _post_cross(app_with_db_portal, json_ast)
        serialized = app_with_db_portal.post("/db-portal/serialize", json={"ast": json_ast})
        assume(serialized.status_code == 200)
        assert post_resp.status_code == 200, post_resp.json()
        assert post_resp.json()["dsl"] == serialized.json()["dsl"]

    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=30, deadline=None)
    @given(node=valid_ast_strategy())
    def test_search_get_post_parity(self, app_with_db_portal: TestClient, node: Any) -> None:
        dsl = ast_to_dsl(node)
        parsed = app_with_db_portal.get("/db-portal/parse", params={"q": dsl})
        assume(parsed.status_code == 200)
        json_ast = parsed.json()["ast"]
        get_resp = app_with_db_portal.get("/db-portal/search", params={"q": dsl, "db": "bioproject"})
        post_resp = _post_search(app_with_db_portal, json_ast, db="bioproject")
        assert post_resp.status_code == get_resp.status_code, (dsl, post_resp.json())
        if get_resp.status_code == 200:
            get_body = get_resp.json()
            post_body = post_resp.json()
            assert post_body["total"] == get_body["total"]
            assert post_body["hits"] == get_body["hits"]


# === OpenAPI ===


class TestOpenAPI:
    def test_cross_post_response_model_has_dsl(self, app_with_db_portal: TestClient) -> None:
        spec = app_with_db_portal.get("/openapi.json").json()
        op = spec["paths"]["/db-portal/cross-search"]["post"]
        ref = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith("/DbPortalCrossSearchByAstResponse")
        model = spec["components"]["schemas"]["DbPortalCrossSearchByAstResponse"]
        assert "dsl" in model["properties"]

    def test_search_post_response_model_has_dsl(self, app_with_db_portal: TestClient) -> None:
        spec = app_with_db_portal.get("/openapi.json").json()
        op = spec["paths"]["/db-portal/search"]["post"]
        ref = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith("/DbPortalHitsByAstResponse")
        model = spec["components"]["schemas"]["DbPortalHitsByAstResponse"]
        assert "dsl" in model["properties"]

    def test_request_body_reuses_parse_node(self, app_with_db_portal: TestClient) -> None:
        spec = app_with_db_portal.get("/openapi.json").json()
        components = spec["components"]["schemas"]
        assert "DbPortalSearchByAstRequest" in components
        ast_prop = components["DbPortalSearchByAstRequest"]["properties"]["ast"]
        assert "oneOf" in ast_prop or "anyOf" in ast_prop or "discriminator" in ast_prop
