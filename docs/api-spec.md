# API 仕様書

## 概要

DDBJ Search API は、BioProject / BioSample / SRA / JGA データを検索・取得するための RESTful API サーバー。
認証なしのパブリック API として提供する。

設計方針やシステム全体のネットワーク構成は [ddbj-search/docs/network-architecture.md](https://github.com/ddbj/ddbj-search/blob/main/docs/network-architecture.md) を参照。

## OpenAPI ドキュメント

インタラクティブな API ドキュメント (Swagger UI) は `/docs` で確認できる。

| 環境 | URL |
|------|-----|
| dev | `http://localhost:8080/search/api/docs` |
| staging | `https://ddbj-staging.nig.ac.jp/search/api/docs` |
| production | `https://ddbj.nig.ac.jp/search/api/docs` |

### デプロイ構成

API サーバーは URL prefix `/search/api` の下にデプロイされる。
本仕様書ではエンドポイントパスを prefix なしの相対パス (例: `/entries/`) で記述する。
実際のリクエストでは prefix を付与する (例: `https://ddbj.nig.ac.jp/search/api/entries/`)。

| 環境 | ベース URL |
|------|-----------|
| dev | `http://localhost:8080/search/api` |
| staging | `https://ddbj-staging.nig.ac.jp/search/api` |
| production | `https://ddbj.nig.ac.jp/search/api` |

### 主要な設計ポイント

- **横断検索とタイプ別検索**: 全 12 タイプを横断検索可能、タイプを絞り込んでの検索も可能
- **ファセット集計**: 検索結果のファセットカウント (type, organism, status 等) を取得可能
- **JSON-LD 対応**: RDF 対応の JSON-LD 形式でエントリー詳細を取得可能
- **一括取得 (Bulk API)**: 複数 ID を指定して一括取得。JSON Array / NDJSON 形式を選択可能
- **タイプ別エンドポイント**: `/entries/{type}/` は `/entries/?types=X` と同等だが、タイプ固有パラメータ (BioProject の `umbrella` 等) を持つため独立エンドポイントとして提供
- **`.json` 拡張子の規約**: `/{id}` と `/{id}.json` は異なるレスポンスを返す。`/{id}` はフロントエンド向け (dbXrefs 切り詰め + dbXrefsCount 付与)、`/{id}.json` はデータアクセス向け (ES ドキュメント + DuckDB dbXrefs)。つまり `.json` 拡張子は「**全データ取得**」を意味する
- **`includeFacets` と `/facets` の使い分け**: `GET /entries/?includeFacets=true` は検索結果とファセットを 1 リクエストで取得 (フロントエンド向け)。`GET /facets` はファセットのみ取得 (検索結果リスト不要の場合)
- **スキーマ定義**: エントリーのスキーマは [ddbj-search-converter](https://github.com/ddbj/ddbj-search-converter) で定義

## エンドポイント一覧

### Entries API (検索系: 2 エンドポイント)

| Method | Path | 説明 |
|--------|------|------|
| GET | `/entries/` | 全タイプ横断検索 |
| GET | `/entries/{type}/` | タイプ別検索 |

### Entry Detail API (詳細取得系: 4 エンドポイント)

| Method | Path | 説明 |
|--------|------|------|
| GET | `/entries/{type}/{id}` | エントリー詳細取得 (JSON) |
| GET | `/entries/{type}/{id}.json` | エントリー詳細取得 (JSON) |
| GET | `/entries/{type}/{id}.jsonld` | エントリー詳細取得 (JSON-LD) |
| GET | `/entries/{type}/{id}/dbxrefs.json` | dbXrefs 全件取得 |

#### sameAs による ID 解決

Entry Detail API の 4 エンドポイントは、`{id}` パスパラメータに対して以下の順序でエントリーを解決する:

1. **identifier 一致**: ES ドキュメント ID (`_id` = `identifier`) で直接取得を試みる
2. **sameAs フォールバック**: 1 で見つからない場合、`sameAs` フィールドを nested query で検索する。検索条件は `sameAs.identifier == {id} AND sameAs.type == {type}` (同一タイプのみ)
3. **404**: いずれでも見つからない場合は `404 Not Found` を返す

sameAs フォールバックでヒットした場合、レスポンスは identifier で直接取得した場合と同一形式 (リダイレクトはしない)。

**対象データ**: JGA エントリー (jga-study, jga-dataset, jga-dac) は XML の `IDENTIFIERS.SECONDARY_ID` を `sameAs` に格納しており、Secondary ID から Primary エントリーを取得できる。ロジック自体は全タイプ共通で、`sameAs` が空のタイプではフォールバックが発火しないだけである。

**Elasticsearch 要件**: `sameAs` フィールドは nested タイプとしてインデックスされている必要がある (ddbj-search-converter 側のマッピング定義)。

### Bulk API (一括取得系: 1 エンドポイント)

| Method | Path | 説明 |
|--------|------|------|
| POST | `/entries/{type}/bulk` | 一括取得 |

### Facets API (ファセット集計: 2 エンドポイント)

| Method | Path | 説明 |
|--------|------|------|
| GET | `/facets` | 横断ファセット集計 |
| GET | `/facets/{type}` | タイプ別ファセット集計 |

### DBLinks API (関連 ID 逆引き: 3 エンドポイント)

| Method | Path | 説明 |
|--------|------|------|
| GET | `/dblink/` | アクセッションタイプ一覧 |
| GET | `/dblink/{type}/{id}` | 関連 ID 取得 |
| POST | `/dblink/counts` | 一括カウント取得 |

### Service Info API (1 エンドポイント)

| Method | Path | 説明 |
|--------|------|------|
| GET | `/service-info` | サービス情報取得 |

## 共通仕様

### 型の命名規則

型名は PascalCase で、以下のサフィックス規則に従う。フィールド詳細は OpenAPI ドキュメントを参照。

| サフィックス | 用途 | 例 |
|------------|------|-----|
| `*Params` | パスパラメータ型 | `TypeParams`, `TypeIdParams` |
| `*Query` | クエリパラメータ型 | `EntriesQuery`, `EntryDetailQuery`, `BulkQuery` |
| `*Request` | リクエストボディ型 (POST/PUT) | `BulkRequest` |
| `*Response` | レスポンスボディ型 | `EntryListResponse`, `FacetsResponse`, `ServiceInfoResponse` |
| (なし) | 共通部品・ドメインモデル | `Pagination`, `EntryListItem`, `ProblemDetails` |

複数のエンドポイントで共通のパラメータやクエリがある場合は、mixin 用の共通型として切り出し、各エンドポイントの `*Query` で合成する。

| Mixin 型 | 用途 | 合成先 |
|----------|------|-------|
| `PaginationQuery` | page, perPage | `EntriesQuery`, `EntriesTypeQuery` |
| `SearchFilterQuery` | 検索フィルタ (keywords, organism, date*) | `EntriesQuery`, `EntriesTypeQuery`, `FacetsQuery`, `FacetsTypeQuery` |
| `ResponseControlQuery` | レスポンス制御 (sort, fields, include*) | `EntriesQuery`, `EntriesTypeQuery` |

タイプ固有のパラメータがある場合は、ベース型を拡張してタイプ別 Query を作る (例: `EntriesBioProjectQuery` は `EntriesTypeQuery` を拡張)。

### データタイプ (DbType)

API で扱うデータベースタイプ。12 タイプを扱う。

`bioproject`, `biosample`, `sra-submission`, `sra-study`, `sra-experiment`, `sra-run`, `sra-sample`, `sra-analysis`, `jga-study`, `jga-dataset`, `jga-dac`, `jga-policy`

### Content-Type

| 形式 | Content-Type | 説明 |
|------|--------------|------|
| JSON | `application/json` | 標準レスポンス |
| JSON-LD | `application/ld+json` | RDF 対応レスポンス |
| NDJSON | `application/x-ndjson` | 一括取得時のストリーミングレスポンス |

### CORS

すべてのオリジンからのリクエストを許可する。

```plain
Access-Control-Allow-Origin: *
Access-Control-Allow-Methods: *
Access-Control-Allow-Headers: *
```

### Trailing Slash

Entries API のコレクション (リスト) 系エンドポイントは trailing slash 付きを canonical パスとする (例: `/entries/`, `/entries/{type}/`)。trailing slash なし (`/entries`) でも同じレスポンスを返す (リダイレクトしない)。

Facets API (`/facets`, `/facets/{type}`) は trailing slash なしのみをサポートする。
個別リソース (例: `/entries/{type}/{id}`) にも trailing slash を付けない。

### リクエスト追跡 (X-Request-ID)

全レスポンスに `X-Request-ID` ヘッダーを付与する。クライアントがリクエスト時に `X-Request-ID` を指定した場合はその値を使用し、指定がない場合はサーバーで UUID を生成する。
エラーレスポンスにも `requestId` フィールドとして含まれるため、ログとの突き合わせに使用できる。

### エラーレスポンス (RFC 7807)

エラー時は [RFC 7807 Problem Details](https://tools.ietf.org/html/rfc7807) 形式の JSON を返す。Content-Type は `application/problem+json` を使用する。

```json
{
  "type": "about:blank",
  "title": "Not Found",
  "status": 404,
  "detail": "The requested BioProject 'INVALID' was not found.",
  "instance": "/entries/bioproject/INVALID",
  "timestamp": "2024-01-15T10:30:00Z",
  "requestId": "req-abc123"
}
```

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `type` | string | 問題タイプ URI |
| `title` | string | 問題の概要 (例: "Not Found") |
| `status` | number | HTTP ステータスコード |
| `detail` | string | 詳細メッセージ |
| `instance` | string | エラーが発生したリクエストパス |
| `timestamp` | string | エラー発生時刻 (ISO 8601) |
| `requestId` | string | リクエスト追跡用 ID (X-Request-ID と同じ) |

**ステータスコード一覧**:

| ステータス | 意味 | 発生条件 |
|-----------|------|---------|
| 400 | Bad Request | Deep paging 制限超過 (`page * perPage > 10000`) |
| 404 | Not Found | エントリーが存在しない、不正な `{type}` |
| 422 | Unprocessable Entity | パラメータバリデーションエラー (`perPage` の範囲外、不正な日付形式 (`YYYY-MM-DD` 以外) や不正な日付 (`2024-02-30` 等)、不正な `types` 値、不正な `umbrella` 値 (`TRUE`/`FALSE` 以外)、不正な `sort` フィールド、不正な `keywordFields` 値など) |
| 500 | Internal Server Error | ES 接続エラー、DuckDB ファイルが見つからない (Entries 検索/詳細/Bulk/DBLinks API)、その他サーバー内部エラー |

400 と 422 の使い分け: リクエストのパラメータ型・形式・制約のバリデーションは 422、アプリケーションのビジネスルール違反 (deep paging 制限など) は 400 を返す。

### ページネーション

リスト系エンドポイントはオフセットベースのページネーションを採用する。

**リクエスト** (`PaginationQuery`):

| パラメータ | 型 | デフォルト | 説明 |
|-----------|-----|-----------|------|
| `page` | integer | `1` | ページ番号 (1 始まり) |
| `perPage` | integer | `10` | 1 ページあたりの件数 (1-100) |

**レスポンス** (`Pagination`):

```json
{
  "page": 1,
  "perPage": 10,
  "total": 150000
}
```

- `total` は常に正確な総件数を返す (`track_total_hits=true`)
- `perPage` が範囲外 (1-100) の場合は `422 Unprocessable Entity`

**Deep paging 制限**:

`page * perPage` が 10000 を超えるリクエストは `400 Bad Request` を返す。これは Elasticsearch の `index.max_result_window` 制限に基づく。10000 件を超える結果を網羅的に取得する必要がある場合は、Bulk API を使用する。

### 日付形式

ISO 8601 形式 (`YYYY-MM-DD`) を使用する。範囲指定は `From` / `To` の 2 パラメータで行う。

### 検索パラメータ

`GET /entries/`, `GET /entries/{type}/`, `GET /facets`, `GET /facets/{type}` で共通の検索パラメータ。2 つの mixin 型に分割して定義する。

**検索フィルタ** (`SearchFilterQuery`):

| パラメータ | 型 | デフォルト | 説明 |
|----------|-----|-----------|------|
| `keywords` | string | — | 検索キーワード (カンマ区切りで複数指定可) |
| `keywordFields` | string | — | 検索対象フィールドを限定 (カンマ区切り)。指定可能な値: `identifier`, `title`, `name`, `description`。不正な値は 422 |
| `keywordOperator` | string | `AND` | キーワードの結合条件。`AND` (すべて一致) / `OR` (いずれか一致) |
| `organism` | string | — | NCBI Taxonomy ID (例: `9606`) |
| `datePublishedFrom` / `datePublishedTo` | string | — | 公開日の範囲 (ISO 8601: `YYYY-MM-DD`) |
| `dateModifiedFrom` / `dateModifiedTo` | string | — | 更新日の範囲 (ISO 8601: `YYYY-MM-DD`) |

**レスポンス制御** (`ResponseControlQuery`):

| パラメータ | 型 | デフォルト | 説明 |
|----------|-----|-----------|------|
| `sort` | string | — | ソート順。形式: `{field}:{direction}`。ソート可能フィールド: `datePublished`, `dateModified`。direction: `asc` / `desc`。未指定時は relevance (検索スコア) 順 |
| `fields` | string | — (全フィールド) | レスポンスに含めるフィールドを限定 (カンマ区切り)。ES ドキュメントのトップレベルフィールド名を指定 (例: `identifier,organism,datePublished`)。未指定時はすべてのフィールドを返す |
| `includeProperties` | boolean | `true` | `true` で `properties` フィールドを含める |
| `includeFacets` | boolean | `false` | `true` で検索結果にファセット集計を含める。`GET /facets` と異なり検索結果リストと同時に取得できる |

Entries API (`GET /entries/`, `GET /entries/{type}/`) は `SearchFilterQuery` + `ResponseControlQuery` を合成して使用する。Facets API (`GET /facets`, `GET /facets/{type}`) は `SearchFilterQuery` のみを使用する。

**エンドポイント固有のパラメータ**:

| 対象 | パラメータ | 説明 |
|------|----------|------|
| `GET /entries/`, `GET /facets` | `types` | データタイプでフィルタ (カンマ区切り) |
| `GET /entries/bioproject/`, `GET /facets/bioproject` | `organization`, `publication`, `grant` | テキストフィルタ |
| | `umbrella` | `TRUE` / `FALSE` (大文字小文字不問) |

### ファセット

検索結果のファセット集計。取得方法は 2 つ:

- `GET /facets` (または `GET /facets/{type}`): ファセットのみ取得。検索結果リストが不要な場合に使う
- `GET /entries/?includeFacets=true` (または `GET /entries/{type}/?includeFacets=true`): 検索結果リストとファセットを 1 リクエストで同時取得。フロントエンドで検索結果とファセットを同時に表示する場合に使う

ファセットのカウントは、検索クエリ (`SearchFilterQuery` のパラメータ) が適用された結果に対して集計される。例えば `keywords=cancer` を指定した場合、ファセットの各値のカウントは `cancer` にマッチするエントリーのみを対象とした件数になる。クエリを指定しない場合は全件が対象になる。

**共通ファセットフィールド** (全タイプ共通):

| フィールド | 説明 |
|-----------|------|
| `organism` | 生物種別カウント |
| `status` | ステータス別カウント |
| `accessibility` | アクセシビリティ別カウント |

**横断検索時の追加フィールド** (`GET /entries/`, `GET /facets`):

| フィールド | 説明 |
|-----------|------|
| `type` | データタイプ別カウント |

**タイプ固有フィールド**:

| タイプ | フィールド | 説明 |
|--------|----------|------|
| bioproject | `objectType` | Umbrella / 通常の区分 |

タイプ固有フィールドは、そのタイプのファセット (`GET /facets/{type}`, `GET /entries/{type}/?includeFacets=true`) でのみ返される。

### dbXrefs

エントリーの `dbXrefs` (データベース間参照) はエントリーによっては数千万件になるため、ES ドキュメントには含めず DuckDB (dblink.duckdb) から取得する。

**データソース**:

dbXrefs は DBLinks API と同じ DuckDB ファイル (`dblink.duckdb`) から取得する。ES ドキュメントの `dbXrefs` フィールドは使用しない (`_source_excludes=dbXrefs` で除外)。

**エンドポイント別の dbXrefs 扱い**:

| エンドポイント | dbXrefs の扱い | dbXrefsCount |
|--------------|---------------|--------------|
| `GET /entries/`, `GET /entries/{type}/` | DuckDB から type ごとに `dbXrefsLimit` 件取得 | あり (DuckDB で集計) |
| `GET /entries/{type}/{id}` | DuckDB から type ごとに `dbXrefsLimit` 件取得 | あり (DuckDB で集計) |
| `GET /entries/{type}/{id}.json` | ES ストリーム + DuckDB 全件 (tail injection) | なし |
| `GET /entries/{type}/{id}.jsonld` | ES ストリーム + DuckDB 全件 (tail injection) | なし |
| `POST /entries/{type}/bulk` | ES ストリーム + DuckDB 全件 | なし |

フロントエンド向けエンドポイント (リスト API・`/{id}`) では `dbXrefsLimit` クエリパラメータ (デフォルト: 100, 範囲: 0-1000) で **type ごとに** `dbXrefs` を切り詰め、`dbXrefsCount` (タイプ別の参照総数) をレスポンスに付与する。例えば `dbXrefsLimit=100` で biosample 200 件 + sra-study 50 件のエントリーでは、biosample 100 件 + sra-study 50 件 = 計 150 件が返る。返却される `dbXrefs` の総数は最大 `dbXrefsLimit × 関連 type 数` になるため、グローバル上限ではなく type ごとの上限である点に注意。`dbXrefsLimit=0` の場合は `dbXrefs` を空配列で返すが、`dbXrefsCount` は常に返す。クライアントは `dbXrefs` の件数と `dbXrefsCount` の合計を比較し、差分がある場合は専用エンドポイントへのリンクを表示する。

データアクセス向けエンドポイント (`.json`, `.jsonld`, `dbxrefs.json`) では ES ストリームから dbXrefs を除外して取得し、DuckDB の dbXrefs を JSON の末尾に注入 (tail injection) する。メモリに全件を載せずチャンクストリーミングで返す。Bulk API (`/bulk`) では 1 エントリーずつ dbXrefs をメモリロードして注入する (JSON array 組み立てのため)。

**専用エンドポイント**:

- `GET /entries/{type}/{id}/dbxrefs.json`: ES HEAD で存在確認後、DuckDB から全件をストリーミング取得 (`DbXrefsFullResponse` 形式: `{"dbXrefs": [...]}` オブジェクト)

### Bulk API

`POST /entries/{type}/bulk` で複数エントリーを一括取得する。パラメータの詳細は型カタログの `BulkQuery` / `BulkRequest` を参照。

各エントリーの `dbXrefs` は ES ドキュメントから除外し、DuckDB から取得して JSON に注入する (1 エントリーずつメモリロード)。

指定された ID のうち見つからなかったものは、レスポンスの `notFound` フィールドで返す。

- `format=json`: `{ "entries": [...], "notFound": ["ID_1", "ID_2"] }`
- `format=ndjson`: エントリーを 1 行 1 件で出力 (`notFound` は含まない)

### DBLinks API

dblink DB (DuckDB) を参照し、アクセッション間の関連 ID を逆引きする。
ES は使用せず、ddbj-search-converter が管理する DuckDB ファイルを直接参照する。

#### データソース

- DuckDB ファイル: `/home/w3ddbjld/const/dblink/dblink.duckdb`
- テーブル: `relation (src_type, dst_type, src_accession, dst_accession)`
- 双方向検索: `src → dst` と `dst → src` の UNION ALL で関連をすべて取得

#### アクセッションタイプ (AccessionType, 21 種)

`bioproject`, `biosample`, `gea`, `geo`, `hum-id`, `insdc`, `insdc-assembly`, `insdc-master`, `jga-dac`, `jga-dataset`, `jga-policy`, `jga-study`, `metabobank`, `pubmed-id`, `sra-analysis`, `sra-experiment`, `sra-run`, `sra-sample`, `sra-study`, `sra-submission`, `taxonomy`

DbType (12 種) とは別の型。dblink 固有のタイプを含む。

#### `GET /dblink/`

利用可能なアクセッションタイプの一覧を返す (静的、DB 不要)。

Trailing slash 両対応 (`/dblink` と `/dblink/` は同じ結果)。

**レスポンス** (`DbLinksTypesResponse`):

```json
{
  "types": [
    "bioproject", "biosample", "gea", "geo", "hum-id",
    "insdc", "insdc-assembly", "insdc-master", "jga-dac", "jga-dataset",
    "jga-policy", "jga-study", "metabobank", "pubmed-id",
    "sra-analysis", "sra-experiment", "sra-run", "sra-sample",
    "sra-study", "sra-submission", "taxonomy"
  ]
}
```

#### `GET /dblink/{type}/{id}`

指定アクセッションに関連する ID を返す。

Trailing slash 両対応 (`/dblink/{type}/{id}` と `/dblink/{type}/{id}/` は同じ結果)。

**パスパラメータ**:

| パラメータ | 型 | 説明 |
|-----------|-----|------|
| `type` | AccessionType | ソースのアクセッションタイプ |
| `id` | string | アクセッション ID |

**クエリパラメータ** (`DbLinksQuery`):

| パラメータ | 型 | デフォルト | 説明 |
|-----------|-----|-----------|------|
| `target` | string | — (全タイプ) | 関連先タイプでフィルタ (カンマ区切りで複数指定可)。値は AccessionType |

**レスポンス** (`DbLinksResponse`):

```json
{
  "identifier": "hum0014",
  "type": "hum-id",
  "dbXrefs": [
    {"identifier": "JGAS000101", "type": "jga-study", "url": "https://ddbj.nig.ac.jp/search/entry/jga-study/JGAS000101"},
    {"identifier": "JGAS000381", "type": "jga-study", "url": "https://ddbj.nig.ac.jp/search/entry/jga-study/JGAS000381"}
  ]
}
```

- `dbXrefs` の各要素は converter 由来の `Xref` (identifier, type, url)
- `dbXrefs` はタイプ昇順 → アクセッション昇順でソート (決定的)
- 該当なしの場合: 200 + 空の `dbXrefs: []`

**ストリーミング**: 関連 ID が大量 (数千万件) になり得るため、DuckDB から chunk 単位で読み出してストリーミングレスポンスで返す。

**エラー**:

| ステータス | 発生条件 |
|-----------|---------|
| 422 | 無効な `{type}` (AccessionType 以外) |
| 422 | 無効な `target` 値 (AccessionType 以外) |
| 500 | DuckDB ファイルが見つからない |

#### `POST /dblink/counts`

複数アクセッションの関連 ID タイプ別カウントを一括取得する。フロントエンドで dbXrefs のサマリー表示に使用する。

**リクエストボディ** (`DbLinksCountsRequest`):

```json
{
  "items": [
    {"type": "bioproject", "id": "PRJDB1"},
    {"type": "biosample", "id": "SAMD00000001"}
  ]
}
```

| フィールド | 型 | 制約 | 説明 |
|-----------|-----|------|------|
| `items` | array | 1-100 件 | カウント対象のアクセッション |
| `items[].type` | AccessionType | — | アクセッションタイプ |
| `items[].id` | string | — | アクセッション ID |

**レスポンス** (`DbLinksCountsResponse`):

```json
{
  "items": [
    {"identifier": "PRJDB1", "type": "bioproject", "counts": {"biosample": 5, "sra-study": 2}},
    {"identifier": "SAMD00000001", "type": "biosample", "counts": {"bioproject": 1}}
  ]
}
```

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `items` | array | カウント結果 |
| `items[].identifier` | string | アクセッション ID |
| `items[].type` | string | アクセッションタイプ |
| `items[].counts` | object | タイプ別カウント (キー: AccessionType, 値: 件数) |

**エラー**:

| ステータス | 発生条件 |
|-----------|---------|
| 422 | `items` が空、100 件超過、無効な `type` |
| 500 | DuckDB ファイルが見つからない |

## スキーマ定義

### エンドポイント別の型

各エンドポイントで使用する Params / Query / Request / Response の型名。

| Endpoint | Params | Query | Request | Response |
|----------|--------|-------|---------|----------|
| `GET /entries/` | — | `EntriesQuery` | — | `EntryListResponse` |
| `GET /entries/{type}/` | `TypeParams` | `EntriesTypeQuery` | — | `EntryListResponse` |
| `GET /entries/bioproject/` | `TypeParams` | `EntriesBioProjectQuery` | — | `EntryListResponse` |
| `GET /entries/{type}/{id}` | `TypeIdParams` | `EntryDetailQuery` | — | `*DetailResponse` (4 種) |
| `GET /entries/{type}/{id}.json` | `TypeIdParams` | — | — | `*EntryResponse` (4 種) |
| `GET /entries/{type}/{id}.jsonld` | `TypeIdParams` | — | — | `*EntryJsonLdResponse` (4 種) |
| `GET /entries/{type}/{id}/dbxrefs.json` | `TypeIdParams` | — | — | `DbXrefsFullResponse` |
| `POST /entries/{type}/bulk` | `TypeParams` | `BulkQuery` | `BulkRequest` | `BulkResponse` |
| `GET /facets` | — | `FacetsQuery` | — | `FacetsResponse` |
| `GET /facets/{type}` | `TypeParams` | `FacetsTypeQuery` | — | `FacetsResponse` |
| `GET /facets/bioproject` | `TypeParams` | `FacetsBioProjectQuery` | — | `FacetsResponse` |
| `GET /dblink/` | — | — | — | `DbLinksTypesResponse` |
| `GET /dblink/{type}/{id}` | `DbLinksParams` | `DbLinksQuery` | — | `DbLinksResponse` |
| `POST /dblink/counts` | — | — | `DbLinksCountsRequest` | `DbLinksCountsResponse` |
| `GET /service-info` | — | — | — | `ServiceInfoResponse` |

**レスポンスのタイプ別展開**:

| converter 型 | 対象 DB タイプ | DetailResponse | EntryResponse | EntryJsonLdResponse |
|-------------|--------------|----------------|---------------|---------------------|
| `BioProject` | bioproject | `BioProjectDetailResponse` | `BioProjectEntryResponse` | `BioProjectEntryJsonLdResponse` |
| `BioSample` | biosample | `BioSampleDetailResponse` | `BioSampleEntryResponse` | `BioSampleEntryJsonLdResponse` |
| `SRA` | sra-* (6 タイプ) | `SraDetailResponse` | `SraEntryResponse` | `SraEntryJsonLdResponse` |
| `JGA` | jga-* (4 タイプ) | `JgaDetailResponse` | `JgaEntryResponse` | `JgaEntryJsonLdResponse` |

- `*DetailResponse`: フロントエンド向け。`dbXrefs` を切り詰め、`dbXrefsCount` を付与
- `*EntryResponse`: converter 型の別名。dbXrefs は DuckDB から注入
- `*EntryJsonLdResponse`: ES ドキュメント + `@context`, `@id`
- Bulk は `BulkResponse` (`entries` + `notFound`) を返す。NDJSON 形式ではエントリーのみ出力 (`notFound` は含まない)

### 型カタログ

#### Path Params (3 型)

| 型名 | フィールド |
|------|----------|
| `TypeParams` | `type` (DbType) |
| `TypeIdParams` | `type` (DbType), `id` |
| `DbLinksParams` | `type` (AccessionType), `id` |

#### Mixin Query (3 型)

| 型名 | フィールド |
|------|----------|
| `PaginationQuery` | `page`, `perPage` |
| `SearchFilterQuery` | `keywords`, `keywordFields`, `keywordOperator`, `organism`, `datePublishedFrom`, `datePublishedTo`, `dateModifiedFrom`, `dateModifiedTo` |
| `ResponseControlQuery` | `sort`, `fields`, `includeProperties`, `includeFacets` |

#### Endpoint Query (9 型)

| 型名 | 合成元 | 追加フィールド |
|------|-------|--------------|
| `EntriesQuery` | Pagination + SearchFilter + ResponseControl | `types`, `dbXrefsLimit` (0-1000, デフォルト: 100, type ごとに適用) |
| `EntriesTypeQuery` | Pagination + SearchFilter + ResponseControl | `dbXrefsLimit` (0-1000, デフォルト: 100, type ごとに適用) |
| `EntriesBioProjectQuery` | EntriesTypeQuery を拡張 | `organization`, `publication`, `grant`, `umbrella` |
| `EntryDetailQuery` | — | `dbXrefsLimit` (0-1000, デフォルト: 100, type ごとに適用) |
| `BulkQuery` | — | `format` (`json` / `ndjson`, デフォルト: `json`) |
| `FacetsQuery` | SearchFilter | `types` |
| `FacetsTypeQuery` | SearchFilter | — |
| `FacetsBioProjectQuery` | FacetsTypeQuery を拡張 | `organization`, `publication`, `grant`, `umbrella` |
| `DbLinksQuery` | — | `target` (カンマ区切り AccessionType、省略可) |

#### Request (2 型)

| 型名 | フィールド |
|------|----------|
| `BulkRequest` | `ids` (最大 1000 件) |
| `DbLinksCountsRequest` | `items` (1-100 件、各要素: `type` (AccessionType), `id`) |

#### Response (21 型)

| 型名 | 説明 |
|------|------|
| `EntryListResponse` | 検索結果リスト (pagination + items: `list[EntryListItem]` + facets: `Optional[Facets]`)。`includeFacets=true` のとき `facets` にファセット集計が含まれる |
| `BioProjectDetailResponse` | BioProject 詳細 (dbXrefs 切り詰め + dbXrefsCount) |
| `BioSampleDetailResponse` | BioSample 詳細 (dbXrefs 切り詰め + dbXrefsCount) |
| `SraDetailResponse` | SRA 詳細 (dbXrefs 切り詰め + dbXrefsCount) |
| `JgaDetailResponse` | JGA 詳細 (dbXrefs 切り詰め + dbXrefsCount) |
| `BioProjectEntryResponse` | BioProject ES ドキュメント (= `BioProject` の別名) |
| `BioSampleEntryResponse` | BioSample ES ドキュメント (= `BioSample` の別名) |
| `SraEntryResponse` | SRA ES ドキュメント (= `SRA` の別名) |
| `JgaEntryResponse` | JGA ES ドキュメント (= `JGA` の別名) |
| `BioProjectEntryJsonLdResponse` | BioProject JSON-LD |
| `BioSampleEntryJsonLdResponse` | BioSample JSON-LD |
| `SraEntryJsonLdResponse` | SRA JSON-LD |
| `JgaEntryJsonLdResponse` | JGA JSON-LD |
| `DbXrefsFullResponse` | dbXrefs 全件取得 (dbXrefs: `list[Xref]`) |
| `BulkResponse` | 一括取得レスポンス (entries: `list[*EntryResponse]` + notFound: `list[string]`) |
| `FacetsResponse` | ファセット集計 |
| `ServiceInfoResponse` | サービス情報 (name, version, description, elasticsearch) |
| `DbLinksTypesResponse` | アクセッションタイプ一覧 (types: `list[AccessionType]`) |
| `DbLinksResponse` | 関連 ID (identifier, type, dbXrefs: `list[Xref]`) |
| `DbLinksCountsResponse` | 一括カウント結果 (items: `list[DbLinksCountsResponseItem]`) |
| `DbLinksCountsResponseItem` | カウント結果の各要素 (identifier, type, counts: `Dict[str, int]`) |

#### ドメインモデル (5 型)

| 型名 | 説明 |
|------|------|
| `Pagination` | ページネーション情報 (page, perPage, total) |
| `EntryListItem` | 検索結果リスト内の各エントリー (サマリー) |
| `Facets` | ファセット集計データ (フィールド名 → 値別カウント) |
| `DbXrefsCount` | dbXrefs のタイプ別カウント (`Dict[str, int]`)。キーはデータベースタイプ名、値は件数 (例: `{"biosample": 200, "sra-study": 50}`) |
| `ProblemDetails` | RFC 7807 エラーレスポンス |

#### ddbj-search-converter 由来 (7 型)

エントリーのスキーマは [ddbj-search-converter](https://github.com/ddbj/ddbj-search-converter) で定義されている。API はこれらの型を再定義せず、直接使用する。

| 型名 | 対象タイプ | 説明 |
|------|----------|------|
| `BioProject` | bioproject | BioProject エントリー |
| `BioSample` | biosample | BioSample エントリー |
| `SRA` | sra-* (6 タイプ共通) | SRA エントリー |
| `JGA` | jga-* (4 タイプ共通) | JGA エントリー |
| `Organism` | 全タイプ共通 | 生物種情報 (identifier, name) |
| `Xref` | 全タイプ共通 | 外部データベース参照 (identifier, type, url)。`dbXrefs` と `sameAs` で使用 |
| `AccessionType` | dblink | アクセッションタイプ (Literal → StrEnum 自動生成, 21 値)。DbType とは別の型 |
