"""Tests for ddbj_search_api.config."""

from __future__ import annotations

import os

import pytest

from ddbj_search_api.config import AppConfig, Env, logging_config

# === AppConfig defaults ===


class TestAppConfigDefaults:
    """AppConfig: default values loaded without any env vars."""

    @pytest.fixture
    def config(self, monkeypatch: pytest.MonkeyPatch) -> AppConfig:
        """Fresh AppConfig with all DDBJ_SEARCH_API_* env vars cleared.

        Overrides the shared ``config`` fixture so that default-value
        assertions are not polluted by runtime env vars (e.g. Docker
        compose sets ``DDBJ_SEARCH_API_ES_URL`` on the app container).
        """
        for var in list(os.environ):
            if var.startswith("DDBJ_SEARCH_API_"):
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

    def test_env_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DDBJ_SEARCH_API_ENV", "production")
        config = AppConfig()
        assert config.env == Env.production
        assert config.debug is False

    def test_url_prefix_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DDBJ_SEARCH_API_URL_PREFIX", "/custom/prefix")
        config = AppConfig()
        assert config.url_prefix == "/custom/prefix"

    def test_solr_arsa_base_url_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DDBJ_SEARCH_API_SOLR_ARSA_BASE_URL", "http://a011:51981/solr")
        config = AppConfig()
        assert config.solr_arsa_base_url == "http://a011:51981/solr"

    def test_solr_arsa_shards_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "DDBJ_SEARCH_API_SOLR_ARSA_SHARDS",
            "a011:51981/solr,a011:51982/solr,a011:51983/solr",
        )
        config = AppConfig()
        assert config.solr_arsa_shards == "a011:51981/solr,a011:51982/solr,a011:51983/solr"

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
