"""FastAPI application factory and entry points."""

from __future__ import annotations

import collections.abc
import http
import importlib.metadata
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from ddbj_search_api.config import AppConfig, get_config, logging_config, parse_args
from ddbj_search_api.routers import router
from ddbj_search_api.routers.db_portal import DbPortalHTTPException

logger = logging.getLogger(__name__)


# === X-Request-ID middleware ===


async def request_id_middleware(request: Request, call_next: Any) -> Response:
    """Attach X-Request-ID to every request/response.

    If the client supplies ``X-Request-ID``, echo it back; otherwise
    generate a new UUID.
    """
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request.state.request_id = request_id
    response: Response = await call_next(request)
    response.headers["X-Request-ID"] = request_id

    return response


# === Error handlers ===


_STATUS_TITLES = {
    400: "Bad Request",
    404: "Not Found",
    422: "Unprocessable Entity",
    500: "Internal Server Error",
    501: "Not Implemented",
}


def _http_status_title(status_code: int) -> str:
    """Derive a human-readable title from an HTTP status code."""
    title = _STATUS_TITLES.get(status_code)
    if title is not None:
        return title
    try:
        return http.HTTPStatus(status_code).phrase
    except ValueError:
        return "Error"


def _problem_json(
    status: int,
    title: str,
    detail: str,
    request: Request,
    problem_type: str = "about:blank",
) -> JSONResponse:
    """Build an RFC 7807 Problem Details JSON response.

    ``problem_type`` maps to the ``type`` URI in the body.  Default
    ``about:blank`` matches the standard "no specific problem type"
    marker; endpoint-specific errors pass a dedicated URI such as
    ``https://ddbj.nig.ac.jp/problems/<slug>`` (RFC 7807 §3.1).
    """
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    body = {
        "type": problem_type,
        "title": title,
        "status": status,
        "detail": detail,
        "instance": str(request.url.path),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "requestId": request_id,
    }

    return JSONResponse(
        status_code=status,
        content=body,
        media_type="application/problem+json",
    )


