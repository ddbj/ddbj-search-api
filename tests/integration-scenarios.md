# Integration テストシナリオ

実 Elasticsearch (場合により Solr) に対する E2E 検証シナリオの一覧。各シナリオは「これが落ちたらどんなバグが検出されたことになるか」に答えられる粒度で書く。

このファイルは **シナリオ列挙の SSOT**。具体的なコードは `tests/integration/test_*.py` にあり、運用上の注意 (環境変数・fixture 戦略・件数 drift 対策) は [integration-note.md](integration-note.md) を参照。

> 注: 本ファイルは枠だけ用意した状態。各シナリオの本文は次の作業で埋める。

## ID 体系

`IT-{機能}-{連番 2 桁}` の形式で振る。例: `IT-DSL-01`, `IT-STATUS-03`, `IT-DBPORTAL-12`。

- 機能ごとに連番をリセット
- 削除したシナリオの ID は **再利用しない** (履歴互換性)
- 機能名は固定リスト (下記カテゴリ): `CORE`, `SEARCH`, `DETAIL`, `BULK`, `FACETS`, `UMBRELLA`, `DSL`, `DBPORTAL`, `STATUS`, `DBLINK`

## シナリオテンプレート

各シナリオは以下 4 項目で記述する。件数の実測値は書かない (drift で壊れるため、構造的不変条件のみ書く)。

```markdown
### IT-XXX-NN: <短いタイトル>

**endpoint**: HTTP method + path + 主要パラメータ

**不変条件**:
- 構造的に守るべき条件 1
- 構造的に守るべき条件 2

**回帰元**: なぜこのテストが必要か (どのバグ・どの spec 章が背景)。コミット SHA または api-spec.md の節を引用

**関連 unit テスト**: SSOT としての unit ファイル + クラス名 (例: `tests/unit/search/dsl/test_compiler_es.py::TestWildcardCaseInsensitive`)
```

## カテゴリ別シナリオ

### IT-CORE-*: 共通仕様

全 endpoint 横断の HTTP レベル不変条件。

- X-Request-ID echo (リクエスト指定時はヘッダーで返す、無ければ自動生成)
- RFC 7807 エラー (`type`, `title`, `status`, `detail` 必須)
- Trailing slash の同一視 (`/entries` と `/entries/` で同じ結果)
- Content-Type (`.json` → `application/json`、`.jsonld` → `application/ld+json`)
- CORS (`Access-Control-Allow-Origin: *`)
- 不在 endpoint の 404

### IT-SEARCH-*: 検索とページネーション

`/entries/` および `/entries/{type}/`。

- ページネーション境界 (`page=1`, `perPage=100`、`page * perPage > 10000` で 400)
- cursor / offset の使い分け (deep paging で cursor が必須)
- sort パース (`{field}:{direction}`、不正な direction で 422)
- fields フィルタ (指定フィールドだけが返る)
- types カンマ区切り (`types=bioproject,biosample`)
- keywords 演算子 (AND / OR / NOT、引用符でフレーズ検索)

### IT-DETAIL-*: Entry Detail / sameAs / dbXrefs

`/entries/{type}/{id}` 4 variant (`/{id}`, `.json`, `.jsonld`, `/dbxrefs.json`)。

- 4 variant の内容差異 (フロント向け切り詰め vs 全データ)
- sameAs フォールバック (Secondary ID 直打ちで Primary が解決される)
- alias ドキュメントヒット (converter が投入した alias 経由)
- 不在 entry の 404 と detail 文字列
- dbXrefs 切り詰め (`dbXrefsLimit` の境界)
- JSON-LD の `@id` が Primary ID

### IT-BULK-*: Bulk API

`POST /entries/{type}/bulk`。

- JSON Array 形式 / NDJSON 形式の選択
- 不変式: `len(entries) + len(notFound) == len(set(request.ids))`
- 重複 ID の扱い (set 化されてから 1 度だけ返る)
- 1000 件制限の境界
- 空 IDs の 422
- `_mget` の呼び出し回数 (大量 ID で 1 回にまとまっているか)

