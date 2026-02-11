"""FastAPI application factory and entry points."""

from __future__ import annotations

import collections.abc
import http
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
) -> JSONResponse:
    """Build an RFC 7807 Problem Details JSON response."""
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    body = {
        "type": "about:blank",
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
        # Path-level {type} enum error → 404 Not Found
        for error in exc.errors():
            loc = error.get("loc", ())
            if len(loc) >= 2 and loc[0] == "path" and loc[1] == "type":
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
        app.state.es_client = httpx.AsyncClient(base_url=config.es_url)
        yield
        await app.state.es_client.aclose()

    return lifespan


# === App factory ===


def create_app(config: AppConfig | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    if config is None:
        config = get_config()

    app = FastAPI(
        title="DDBJ Search API",
        description=("RESTful API for searching and retrieving BioProject, BioSample, SRA, and JGA entries from DDBJ."),
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        redirect_slashes=False,
        root_path=config.url_prefix,
        lifespan=_make_lifespan(config),
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

    # Customize OpenAPI: remove FastAPI's default HTTPValidationError/
    # ValidationError schemas (we use ProblemDetails for all errors).
    _original_openapi = app.openapi

    def custom_openapi() -> dict[str, Any]:
        schema = _original_openapi()
        schemas = schema.get("components", {}).get("schemas", {})
        schemas.pop("HTTPValidationError", None)
        schemas.pop("ValidationError", None)

        # Add servers (root_path is not reflected in static openapi())
        schema["servers"] = [{"url": config.url_prefix}]

        _error_codes = {"400", "404", "422", "500"}
        for path, path_item in schema.get("paths", {}).items():
            for operation in path_item.values():
                if not isinstance(operation, dict):
                    continue
                responses = operation.get("responses", {})

                # Remove inapplicable error codes per endpoint.
                # 400: only paginated list endpoints (deep paging).
                _is_list = path == "/entries/" or (
                    path.startswith("/entries/") and path.endswith("/") and "{" not in path
                )
                if not _is_list:
                    responses.pop("400", None)

                # 404: only endpoints with {type}/{id} or per-type path.
                if path in ("/entries/", "/facets", "/service-info"):
                    responses.pop("404", None)

                # 422: not needed for /service-info or extension
                # endpoints that have no query params.
                if path == "/service-info":
                    responses.pop("422", None)
                if path.endswith((".json", ".jsonld")):
                    responses.pop("422", None)

                # Fix error Content-Type: application/problem+json
                for status_code, resp in responses.items():
                    if status_code in _error_codes:
                        content = resp.get("content", {})
                        for media_type in list(content.keys()):
                            if media_type != "application/problem+json":
                                content["application/problem+json"] = content.pop(media_type)

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
