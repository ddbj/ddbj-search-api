# 開発ガイド

Docker image の構成、日常コマンド、ddbj-search-converter の更新、OpenAPI 出力ワークフロー、パッケージ管理。

初回セットアップとローカル起動は [README.md § クイックスタート](../README.md) を参照。本ガイドはそのあとの「日々の作業」を扱う。

## 前提

- Docker (もしくは Podman) と uv
- ddbj-search-converter 側で Elasticsearch が起動していること (`ddbj-search-network-dev` 内で `ddbj-search-es-dev` が解決できる状態)。converter の compose で ES を立ち上げてから、本リポジトリの compose を起動する

## コンテナ内実行を貫く

すべてのコマンド (`uv sync`, `pytest`, `ruff`, `mypy`, `dump_openapi_spec`) は **コンテナ内で実行する**。ホスト側の Python / uv で動かさない。理由は (a) Python のバージョンを image の Python と揃える、(b) Linux 専用の依存ライブラリでホスト依存の差分を出さない、(c) staging / production と同じ環境で検証する、の 3 点。

dev の `compose.yml` は `command: sleep infinity` で起動するため、コンテナは立ち上がるが API サーバー本体は手動で起動する (`docker compose -f compose.yml -f compose.dev.yml exec app ddbj_search_api`)。`DDBJ_SEARCH_ENV=dev` が設定されていると config が debug モードに切り替わり、ログレベル DEBUG・uvicorn reload 有効になる。

dev では `compose.dev.yml` を override として併用する (`-f compose.yml -f compose.dev.yml`)。`compose.dev.yml` は下記の `dev` target を build し、8080 を host に publish する。`compose.dev.yml` を付けずに build すると `runtime` target の image になり、pytest・ruff・mypy が入らず、`curl http://localhost:8080/...` も到達しない。

## Docker image の構成

Dockerfile は 3 つの stage からなる。

- `builder`: Python の full image で venv (`/opt/venv`) を作る。uv は ddbj-search-converter を git から取るので、git を含む full image を使う
- `dev`: `builder` に dev dependency group (pytest・ruff・mypy など)、uv、デバッグ用の道具を足す。dev 環境と、staging / production での integration テスト ([tests/integration-note.md](../tests/integration-note.md)) に使う
- `runtime`: Python の slim image に、`builder` の venv とアプリだけをコピーする。target を指定しない build はこの stage になり、staging / production はこの image で動く。pytest・uv・git を含まないので、`runtime` の image の中では test を実行できない

base image の tag には Python の minor version と Debian の codename までを書き、patch version と digest では固定しない。docker library が rebuild した base image を取り込むため、build するときは常に `--pull` を付ける。

## ddbj-search-converter の更新

依存に `ddbj-search-converter@git+https://github.com/ddbj/ddbj-search-converter@main` を持つため、converter は git の main ブランチを直接参照している。`uv.lock` は SHA を固定するので、main の更新を取り込むには明示的に upgrade する必要がある。

```bash
docker compose -f compose.yml -f compose.dev.yml exec app uv sync -P ddbj-search-converter
```

Docker イメージのビルド時も同じく `-P ddbj-search-converter` で常に最新 main を取り込む運用。converter の Pydantic スキーマや mapping を変更したら、まず converter を `-P` で更新してから本リポジトリ側の対応を進める。

## 日常コマンド

```bash
docker compose -f compose.yml -f compose.dev.yml exec app uv run pytest                         # unit テスト (デフォルト、testpaths=tests/unit)
docker compose -f compose.yml -f compose.dev.yml exec app uv run pytest tests/integration/      # integration テスト (要 ES)
docker compose -f compose.yml -f compose.dev.yml exec app uv run ruff check ./ddbj_search_api ./tests
docker compose -f compose.yml -f compose.dev.yml exec app uv run ruff format ./ddbj_search_api ./tests
docker compose -f compose.yml -f compose.dev.yml exec app uv run mypy ./ddbj_search_api ./tests
```

integration テストは `DDBJ_SEARCH_INTEGRATION_ES_URL` (デフォルト `http://localhost:9200`) で接続先を切り替える。ES に到達できない場合は `pytest.skip` で全 integration テストが skip される。テスト方針の詳細は [tests/testing.md](../tests/testing.md)、シナリオは [tests/integration-scenarios.md](../tests/integration-scenarios.md)、運用注意は [tests/integration-note.md](../tests/integration-note.md)。

## OpenAPI 出力ワークフロー

API スキーマを変更したら、必ず `docs/openapi.json` を再生成して commit する。

```bash
docker compose -f compose.yml -f compose.dev.yml exec app uv run dump_openapi_spec > docs/openapi.json
```

再生成漏れはレビュー時に目視で確認する。

## パッケージ追加・削除

`uv add` / `uv remove` をコンテナ内で実行する。test・lint にだけ使う package は `--dev` を付けて `[dependency-groups]` の `dev` に入れる。`runtime` の image は `uv sync --no-dev` で作るので、`dev` に入れた package は staging / production に入らない。`pyproject.toml` と `uv.lock` の変更は bind mount でホスト側に反映される。

uvicorn は `[standard]` extra を使わず、本番で使う `uvloop` と `httptools` だけを dependencies に書く。uvicorn は、この 2 つが入っていれば起動時に自動で使う。dev の auto-reload に使う `watchfiles` は `dev` group に入れる。
