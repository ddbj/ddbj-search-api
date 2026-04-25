# ddbj-search-api

[DDBJ Search](https://ddbj.nig.ac.jp/search) の RESTful API サーバー実装。BioProject / BioSample / SRA / JGA / GEA / MetaboBank の各タイプのデータを横断検索・取得する。

ddbj-search-converter が管理する Elasticsearch を読み、関連 ID の逆引きには DuckDB を併用する。詳しい設計判断は [docs/overview.md](docs/overview.md)、API の振る舞いは [docs/api-spec.md](docs/api-spec.md) を参照。

## 関連プロジェクト

- [ddbj-search](https://github.com/ddbj/ddbj-search) - nginx reverse proxy
- [ddbj-search-converter](https://github.com/ddbj/ddbj-search-converter) - データ投入パイプライン (Elasticsearch + DuckDB を管理)
- [ddbj-search-front](https://github.com/ddbj/ddbj-search-front) - フロントエンド

## クイックスタート (Dev)

ddbj-search-converter 側で Elasticsearch (`ddbj-search-es-dev`) が `ddbj-search-network-dev` 上に起動している前提。

```bash
cp env.dev .env
docker network create ddbj-search-network-dev || true
docker compose up -d --build
docker compose exec app uv sync --extra tests
docker compose exec app ddbj_search_api
```

別ターミナルで動作確認:

```bash
curl "http://localhost:8080/search/api/entries/bioproject/PRJNA16"
curl "http://localhost:8080/search/api/entries/biosample/SAMN02953658.jsonld"
```

Swagger UI は `http://localhost:8080/search/api/docs`。staging / production の手順は [docs/deployment.md](docs/deployment.md)。

## ドキュメント

- [docs/overview.md](docs/overview.md) - 設計判断・システム構成・converter コードガイド
- [docs/api-spec.md](docs/api-spec.md) - API 仕様 (`/entries/*`, `/facets/*`, `/dblink/*` 等)
- [docs/db-portal-api-spec.md](docs/db-portal-api-spec.md) - DB Portal API 仕様 (`/db-portal/cross-search`, `/db-portal/search`, `/db-portal/parse`)
- [docs/openapi.json](docs/openapi.json) - OpenAPI 3.x spec
- [docs/development.md](docs/development.md) - 開発環境・日常コマンド・OpenAPI 出力
- [docs/deployment.md](docs/deployment.md) - staging / production 手順・URL prefix・podman 注意点
- [tests/testing.md](tests/testing.md) - テスト方針 (TDD・PBT・mock 戦略)
- [tests/integration-scenarios.md](tests/integration-scenarios.md) - integration テストシナリオ
- [tests/integration-note.md](tests/integration-note.md) - integration テストの運用注意

## License

This project is licensed under the [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0) license. See the [LICENSE](./LICENSE) file for details.
