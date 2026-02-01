from fastapi.testclient import TestClient


class TestEntryDetailTrimProperties:
    """GET /entries/{type}/{id} の trimProperties パラメータテスト。"""

    def test_default_includes_properties(self, client: TestClient) -> None:
        resp = client.get("/entries/bioproject/PRJNA1")
        assert resp.status_code == 200

    def test_trim_properties_false(self, client: TestClient) -> None:
        resp = client.get("/entries/bioproject/PRJNA1?trimProperties=false")
        assert resp.status_code == 200

    def test_trim_properties_true(self, client: TestClient) -> None:
        resp = client.get("/entries/bioproject/PRJNA1?trimProperties=true")
        assert resp.status_code == 200
        data = resp.json()
        assert "properties" not in data

    def test_json_endpoint_trim(self, client: TestClient) -> None:
        resp = client.get("/entries/bioproject/PRJNA1.json?trimProperties=true")
        assert resp.status_code == 200
        data = resp.json()
        assert "properties" not in data

    def test_jsonld_endpoint_trim(self, client: TestClient) -> None:
        resp = client.get("/entries/bioproject/PRJNA1.jsonld?trimProperties=true")
        assert resp.status_code == 200
        data = resp.json()
        assert "properties" not in data


class TestEntriesListTrimProperties:
    """GET /entries/ 系の trimProperties パラメータテスト。"""

    def test_all_entries_default(self, client: TestClient) -> None:
        resp = client.get("/entries/")
        assert resp.status_code == 200

    def test_all_entries_trim(self, client: TestClient) -> None:
        resp = client.get("/entries/?trimProperties=true")
        assert resp.status_code == 200

    def test_typed_entries_default(self, client: TestClient) -> None:
        resp = client.get("/entries/bioproject/")
        assert resp.status_code == 200

    def test_typed_entries_trim(self, client: TestClient) -> None:
        resp = client.get("/entries/bioproject/?trimProperties=true")
        assert resp.status_code == 200


class TestBulkTrimProperties:
    """Bulk エンドポイントの trimProperties パラメータテスト。"""

    def test_bulk_get_default(self, client: TestClient) -> None:
        resp = client.get("/entries/bioproject/bulk?ids=PRJNA1,PRJNA2")
        assert resp.status_code == 200

    def test_bulk_get_trim(self, client: TestClient) -> None:
        resp = client.get("/entries/bioproject/bulk?ids=PRJNA1,PRJNA2&trimProperties=true")
        assert resp.status_code == 200

    def test_bulk_post_default(self, client: TestClient) -> None:
        resp = client.post(
            "/entries/bioproject/bulk",
            json={"ids": ["PRJNA1", "PRJNA2"]},
        )
        assert resp.status_code == 200

    def test_bulk_post_trim(self, client: TestClient) -> None:
        resp = client.post(
            "/entries/bioproject/bulk?trimProperties=true",
            json={"ids": ["PRJNA1", "PRJNA2"]},
        )
        assert resp.status_code == 200
