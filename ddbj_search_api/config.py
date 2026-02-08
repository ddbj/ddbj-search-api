"""Application configuration via pydantic-settings."""
import argparse
from enum import Enum
from typing import Dict, Optional

from pydantic import computed_field
from pydantic_settings import BaseSettings

# JSON-LD @context URLs per database type.
# Context files are maintained in ddbj-search-converter/ontology/.
_CONTEXT_BASE = (
    "https://raw.githubusercontent.com"
    "/ddbj/ddbj-search-converter/main/ontology"
)
JSONLD_CONTEXT_URLS: Dict[str, str] = {
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
    base_url: str = "http://localhost:8080/search/api"
    host: str = "0.0.0.0"
    port: int = 8080
    env: Env = Env.dev

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


_config: Optional[AppConfig] = None


def get_config(
    host: Optional[str] = None,
    port: Optional[int] = None,
) -> AppConfig:
    """Get the application config, creating it on first call.

    CLI argument overrides are applied on top of env-var settings.
    """
    global _config  # pylint: disable=global-statement
    if _config is not None:
        return _config

    config = AppConfig()
    if host is not None:
        object.__setattr__(config, "host", host)
    if port is not None:
        object.__setattr__(config, "port", port)

    _config = config

    return _config


def logging_config(debug: bool) -> Dict[str, object]:
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
