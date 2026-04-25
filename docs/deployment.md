# デプロイガイド

staging / production への deploy 手順、podman 固有の注意、URL prefix の前提。

## デプロイ環境一覧

| 環境 | ランタイム | network | コンテナ名 | base URL |
|------|----------|---------|-----------|----------|
| dev | docker compose | `ddbj-search-network-dev` | `ddbj-search-api-dev` | `http://localhost:8080/search/api` |
| staging | podman-compose | `ddbj-search-network-staging` | `ddbj-search-api-staging` | `https://ddbj-staging.nig.ac.jp/search/api` |
| production | podman-compose | `ddbj-search-network-production` | `ddbj-search-api-production` | `https://ddbj.nig.ac.jp/search/api` |

`DDBJ_SEARCH_ENV` (`env.{dev,staging,production}` で設定) から、コンテナ名・network 名・debug モードが自動決定される。設定値は `env.*` ファイルを直接見るのが SSOT。

## URL prefix と nginx pass-through

API サーバーは URL prefix `/search/api` の下にデプロイされる。上流の nginx は **prefix を trim せず** そのまま pass-through する前提で、FastAPI 側は `DDBJ_SEARCH_API_URL_PREFIX=/search/api` を内部で解釈する。

物理ネットワーク構成 (gateway / 内部 nginx / コンテナ間通信) は [ddbj-search/docs/network-architecture.md](https://github.com/ddbj/ddbj-search/blob/main/docs/network-architecture.md) を参照。

## デプロイ手順 (staging / production 共通)

```bash
# 1. 環境変数と podman override を設定
cp env.staging .env  # または env.production
cp compose.override.podman.yml compose.override.yml

# 2. network 作成 (初回のみ、既存ならエラーにならない)
podman network create ddbj-search-network-staging || true

# 3. 起動
podman-compose up -d --build
```

ソースコードは bind mount (`.:/app:rw`) のため、git pull したら `podman-compose restart app` だけで反映される。Dockerfile の依存関係を変えていない限り `--build` も不要。

### converter のリリース取り込み

`ddbj-search-converter` は git の main 参照のため、リリース取り込み時は `-P` で再 build する。

```bash
podman-compose build --no-cache app  # uv が -P 相当で main を取り直す
podman-compose up -d
```

## podman 固有の注意

- `compose.override.podman.yml` は podman 用の差分設定 (`userns_mode: keep-id`、bind mount の `:U` フラグ等)。docker compose では使わないので、podman 環境でだけ override に置く
- network は `external: true` で `ddbj-search-network-{env}` を参照する。converter 側の compose で network が作られているはずだが、無ければ `podman network create` する

## ロールバック

```bash
git checkout <previous-commit>
podman-compose restart app
```

bind mount のため git の HEAD を戻すだけで前バージョンに戻る。Dockerfile を変えていない限り rebuild 不要。converter のスキーマ変更が絡む場合は converter 側のロールバックも合わせて行う。

## 環境変数

設定値は `env.dev` / `env.staging` / `env.production` を直接参照する。各変数は `compose.yml` で受け取られて API コンテナに渡る。環境差分 (例: dev のみ Solr backend を未設定、staging / production はどちらも production ARSA cluster へ向ける) もファイル diff で確認する。
