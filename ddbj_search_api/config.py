import logging
import os
import sys
from argparse import ArgumentParser, Namespace
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from ddbj_search_api.utils import inside_docker, str2bool

PKG_DIR = Path(__file__).resolve().parent


BIOPROJECT_CONTEXT_URL = "https://raw.githubusercontent.com/ddbj/rdf/main/context/bioproject.jsonld"
BIOSAMPLE_CONTEXT_URL = "https://raw.githubusercontent.com/ddbj/rdf/main/context/biosample.jsonld"


# === Global Configuration ===


class AppConfig(BaseModel):
    host: str = "0.0.0.0" if inside_docker() else "127.0.0.1"
    port: int = 8080
    debug: bool = False
    url_prefix: str = "/search"
    base_url: str = f"http://{'0.0.0.0' if inside_docker() else '127.0.0.1'}:8080"
    es_url: str = "https://ddbj.nig.ac.jp/search/resources"


def parse_args(args: Optional[List[str]] = None) -> Namespace:
    parser = ArgumentParser(
        description="DDBJ Search API",
    )

    parser.add_argument(
        "--host",
        type=str,
        metavar="",
        help="Host address for the service. (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        metavar="",
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
        metavar="",
        help="URL prefix for the service endpoints. (default: '/search', e.g., /dfast/api)"
    )
    parser.add_argument(
        "--base-url",
        type=str,
        metavar="",
        help="Base URL for JSON-LD @id field. This field is generated using the format: {base_url}/entry/bioproject/{bioproject_id}.jsonld. (default: http://{host}:{port}{url_prefix})"
    )
    parser.add_argument(
        "--es-url",
        type=str,
        metavar="",
        help="URL for Elasticsearch resources. (default: 'https://ddbj.nig.ac.jp/search/resources')"
    )

    return parser.parse_args(args)


@lru_cache(maxsize=None)
def get_config() -> AppConfig:
    args = parse_args(sys.argv[1:])
    default_config = AppConfig()

    host = args.host or os.environ.get("DDBJ_SEARCH_API_HOST", default_config.host)
    port = args.port or int(os.environ.get("DDBJ_SEARCH_API_PORT", default_config.port))
    url_prefix = args.url_prefix or os.environ.get("DDBJ_SEARCH_API_URL_PREFIX", default_config.url_prefix)
    base_url = args.base_url or os.environ.get("DDBJ_SEARCH_API_BASE_URL", f"http://{host}:{port}{url_prefix}")

    return AppConfig(
        host=host,
        port=port,
        debug=args.debug or str2bool(os.environ.get("DDBJ_SEARCH_API_DEBUG", default_config.debug)),
        url_prefix=url_prefix,
        base_url=base_url,
        es_url=args.es_url or os.environ.get("DDBJ_SEARCH_API_ES_URL", default_config.es_url),
    )


# === Logging ===


# Ref.: https://github.com/encode/uvicorn/blob/master/uvicorn/config.py
def logging_config(debug: bool = False) -> Dict[str, Any]:
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(levelprefix)s %(message)s",
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
