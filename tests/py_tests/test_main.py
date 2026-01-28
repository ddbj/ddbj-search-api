from fastapi import FastAPI


class TestCreateApp:
    def test_returns_fastapi_instance(self, app: FastAPI) -> None:
        assert isinstance(app, FastAPI)

    def test_app_title(self, app: FastAPI) -> None:
        assert app.title == "DDBJ Search API"

    def test_app_version(self, app: FastAPI) -> None:
        assert app.version == "1.0.0"