def setup_error_handlers(app: FastAPI) -> None:
    """Register exception handlers that return RFC 7807 responses."""

    @app.exception_handler(DbPortalHTTPException)
    async def db_portal_http_exception_handler(
        request: Request,
        exc: DbPortalHTTPException,
    ) -> JSONResponse:
        return _problem_json(
            status=exc.status_code,
            title=_http_status_title(exc.status_code),
            detail=str(exc.detail),
            request=request,
            problem_type=exc.type_uri,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        return _problem_json(
            status=exc.status_code,
            title=_http_status_title(exc.status_code),
            detail=str(exc.detail),
            request=request,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        # Path-level {type} enum error on /entries/ → 404 Not Found
        # (DbType validation for entries/facets endpoints; dblink uses 422)
        request_path = str(request.url.path)
        for error in exc.errors():
            loc = error.get("loc", ())
            if len(loc) >= 2 and loc[0] == "path" and loc[1] == "type" and "/dblink" not in request_path:
                return _problem_json(
                    status=404,
                    title="Not Found",
                    detail=f"Unknown database type: '{error.get('input', '')}'",
                    request=request,
                )

        details = "; ".join(f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}" for e in exc.errors())

        return _problem_json(
            status=422,
            title="Unprocessable Entity",
            detail=details,
            request=request,
        )

    @app.exception_handler(NotImplementedError)
    async def not_implemented_handler(
        request: Request,
        exc: NotImplementedError,
    ) -> JSONResponse:
        return _problem_json(
            status=501,
            title="Not Implemented",
            detail="This endpoint is not yet implemented.",
            request=request,
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        logger.exception("Unhandled exception: %s", exc)

        return _problem_json(
            status=500,
            title="Internal Server Error",
            detail="An unexpected error occurred.",
            request=request,
        )


# === Lifespan ===


def _make_lifespan(config: AppConfig) -> Any:
    """Create a lifespan context manager for the given config."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> collections.abc.AsyncIterator[None]:
        app.state.es_client = httpx.AsyncClient(
            base_url=config.es_url,
            timeout=httpx.Timeout(config.es_timeout),
            limits=httpx.Limits(max_connections=1000),
        )
        # Shared Solr client for ARSA and TXSearch. No ``base_url``: each
        # call passes a full URL (ARSA ``{base}/{core}/select``, TXSearch
        # preformed ``/solr-rgm/.../select``). Smaller pool than ES: Solr
        # traffic comes only from ``/db-portal/cross-search`` fan-out and
        # ``/db-portal/search?db=trad|taxonomy``.
        #
        # Client-level timeout is the hard cap for Solr requests; cross-search
        # per-call bounds (``arsa_timeout`` / ``txsearch_timeout``) are further
        # tightened by ``asyncio.wait_for`` inside ``routers.db_portal``.
        app.state.solr_client = httpx.AsyncClient(
            timeout=httpx.Timeout(max(config.arsa_timeout, config.txsearch_timeout)),
            limits=httpx.Limits(max_connections=100),
        )
        yield
        await app.state.solr_client.aclose()
        await app.state.es_client.aclose()

    return lifespan


# === App factory ===


_OPENAPI_TAGS: list[dict[str, str]] = [
    {
        "name": "Entries",
        "description": "List / search entries (cross-type and per-database-type).",
    },
    {
        "name": "Entry Detail",
        "description": (
            "Single-entry retrieval (frontend-oriented detail, raw ES document, "
            "JSON-LD, and the umbrella tree for BioProject)."
        ),
    },
    {
        "name": "Bulk",
        "description": "Multi-entry retrieval (JSON array or NDJSON streaming).",
    },
    {
        "name": "Facets",
        "description": "Facet aggregation (cross-type and per-database-type).",
    },
    {
        "name": "dblink",
        "description": "DBLinks API: cross-reference lookup via DuckDB.",
    },
    {
        "name": "db-portal",
        "description": (
            "DB Portal frontend API: unified search across ES (6 DBs) "
            "and Solr (ARSA / TXSearch), plus the Advanced Search DSL parser."
        ),
    },
    {
        "name": "Service Info",
        "description": "Service metadata + Elasticsearch health probe.",
    },
]

_DETAIL_DISCRIMINATOR_TARGETS: dict[str, dict[str, str]] = {
    # path -> {variant_const: schema_name}
    "/entries/{type}/{id}": {
        "bioproject": "BioProjectDetailResponse",
        "biosample": "BioSampleDetailResponse",
        "sra-submission": "SraDetailResponse",
        "sra-study": "SraDetailResponse",
        "sra-experiment": "SraDetailResponse",
        "sra-run": "SraDetailResponse",
        "sra-sample": "SraDetailResponse",
        "sra-analysis": "SraDetailResponse",
        "jga-study": "JgaDetailResponse",
        "jga-dataset": "JgaDetailResponse",
        "jga-dac": "JgaDetailResponse",
        "jga-policy": "JgaDetailResponse",
        "gea": "GeaDetailResponse",
        "metabobank": "MetaboBankDetailResponse",
    },
    "/entries/{type}/{id}.json": {
        "bioproject": "BioProject",
        "biosample": "BioSample",
        "sra-submission": "SRA",
        "sra-study": "SRA",
        "sra-experiment": "SRA",
        "sra-run": "SRA",
        "sra-sample": "SRA",
        "sra-analysis": "SRA",
        "jga-study": "JGA",
        "jga-dataset": "JGA",
        "jga-dac": "JGA",
        "jga-policy": "JGA",
        "gea": "GEA",
        "metabobank": "MetaboBank",
    },
    "/entries/{type}/{id}.jsonld": {
        "bioproject": "BioProjectEntryJsonLdResponse",
        "biosample": "BioSampleEntryJsonLdResponse",
        "sra-submission": "SraEntryJsonLdResponse",
        "sra-study": "SraEntryJsonLdResponse",
        "sra-experiment": "SraEntryJsonLdResponse",
        "sra-run": "SraEntryJsonLdResponse",
        "sra-sample": "SraEntryJsonLdResponse",
        "sra-analysis": "SraEntryJsonLdResponse",
        "jga-study": "JgaEntryJsonLdResponse",
        "jga-dataset": "JgaEntryJsonLdResponse",
        "jga-dac": "JgaEntryJsonLdResponse",
        "jga-policy": "JgaEntryJsonLdResponse",
        "gea": "GeaEntryJsonLdResponse",
        "metabobank": "MetaboBankEntryJsonLdResponse",
    },
}

_ERROR_STATUS_CODES = frozenset({"400", "404", "422", "500", "501", "502"})

# Schema examples that contain JSON ``null`` values.  Pydantic's
# ``json_schema_extra`` drops ``None`` literals when merging the dict form,
# so we attach these examples after the OpenAPI schema is generated to
# preserve nullable cursor-mode samples.
_NULL_AWARE_SCHEMA_EXAMPLES: dict[str, list[dict[str, Any]]] = {
    "Pagination": [
        {
            "page": 1,
            "perPage": 10,
            "total": 150000,
            "nextCursor": "eyJwaXRfaWQiOm51bGwsInNlYXJjaF9hZnRlciI6Wy4uLl19",
            "hasNext": True,
        },
        {
            "page": None,
            "perPage": 10,
            "total": 150000,
            "nextCursor": None,
            "hasNext": False,
        },
    ],
}


def _rewrite_error_content_types(operation: dict[str, Any]) -> None:
    """Replace JSON content-type with ``application/problem+json`` on error responses."""
    responses = operation.get("responses", {})
    for status_code, resp in responses.items():
        if status_code not in _ERROR_STATUS_CODES:
            continue
        content = resp.get("content")
        if not content:
            continue
        for media_type in list(content.keys()):
            if media_type != "application/problem+json":
                content["application/problem+json"] = content.pop(media_type)


def _convert_anyof_to_oneof_with_discriminator(
    operation: dict[str, Any],
    path: str,
) -> None:
    """Promote anyOf polymorphic 200 responses to oneOf + discriminator.

    Pydantic emits ``anyOf`` for ``A | B | C`` typed responses; OpenAPI
    consumers (codegen / Redoc) treat ``anyOf`` as a permissive union.
    For our entry-detail endpoints the variants are mutually exclusive
    keyed on a constant ``type`` field — promoting to ``oneOf`` and
    attaching a ``discriminator`` lets generated clients build a proper
    tagged union.
    """
    mapping = _DETAIL_DISCRIMINATOR_TARGETS.get(path)
    if mapping is None:
        return
    success = operation.get("responses", {}).get("200")
    if not success:
        return
    for body in (success.get("content") or {}).values():
        schema = body.get("schema") or {}
        variants = schema.pop("anyOf", None)
        if not variants:
            continue
        schema["oneOf"] = variants
        schema["discriminator"] = {
            "propertyName": "type",
            "mapping": {const_value: f"#/components/schemas/{name}" for const_value, name in mapping.items()},
        }
        body["schema"] = schema


def create_app(config: AppConfig | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    if config is None:
        config = get_config()

    app = FastAPI(
        title="DDBJ Search API",
        description=(
            "RESTful API for searching and retrieving BioProject, BioSample, SRA, and JGA entries from DDBJ.\n\n"
            "See [docs/api-spec.md](https://github.com/ddbj/ddbj-search-api/blob/main/docs/api-spec.md) "
            "for behaviour-level specifications (status visibility, sameAs resolution, pagination semantics) "
            "and [docs/db-portal-api-spec.md]("
            "https://github.com/ddbj/ddbj-search-api/blob/main/docs/db-portal-api-spec.md) "
            "for the ``/db-portal/*`` endpoints (unified search, Advanced Search DSL)."
        ),
        version=importlib.metadata.version("ddbj-search-api"),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        redirect_slashes=False,
        root_path=config.url_prefix,
        lifespan=_make_lifespan(config),
        openapi_tags=_OPENAPI_TAGS,
        contact={
            "name": "DDBJ Search team",
            "url": "https://www.ddbj.nig.ac.jp/contact-e.html",
        },
        license_info={
            "name": "Apache-2.0",
            "url": "https://www.apache.org/licenses/LICENSE-2.0",
        },
    )

    # CORS: allow all origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # X-Request-ID middleware
    app.middleware("http")(request_id_middleware)

    # Error handlers
    setup_error_handlers(app)

    # Routers
    app.include_router(router)

    # Customize OpenAPI:
    # - drop FastAPI's default HTTPValidationError / ValidationError schemas
    #   (we serve all errors as ProblemDetails),
    # - rewrite error response content types to application/problem+json,
    # - promote entry-detail anyOf unions to oneOf + discriminator so SDKs
    #   produce discriminated unions instead of permissive any-of types,
    # - publish servers / contact / license / tags metadata that FastAPI
    #   does not derive automatically from ``root_path``.
    _original_openapi = app.openapi

    def custom_openapi() -> dict[str, Any]:
        schema = _original_openapi()
        schemas = schema.get("components", {}).get("schemas", {})
        schemas.pop("HTTPValidationError", None)
        schemas.pop("ValidationError", None)

        # ``root_path`` does not propagate to ``openapi()`` output.
        schema["servers"] = [{"url": config.url_prefix, "description": "Current deployment"}]

        for schema_name, examples in _NULL_AWARE_SCHEMA_EXAMPLES.items():
            target = schemas.get(schema_name)
            if target is not None:
                target["examples"] = examples

        for path, path_item in schema.get("paths", {}).items():
            for operation in path_item.values():
                if not isinstance(operation, dict):
                    continue
                _rewrite_error_content_types(operation)
                _convert_anyof_to_oneof_with_discriminator(operation, path)

        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]

    return app


# === Entry points ===


def main() -> None:
    """CLI entry point: start the API server via uvicorn."""
    args = parse_args()
    config = get_config(
        host=args.host,
        port=args.port,
    )
    log_config = logging_config(config.debug)

    uvicorn.run(
        "ddbj_search_api.main:create_app",
        factory=True,
        host=config.host,
        port=config.port,
        reload=config.debug,
        log_config=log_config,
    )


def dump_openapi_spec() -> None:
    """CLI entry point: print OpenAPI spec as JSON to stdout."""
    app = create_app()
    spec = app.openapi()
    print(json.dumps(spec, indent=2, ensure_ascii=False))
