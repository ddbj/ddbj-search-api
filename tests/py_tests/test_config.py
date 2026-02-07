import pytest

from ddbj_search_api.config import AppConfig, Env, get_env, parse_args


class TestEnv:
    def test_get_env_default_is_production(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DDBJ_SEARCH_ENV", raising=False)
        assert get_env() == Env.PRODUCTION

    def test_get_env_dev(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DDBJ_SEARCH_ENV", "dev")
        assert get_env() == Env.DEV

    def test_get_env_staging(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DDBJ_SEARCH_ENV", "staging")
        assert get_env() == Env.STAGING

    def test_get_env_production(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DDBJ_SEARCH_ENV", "production")
        assert get_env() == Env.PRODUCTION

    def test_get_env_invalid_raises_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DDBJ_SEARCH_ENV", "invalid")
        with pytest.raises(ValueError):
            get_env()


class TestParseArgs:
    def test_defaults(self) -> None:
        args = parse_args([])
        assert args.host is None
        assert args.port is None
        assert args.url_prefix is None
        assert args.es_url is None

    def test_host(self) -> None:
        args = parse_args(["--host", "0.0.0.0"])
        assert args.host == "0.0.0.0"

    def test_port(self) -> None:
        args = parse_args(["--port", "9090"])
        assert args.port == 9090

    def test_url_prefix(self) -> None:
        args = parse_args(["--url-prefix", "/api"])
        assert args.url_prefix == "/api"

    def test_es_url(self) -> None:
        args = parse_args(["--es-url", "http://localhost:9200"])
        assert args.es_url == "http://localhost:9200"


class TestAppConfig:
    def test_defaults(self) -> None:
        config = AppConfig()
        assert config.port == 8080
        assert config.url_prefix == ""
        assert config.es_url == "https://ddbj.nig.ac.jp/search/resources"

    def test_host_is_string(self) -> None:
        config = AppConfig()
        assert isinstance(config.host, str)

    def test_debug_true_when_dev(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DDBJ_SEARCH_ENV", "dev")
        config = AppConfig()
        assert config.debug is True

    def test_debug_true_when_staging(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DDBJ_SEARCH_ENV", "staging")
        config = AppConfig()
        assert config.debug is True

    def test_debug_false_when_production(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DDBJ_SEARCH_ENV", "production")
        config = AppConfig()
        assert config.debug is False

    def test_debug_false_when_env_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DDBJ_SEARCH_ENV", raising=False)
        config = AppConfig()
        assert config.debug is False
