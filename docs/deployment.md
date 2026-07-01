# デプロイガイド

staging / production への deploy 手順、podman 固有の注意、URL prefix の前提。

## デプロイ環境一覧

| 環境 | ランタイム | network | コンテナ名 | base URL |
|------|----------|---------|-----------|----------|
| dev | docker compose | `ddbj-search-network-dev` | `ddbj-search-api-dev` | `http://localhost:8080/search/api` |
| staging | podman-compose | `ddbj-search-network-staging` | `ddbj-search-api-staging` | `https://ddbj-staging.nig.ac.jp/search/api` |
| production | podman-compose | `ddbj-search-network-production` | `ddbj-search-api-production` | `https://ddbj.nig.ac.jp/search/api` |

`DDBJ_SEARCH_ENV` (`env.{dev,staging,production}` で設定) から、コンテナ名・network 名・debug モードが自動決定される。設定値は `env.*` ファイルを直接見るのが SSOT。

## URL prefix と nginx routing

API サーバーは URL prefix `/search/api` の下に公開される。FastAPI 側の router は root (`/`) に mount しており (= 個別 endpoint は `/entries/...` や `/db-portal/search` として登録)、`DDBJ_SEARCH_API_URL_PREFIX=/search/api` は OpenAPI schema の `servers` block にだけ反映される。上流の nginx は `/search/api/` prefix を strip してから backend に転送する前提。

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

- `compose.override.podman.yml` は podman 用の差分設定 (`userns_mode: keep-id`)。docker compose では使わないので、podman 環境でだけ override に置く
- network は `external: true` で `ddbj-search-network-{env}` を参照する。converter 側の compose で network が作られているはずだが、無ければ `podman network create` する

## venv は image layer 内

venv は image layer 内 (`/opt/venv/`) に焼き込んでいる。`pyproject.toml` / `uv.lock` を更新したら `podman-compose up -d --build` で image を再構築すれば反映される。bind mount (`.:/app:rw`) は Python ソース変更を即時反映するが、venv 自体は image の外には出していない (= UID mismatch + named volume で venv が壊れるケースを構造的に避けるため)。

旧構成の `app-venv` named volume は使わない。以前の deploy で作られた `<project>_app-venv` が残っている場合は `podman volume rm <project>_app-venv` で掃除しておく (= 残しても害はないがディスクを占有するだけ)。

## ロールバック

2 手順。基本は (A) git rebuild、緊急時は (B) image tag backup を使う。converter と同じパターンで、詳細な選択基準と動作確認手順は [converter docs/deployment.md § ロールバック](https://github.com/ddbj/ddbj-search-converter/blob/main/docs/deployment.md) を参照する。

### A. git rebuild (default)

```bash
cd /data1/ddbj-search/ddbj-search-api/
git checkout <previous-commit>
podman-compose down
podman-compose up -d --build
```

Python ソースだけのロールバックで Dockerfile / `pyproject.toml` / `uv.lock` が変わっていない場合は、`--build` を省略すれば bind mount だけで戻せる (`git checkout <commit> && podman-compose restart app`)。依存パッケージが変わっているときは `--build` 必須。

### B. image tag backup (緊急 option)

build を待たずに戻したいときの 30〜60 秒 rollback。deploy **前** に prev image を別 tag で保存しておくのが前提。

```bash
# deploy 直前 (latest を prev として退避)
podman tag ddbj-search-api-${DDBJ_SEARCH_ENV}:latest ddbj-search-api-${DDBJ_SEARCH_ENV}:prev
```

rollback 時:

```bash
podman-compose down
podman tag ddbj-search-api-${DDBJ_SEARCH_ENV}:prev ddbj-search-api-${DDBJ_SEARCH_ENV}:latest
podman-compose up -d   # --build なし
```

### スキーマ変更が絡む場合

converter の Pydantic スキーマ / ES mapping 変更が絡むロールバックは、converter 側のロールバックも合わせて行う (本リポジトリは `git+...@main` で converter を依存しているため、api 単独の git checkout では `uv.lock` で固定された converter SHA に戻るだけで、converter リポジトリ側の状態は変わらない)。

## 環境変数

設定値は `env.dev` / `env.staging` / `env.production` を直接参照する。各変数は `compose.yml` で受け取られて API コンテナに渡る。環境差分 (例: dev のみ Solr backend を未設定、staging / production はどちらも a012 上の 3 shard ARSA cluster へ向ける) もファイル diff で確認する。

### `DDBJ_SEARCH_API_CURSOR_SECRET`

cursor token の HMAC 署名鍵。未設定の場合はプロセス起動時にランダム生成されるため、(a) プロセスを再起動するとそれまでに発行した cursor が全部無効になる、(b) `uvicorn --workers N` のような multi-worker 構成では worker ごとに別の鍵を持ち、ある worker が発行した cursor を別の worker が受け取ると 400 になる。

シングルワーカー運用なら未設定で問題ない (cursor は PIT の 5 分 expiry と同等に再起動で失効する設計)。multi-worker / 複数インスタンスのロードバランス構成では、全 worker / 全インスタンスに **同じ値** を必ず設定する。値は十分長い (32 バイト以上の) ランダム文字列が望ましい (`openssl rand -hex 32` で生成可)。
