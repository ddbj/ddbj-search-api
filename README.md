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

```plain
Internal nginx (ddbj-search-network)
  -> /search/api/* -> ddbj-search-api (this project)
  -> /search/*     -> ddbj-search-front (frontend)
```

API サーバーは ddbj-search-converter が管理する Elasticsearch を参照する。
同一の Docker network (`ddbj-search-network-{env}`) を通じてアクセスする。
詳細は [ddbj-search/docs/network-architecture.md](https://github.com/ddbj/ddbj-search/blob/main/docs/network-architecture.md) を参照。

## クイックスタート

### 前提条件

- Podman (本番/ステージング) または Docker (開発)
- ddbj-search-converter の環境が起動済み (Elasticsearch が利用可能な状態)

### 環境起動 (Dev)

```bash
# 1. 環境変数を設定
cp env.dev .env

# 2. Docker network 作成（初回のみ、既に存在していてもエラーにならない）
docker network create ddbj-search-network-dev || true

# 3. 起動
docker compose up -d --build

# 4. コンテナに入る
docker compose exec app bash

# 5. API サーバーを起動 (コンテナ内で実行)
ddbj_search_api
```

### 環境起動 (Staging / Production)

```bash
# 1. 環境変数と override を設定
cp env.staging .env  # または env.production
cp compose.override.podman.yml compose.override.yml

# 2. Podman network 作成（初回のみ、既に存在していてもエラーにならない）
podman network create ddbj-search-network-staging || true
# production の場合: podman network create ddbj-search-network-production || true

# 3. 起動 (API サーバーが自動起動する)
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
| `env.dev` | 開発環境 (`ddbj-search-es-dev` に接続) |
| `env.staging` | ステージング環境 (`ddbj-search-es-staging` に接続) |
| `env.production` | 本番環境 (`ddbj-search-es-production` に接続) |

### .env の主要設定

`env.*` ファイルをコピーして使用する。

```bash
# === Environment ===
DDBJ_SEARCH_ENV=production   # dev, staging, production

# === Application Settings (config.py) ===
DDBJ_SEARCH_API_URL_PREFIX=/search/api                         # URL prefix
DDBJ_SEARCH_API_ES_URL=http://ddbj-search-es-production:9200   # ES URL
DDBJ_SEARCH_API_BASE_URL=https://ddbj.nig.ac.jp/search/api     # Public base URL

# === Command ===
DDBJ_SEARCH_API_COMMAND=ddbj_search_api   # sleep infinity (dev)
```

`DDBJ_SEARCH_ENV` により、コンテナ名（`ddbj-search-api-{env}`）と Docker network 名（`ddbj-search-network-{env}`）が自動決定される。
また、`dev`/`staging` では debug モード（ログレベル DEBUG、uvicorn reload 有効）、`production` では非 debug モードとなる。

## 開発

### セットアップ

```bash
# uv がインストールされていない場合
curl -LsSf https://astral.sh/uv/install.sh | sh

# 依存パッケージのインストール
uv sync --extra tests
```

### ddbj-search-converter の更新

ddbj-search-converter は git の main ブランチを参照しているため、`uv.lock` は自動更新されない。
最新の converter を取り込むには明示的に upgrade する。

```bash
uv sync --extra tests -P ddbj-search-converter
```

Docker イメージのビルド時も同様に `-P ddbj-search-converter` で常に最新の converter を取り込む。

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
