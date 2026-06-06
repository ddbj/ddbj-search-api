"""Application configuration via pydantic-settings."""

from __future__ import annotations

import argparse
import re
from enum import Enum
from pathlib import Path

from pydantic import computed_field, field_validator
from pydantic_settings import BaseSettings

# Solr URL components (core / shards / base URLs) are interpolated into
# request URLs and ``shards`` query params. Restrict to the character set
# expected from production env (`a012-1:51981/solr/collection1,...`) so that
# misconfiguration with e.g. ``?`` (query separator), whitespace, or pipe
# (`|`) surfaces at startup rather than producing malformed Solr requests.
_SOLR_URL_SAFE_RE = re.compile(r"^[A-Za-z0-9._:/,-]+$")

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
    "gea": f"{_CONTEXT_BASE}/gea.jsonld",
    "metabobank": f"{_CONTEXT_BASE}/metabobank.jsonld",
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

    # Solr (ARSA = Ddbj, TXSearch = NCBI Taxonomy). Unset in dev; staging and
    # production both point at the production ARSA cluster (a011, 8 shards,
    # Solr 4.4.0, core ``collection1``); the core name stays env-overridable.
    solr_arsa_base_url: str | None = None
    solr_arsa_shards: str | None = None
    solr_arsa_core: str = "collection1"
    solr_txsearch_url: str | None = None

    # Cross-search (``/db-portal/cross-search``) per-backend and overall
    # timeouts. ``es_timeout`` above stays as the client-level default for
    # /entries/* and other routers; these four apply only inside
    # ``routers.db_portal._cross_search`` / ``_adv_cross_search`` via
    # ``asyncio.wait_for``.
    es_search_timeout: float = 10.0
    arsa_timeout: float = 15.0
    txsearch_timeout: float = 5.0
    cross_search_total_timeout: float = 20.0

    # Search query limits (DoS / complexity guard).
    dsl_max_length: int = 4096
    dsl_max_depth: int = 5
    dsl_max_nodes: int = 512

    @computed_field  # type: ignore[prop-decorator]
    @property
    def debug(self) -> bool:
        """Enable debug mode for the dev environment only."""

        return self.env == Env.dev

    @field_validator(
        "solr_arsa_base_url",
        "solr_arsa_shards",
        "solr_arsa_core",
        "solr_txsearch_url",
    )
    @classmethod
    def _validate_solr_url_safe_chars(cls, v: str | None) -> str | None:
        """Reject Solr URL components containing characters outside the safe allowlist.

        Defence-in-depth at config load time: these values flow into Solr
        request URLs / ``shards`` params, so anything outside
        ``[A-Za-z0-9._:/,-]`` (e.g. ``?``, ``..``, whitespace, ``|``) is
        rejected so a misconfigured env var fails fast instead of producing
        malformed Solr requests at runtime.
        """
        if v is None or v == "":
            return v
        if not _SOLR_URL_SAFE_RE.match(v):
            raise ValueError(
                f"contains characters outside the safe allowlist [A-Za-z0-9._:/,-]: {v!r}",
            )
        return v


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments.

    Debug mode is *not* a CLI argument: it is derived from
    :data:`AppConfig.env` (``Env.dev`` -> debug on), which in turn comes
    from ``DDBJ_SEARCH_ENV`` in the per-environment ``env.{dev,staging,
    production}`` files. Keeping debug under one env-driven source avoids
    a CLI flag that would silently disagree with the deployment env.
    """
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
