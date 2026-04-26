# Integration テスト運用ノート

シナリオ列挙は [integration-scenarios.md](integration-scenarios.md)、テスト方針は [testing.md](testing.md)。本書は環境準備・fixture 戦略・件数 drift 対策・Solr 用 marker・CI といった「どう運用するか」をまとめる。

## 接続切替

`tests/integration/conftest.py` は環境変数 `DDBJ_SEARCH_INTEGRATION_ES_URL` を読む。デフォルトは `http://localhost:9200`。

```bash
DDBJ_SEARCH_INTEGRATION_ES_URL=http://es-host:9200 uv run pytest tests/integration/
```

session-scoped の `ensure_es` fixture が起動時に ES の疎通を確認し、到達できなければ `pytest.skip(allow_module_level=True)` で session 全体を skip する。

## ES の用意

ddbj-search-converter リポジトリ側に compose があり、`ddbj-search-network-dev` 上に `ddbj-search-es-dev` を起動できる。本リポジトリの `compose.yml` も同じ network を `external: true` で参照しているので、ホスト側からは `localhost:9200` (publish) または network 内の `ddbj-search-es-dev:9200` で接続できる。

別の ES に向けたい場合は `DDBJ_SEARCH_INTEGRATION_ES_URL` で接続先を切り替える。

## fixture 戦略

`tests/integration/conftest.py` に accession / 代表 token / bucket key を定数として置く。種類は 3 系統:

- 代表 accession (status / shape を pin する例: `public` / `suppressed` / `private` 各 1 件)
- type-specific term filter の代表 bucket (例: `SRA_LIBRARY_STRATEGY="WGS"`)
- type-specific text-match の代表 token (例: `BIOSAMPLE_HOST="Homo sapiens"`)

値は実 ES への aggregation / count probe で実測し、converter のリリース取り込みのタイミングで再採取する。

接続先のデータに対象が存在しない場合は対応する定数を空文字 (`""`) のまま置き、`require_value` ヘルパー経由で当該テストを skip する。**使う見込みのない定数は置かない** (drift しても気付かれずに腐るため)。データが揃ったら値を埋めて skip を外す。

テスト用 doc を共有 ES に POST して setUp/tearDown する運用は禁止 (汚染リスクがあるため)。

## 件数 drift 対策

ES / Solr のデータは converter の更新で件数が変わる。固定値 assert は壊れる前提で書かない。代わりに **構造的不変条件** で書く。

```python
# 件数 drift に弱い (NG)
assert total == 26537  # 来月にはずれる

# 件数 drift に強い (OK)
r_lower = fetch("adv=title:cancer*&db=bioproject")
r_upper = fetch("adv=title:Cancer*&db=bioproject")
assert r_lower["total"] == r_upper["total"]  # case-insensitive で値は何でも良い
assert r_lower["total"] > 0                  # 「何かヒットしている」の最小保証
```

```python
# 相対比較で regression を検出する例
total_all = fetch("adv=title:*&db=trad")["total"]
total_bct = fetch("adv=division:BCT&db=trad")["total"]
assert total_bct < total_all / 2  # 全件 fallback (regression) を弾く
```

```python
# 隠匿 entry と存在しない entry の detail が完全一致
resp_hidden = client.get(f"/entries/bioproject/{withdrawn_id}")
resp_missing = client.get("/entries/bioproject/PRJDB_DOES_NOT_EXIST_99999")
assert resp_hidden.status_code == resp_missing.status_code == 404
assert resp_hidden.json()["detail"] == resp_missing.json()["detail"]
```

使ってよい assert パターン:

- set 一致: `{item["id"] for item in body["items"]} == expected_ids`
- 相対比較: `total_kw <= total_all`、`total_filtered < total_all / 2`
- 最小保証: `total > 0`、`len(items) >= 1`
- 文字列一致: `resp_hidden.json()["detail"] == resp_missing.json()["detail"]`

`docs/api-spec.md` で値の集合自体が SSOT になっているもの (例: AccessionType の列挙) は固定値 assert (set 一致) を使ってよい。

## Solr 必須シナリオ

`/db-portal/cross-search` の 8 DB fan-out と `/db-portal/search?db=trad|taxonomy` は ARSA / TXSearch (Solr) を経由する。Solr はローカルに代替がなく、staging 環境にしかない。これらのシナリオは `@pytest.mark.staging_only` で分離する。

`pyproject.toml` の `[tool.pytest.ini_options]` に marker を登録する。

```toml
markers = [
    "staging_only: requires staging environment (Solr endpoints: ARSA/TXSearch)",
]
```

`addopts` には `-m "not staging_only"` を入れない (デフォルトで全 marker を含めて実行する方針)。実行コマンドは目的に応じて使い分ける。

| コマンド | 含まれるシナリオ | 用途 |
|---------|----------------|------|
| `pytest tests/integration/` | 全シナリオ (Solr 含む) | staging 環境 (default) |
| `pytest tests/integration/ -m "not staging_only"` | Solr 抜き | CI 統合時 (Future work、Solr が無い環境) |
| `pytest tests/integration/ -m staging_only` | Solr のみ | 限定確認 (ARSA / TXSearch の整合性チェック) |

## CI

現状の `uv run pytest` は `testpaths = ["tests/unit"]` で unit のみ実行する。integration は手動 (`uv run pytest tests/integration/`)。

GitHub Actions で integration を回すなら ES service container を `services:` で起動する必要があるが、データを投入する仕組み (converter の bring-up + 最小データセット) が要るので Future work。

unit の coverage は `pyproject.toml` の addopts で常に計測され、`tests/htmlcov/` に出力される。integration は coverage 計測対象外として扱う (実 ES の挙動を見るのが目的のため)。
