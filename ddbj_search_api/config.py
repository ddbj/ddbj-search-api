import logging
import os
import sys
from argparse import ArgumentParser, Namespace
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ddbj_search_api.utils import inside_container

PKG_DIR = Path(__file__).resolve().parent


BIOPROJECT_CONTEXT_URL = "https://raw.githubusercontent.com/ddbj/rdf/main/context/bioproject.jsonld"
BIOSAMPLE_CONTEXT_URL = "https://raw.githubusercontent.com/ddbj/rdf/main/context/biosample.jsonld"


# === Environment ===


class Env(str, Enum):
    DEV = "dev"
    STAGING = "staging"
    PRODUCTION = "production"


def get_env() -> Env:
    """DDBJ_SEARCH_ENV 環境変数から実行環境を取得する。デフォルトは production。"""

    return Env(os.environ.get("DDBJ_SEARCH_ENV", Env.PRODUCTION.value))


# === Global Configuration ===


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DDBJ_SEARCH_API_",
    )

    host: str = "0.0.0.0" if inside_container() else "127.0.0.1"
    port: int = 8080
    url_prefix: str = ""
    es_url: str = "https://ddbj.nig.ac.jp/search/resources"

    # Public base URL for this service.
    # Used for generating JSON-LD @id URIs and other external references.
    # Example: https://ddbj.nig.ac.jp/search -> @id: https://ddbj.nig.ac.jp/search/entries/bioproject/PRJNA16
    base_url: str = "https://ddbj.nig.ac.jp/search"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def debug(self) -> bool:
        """dev/staging は debug モード、production は非 debug モード。DDBJ_SEARCH_ENV から導出。"""

        return get_env() != Env.PRODUCTION


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
        "--url-prefix",
        type=str,
        metavar="PREFIX",
        help="URL prefix for the service endpoints. (default: '', e.g., /search, /api)"
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
    if args.url_prefix is not None:
        overrides["url_prefix"] = args.url_prefix
    if args.es_url is not None:
        overrides["es_url"] = args.es_url

    if overrides:
        settings = settings.model_copy(update=overrides)

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
