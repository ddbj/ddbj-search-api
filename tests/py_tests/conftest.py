from collections.abc import Generator
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ddbj_search_api.config import get_config, parse_args
from ddbj_search_api.main import create_app


@pytest.fixture(autouse=True)
def _clear_config_cache() -> Generator[None, None, None]:
    get_config.cache_clear()
    yield
    get_config.cache_clear()


@pytest.fixture
def app() -> FastAPI:
    with patch("ddbj_search_api.config.parse_args") as mock:
        mock.return_value = parse_args([])
        application = create_app()
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)
