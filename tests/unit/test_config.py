"""Tests for ddbj_search_api.config."""
import pytest

from ddbj_search_api.config import AppConfig, Env, logging_config


# === AppConfig defaults ===


class TestAppConfigDefaults:
    """AppConfig: default values loaded without any env vars."""

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


# === Computed field: debug ===


class TestAppConfigDebug:
    """AppConfig.debug: derived from env."""

    def test_dev_is_debug(self) -> None:
        config = AppConfig(env=Env.dev)
        assert config.debug is True

    def test_staging_is_debug(self) -> None:
        config = AppConfig(env=Env.staging)
        assert config.debug is True

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

    def test_url_prefix_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DDBJ_SEARCH_API_URL_PREFIX", "/custom/prefix")
        config = AppConfig()
        assert config.url_prefix == "/custom/prefix"


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
