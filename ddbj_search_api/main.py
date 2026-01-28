import json
import logging.config
import sys

import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ddbj_search_api.config import LOGGER, PKG_DIR, get_config, logging_config
from ddbj_search_api.routers import router
from ddbj_search_api.schemas import ProblemDetails


def setup_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        app_config = get_config()
        if app_config.debug:
            LOGGER.exception("Something http exception occurred.", exc_info=exc)

        problem = ProblemDetails(
            type="about:blank",
            title=exc.detail if isinstance(exc.detail, str) else "Error",
            status=exc.status_code,
            detail=exc.detail if isinstance(exc.detail, str) else str(exc.detail),
            instance=str(request.url.path),
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=problem.model_dump(exclude_none=True),
            media_type="application/problem+json",
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        app_config = get_config()
        if app_config.debug:
            LOGGER.exception("Request validation error occurred.", exc_info=exc)

        problem = ProblemDetails(
            type="about:blank",
            title="Bad Request",
            status=status.HTTP_400_BAD_REQUEST,
            detail=json.dumps(exc.errors(), ensure_ascii=False, default=str),
            instance=str(request.url.path),
        )
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=problem.model_dump(exclude_none=True),
            media_type="application/problem+json",
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        LOGGER.exception("Unhandled exception occurred.", exc_info=exc)
        problem = ProblemDetails(
            type="about:blank",
            title="Internal Server Error",
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The server encountered an internal error and was unable to complete your request.",
            instance=str(request.url.path),
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=problem.model_dump(exclude_none=True),
            media_type="application/problem+json",
        )


def init_app_state() -> None:
    LOGGER.info("=== Initializing app state ===")

    app_config = get_config()
    LOGGER.info("App config: %s", app_config)

    LOGGER.info("=== App state initialized ===")


def create_app() -> FastAPI:
    app_config = get_config()
    logging.config.dictConfig(logging_config(app_config.debug))
    url_prefix = app_config.url_prefix

    app = FastAPI(
        title="DDBJ Search API",
        description="REST API for [DDBJ-Search](https://ddbj.nig.ac.jp/search)\n\nSource code: [ddbj/ddbj-search-api](https://github.com/ddbj/ddbj-search-api)",
        version="1.0.0",
        contact={"name": "Bioinformatics and DDBJ Center"},
        license_info={"name": "Apache-2.0", "url": "https://www.apache.org/licenses/LICENSE-2.0"},
        docs_url=f"{url_prefix}/docs",
        redoc_url=f"{url_prefix}/redoc",
        openapi_url=f"{url_prefix}/openapi.json",
        debug=app_config.debug,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router, prefix=url_prefix)
    setup_error_handlers(app)

    return app


def main() -> None:
    app_config = get_config()
    logging.config.dictConfig(logging_config(app_config.debug))
    init_app_state()
    uvicorn.run(
        "ddbj_search_api.main:create_app",
        host=app_config.host,
        port=app_config.port,
        reload=app_config.debug,
        reload_dirs=[str(PKG_DIR)],
        factory=True,
    )


def dump_openapi() -> None:
    app = create_app()
    json.dump(app.openapi(), sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
