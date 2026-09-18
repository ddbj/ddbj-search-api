"""Tests for ddbj_search_api.config."""

from __future__ import annotations

import os

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from ddbj_search_api.config import AppConfig, Env, logging_config

# === AppConfig defaults ===


class TestAppConfigDefaults:
    """AppConfig: default values loaded without any env vars."""

    @pytest.fixture
    def config(self, monkeypatch: pytest.MonkeyPatch) -> AppConfig:
        """Fresh AppConfig with all DDBJ_SEARCH_* env vars cleared.

        Overrides the shared ``config`` fixture so that default-value
        assertions are not polluted by runtime env vars (e.g. Docker
        compose sets ``DDBJ_SEARCH_API_ES_URL`` and ``DDBJ_SEARCH_ENV``
        on the app container).
        """
        for var in list(os.environ):
            if var.startswith("DDBJ_SEARCH_"):
                monkeypatch.delenv(var, raising=False)
        return AppConfig()

    def test_url_prefix(self, config: AppConfig) -> None:
        assert config.url_prefix == "/search/api"

    def test_es_url(self, config: AppConfig) -> None:
        assert config.es_url == "http://localhost:9200"

    def test_base_url(self, config: AppConfig) -> None:
        assert config.base_url == "http://localhost:8080/search/api"

    def test_host(self, config: AppConfig) -> None:
        assert config.host == "0.0.0.0"

    def test_port(self, config: AppConfig) -> None:
        assert config.port == 8080

    def test_env(self, config: AppConfig) -> None:
        assert config.env == Env.dev

    def test_solr_arsa_base_url_default(self, config: AppConfig) -> None:
        assert config.solr_arsa_base_url is None

    def test_solr_arsa_shards_default(self, config: AppConfig) -> None:
        assert config.solr_arsa_shards is None

    def test_solr_arsa_core_default(self, config: AppConfig) -> None:
        assert config.solr_arsa_core == "collection1"

    def test_solr_txsearch_url_default(self, config: AppConfig) -> None:
        assert config.solr_txsearch_url is None


# === Computed field: debug ===


class TestAppConfigDebug:
    """AppConfig.debug: derived from env."""

    def test_dev_is_debug(self) -> None:
        config = AppConfig(env=Env.dev)
        assert config.debug is True

    def test_staging_is_not_debug(self) -> None:
        config = AppConfig(env=Env.staging)
        assert config.debug is False

    def test_production_is_not_debug(self) -> None:
        config = AppConfig(env=Env.production)
        assert config.debug is False


# === Env var overrides ===


class TestAppConfigEnvOverrides:
    """AppConfig: values can be overridden via environment variables."""

    def test_port_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DDBJ_SEARCH_API_PORT", "9090")
        config = AppConfig()
        assert config.port == 9090

    def test_es_url_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DDBJ_SEARCH_API_ES_URL", "http://es:9200")
        config = AppConfig()
        assert config.es_url == "http://es:9200"

    @pytest.mark.parametrize(
        ("value", "expected_env", "expected_debug"),
        [
            ("dev", Env.dev, True),
            ("staging", Env.staging, False),
            ("production", Env.production, False),
        ],
    )
    def test_env_from_unprefixed_env_var(
        self,
        monkeypatch: pytest.MonkeyPatch,
        value: str,
        expected_env: Env,
        expected_debug: bool,
    ) -> None:
        """``DDBJ_SEARCH_ENV`` is the name every deployment actually sets.

        Guards the regression where ``env`` was only readable as
        ``DDBJ_SEARCH_API_ENV``: nothing publishes that name, so every
        deployed service silently fell back to the dev default and ran with
        debug logging and uvicorn reload enabled.
        """
        monkeypatch.setenv("DDBJ_SEARCH_ENV", value)
        config = AppConfig()
        assert config.env == expected_env
        assert config.debug is expected_debug

    def test_env_from_prefixed_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The prefixed name stays usable as a per-service override."""
        monkeypatch.delenv("DDBJ_SEARCH_ENV", raising=False)
        monkeypatch.setenv("DDBJ_SEARCH_API_ENV", "production")
        config = AppConfig()
        assert config.env == Env.production
        assert config.debug is False

    def test_env_unprefixed_wins_over_prefixed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The stack-wide name is authoritative when both are set."""
        monkeypatch.setenv("DDBJ_SEARCH_ENV", "production")
        monkeypatch.setenv("DDBJ_SEARCH_API_ENV", "staging")
        config = AppConfig()
        assert config.env == Env.production

    def test_env_invalid_value_raises_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DDBJ_SEARCH_ENV", "prod")
        with pytest.raises(ValidationError):
            AppConfig()

    def test_url_prefix_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DDBJ_SEARCH_API_URL_PREFIX", "/custom/prefix")
        config = AppConfig()
        assert config.url_prefix == "/custom/prefix"

    def test_solr_arsa_base_url_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DDBJ_SEARCH_API_SOLR_ARSA_BASE_URL", "http://a012:51981/solr")
        config = AppConfig()
        assert config.solr_arsa_base_url == "http://a012:51981/solr"

    def test_solr_arsa_shards_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "DDBJ_SEARCH_API_SOLR_ARSA_SHARDS",
            "a012:51981/solr,a012:51982/solr,a012:51983/solr",
        )
        config = AppConfig()
        assert config.solr_arsa_shards == "a012:51981/solr,a012:51982/solr,a012:51983/solr"

    def test_solr_arsa_core_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DDBJ_SEARCH_API_SOLR_ARSA_CORE", "trad")
        config = AppConfig()
        assert config.solr_arsa_core == "trad"

    def test_solr_txsearch_url_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "DDBJ_SEARCH_API_SOLR_TXSEARCH_URL",
            "http://localhost:32005/solr-rgm/ncbi_taxonomy/select",
        )
        config = AppConfig()
        assert config.solr_txsearch_url == "http://localhost:32005/solr-rgm/ncbi_taxonomy/select"


