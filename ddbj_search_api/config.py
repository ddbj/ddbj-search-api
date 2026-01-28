import logging
import sys
from argparse import ArgumentParser, Namespace
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

from ddbj_search_api.utils import inside_docker

PKG_DIR = Path(__file__).resolve().parent


BIOPROJECT_CONTEXT_URL = "https://raw.githubusercontent.com/ddbj/rdf/main/context/bioproject.jsonld"
BIOSAMPLE_CONTEXT_URL = "https://raw.githubusercontent.com/ddbj/rdf/main/context/biosample.jsonld"


# === Global Configuration ===


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DDBJ_SEARCH_API_",
    )

    host: str = "0.0.0.0" if inside_docker() else "127.0.0.1"
    port: int = 8080
    debug: bool = False
    url_prefix: str = ""
    base_url: str = ""
    es_url: str = "https://ddbj.nig.ac.jp/search/resources"


def parse_args(args: Optional[List[str]] = None) -> Namespace:
    parser = ArgumentParser(
        description="DDBJ Search API",
    )

    parser.add_argument(
        "--host",
        type=str,
        metavar="HOST",
        help="Host address for the service. (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        metavar="PORT",
        help="Port number for the service. (default: 8080)"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode."
    )
    parser.add_argument(
        "--url-prefix",
        type=str,
        metavar="PREFIX",
        help="URL prefix for the service endpoints. (default: '', e.g., /search, /api)"
    )
    parser.add_argument(
        "--base-url",
        type=str,
        metavar="URL",
        help="Base URL for JSON-LD @id field. This field is generated using the format: {base_url}/entries/bioproject/{bioproject_id}.jsonld. (default: http://{host}:{port}{url_prefix})"
    )
    parser.add_argument(
        "--es-url",
        type=str,
        metavar="URL",
        help="URL for Elasticsearch resources. (default: 'https://ddbj.nig.ac.jp/search/resources')"
    )

    return parser.parse_args(args)


@lru_cache(maxsize=None)
def get_config() -> AppConfig:
    args = parse_args(sys.argv[1:])

    # pydantic-settings が env var + defaults を自動処理
    settings = AppConfig()

    # CLI 引数で override（指定されたもののみ）
    overrides: Dict[str, Any] = {}
    if args.host is not None:
        overrides["host"] = args.host
    if args.port is not None:
        overrides["port"] = args.port
    if args.debug:
        overrides["debug"] = True
    if args.url_prefix is not None:
        overrides["url_prefix"] = args.url_prefix
    if args.base_url is not None:
        overrides["base_url"] = args.base_url
    if args.es_url is not None:
        overrides["es_url"] = args.es_url

    if overrides:
        settings = settings.model_copy(update=overrides)

    # base_url が未設定なら host/port/url_prefix から生成
    if not settings.base_url:
        settings = settings.model_copy(
            update={"base_url": f"http://{settings.host}:{settings.port}{settings.url_prefix}"}
        )

    return settings


# === Logging ===


# Ref.: https://github.com/encode/uvicorn/blob/master/uvicorn/config.py
def logging_config(debug: bool = False) -> Dict[str, Any]:
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(asctime)s %(levelprefix)s %(message)s",
                "use_colors": True,
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
        },
        "loggers": {
            "ddbj_search_api": {
                "handlers": ["default"],
                "level": "DEBUG" if debug else "INFO",
                "propagate": False
            },
        },
    }


LOGGER = logging.getLogger("ddbj_search_api")
