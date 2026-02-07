# ddbj-search-api

[DDBJ-Search](https://ddbj.nig.ac.jp/search) の API サーバー実装。

## 概要

DDBJ-Search API は、BioProject / BioSample / SRA / JGA データを検索・取得するための RESTful API サーバー。

**主な機能:**

- 全タイプ横断検索・タイプ別検索
- エントリー詳細取得 (JSON / JSON-LD)
- 一括取得 (NDJSON ストリーミング)
- タイプ別件数取得

**関連プロジェクト:**

- [ddbj-search-converter](https://github.com/ddbj/ddbj-search-converter) - データ投入用パイプラインツール (Elasticsearch 管理)

### システム構成

```
Internal nginx (ddbj-search-network)
  -> /search/api/* -> ddbj-search-api (this project)
  -> /search/*     -> ddbj-search-front (frontend)
```

API サーバーは ddbj-search-converter が管理する Elasticsearch を参照する。
同一の Docker network (`ddbj-search-network`) を通じてアクセスする。
詳細は [ddbj-search/docs/network-architecture.md](https://github.com/ddbj/ddbj-search/blob/main/docs/network-architecture.md) を参照。

## クイックスタート

### 前提条件

- Podman (本番/ステージング) または Docker (開発)
- ddbj-search-converter の環境が起動済み (Elasticsearch が利用可能な状態)

### 環境起動 (Dev)

```bash
# 1. 環境変数を設定
cp env.dev .env

# 2. 起動
docker compose up -d --build

# 3. コンテナに入る
docker compose exec app bash

# 4. API サーバーを起動 (コンテナ内で実行)
ddbj_search_api --debug
```

> **Note:** Docker network (`ddbj-search-network`) は ddbj-search-converter が作成・管理する。converter の環境を先に起動しておくこと。

### 環境起動 (Staging / Production)

```bash
# 1. 環境変数と override を設定
cp env.staging .env  # または env.production
cp compose.override.podman.yml compose.override.yml

# 2. 起動 (API サーバーが自動起動する)
podman-compose up -d --build
```

### 動作確認

```bash
# BioProject データ取得 (JSON)
curl "http://localhost:8080/search/api/entries/bioproject/PRJNA16"

# BioSample データ取得 (JSON-LD)
curl "http://localhost:8080/search/api/entries/biosample/SAMN02953658.jsonld"
```

## 環境構築

### 環境ファイル

| ファイル | 説明 |
|---------|------|
| `compose.yml` | 統合版 Docker Compose |
| `compose.override.podman.yml` | Podman 用の差分設定 |
| `env.dev` | 開発環境 (converter dev 環境の ES に接続) |
| `env.staging` | ステージング環境 (converter 本番 ES に接続) |
| `env.production` | 本番環境 (converter 本番 ES に接続) |

### .env の設定項目

`.env` ファイルで設定可能な項目。`env.*` ファイルをコピーして使用する。

| 項目 | 説明 |
|------|------|
| `APP_CONTAINER_NAME` | コンテナ名 |
| `DDBJ_SEARCH_API_DEBUG` | デバッグモードの有効化 (`True` / `False`) |
| `DDBJ_SEARCH_API_HOST` | バインドするホストアドレス |
| `DDBJ_SEARCH_API_PORT` | リッスンするポート番号 |
| `DDBJ_SEARCH_API_URL_PREFIX` | API エンドポイントの URL プレフィックス (例: `/search/api`) |
| `DDBJ_SEARCH_API_ES_URL` | Elasticsearch の URL (converter の ES コンテナを指定) |
| `DDBJ_SEARCH_API_BASE_URL` | 公開ベース URL (JSON-LD の `@id` 生成に使用) |
| `DDBJ_SEARCH_API_COMMAND` | コンテナ起動時のコマンド (`sleep infinity` / `ddbj_search_api`) |

### Elasticsearch への接続

API は converter が管理する Elasticsearch に Docker network 経由でアクセスする。

| 環境 | ES コンテナ名 | ES_URL |
|------|--------------|--------|
| dev | `ddbj-search-es-dev` | `http://ddbj-search-es-dev:9200` |
| staging/production | `ddbj-search-elasticsearch` | `http://ddbj-search-elasticsearch:9200` |

## 開発

### セットアップ

```bash
# uv がインストールされていない場合
curl -LsSf https://astral.sh/uv/install.sh | sh

# 依存パッケージのインストール
uv sync --extra tests
```

### パッケージ管理

```bash
# パッケージ追加
uv add <package>

# 開発用パッケージ追加
uv add --optional tests <package>

# パッケージ削除
uv remove <package>
```

`uv add` / `uv remove` で `pyproject.toml` と `uv.lock` が更新される。

### テスト・リント

```bash
uv run pytest -s
uv run pylint ./ddbj_search_api
uv run mypy ./ddbj_search_api
uv run isort ./ddbj_search_api
```

## ドキュメント

- [API 仕様書](docs/api-spec.md)

## License

This project is licensed under the [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0) license. See the [LICENSE](./LICENSE) file for details.