# === Per-backend timeouts ===


class TestAppConfigPerBackendTimeouts:
    """Cross-search per-backend timeouts replace the single ``solr_timeout``.

    Default values: ES 10s / ARSA 15s / TXSearch 5s / total 20s.
    """

    @pytest.fixture
    def config(self, monkeypatch: pytest.MonkeyPatch) -> AppConfig:
        for var in list(os.environ):
            if var.startswith("DDBJ_SEARCH_API_"):
                monkeypatch.delenv(var, raising=False)
        return AppConfig()

    def test_es_search_timeout_default(self, config: AppConfig) -> None:
        assert config.es_search_timeout == 10.0

    def test_arsa_timeout_default(self, config: AppConfig) -> None:
        assert config.arsa_timeout == 15.0

    def test_txsearch_timeout_default(self, config: AppConfig) -> None:
        assert config.txsearch_timeout == 5.0

    def test_cross_search_total_timeout_default(self, config: AppConfig) -> None:
        assert config.cross_search_total_timeout == 20.0

    def test_solr_timeout_field_removed(self) -> None:
        assert "solr_timeout" not in AppConfig.model_fields

    def test_es_search_timeout_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DDBJ_SEARCH_API_ES_SEARCH_TIMEOUT", "7.5")
        config = AppConfig()
        assert config.es_search_timeout == 7.5

    def test_arsa_timeout_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DDBJ_SEARCH_API_ARSA_TIMEOUT", "25.0")
        config = AppConfig()
        assert config.arsa_timeout == 25.0

    def test_txsearch_timeout_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DDBJ_SEARCH_API_TXSEARCH_TIMEOUT", "3.0")
        config = AppConfig()
        assert config.txsearch_timeout == 3.0

    def test_cross_search_total_timeout_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DDBJ_SEARCH_API_CROSS_SEARCH_TOTAL_TIMEOUT", "30.0")
        config = AppConfig()
        assert config.cross_search_total_timeout == 30.0


# === Solr URL safe-char validation ===


