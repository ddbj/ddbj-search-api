import logging.config

import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Message, Receive, Scope, Send

from ddbj_search_api.config import LOGGER, PKG_DIR, get_config, logging_config
from ddbj_search_api.routers import router
from ddbj_search_api.schemas import ErrorResponse


def fix_error_handler(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        app_config = get_config()
        if app_config.debug:
            LOGGER.exception("Something http exception occurred.", exc_info=exc)

        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                msg=exc.detail,
                status_code=exc.status_code,
            ).model_dump()
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_exception_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        app_config = get_config()
        if app_config.debug:
            LOGGER.exception("Request validation error occurred.", exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": exc.errors()}
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(_request: Request, _exc: Exception) -> JSONResponse:
        # If a general Exception occurs, a traceback will be output without using LOGGER.
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                msg="The server encountered an internal error and was unable to complete your request.",
                status_code=500,
            ).model_dump()
        )


class CustomCORSMiddleware(CORSMiddleware):
    """\
    CORSMiddleware that returns CORS headers even if the Origin header is not present
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope["method"]
        headers = Headers(scope=scope)

        if method == "OPTIONS" and "access-control-request-method" in headers:
            response = self.preflight_response(request_headers=headers)
            await response(scope, receive, send)
            return

        await self.simple_response(scope, receive, send, request_headers=headers)

    async def send(
        self, message: Message, send: Send, request_headers: Headers
    ) -> None:
        if message["type"] != "http.response.start":
            await send(message)
            return

        message.setdefault("headers", [])
        headers = MutableHeaders(scope=message)
        headers.update(self.simple_headers)
        origin = request_headers.get("Origin", "*")
        has_cookie = "cookie" in request_headers

        # If request includes any cookie headers, then we must respond
        # with the specific origin instead of '*'.
        if self.allow_all_origins and has_cookie:
            self.allow_explicit_origin(headers, origin)

        # If we only allow specific origins, then we have to mirror back
        # the Origin header in the response.
        elif not self.allow_all_origins and self.is_allowed_origin(origin=origin):
            self.allow_explicit_origin(headers, origin)

        await send(message)


def init_app_state() -> None:
    LOGGER.info("=== Initializing app state ===")

    app_config = get_config()
    LOGGER.info("App config: %s", app_config)

    LOGGER.info("=== App state initialized ===")


def create_app() -> FastAPI:
    app_config = get_config()
    logging.config.dictConfig(logging_config(app_config.debug))

    app = FastAPI(
        root_path=app_config.url_prefix,
        debug=app_config.debug,
    )
    app.add_middleware(
        CustomCORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    fix_error_handler(app)

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


if __name__ == "__main__":
    main()
