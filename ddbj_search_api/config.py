"""Application configuration via pydantic-settings."""

from __future__ import annotations

import argparse
from enum import Enum
from pathlib import Path

from pydantic import computed_field
from pydantic_settings import BaseSettings

DBLINK_DB_PATH = Path("/home/w3ddbjld/const/dblink/dblink.duckdb")

# JSON-LD @context URLs per database type.
# Context files are maintained in ddbj-search-converter/ontology/.
_CONTEXT_BASE = "https://raw.githubusercontent.com/ddbj/ddbj-search-converter/main/ontology"
JSONLD_CONTEXT_URLS: dict[str, str] = {
    "bioproject": f"{_CONTEXT_BASE}/bioproject.jsonld",
    "biosample": f"{_CONTEXT_BASE}/biosample.jsonld",
    "sra-submission": f"{_CONTEXT_BASE}/sra.jsonld",
    "sra-study": f"{_CONTEXT_BASE}/sra.jsonld",
    "sra-experiment": f"{_CONTEXT_BASE}/sra.jsonld",
    "sra-run": f"{_CONTEXT_BASE}/sra.jsonld",
    "sra-sample": f"{_CONTEXT_BASE}/sra.jsonld",
    "sra-analysis": f"{_CONTEXT_BASE}/sra.jsonld",
    "jga-study": f"{_CONTEXT_BASE}/jga.jsonld",
    "jga-dataset": f"{_CONTEXT_BASE}/jga.jsonld",
    "jga-dac": f"{_CONTEXT_BASE}/jga.jsonld",
    "jga-policy": f"{_CONTEXT_BASE}/jga.jsonld",
}


class Env(str, Enum):
    """Deployment environment."""

    dev = "dev"
    staging = "staging"
    production = "production"


class AppConfig(BaseSettings):
    """Application settings loaded from environment variables.

    All settings can be overridden by environment variables prefixed with
    ``DDBJ_SEARCH_API_``.
    """

    model_config = {"env_prefix": "DDBJ_SEARCH_API_"}

    url_prefix: str = "/search/api"
    es_url: str = "http://localhost:9200"
    es_timeout: float = 60.0
    base_url: str = "http://localhost:8080/search/api"
    host: str = "0.0.0.0"
    port: int = 8080
    env: Env = Env.dev

    # Solr (ARSA = Trad, TXSearch = NCBI Taxonomy). Unset in dev; staging/prod
    # provide full URLs. ARSA staging runs Solr 4.4.0 with core ``collection1``
    # (confirmed 2026-04-23); the core name stays env-overridable for prod.
    solr_arsa_base_url: str | None = None
    solr_arsa_shards: str | None = None
    solr_arsa_core: str = "collection1"
    solr_txsearch_url: str | None = None

    # Cross-search (``/db-portal/search`` count-only) per-backend and
    # overall timeouts. ``es_timeout`` above stays as the client-level default
    # for /entries/* and other routers; these four apply only inside
    # ``routers.db_portal._cross_search_count_only`` via ``asyncio.wait_for``.
    es_search_timeout: float = 10.0
    arsa_timeout: float = 15.0
    txsearch_timeout: float = 5.0
    cross_search_total_timeout: float = 20.0

    # Advanced Search DSL limits (DoS / complexity guard).
    dsl_max_length: int = 4096
    dsl_max_depth: int = 5

    @computed_field  # type: ignore[prop-decorator]
    @property
    def debug(self) -> bool:
        """Enable debug mode for dev and staging environments."""

        return self.env in (Env.dev, Env.staging)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="DDBJ Search API Server")
    parser.add_argument(
        "--host",
        default=None,
        help="Bind host (default: from env or 0.0.0.0).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind port (default: from env or 8080).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=None,
        help="Enable debug mode (reload, verbose logging).",
    )

    return parser.parse_args()


_config: AppConfig | None = None


def get_config(
    host: str | None = None,
    port: int | None = None,
) -> AppConfig:
    """Get the application config, creating it on first call.

    CLI argument overrides are applied on top of env-var settings.
    """
    global _config  # noqa: PLW0603
    if _config is not None:
        return _config

    config = AppConfig()
    if host is not None:
        object.__setattr__(config, "host", host)
    if port is not None:
        object.__setattr__(config, "port", port)

    _config = config

    return _config


def logging_config(debug: bool) -> dict[str, object]:
    """Build uvicorn-compatible logging configuration."""
    level = "DEBUG" if debug else "INFO"

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s %(levelname)s %(name)s: %(message)s",
            },
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "stream": "ext://sys.stderr",
            },
        },
        "root": {
            "level": level,
            "handlers": ["default"],
        },
        "loggers": {
            "uvicorn": {"level": level},
            "uvicorn.error": {"level": level},
            "uvicorn.access": {"level": level},
        },
    }
