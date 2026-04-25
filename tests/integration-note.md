# Integration テスト運用ノート

シナリオ本文 ([integration-scenarios.md](integration-scenarios.md)) からは切り離し、ES の用意・fixture 戦略・件数 drift 対策・CI 戦略といった「どう運用するか」を集める。

## 接続切替

`tests/integration/conftest.py` は環境変数 `DDBJ_SEARCH_INTEGRATION_ES_URL` を読む。デフォルトは `http://localhost:9200`。

```bash
DDBJ_SEARCH_INTEGRATION_ES_URL=http://es-host:9200 uv run pytest tests/integration/
```

session-scoped の `ensure_es` fixture が起動時に ES の疎通を確認し、到達できなければ `pytest.skip(allow_module_level=True)` で session 全体を skip する。CI でも開発時でも、ES が立っていなければ自動的に飛ばされる。

## ES の用意

ddbj-search-converter リポジトリ側に compose があり、`ddbj-search-network-dev` 上に `ddbj-search-es-dev` を起動できる。本リポジトリの `compose.yml` も同じ network を `external: true` で参照しているので、ホスト側からは `localhost:9200` (publish) または network 内の `ddbj-search-es-dev:9200` で接続できる。

別の ES に向けたい場合は `DDBJ_SEARCH_INTEGRATION_ES_URL` で接続先を切り替える。

ローカル用の固定 fixture (専用 mini インデックス) は今は持たない。代表 accession の値が変動するときは converter のリリース取り込みのタイミングで再採取する運用。

## fixture 戦略

特定の不変条件を assert するためにいくつか「代表 accession」が必要。例: status filter のテストには `public` / `suppressed` / `withdrawn` / `private` の 4 値の代表 ID。3 案を比較する。

| 案 | 内容 | メリット | デメリット |
|----|------|---------|-----------|
| A (推奨) | 実 ES の代表 accession を実測 → `tests/integration/conftest.py` に定数登録 | コード追跡可能、レビュー時に値が見える | converter リリースで accession が消えると更新が必要 |
| B | test 内で `_search` を叩いて動的に seed (例: `term: {status: suppressed}` で 1 件取得) | 値の劣化に強い | テスト失敗時の再現性が悪い、ES クエリの遅延でテストが遅くなる |
| C | テスト用 doc を POST で投入 → setUp/tearDown | 完全に決定的 | staging を汚染するリスク、teardown 失敗で残留 |

**推奨は案 A**。converter のリリース取り込み手順に「代表 accession の更新」を組み込む。案 B は補助的に「条件を満たす ID を 1 件以上見つける」スモークでだけ使う。案 C は禁止 (共有 ES を汚さない)。

## 件数 drift 対策

ES / Solr のデータは converter の更新で件数が変わる。固定値 assert は壊れる前提で書かない。代わりに **構造的不変条件** で書く。既存テストもこのパターンで揃えてある。

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

## Solr 必須シナリオ

`/db-portal/search` の一部は ARSA / TXSearch (Solr) を経由する。Solr はローカルに代替がなく、staging 環境にしかない。これらのシナリオは `@pytest.mark.staging_only` などで分離する想定 (現状 marker 未定義、整備時に `pyproject.toml` に追加)。

`staging_only` を付けたテストはデフォルトの `pytest tests/integration/` では skip し、`pytest -m staging_only` で明示的に有効化する。CI (Solr が無い) では skip され、staging に実環境を持つ作業者が手元で回す。

## CI

現状の `uv run pytest` は `testpaths = ["tests/unit"]` で unit のみ実行する。integration は手動 (`uv run pytest tests/integration/`)。

GitHub Actions で integration を回すなら ES service container を `services:` で起動する必要があるが、データを投入する仕組み (converter の bring-up + 最小データセット) が要るので Future work。

unit の coverage は `pyproject.toml` の addopts で常に計測され、`tests/htmlcov/` に出力される。integration は coverage 計測対象外として扱う (実 ES の挙動を見るのが目的のため)。