class TestAppConfigSolrUrlSanitize:
    """Solr URL components are validated against an allowlist at startup.

    Allowed: ``A-Z a-z 0-9 . _ : / , -``. Anything else (``?``, whitespace,
    pipe, etc.) raises ``ValidationError`` so misconfiguration surfaces
    immediately rather than producing malformed Solr requests.
    """

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in list(os.environ):
            if var.startswith("DDBJ_SEARCH_API_"):
                monkeypatch.delenv(var, raising=False)

    def test_safe_core_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DDBJ_SEARCH_API_SOLR_ARSA_CORE", "collection1")
        config = AppConfig()
        assert config.solr_arsa_core == "collection1"

    def test_core_with_query_string_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DDBJ_SEARCH_API_SOLR_ARSA_CORE", "collection1?q=*:*")
        with pytest.raises(ValidationError):
            AppConfig()

    def test_core_with_whitespace_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DDBJ_SEARCH_API_SOLR_ARSA_CORE", "collection 1")
        with pytest.raises(ValidationError):
            AppConfig()

    def test_safe_shards_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "DDBJ_SEARCH_API_SOLR_ARSA_SHARDS",
            "a012-1:51981/solr/collection1,a012-2:51981/solr/collection1",
        )
        config = AppConfig()
        assert config.solr_arsa_shards is not None

    def test_shards_with_pipe_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Solr の shards 区切りはコンマ; pipe は意図しない区切り
        monkeypatch.setenv(
            "DDBJ_SEARCH_API_SOLR_ARSA_SHARDS",
            "a012-1:51981/solr/collection1|other:51981/solr",
        )
        with pytest.raises(ValidationError):
            AppConfig()

    def test_shards_with_space_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "DDBJ_SEARCH_API_SOLR_ARSA_SHARDS",
            "a012-1:51981 /solr/collection1",
        )
        with pytest.raises(ValidationError):
            AppConfig()

    def test_base_url_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DDBJ_SEARCH_API_SOLR_ARSA_BASE_URL", "http://a012:51981/solr")
        config = AppConfig()
        assert config.solr_arsa_base_url == "http://a012:51981/solr"

    def test_base_url_with_query_string_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "DDBJ_SEARCH_API_SOLR_ARSA_BASE_URL",
            "http://a012:51981/solr?evil=1",
        )
        with pytest.raises(ValidationError):
            AppConfig()

    def test_txsearch_url_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "DDBJ_SEARCH_API_SOLR_TXSEARCH_URL",
            "http://localhost:32005/solr-rgm/ncbi_taxonomy/select",
        )
        config = AppConfig()
        assert config.solr_txsearch_url == "http://localhost:32005/solr-rgm/ncbi_taxonomy/select"

    def test_txsearch_url_with_question_mark_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            "DDBJ_SEARCH_API_SOLR_TXSEARCH_URL",
            "http://localhost:32005/solr-rgm/x/select?q=*",
        )
        with pytest.raises(ValidationError):
            AppConfig()

    def test_empty_string_treated_as_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 空文字は通す (None と同じ扱い、実運用で env を一時的に空にする操作を許容)
        monkeypatch.setenv("DDBJ_SEARCH_API_SOLR_ARSA_SHARDS", "")
        AppConfig()


# === Env enum ===


class TestEnv:
    """Env enum: 3 deployment environments."""

    def test_has_3_members(self) -> None:
        assert len(Env) == 3

    @pytest.mark.parametrize("value", ["dev", "staging", "production"])
    def test_valid_values(self, value: str) -> None:
        assert Env(value).value == value

    def test_invalid_value_raises_error(self) -> None:
        with pytest.raises(ValueError):
            Env("test")


# === logging_config ===


class TestLoggingConfig:
    """logging_config: build uvicorn-compatible logging config."""

    def test_debug_true_sets_debug_level(self) -> None:
        cfg = logging_config(debug=True)
        assert cfg["root"]["level"] == "DEBUG"  # type: ignore[index]

    def test_debug_false_sets_info_level(self) -> None:
        cfg = logging_config(debug=False)
        assert cfg["root"]["level"] == "INFO"  # type: ignore[index]

    def test_returns_valid_dict_config(self) -> None:
        cfg = logging_config(debug=False)
        assert cfg["version"] == 1
        assert "handlers" in cfg
        assert "formatters" in cfg

    def test_per_request_http_client_logs_are_silenced_outside_debug(self) -> None:
        """httpx は ES への 1 リクエストごとに INFO を 1 行出す。API の 1 リクエストで複数行になる。"""
        loggers = logging_config(debug=False)["loggers"]
        assert loggers["httpx"]["level"] == "WARNING"  # type: ignore[index]
        assert loggers["httpcore"]["level"] == "WARNING"  # type: ignore[index]
        # access log は 1 リクエスト 1 行なので残す
        assert loggers["uvicorn.access"]["level"] == "INFO"  # type: ignore[index]

    def test_http_client_logs_stay_visible_in_debug(self) -> None:
        loggers = logging_config(debug=True)["loggers"]
        assert loggers["httpx"]["level"] == "DEBUG"  # type: ignore[index]


# === workers ===


class TestWorkers:
    """AppConfig.workers: number of uvicorn worker processes."""

    def test_default_is_single_worker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DDBJ_SEARCH_API_WORKERS", raising=False)
        assert AppConfig().workers == 1

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DDBJ_SEARCH_API_WORKERS", "8")
        assert AppConfig().workers == 8

    @pytest.mark.parametrize("value", ["0", "-1", "1.5", "many", ""])
    def test_invalid_values_are_rejected(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("DDBJ_SEARCH_API_WORKERS", value)
        with pytest.raises(ValidationError):
            AppConfig()

    @given(workers=st.integers(min_value=1, max_value=512))
    def test_any_positive_integer_is_accepted(self, workers: int) -> None:
        assert AppConfig(workers=workers).workers == workers

    @given(workers=st.integers(max_value=0))
    def test_no_non_positive_integer_is_accepted(self, workers: int) -> None:
        with pytest.raises(ValidationError):
            AppConfig(workers=workers)
