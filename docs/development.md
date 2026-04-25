# 開発ガイド

日常コマンド、ddbj-search-converter の更新、OpenAPI 出力ワークフロー、パッケージ管理。

初回セットアップとローカル起動は [README.md § クイックスタート](../README.md) を参照。本ガイドはそのあとの「日々の作業」を扱う。

## 前提

- Docker (もしくは Podman) と uv
- ddbj-search-converter 側で Elasticsearch が起動していること (`ddbj-search-network-dev` 内で `ddbj-search-es-dev` が解決できる状態)。converter の compose で ES を立ち上げてから、本リポジトリの compose を起動する

## コンテナ内実行を貫く

すべてのコマンド (`uv sync`, `pytest`, `ruff`, `mypy`, `dump_openapi_spec`) は **コンテナ内で実行する**。ホスト側の Python / uv で動かさない。理由は (a) Python のバージョンを `pyproject.toml` の `requires-python` と揃える、(b) Linux 専用の依存ライブラリでホスト依存の差分を出さない、(c) staging / production と同じ環境で検証する、の 3 点。

dev の `compose.yml` は `command: sleep infinity` で起動するため、コンテナは立ち上がるが API サーバー本体は手動で起動する (`docker compose exec app ddbj_search_api`)。`DDBJ_SEARCH_ENV=dev` が設定されていると config が debug モードに切り替わり、ログレベル DEBUG・uvicorn reload 有効になる。

## ddbj-search-converter の更新

依存に `ddbj-search-converter@git+https://github.com/ddbj/ddbj-search-converter@main` を持つため、converter は git の main ブランチを直接参照している。`uv.lock` は SHA を固定するので、main の更新を取り込むには明示的に upgrade する必要がある。

```bash
docker compose exec app uv sync --extra tests -P ddbj-search-converter
```

Docker イメージのビルド時も同じく `-P ddbj-search-converter` で常に最新 main を取り込む運用。converter の Pydantic スキーマや mapping を変更したら、まず converter を `-P` で更新してから本リポジトリ側の対応を進める。

## 日常コマンド

```bash
docker compose exec app uv run pytest                         # unit テスト (デフォルト、testpaths=tests/unit)
docker compose exec app uv run pytest tests/integration/      # integration テスト (要 ES)
docker compose exec app uv run ruff check ./ddbj_search_api ./tests
docker compose exec app uv run ruff format ./ddbj_search_api ./tests
docker compose exec app uv run mypy ./ddbj_search_api ./tests
```

integration テストは `DDBJ_SEARCH_INTEGRATION_ES_URL` (デフォルト `http://localhost:9200`) で接続先を切り替える。ES に到達できない場合は `pytest.skip` で全 integration テストが skip される。テスト方針の詳細は [tests/testing.md](../tests/testing.md)、シナリオは [tests/integration-scenarios.md](../tests/integration-scenarios.md)、運用注意は [tests/integration-note.md](../tests/integration-note.md)。

## OpenAPI 出力ワークフロー

API スキーマを変更したら、必ず `docs/openapi.json` を再生成して commit する。

```bash
docker compose exec app uv run dump_openapi_spec > docs/openapi.json
```

CI で diff チェックして再生成漏れを検出する仕組みは Future work。現状はレビュー時に目視で確認する運用。

## パッケージ追加・削除

`uv add` / `uv remove` をコンテナ内で実行する。テスト依存は `--optional tests` を付ける。`pyproject.toml` と `uv.lock` の変更は bind mount でホスト側に反映される。
