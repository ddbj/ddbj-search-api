"""Tests for POST /db-portal/serialize.

AST JSON tree (``GET /db-portal/parse`` 形式) を DSL 文字列に逆変換する endpoint.
``serializer.ast_to_dsl`` と ``serde.json_to_ast`` を wiring するだけで、コア処理は
完全再利用.  validator mode は ``/db-portal/parse`` と同一 (``db`` 未指定 →
``mode='cross'`` / 指定 → ``mode='single'``).

エラー契約:
- body schema 違反 → 400 ``invalid-ast`` (RequestValidationError handler 経由).
- AST validate → 既存 DSL parser/validator のエラー slug (``unknown-field`` 等).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ddbj_search_api.schemas.db_portal import DbPortalErrorType

# === Routing ===


class TestDbPortalSerializeRouting:
    def test_route_exists_accepts_minimal_body(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.post(
            "/db-portal/serialize",
            json={"ast": {"op": "free_text", "value": "cancer"}},
        )
        assert resp.status_code == 200

    def test_get_returns_405(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.get("/db-portal/serialize")
        assert resp.status_code == 405

    def test_trailing_slash_not_canonical(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.post(
            "/db-portal/serialize/",
            json={"ast": {"op": "free_text", "value": "cancer"}},
        )
        assert resp.status_code == 404

    def test_tag_is_db_portal(self, app_with_db_portal: TestClient) -> None:
        spec = app_with_db_portal.get("/openapi.json").json()
        assert spec["paths"]["/db-portal/serialize"]["post"]["tags"] == ["db-portal"]


# === Spec §4 conversion examples (HTTP 経由) ===


def _post(client: TestClient, ast: dict[str, Any], **params: str) -> Any:
    return client.post("/db-portal/serialize", json={"ast": ast}, params=params or None)


class TestSerializeSpecExamples:
    """依頼書 (db-portal/.claude/docs/api-requests/serialize-endpoint.md §4) の変換例."""

    def test_simple_keyword(self, app_with_db_portal: TestClient) -> None:
        resp = _post(app_with_db_portal, {"op": "free_text", "value": "cancer"})
        assert resp.status_code == 200
        assert resp.json() == {"dsl": "cancer"}

    def test_single_field_contains_phrase(self, app_with_db_portal: TestClient) -> None:
        resp = _post(
            app_with_db_portal,
            {"field": "organism_name", "op": "contains", "value": "Homo sapiens"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"dsl": 'organism_name:"Homo sapiens"'}

    def test_free_text_plus_field(self, app_with_db_portal: TestClient) -> None:
        ast = {
            "op": "AND",
            "rules": [
                {"op": "free_text", "value": "cancer"},
                {"field": "organism_name", "op": "contains", "value": "Homo sapiens"},
            ],
        }
        resp = _post(app_with_db_portal, ast)
        assert resp.status_code == 200
        assert resp.json() == {"dsl": 'cancer AND organism_name:"Homo sapiens"'}

    def test_or_inside_and_requires_paren(self, app_with_db_portal: TestClient) -> None:
        # 依頼書 §4 の OR + 括弧例は FreeText 同士 OR を含むが、validator が
        # invalid-freetext-position で reject するので、ここでは FieldClause 同士 OR で確認.
        ast = {
            "op": "AND",
            "rules": [
                {
                    "op": "OR",
                    "rules": [
                        {"field": "title", "op": "contains", "value": "cancer"},
                        {"field": "title", "op": "contains", "value": "tumor"},
                    ],
                },
                {"field": "organism_name", "op": "contains", "value": "Homo sapiens"},
            ],
        }
        resp = _post(app_with_db_portal, ast)
        assert resp.status_code == 200
        assert resp.json() == {
            "dsl": '(title:cancer OR title:tumor) AND organism_name:"Homo sapiens"',
        }

    def test_not_with_and_parent(self, app_with_db_portal: TestClient) -> None:
        ast = {
            "op": "AND",
            "rules": [
                {"op": "free_text", "value": "cancer"},
                {
                    "op": "NOT",
                    "rules": [
                        {"field": "organism_name", "op": "contains", "value": "Mus musculus"},
                    ],
                },
            ],
        }
        resp = _post(app_with_db_portal, ast)
        assert resp.status_code == 200
        assert resp.json() == {"dsl": 'cancer AND NOT organism_name:"Mus musculus"'}

    def test_range(self, app_with_db_portal: TestClient) -> None:
        ast = {
            "field": "date_published",
            "op": "between",
            "from": "2020-01-01",
            "to": "2024-12-31",
        }
        resp = _post(app_with_db_portal, ast)
        assert resp.status_code == 200
        assert resp.json() == {"dsl": "date_published:[2020-01-01 TO 2024-12-31]"}

    def test_wildcard(self, app_with_db_portal: TestClient) -> None:
        # 依頼書 §4 wildcard 例の ``gene`` は allowlist 外.  ここでは ``identifier`` で代替.
        resp = _post(app_with_db_portal, {"field": "identifier", "op": "wildcard", "value": "PRJDB*"})
        assert resp.status_code == 200
        assert resp.json() == {"dsl": "identifier:PRJDB*"}


# === Error contract ===


class TestInvalidAstSlug:
    """body 形が schema に合わない場合は 400 ``invalid-ast`` (Pydantic 422 ではない)."""

    def test_unknown_op_discriminator(self, app_with_db_portal: TestClient) -> None:
        resp = _post(app_with_db_portal, {"op": "XOR", "rules": []})
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.invalid_ast.value
        assert body["title"] == "Bad Request"

    def test_missing_required_field_on_leaf(self, app_with_db_portal: TestClient) -> None:
        # field 指定の leaf は value 必須.
        resp = _post(app_with_db_portal, {"field": "title", "op": "eq"})
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.invalid_ast.value

    def test_missing_ast_key(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.post("/db-portal/serialize", json={})
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.invalid_ast.value

    def test_ast_not_object(self, app_with_db_portal: TestClient) -> None:
        resp = app_with_db_portal.post("/db-portal/serialize", json={"ast": "not-an-object"})
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.invalid_ast.value

    def test_range_missing_to(self, app_with_db_portal: TestClient) -> None:
        resp = _post(
            app_with_db_portal,
            {"field": "date_published", "op": "between", "from": "2020-01-01"},
        )
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.invalid_ast.value


class TestValidatorErrorSlugs:
    """既存 DSL validator から伝播するエラー slug を 400 + RFC 7807 で返す."""

    def test_unknown_field(self, app_with_db_portal: TestClient) -> None:
        resp = _post(
            app_with_db_portal,
            {"field": "nonexistent", "op": "eq", "value": "x"},
        )
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.unknown_field.value

    def test_invalid_operator_for_field(self, app_with_db_portal: TestClient) -> None:
        # date field に wildcard.  json_to_ast は (date, wildcard) を value_kind=phrase に推定
        # → validate で (date, phrase) は OPERATOR_BY_KIND に無いので invalid-operator-for-field.
        resp = _post(
            app_with_db_portal,
            {"field": "date_published", "op": "wildcard", "value": "20*"},
        )
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.invalid_operator_for_field.value

    def test_invalid_date_format(self, app_with_db_portal: TestClient) -> None:
        resp = _post(
            app_with_db_portal,
            {"field": "date_published", "op": "eq", "value": "2024-99-99"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.invalid_date_format.value
        assert "2024-99-99" in body["detail"]

    def test_invalid_freetext_position_under_or(self, app_with_db_portal: TestClient) -> None:
        ast = {
            "op": "OR",
            "rules": [
                {"op": "free_text", "value": "cancer"},
                {"field": "title", "op": "contains", "value": "tumor"},
            ],
        }
        resp = _post(app_with_db_portal, ast)
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.invalid_freetext_position.value

    def test_duplicate_freetext(self, app_with_db_portal: TestClient) -> None:
        ast = {
            "op": "AND",
            "rules": [
                {"op": "free_text", "value": "cancer"},
                {"op": "free_text", "value": "tumor"},
            ],
        }
        resp = _post(app_with_db_portal, ast)
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.duplicate_freetext.value


# === Mode (db query param) ===


class TestDbModeSwitch:
    def test_tier1_same_result_both_modes(self, app_with_db_portal: TestClient) -> None:
        ast = {"field": "title", "op": "contains", "value": "cancer"}
        r_cross = _post(app_with_db_portal, ast)
        r_single = _post(app_with_db_portal, ast, db="bioproject")
        assert r_cross.status_code == 200
        assert r_single.status_code == 200
        assert r_cross.json() == r_single.json()

    def test_cross_mode_rejects_tier3_field(self, app_with_db_portal: TestClient) -> None:
        # library_strategy は Tier 3 (SRA only).
        ast = {"field": "library_strategy", "op": "eq", "value": "WGS"}
        resp = _post(app_with_db_portal, ast)
        assert resp.status_code == 400
        assert resp.json()["type"] == DbPortalErrorType.field_not_available_in_cross_db.value

    def test_single_mode_accepts_tier3_field(self, app_with_db_portal: TestClient) -> None:
        ast = {"field": "library_strategy", "op": "eq", "value": "WGS"}
        resp = _post(app_with_db_portal, ast, db="sra")
        assert resp.status_code == 200
        assert resp.json() == {"dsl": "library_strategy:WGS"}

    def test_invalid_db_enum_returns_422(self, app_with_db_portal: TestClient) -> None:
        # query 由来の validation error は通常通り 422 (invalid-ast 化しない).
        ast = {"field": "title", "op": "contains", "value": "cancer"}
        resp = _post(app_with_db_portal, ast, db="nosuch")
        assert resp.status_code == 422

    def test_body_and_query_both_invalid_body_wins(self, app_with_db_portal: TestClient) -> None:
        # body schema 違反 + query db enum 不正が同時に起きた場合は body が優先される.
        # any(loc[0]=="body") で body 由来があれば 400 invalid-ast 化する設計.
        resp = app_with_db_portal.post(
            "/db-portal/serialize",
            json={"ast": {"op": "XOR", "rules": []}},
            params={"db": "nosuch"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["type"] == DbPortalErrorType.invalid_ast.value


class TestErrorDetailStripsDummyColumn:
    """json_to_ast 経由は元 DSL を持たないので、validator detail の
    ``at column N (length M)`` 表記を strip する.  parser 由来 Position は dummy で
    column=1, length=0 になり、その表記は client にとって誤誘導になるため.
    """

    def test_unknown_field_detail_has_no_column_info(self, app_with_db_portal: TestClient) -> None:
        resp = _post(app_with_db_portal, {"field": "nonexistent", "op": "eq", "value": "x"})
        assert resp.status_code == 400
        # ``at column N`` / ``(length M)`` 表記が detail から除去されている.  本文の
        # ``2 literal characters are required ...`` 等の意味ある説明は残る.
        assert "at column" not in resp.json()["detail"]

    def test_invalid_date_detail_has_no_column_info(self, app_with_db_portal: TestClient) -> None:
        resp = _post(
            app_with_db_portal,
            {"field": "date_published", "op": "eq", "value": "2024-99-99"},
        )
        assert resp.status_code == 400
        assert "at column" not in resp.json()["detail"]


# === Response shape ===


class TestResponseShape:
    def test_dsl_only(self, app_with_db_portal: TestClient) -> None:
        resp = _post(app_with_db_portal, {"op": "free_text", "value": "cancer"})
        body = resp.json()
        assert set(body) == {"dsl"}
        assert isinstance(body["dsl"], str)

    def test_response_json_roundtrip(self, app_with_db_portal: TestClient) -> None:
        resp = _post(
            app_with_db_portal,
            {"field": "title", "op": "contains", "value": "cancer treatment"},
        )
        body = resp.json()
        assert json.loads(json.dumps(body)) == body


# === Parse ↔ Serialize 対称性 (HTTP 経由) ===


class TestParseSerializeSymmetry:
    """``GET /parse`` の出力を ``POST /serialize`` に渡して元の DSL 相当が戻ること.

    parametrize で各種 corner case (DATE-shape value、reserved literal、wildcard、
    range、深いネスト、phrase escape) を網羅する.
    """

    @pytest.mark.parametrize(
        "dsl",
        [
            # 基本 leaf / FreeText
            "cancer",
            'organism_name:"Homo sapiens"',
            "identifier:PRJDB1234",
            "title:cancer",
            # wildcard
            "identifier:PRJ*",
            "title:canc*",
            # phrase escape + 空白
            'title:"cancer treatment"',
            # range / date
            "date:[2020-01-01 TO 2024-12-31]",
            "date_published:2024-01-01",
            # NOT (atom と BoolOp 子)
            "NOT title:cancer",
            "NOT (title:a AND title:b)",
            # 複合 (top-level AND with FreeText / OR inside AND)
            'cancer AND organism_name:"Homo sapiens"',
            "(title:a OR title:b) AND title:c",
            'organism_name:"Homo sapiens" AND date_published:[2020-01-01 TO 2024-12-31] '
            "AND (title:cancer OR title:tumor)",
            # token-collision corner: DATE-shape value as identifier / reserved literal as text
            'identifier:"2024-01-01"',
            'title:"AND"',
            'title:"NOT"',
        ],
    )
    def test_round_trip_via_http(self, app_with_db_portal: TestClient, dsl: str) -> None:
        parsed = app_with_db_portal.get("/db-portal/parse", params={"q": dsl})
        assert parsed.status_code == 200, parsed.json()
        ast = parsed.json()["ast"]

        serialized = app_with_db_portal.post("/db-portal/serialize", json={"ast": ast})
        assert serialized.status_code == 200, serialized.json()
        re_dsl = serialized.json()["dsl"]

        # 再度 parse して同じ AST に戻る.
        reparsed = app_with_db_portal.get("/db-portal/parse", params={"q": re_dsl})
        assert reparsed.status_code == 200, reparsed.json()
        assert reparsed.json()["ast"] == ast


# === OpenAPI ===


class TestOpenAPI:
    def test_endpoint_present(self, app_with_db_portal: TestClient) -> None:
        spec = app_with_db_portal.get("/openapi.json").json()
        assert "/db-portal/serialize" in spec["paths"]
        assert "post" in spec["paths"]["/db-portal/serialize"]

    def test_200_response_model(self, app_with_db_portal: TestClient) -> None:
        spec = app_with_db_portal.get("/openapi.json").json()
        op = spec["paths"]["/db-portal/serialize"]["post"]
        schema = op["responses"]["200"]["content"]["application/json"]["schema"]
        ref = schema.get("$ref", "")
        assert ref.endswith("/DbPortalSerializeResponse")

    def test_400_declared(self, app_with_db_portal: TestClient) -> None:
        spec = app_with_db_portal.get("/openapi.json").json()
        op = spec["paths"]["/db-portal/serialize"]["post"]
        assert "400" in op["responses"]

    def test_request_body_schema_reuses_parse_node(self, app_with_db_portal: TestClient) -> None:
        spec = app_with_db_portal.get("/openapi.json").json()
        components = spec["components"]["schemas"]
        assert "DbPortalSerializeRequest" in components
        ast_prop = components["DbPortalSerializeRequest"]["properties"]["ast"]
        # discriminated union として表現される (anyOf/oneOf with discriminator).
        assert "oneOf" in ast_prop or "anyOf" in ast_prop or "discriminator" in ast_prop

    def test_invalid_ast_enum_present_in_problem_details(self, app_with_db_portal: TestClient) -> None:
        # 新規 enum 値が schema に出ているか.
        spec = app_with_db_portal.get("/openapi.json").json()
        # DbPortalErrorType は enum なので components に出ない (URI 値が直接埋まる) ことが多い.
        # ここでは enum 値が schema に登録されているか or 値が文字列リテラルで使用されているかを確認.
        spec_text = json.dumps(spec)
        assert "invalid-ast" in spec_text