### IT-FACETS-*: Facets

`/facets`、`/facets/{type}`、`/entries/?includeFacets=true`。

- cross-type と type 別の facet 構造差
- includeFacets で検索結果と facet が一括取得される
- facet 集計が `status:public` のみで行われている
- OpenAPI Facets schema の整合 (`status` キーが無い、`organism` / `accessibility` が必須)

### IT-UMBRELLA-*: Umbrella Tree

`GET /entries/bioproject/{accession}/umbrella-tree`。

- orphan (親子なし) で `roots = [self]`, `edges = []`
- depth 1 (umbrella → leaf) の典型構造
- multi-parent DAG (子が複数親を持つ) でエッジが正しく重複排除される
- `MAX_DEPTH = 10` 超過で 500
- 中間 node の参照切れで該当 edge を除外、API 全体は 200
- hidden node (status filter) の edge 削除 (status filter シナリオと連動)

### IT-DSL-*: ES DSL コンパイル動作

`/db-portal/cross-search`、`/db-portal/search`、`/db-portal/parse` の DSL 関連。

- ES wildcard が `case_insensitive: true` で大文字小文字を吸収
- cursor + adv 同時指定で `cursor-not-supported` slug を 400 で返す (ES / Solr で同一 slug)
- `/db-portal/parse` が DSL を AST JSON に変換
- `/db-portal/parse` の OpenAPI responses が `{200, 400, 422, 500}` (404 を含まない)
- grammar が symbol 含み wildcard を受理 (`HIF-1*`, `COVID-19*` など)

### IT-DBPORTAL-*: db-portal 横断

`/db-portal/cross-search` (横断 fan-out) と `/db-portal/search?db=trad|taxonomy` (DB 指定) の Solr 経由 (ARSA / TXSearch) シナリオ。`@pytest.mark.staging_only` で分離する想定。

- ARSA (trad) `MolecularType` / `SequenceLength` が response に含まれる
- ARSA `organism.identifier` が Feature の `db_xref="taxon:..."` から抽出される
- trad / taxonomy の `description` が常に null (冗長な機械連結を廃止)
- TXSearch lineage の自身除去 (`lineage[0]` が `scientific_name` と一致する場合のみ削除)
- adv Tier 3 field の uf allowlist 完全性 (compile_to_solr が emit する field が edismax に通る)

### IT-STATUS-*: status filter

ES `status` フィールド (`public` / `suppressed` / `withdrawn` / `private`) によるアクセス制御。converter リリース取り込み待ち、accession の代表値が確定するまで一部 TBD。

- `/entries/` 自由文検索で `withdrawn` / `private` がヒットしない
- `/entries/?keywords=<accession>` で suppressed accession がヒットする (UX 維持)
- `/entries/{type}/{id}` 4 variant で withdrawn / private は 404、suppressed は 200
- 404 の `detail` 文字列が不在 entry と完全一致 (status 推測を防ぐ)
- bulk で混在 IDs が `entries` (public + suppressed) と `notFound` (withdrawn + private + 不在) に分類される
- umbrella-tree で seed が hidden なら 404、中間 node が hidden なら edge から除外
- facets が `status:public` のみで集計される

### IT-DBLINK-*: DBLinks

`/dblink/`、`/dblink/{type}/{id}`、`POST /dblink/counts`。

- 21 種の AccessionType がすべて返る
- target フィルタ (単一・複数・存在しない target)
- DuckDB 不在で 500
- ソート順 (タイプ昇順 → アクセッション昇順)
- 一括 count の上限件数

## 移植トレーサビリティ

過去のレビューで検出した bug fix から導かれた検証ケースを IT-XXX に紐付ける作業表。次会話で本文を埋める際、各 IT に「**回帰元**: コミット SHA + 主旨」を残し、後から「なぜこのテストがあるか」を辿れるようにする。

(対応表は次の作業で埋める)
