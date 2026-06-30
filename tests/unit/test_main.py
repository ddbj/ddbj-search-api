"""Tests for ddbj_search_api.main.

Tests the app factory, X-Request-ID middleware, CORS headers,
error handlers, and OpenAPI customisation.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openapi_spec_validator import validate as validate_openapi_spec

from ddbj_search_api.config import AppConfig
from ddbj_search_api.main import create_app
from tests._required_list_fields import (
    REQUIRED_LIST_FIELDS_BIOPROJECT,
    REQUIRED_LIST_FIELDS_BIOSAMPLE,
    REQUIRED_LIST_FIELDS_GEA,
    REQUIRED_LIST_FIELDS_JGA,
    REQUIRED_LIST_FIELDS_METABOBANK,
    REQUIRED_LIST_FIELDS_SRA,
)

# === App factory ===


class TestCreateApp:
    """create_app: returns a configured FastAPI instance."""

    def test_returns_fastapi_instance(self) -> None:
        app = create_app(AppConfig())
        assert isinstance(app, FastAPI)

    def test_title(self) -> None:
        app = create_app(AppConfig())
        assert app.title == "DDBJ Search API"

    def test_root_path_matches_url_prefix(self) -> None:
        config = AppConfig(url_prefix="/custom/prefix")
        app = create_app(config)
        assert app.root_path == "/custom/prefix"

    def test_redirect_slashes_disabled(self) -> None:
        app = create_app(AppConfig())
        assert app.router.redirect_slashes is False


# === X-Request-ID middleware ===


class TestXRequestIdMiddleware:
    """X-Request-ID: generated or echoed in every response."""

    def test_generates_uuid_when_not_provided(self, app: TestClient) -> None:
        resp = app.get("/service-info")
        request_id = resp.headers.get("X-Request-ID")
        assert request_id is not None
        uuid.UUID(request_id)  # raises ValueError if not valid UUID

    def test_echoes_client_provided_id(self, app: TestClient) -> None:
        resp = app.get(
            "/service-info",
            headers={"X-Request-ID": "my-custom-id"},
        )
        assert resp.headers["X-Request-ID"] == "my-custom-id"

    def test_generates_uuid_when_empty_string_provided(self, app: TestClient) -> None:
        """nginx forwards an empty ``X-Request-ID`` when the caller did not send one;
        the middleware must treat that the same as a missing header (UUID v4)."""
        resp = app.get(
            "/service-info",
            headers={"X-Request-ID": ""},
        )
        request_id = resp.headers.get("X-Request-ID")
        assert request_id
        uuid.UUID(request_id)  # raises ValueError if the middleware echoed ""

    def test_present_on_error_responses(self, app: TestClient) -> None:
        resp = app.get("/nonexistent-path")
        assert "X-Request-ID" in resp.headers


# === CORS ===


class TestCORS:
    """CORS headers: allow all origins."""

    def test_access_control_allow_origin(self, app: TestClient) -> None:
        resp = app.get(
            "/service-info",
            headers={"Origin": "https://example.com"},
        )
        assert resp.headers.get("access-control-allow-origin") == "*"

    def test_preflight_request(self, app: TestClient) -> None:
        resp = app.options(
            "/service-info",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-Request-ID",
            },
        )
        assert resp.headers.get("access-control-allow-origin") == "*"
        assert "GET" in resp.headers.get("access-control-allow-methods", "")


# === Error handlers ===


class TestErrorHandlerNotImplemented:
    """NotImplementedError -> 501 with RFC 7807 ProblemDetails.

    Uses a temporary route that raises NotImplementedError, since all
    real endpoints are now implemented.
    """

    @pytest.fixture
    def app_with_not_implemented(self) -> TestClient:
        """Create an app with a route that raises NotImplementedError."""
        application = create_app(AppConfig())

        @application.get("/test-not-implemented")
        async def _raise_not_implemented() -> None:
            raise NotImplementedError

        return TestClient(application, raise_server_exceptions=False)

    def test_status_code_501(
        self,
        app_with_not_implemented: TestClient,
    ) -> None:
        resp = app_with_not_implemented.get("/test-not-implemented")
        assert resp.status_code == 501

    def test_problem_details_structure(
        self,
        app_with_not_implemented: TestClient,
    ) -> None:
        resp = app_with_not_implemented.get("/test-not-implemented")
        body = resp.json()
        assert body["type"] == "about:blank"
        assert body["title"] == "Not Implemented"
        assert body["status"] == 501
        assert "detail" in body
        assert "instance" in body
        assert "timestamp" in body
        assert "requestId" in body

    def test_content_type_is_problem_json(
        self,
        app_with_not_implemented: TestClient,
    ) -> None:
        resp = app_with_not_implemented.get("/test-not-implemented")
        assert "application/problem+json" in resp.headers["content-type"]

    def test_request_id_in_body_matches_header(
        self,
        app_with_not_implemented: TestClient,
    ) -> None:
        resp = app_with_not_implemented.get(
            "/test-not-implemented",
            headers={"X-Request-ID": "test-req-id"},
        )
        body = resp.json()
        assert body["requestId"] == "test-req-id"
        assert resp.headers["X-Request-ID"] == "test-req-id"


class TestErrorHandlerValidation:
    """RequestValidationError -> 422 with RFC 7807 ProblemDetails."""

    def test_invalid_per_page_returns_422(self, app: TestClient) -> None:
        resp = app.get("/entries/", params={"perPage": -1})
        assert resp.status_code == 422

    def test_validation_error_has_problem_details(self, app: TestClient) -> None:
        resp = app.get("/entries/", params={"perPage": -1})
        body = resp.json()
        assert body["status"] == 422
        assert body["title"] == "Unprocessable Entity"
        assert "detail" in body

    def test_invalid_db_type_returns_404(self, app: TestClient) -> None:
        """Invalid DB type in path returns 404 Not Found."""
        resp = app.get("/entries/invalid-type/PRJDB1")
        assert resp.status_code == 404
        body = resp.json()
        assert body["status"] == 404
        assert "invalid-type" in body["detail"]


class TestErrorHandlerNotFound:
    """404 for truly unknown paths."""

    def test_unknown_path_returns_404(self, app: TestClient) -> None:
        resp = app.get("/completely-unknown")
        assert resp.status_code == 404

    def test_404_has_problem_details(self, app: TestClient) -> None:
        resp = app.get("/completely-unknown")
        body = resp.json()
        assert body["status"] == 404
        assert "detail" in body


# === OpenAPI customisation ===


class TestOpenAPICustomisation:
    """OpenAPI schema: no FastAPI default validation error schemas."""

    def test_no_http_validation_error_schema(self) -> None:
        app = create_app(AppConfig())
        schema = app.openapi()
        schemas = schema.get("components", {}).get("schemas", {})
        assert "HTTPValidationError" not in schemas

    def test_no_validation_error_schema(self) -> None:
        app = create_app(AppConfig())
        schema = app.openapi()
        schemas = schema.get("components", {}).get("schemas", {})
        assert "ValidationError" not in schemas

    def test_servers_publish_public_absolute_urls(self) -> None:
        """SDK clients need absolute production / staging URLs, not just the relative path."""
        app = create_app(AppConfig())
        schema = app.openapi()
        urls = [s.get("url") for s in schema.get("servers", [])]
        assert "https://ddbj.nig.ac.jp/search/api" in urls
        assert "https://ddbj-staging.nig.ac.jp/search/api" in urls
        # The relative URL stays so reverse-proxy callers keep their existing path.
        assert "/search/api" in urls


# === OpenAPI required array fields ===
#
# converter で list[X] 必須化された項目は、子クラスの *DetailResponse / *EntryJsonLdResponse で
# 再宣言しなくても継承で required になる。SDK 利用者が見るのは OpenAPI スキーマ生成結果なので、
# raw / Detail / JsonLd すべての schema で `required` に含まれることを end-to-end 確認する。

_OPENAPI_REQUIRED_LIST_FIELDS: dict[str, list[str]] = {
    "BioProject": REQUIRED_LIST_FIELDS_BIOPROJECT,
    "BioProjectDetailResponse": REQUIRED_LIST_FIELDS_BIOPROJECT,
    "BioProjectEntryJsonLdResponse": REQUIRED_LIST_FIELDS_BIOPROJECT,
    "BioSample": REQUIRED_LIST_FIELDS_BIOSAMPLE,
    "BioSampleDetailResponse": REQUIRED_LIST_FIELDS_BIOSAMPLE,
    "BioSampleEntryJsonLdResponse": REQUIRED_LIST_FIELDS_BIOSAMPLE,
    "SRA": REQUIRED_LIST_FIELDS_SRA,
    "SraDetailResponse": REQUIRED_LIST_FIELDS_SRA,
    "SraEntryJsonLdResponse": REQUIRED_LIST_FIELDS_SRA,
    "JGA": REQUIRED_LIST_FIELDS_JGA,
    "JgaDetailResponse": REQUIRED_LIST_FIELDS_JGA,
    "JgaEntryJsonLdResponse": REQUIRED_LIST_FIELDS_JGA,
    "GEA": REQUIRED_LIST_FIELDS_GEA,
    "GeaDetailResponse": REQUIRED_LIST_FIELDS_GEA,
    "GeaEntryJsonLdResponse": REQUIRED_LIST_FIELDS_GEA,
    "MetaboBank": REQUIRED_LIST_FIELDS_METABOBANK,
    "MetaboBankDetailResponse": REQUIRED_LIST_FIELDS_METABOBANK,
    "MetaboBankEntryJsonLdResponse": REQUIRED_LIST_FIELDS_METABOBANK,
}


class TestOpenAPIRequiredArrayFields:
    """All entry-related OpenAPI schemas surface converter-required list fields in `required`."""

    @pytest.fixture(scope="class")
    def schema(self) -> dict[str, Any]:
        return create_app(AppConfig()).openapi()

    @pytest.mark.parametrize(
        ("schema_name", "field"),
        [(name, field) for name, fields in _OPENAPI_REQUIRED_LIST_FIELDS.items() for field in fields],
        ids=[f"{name}.{field}" for name, fields in _OPENAPI_REQUIRED_LIST_FIELDS.items() for field in fields],
    )
    def test_field_in_required(
        self,
        schema: dict[str, Any],
        schema_name: str,
        field: str,
    ) -> None:
        target = schema["components"]["schemas"][schema_name]
        required = target.get("required", [])
        assert field in required, f"OpenAPI schema {schema_name}: required does not include {field}"


# === OpenAPI descriptions on converter-origin schemas ===


_CONVERTER_ENTITY_SCHEMAS: tuple[str, ...] = (
    "BioProject",
    "BioSample",
    "SRA",
    "JGA",
    "GEA",
    "MetaboBank",
)

_CONVERTER_NESTED_SCHEMAS: tuple[str, ...] = (
    "Distribution",
    "Organism",
    "Organization",
    "Publication",
    "Grant",
    "ExternalLink",
    "Xref",
    "BioSamplePackage",
)

_CONVERTER_SCHEMAS: tuple[str, ...] = _CONVERTER_ENTITY_SCHEMAS + _CONVERTER_NESTED_SCHEMAS

# Enum-bearing fields whose description must spell out the value semantics
# (rather than just labelling the field). Limited to enums with small
# value spaces where listing each value's meaning is feasible; broader
# enums (`Xref.type` with 21 values, `Organization.organizationType`)
# carry a categorical description instead.
_CONVERTER_ENUM_DESCRIPTION_KEYWORDS: list[tuple[str, str, tuple[str, ...]]] = [
    ("Organization", "role", ("owner", "broker")),
    ("Publication", "dbType", ("pubmed", "doi")),
    ("Distribution", "encodingFormat", ("JSON", "FASTQ")),
    ("BioProject", "objectType", ("UmbrellaBioProject",)),
]


class TestOpenAPIConverterDescriptions:
    """Converter-origin schemas surface description + ``additionalProperties`` so TS codegen produces JSDoc.

    These assertions catch regressions where a future converter refactor
    drops a ``Field(description=...)`` or unwraps the ``properties:
    dict[str, Any]`` blob back into bare ``Any``.
    """

    @pytest.fixture(scope="class")
    def schema(self) -> dict[str, Any]:
        return create_app(AppConfig()).openapi()

    @pytest.mark.parametrize("schema_name", _CONVERTER_SCHEMAS)
    def test_schema_level_description_present(
        self,
        schema: dict[str, Any],
        schema_name: str,
    ) -> None:
        target = schema["components"]["schemas"][schema_name]
        assert target.get("description"), f"{schema_name}: schema-level description missing"

    @pytest.mark.parametrize("schema_name", _CONVERTER_SCHEMAS)
    def test_every_field_has_description(
        self,
        schema: dict[str, Any],
        schema_name: str,
    ) -> None:
        target = schema["components"]["schemas"][schema_name]
        missing = [
            field_name
            for field_name, field_schema in target.get("properties", {}).items()
            if not field_schema.get("description")
        ]
        assert not missing, f"{schema_name}: fields missing description: {missing}"

    @pytest.mark.parametrize("schema_name", _CONVERTER_ENTITY_SCHEMAS)
    def test_properties_opaque_blob_has_additional_properties_and_description(
        self,
        schema: dict[str, Any],
        schema_name: str,
    ) -> None:
        properties_field = schema["components"]["schemas"][schema_name]["properties"]["properties"]
        assert properties_field.get("additionalProperties") is True, (
            f"{schema_name}.properties: missing additionalProperties=true (TS codegen falls back to opaque)"
        )
        assert properties_field.get("description"), (
            f"{schema_name}.properties: missing description (round-trip blob would look unintentional)"
        )

    @pytest.mark.parametrize(
        ("schema_name", "field_name", "required_substrings"),
        [
            (schema_name, field_name, required_substrings)
            for schema_name, field_name, required_substrings in _CONVERTER_ENUM_DESCRIPTION_KEYWORDS
        ],
    )
    def test_enum_field_description_contains_value_semantics(
        self,
        schema: dict[str, Any],
        schema_name: str,
        field_name: str,
        required_substrings: tuple[str, ...],
    ) -> None:
        # Enum-typed fields are emitted either inline ($ref) or with their
        # description on the parent property.  Inspect both layers.
        property_schema = schema["components"]["schemas"][schema_name]["properties"][field_name]
        descriptions: list[str] = []
        if "description" in property_schema:
            descriptions.append(property_schema["description"])
        for layer in property_schema.get("anyOf", []):
            if "description" in layer:
                descriptions.append(layer["description"])
            ref = layer.get("$ref")
            if ref:
                ref_name = ref.rsplit("/", 1)[-1]
                ref_schema = schema["components"]["schemas"].get(ref_name, {})
                if "description" in ref_schema:
                    descriptions.append(ref_schema["description"])
        if "$ref" in property_schema:
            ref_name = property_schema["$ref"].rsplit("/", 1)[-1]
            ref_schema = schema["components"]["schemas"].get(ref_name, {})
            if "description" in ref_schema:
                descriptions.append(ref_schema["description"])
        combined = " ".join(descriptions)
        missing = [s for s in required_substrings if s not in combined]
        assert not missing, (
            f"{schema_name}.{field_name}: enum description missing keywords {missing}. "
            f"Combined description: {combined!r}"
        )


# === OpenAPI 3.1 spec compliance ===


class TestOpenAPISpecCompliance:
    """``openapi-spec-validator`` accepts the generated schema as a valid OpenAPI 3.1 document."""

    def test_spec_validates(self) -> None:
        spec = create_app(AppConfig()).openapi()
        validate_openapi_spec(spec)  # raises OpenAPIValidationError on any structural issue


# === OpenAPI response examples on flagship endpoints ===


_EXAMPLE_ENDPOINTS: list[tuple[str, str]] = [
    ("/entries/", "get"),
    ("/entries/{type}/{id}", "get"),
    ("/db-portal/cross-search", "get"),
    ("/db-portal/search", "get"),
    ("/db-portal/serialize", "post"),
]


class TestOpenAPIResponseExamples:
    """Flagship endpoints carry an operation-level example on their 200 response.

    Without these, Swagger UI ``Try it out`` falls back to schema-only
    rendering and the response body shape is hard to grasp at a glance.
    """

    @pytest.fixture(scope="class")
    def schema(self) -> dict[str, Any]:
        return create_app(AppConfig()).openapi()

    @pytest.mark.parametrize(("path", "method"), _EXAMPLE_ENDPOINTS)
    def test_200_has_example(
        self,
        schema: dict[str, Any],
        path: str,
        method: str,
    ) -> None:
        operation = schema["paths"][path][method]
        json_body = operation["responses"]["200"]["content"]["application/json"]
        has_single = "example" in json_body
        has_named = "examples" in json_body and bool(json_body["examples"])
        assert has_single or has_named, (
            f"{method.upper()} {path}: 200 response has neither 'example' nor 'examples' on application/json"
        )


# === OpenAPI type-specific query semantics labels ===


_QUERY_SEMANTICS_ENDPOINTS_PARAMS: list[tuple[str, str]] = [
    # BioProject
    ("/entries/bioproject/", "objectTypes"),
    ("/entries/bioproject/", "externalLinkLabel"),
    ("/entries/bioproject/", "projectType"),
    ("/entries/bioproject/", "relevance"),
    # BioSample
    ("/entries/biosample/", "derivedFromId"),
    ("/entries/biosample/", "host"),
    ("/entries/biosample/", "strain"),
    ("/entries/biosample/", "geoLocName"),
    ("/entries/biosample/", "package"),
    ("/entries/biosample/", "model"),
    # SRA-experiment (carries the full SRA filter set)
    ("/entries/sra-experiment/", "libraryStrategy"),
    ("/entries/sra-experiment/", "libraryName"),
    ("/entries/sra-experiment/", "derivedFromId"),
    # JGA-study
    ("/entries/jga-study/", "studyType"),
    ("/entries/jga-study/", "vendor"),
    ("/entries/jga-study/", "externalLinkLabel"),
    # GEA / MetaboBank
    ("/entries/gea/", "experimentType"),
    ("/entries/metabobank/", "studyType"),
]

_QUERY_SEMANTICS_LABELS = ("Term filter", "Text match", "Nested filter")


class TestOpenAPIQuerySemantics:
    """Type-specific search parameters surface their backend semantics label.

    Every type-specific param description starts with one of
    ``Term filter`` / ``Text match`` / ``Nested filter`` so downstream
    consumers can distinguish exact-match vs analyzed vs nested behaviour
    from the OpenAPI description alone.
    """

    @pytest.fixture(scope="class")
    def schema(self) -> dict[str, Any]:
        return create_app(AppConfig()).openapi()

    @pytest.mark.parametrize(("path", "param_name"), _QUERY_SEMANTICS_ENDPOINTS_PARAMS)
    def test_param_description_carries_semantics_label(
        self,
        schema: dict[str, Any],
        path: str,
        param_name: str,
    ) -> None:
        operation = schema["paths"][path]["get"]
        params = {p["name"]: p for p in operation.get("parameters", [])}
        assert param_name in params, f"GET {path}: parameter '{param_name}' not in OpenAPI spec"
        description = params[param_name].get("description", "")
        assert any(label in description for label in _QUERY_SEMANTICS_LABELS), (
            f"GET {path} parameter '{param_name}': description missing semantics label "
            f"({_QUERY_SEMANTICS_LABELS}). Got: {description!r}"
        )


# === OpenAPI bulk NDJSON description ===


class TestOpenAPINdjsonDescription:
    """Bulk NDJSON response description spells out the notFound discrepancy.

    NDJSON skips missing / hidden ids silently; without explicit mention
    callers expect the JSON-mode ``notFound`` array to also appear.
    """

    def test_ndjson_description_mentions_not_found_skip(self) -> None:
        schema = create_app(AppConfig()).openapi()
        ndjson = schema["paths"]["/entries/{type}/bulk"]["post"]["responses"]["200"]["content"]["application/x-ndjson"][
            "schema"
        ]
        description = ndjson.get("description", "")
        assert "notFound" in description, f"NDJSON schema description must mention notFound. Got: {description!r}"
        assert "silently skipped" in description or "skipped" in description, (
            f"NDJSON schema description must mention that missing/hidden ids are skipped. Got: {description!r}"
        )


# === Lifespan: Solr client ===


class TestLifespanSolrClient:
    """Solr client provisioned alongside ES client in the lifespan."""

    def test_solr_client_initialized(self) -> None:
        application = create_app(AppConfig())
        with TestClient(application):
            assert isinstance(application.state.solr_client, httpx.AsyncClient)

    def test_solr_client_closed_after_exit(self) -> None:
        application = create_app(AppConfig())
        with TestClient(application):
            client = application.state.solr_client
        assert client.is_closed is True

    def test_solr_client_timeout_uses_max_of_backend_timeouts(self) -> None:
        """Solr client's client-level timeout is the hard cap shared by ARSA
        and TXSearch; per-call ``asyncio.wait_for`` tightens it further.
        """
        config = AppConfig()
        object.__setattr__(config, "arsa_timeout", 30.0)
        object.__setattr__(config, "txsearch_timeout", 5.0)
        application = create_app(config)
        with TestClient(application):
            assert application.state.solr_client.timeout.read == 30.0

    def test_es_and_solr_clients_are_distinct(self) -> None:
        application = create_app(AppConfig())
        with TestClient(application):
            assert application.state.solr_client is not application.state.es_client


# === Lifespan: defensive resource cleanup ===


class TestLifespanResourceCleanup:
    """ES / Solr いずれの ``aclose`` が raise しても他方を必ず close する.

    httpx.AsyncClient.aclose は運用中ほぼ raise しないが、shutdown 経路で
    transport / connection 層の例外が発生する可能性はゼロではない。片側の
    raise で他方をリークさせない契約を回帰防御する。
    """

    def test_es_client_closed_when_solr_close_raises(self) -> None:
        application = create_app(AppConfig())

        async def _raising_aclose() -> None:
            raise RuntimeError("simulated solr close failure")

        with TestClient(application):
            es_client = application.state.es_client
            # context 内で solr_client.aclose を例外送出に差し替え
            application.state.solr_client.aclose = _raising_aclose

        # context 終了後: solr の aclose が raise しても es は確実に閉じる
        assert es_client.is_closed is True

    def test_solr_client_closed_when_es_close_raises(self) -> None:
        application = create_app(AppConfig())

        async def _raising_aclose() -> None:
            raise RuntimeError("simulated es close failure")

        with TestClient(application):
            solr_client = application.state.solr_client
            application.state.es_client.aclose = _raising_aclose

        # 順序的に solr が先に close されるため、es の aclose が raise しても solr は閉じている
        assert solr_client.is_closed is True

    def test_normal_shutdown_closes_both_clients(self) -> None:
        # 例外なしの通常 shutdown で両 client が閉じることを再確認 (回帰防御)
        application = create_app(AppConfig())
        with TestClient(application):
            es_client = application.state.es_client
            solr_client = application.state.solr_client
        assert es_client.is_closed is True
        assert solr_client.is_closed is True
