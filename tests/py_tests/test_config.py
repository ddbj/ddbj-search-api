from ddbj_search_api.config import AppConfig, parse_args


class TestParseArgs:
    def test_defaults(self) -> None:
        args = parse_args([])
        assert args.host is None
        assert args.port is None
        assert args.debug is False
        assert args.url_prefix is None
        assert args.es_url is None

    def test_host(self) -> None:
        args = parse_args(["--host", "0.0.0.0"])
        assert args.host == "0.0.0.0"

    def test_port(self) -> None:
        args = parse_args(["--port", "9090"])
        assert args.port == 9090

    def test_debug(self) -> None:
        args = parse_args(["--debug"])
        assert args.debug is True

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
        assert config.debug is False
        assert config.url_prefix == ""
        assert config.es_url == "https://ddbj.nig.ac.jp/search/resources"

    def test_host_is_string(self) -> None:
        config = AppConfig()
        assert isinstance(config.host, str)
