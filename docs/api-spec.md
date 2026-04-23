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

### Entry Detail API (詳細取得系: 5 エンドポイント)

| Method | Path | 説明 |
|--------|------|------|
| GET | `/entries/{type}/{id}` | エントリー詳細取得 (JSON) |
| GET | `/entries/{type}/{id}.json` | エントリー詳細取得 (JSON) |
| GET | `/entries/{type}/{id}.jsonld` | エントリー詳細取得 (JSON-LD) |
| GET | `/entries/{type}/{id}/dbxrefs.json` | dbXrefs 全件取得 |
| GET | `/entries/bioproject/{accession}/umbrella-tree` | umbrella tree (flat graph、bioproject 専用) |

#### sameAs による ID 解決

Entry Detail API の 4 エンドポイントは、`{id}` パスパラメータに対して以下の順序でエントリーを解決する:

1. **identifier 一致**: ES ドキュメント ID (`_id` = `identifier`) で直接取得を試みる
2. **sameAs フォールバック**: 1 で見つからない場合、`sameAs` フィールドを nested query で検索する。検索条件は `sameAs.identifier == {id} AND sameAs.type == {type}` (同一タイプのみ)
3. **404**: いずれでも見つからない場合は `404 Not Found` を返す

sameAs フォールバックでヒットした場合、レスポンスは identifier で直接取得した場合と同一形式 (リダイレクトはしない)。

**sameAs クエリのエラーハンドリング**: sameAs nested query が ES エラー (400 等) を返した場合、「見つからない」として扱い、ステップ 3 の 404 へフォールスルーする。これにより、`sameAs` フィールドのマッピングが存在しないインデックスに対するリクエストでも 500 ではなく 404 を返す。

**対象データ**: JGA エントリー (jga-study, jga-dataset, jga-dac) は XML の `IDENTIFIERS.SECONDARY_ID` を `sameAs` に格納しており、Secondary ID から Primary エントリーを取得できる。ロジック自体は全タイプ共通で、`sameAs` が空のタイプではフォールバックが発火しないだけである。

**Elasticsearch 要件**: `sameAs` フィールドは nested タイプとしてインデックスされている必要がある (ddbj-search-converter 側のマッピング定義)。

**alias ドキュメント (converter 連携)**: ddbj-search-converter は、エントリーの `sameAs` に含まれる Secondary ID を `_id` とする alias ドキュメントを ES に投入する (alias ドキュメントの `_source` は Primary ドキュメントと同一で、`identifier` フィールドは Primary ID のまま)。これにより、Secondary ID による直接取得 (ステップ 1) が alias ドキュメントにヒットし、sameAs nested query (ステップ 2) へのフォールバックが不要になる。API 側では、直接取得成功時に `_source.identifier` を確認し、リクエストの `{id}` と異なる場合は `identifier` を Primary ID として DuckDB 検索・JSON-LD `@id` に使用する。

#### Umbrella Tree API (BioProject 専用)

BioProject の umbrella tree を flat graph 形式 (`query` + `roots` + `edges`) で取得する。描画ライブラリに依存せず、DAG (multi-parent) も正しく表現できる。ノード付随情報 (title, objectType, status 等) は含めず、必要ならフロント側が `/entries/bioproject/{accession}` を別途呼び出す想定。

**Path パラメータ**: `accession` は BioProject の Primary ID (例: `PRJDB1234`) または sameAs Secondary ID。sameAs ID 解決は上記「sameAs による ID 解決」と共通。

**Query パラメータ**: なし。

**レスポンス**: `200 OK`, `application/json`, `UmbrellaTreeResponse`。

**振る舞い**:

1. 入力 `accession` に対応する BioProject を解決する (sameAs フォールバック含む)。見つからなければ 404
2. 解決したエントリーの `parentBioProjects == [] && childBioProjects == []` であれば orphan と判定し、`{"query": <primary>, "roots": [<primary>], "edges": []}` を返す (`objectType` は判定に使わない)
3. orphan でなければ、`parentBioProjects` を root (`parentBioProjects == []` の BioProject) まで遡って `roots` を確定し、`roots` から `childBioProjects` を辿って到達可能な全ノード・エッジを集める。DAG で同一の `(parent, child)` が複数経路から現れても重複エッジは排除する
4. トラバース深度が `MAX_DEPTH = 10` を超えたら 500 を返す (converter 側で最大深度 5 が保証されているため、10 を超える場合はデータ異常の兆候)

**レスポンス例**:

**orphan (親も子もなし)**:

```json
{
  "query": "PRJDB9999",
  "roots": ["PRJDB9999"],
  "edges": []
}
```

**depth 1 (umbrella → leaf のみ、全体の約 99.6%)**:

```json
{
  "query": "PRJDB1234",
  "roots": ["PRJDB0001"],
  "edges": [
    { "parent": "PRJDB0001", "child": "PRJDB1234" },
    { "parent": "PRJDB0001", "child": "PRJDB1235" }
  ]
}
```

**multi-parent DAG (子が複数親を持つ、約 6,700 件)**:

```json
{
  "query": "PRJDB0555",
  "roots": ["PRJDB0001", "PRJDB0002"],
  "edges": [
    { "parent": "PRJDB0001", "child": "PRJDB0555" },
    { "parent": "PRJDB0002", "child": "PRJDB0555" }
  ]
}
```

**エラー**:

| ステータス | 発生条件 |
|-----------|---------|
| 404 | `accession` (sameAs フォールバック含む) が bioproject インデックスに存在しない |
| 422 | path validation エラー (空文字など) |
| 500 | トラバース深度が `MAX_DEPTH = 10` を超過、または ES 通信エラー |

**補足**:

- `query` は常に Primary ID を返す (sameAs フォールバック時はリクエスト値と異なることがある)
- `roots` および `edges` は決定論的にソートされた順序で返す (`edges` は `(parent, child)` の辞書順)
- 中間ノードが bioproject インデックスから取得できなかった場合 (参照切れ) は、該当 edge を結果から除外する (converter 側の整合性エラーとして扱い、API 全体は 500 にしない)

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

### DB Portal API (1 エンドポイント)

| Method | Path | 説明 |
|--------|------|------|
| GET | `/db-portal/search` | db-portal 向け統合検索 (横断 count / DB 指定 hits、シンプル / Advanced) |

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

**type URI**: 一般エラーは `about:blank`。エンドポイント固有のエラー種別には `https://ddbj.nig.ac.jp/problems/<slug>` 形式の URI を使用する (RFC 7807 §3.1 / 後継 RFC 9457 準拠。URI は dereferenceable である必要はなく、識別子として機能する)。DB Portal API のエラー type URI 一覧は `## DB Portal API` セクションを参照。

**ステータスコード一覧**:

| ステータス | 意味 | 発生条件 |
|-----------|------|---------|
| 400 | Bad Request | Deep paging 制限超過 (`page * perPage > 10000`)、`cursor` と検索条件/`page` の同時指定、不正な cursor トークン、cursor 期限切れ (PIT 失効)、DB Portal API の `q`/`adv` 同時指定 |
| 404 | Not Found | エントリーが存在しない、不正な `{type}` |
| 422 | Unprocessable Entity | パラメータバリデーションエラー (`perPage` の範囲外、不正な日付形式 (`YYYY-MM-DD` 以外) や不正な日付 (`2024-02-30` 等)、不正な `types` 値、不正な `umbrella` 値 (`TRUE`/`FALSE` 以外)、不正な `sort` フィールド、不正な `keywordFields` 値など) |
| 500 | Internal Server Error | ES 接続エラー、DuckDB ファイルが見つからない (Entries 検索/詳細/Bulk/DBLinks API)、その他サーバー内部エラー |
| 501 | Not Implemented | DB Portal API の未実装機能 (Advanced Search) |
| 502 | Bad Gateway | DB Portal API の横断 count-only で全 DB への問い合わせが失敗 |

400 と 422 の使い分け: リクエストのパラメータ型・形式・制約のバリデーションは 422、アプリケーションのビジネスルール違反 (deep paging 制限、`q`/`adv` 排他など) は 400 を返す。

### ページネーション

リスト系エンドポイントは 2 種類のページネーションをサポートする。

#### オフセットベースページネーション (デフォルト)

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
  "total": 150000,
  "nextCursor": "eyJwaXRfaWQiOm51bGwsInNlYXJjaF9hZnRlci...",
  "hasNext": true
}
```

- `total` は常に正確な総件数を返す (`track_total_hits=true`)
- `perPage` が範囲外 (1-100) の場合は `422 Unprocessable Entity`
- `nextCursor`: 次ページ取得用のカーソルトークン。最終ページでは `null`
- `hasNext`: 次のページが存在するかどうか

**Deep paging 制限**:

`page * perPage` が 10000 を超えるリクエストは `400 Bad Request` を返す。これは Elasticsearch の `index.max_result_window` 制限に基づく。10000 件を超える結果を取得する必要がある場合は、カーソルベースページネーションを使用する。

#### カーソルベースページネーション

10,000 件を超える検索結果を順次取得するためのページネーション方式。Elasticsearch の `search_after` + PIT (Point in Time) を内部で使用する。

**使い方**:

1. 通常のオフセットベース検索を実行する
2. レスポンスの `nextCursor` を取得する
3. `cursor` パラメータに `nextCursor` の値を指定してリクエストする
4. 以降、`nextCursor` が `null` になるまで繰り返す

**リクエスト**:

| パラメータ | 型 | デフォルト | 説明 |
|-----------|-----|-----------|------|
| `cursor` | string | — | カーソルトークン (opaque な文字列) |
| `perPage` | integer | `10` | 1 ページあたりの件数 (1-100)。cursor と併用可能 |

**排他ルール**: `cursor` を指定した場合、以下のパラメータは指定不可 (`400 Bad Request`):

- `page` (デフォルト値 `1` 以外)
- `keywords`, `keywordFields`, `keywordOperator` (デフォルト値 `AND` 以外)
- `organism`, `datePublishedFrom`, `datePublishedTo`, `dateModifiedFrom`, `dateModifiedTo`
- `sort`
- `types`, `organization`, `publication`, `grant`, `umbrella`
- `includeFacets`, `includeProperties` (デフォルト値以外), `fields`

`perPage`、`dbXrefsLimit`、`includeDbXrefs` は `cursor` と併用可能。

**レスポンス** (`Pagination`):

```json
{
  "page": null,
  "perPage": 10,
  "total": 150000,
  "nextCursor": "eyJwaXRfaWQiOiJhYmMxMjMiLCJzZWFyY2hfYWZ0...",
  "hasNext": true
}
```

- `page` はカーソルモードでは `null` (ページ番号の概念がないため)
- `nextCursor`: 次ページ用トークン。最終ページでは `null`
- `hasNext`: 次のページが存在するかどうか

**PIT (Point in Time) のライフサイクル**:

- オフセットベースの検索では PIT を使用しない。レスポンスの `nextCursor` には検索条件とソート情報のみが含まれる
- クライアントが `cursor` を初めて使用した時点で PIT がオープンされ、以降のカーソルリクエストで再利用される
- PIT の有効期限は 5 分。5 分以上間隔を空けると `400 Bad Request` (cursor 期限切れ) になる。その場合は検索をやり直す
- PIT は有効期限で自動的にクリーンアップされるため、明示的なクローズは不要
- cursor トークンはサーバーで HMAC 署名されており、改ざんされた場合は `400 Bad Request` を返す。署名鍵はプロセス再起動時に再生成されるため、再起動後は既存の cursor トークンが無効になる

**エラー**:

| 条件 | ステータス | 説明 |
|------|-----------|------|
| `cursor` と検索条件/`page` の同時指定 | 400 | 排他ルール違反 |
| 不正な cursor トークン | 400 | デコード不可、必須フィールド欠落、署名不一致 |
| cursor 期限切れ | 400 | PIT が失効。検索をやり直す必要がある |

### 日付形式

ISO 8601 形式 (`YYYY-MM-DD`) を使用する。範囲指定は `From` / `To` の 2 パラメータで行う。

### 検索パラメータ

`GET /entries/`, `GET /entries/{type}/`, `GET /facets`, `GET /facets/{type}` で共通の検索パラメータ。2 つの mixin 型に分割して定義する。

**検索フィルタ** (`SearchFilterQuery`):

| パラメータ | 型 | デフォルト | 説明 |
|----------|-----|-----------|------|
| `keywords` | string | — | 検索キーワード (カンマ区切りで複数指定可)。ダブルクオートで囲むとフレーズマッチ (例: `"RNA-Seq"`)。記号 (`-` `/` `.` `+` `:`) を含むキーワードは自動フレーズマッチ (例: `HIF-1`) |
| `keywordFields` | string | — | 検索対象フィールドを限定 (カンマ区切り)。指定可能な値: `identifier`, `title`, `name`, `description`。不正な値は 422 |
| `keywordOperator` | string | `AND` | キーワードの結合条件。`AND` (すべて一致) / `OR` (いずれか一致) |
| `organism` | string | — | NCBI Taxonomy ID (例: `9606`) |
| `datePublishedFrom` / `datePublishedTo` | string | — | 公開日の範囲 (ISO 8601: `YYYY-MM-DD`) |
| `dateModifiedFrom` / `dateModifiedTo` | string | — | 更新日の範囲 (ISO 8601: `YYYY-MM-DD`) |

**フレーズマッチ**: 以下のいずれかに該当するキーワードは ES `multi_match` の `type: "phrase"` でフレーズマッチ検索する。トークン順序を保持した完全一致となるため、analyzer による token 分割で精度が下がるのを防ぐ:

- **明示フレーズ**: ダブルクオートで囲む (例: `"whole genome"`)
- **自動フレーズ化**: 記号 `-` `/` `.` `+` `:` のいずれかを含むキーワード (例: `HIF-1`, `COVID-19`, `SARS-CoV-2`, `GSE12345/analysis`)

クオートなし・記号なしのキーワードは従来通りのトークンベースマッチ (analyzer による token 分割あり)。混在可能: `keywords=HIF-1,"whole genome",cancer` → 前 2 つは phrase、最後は通常マッチ。自動フレーズ化は `HIF-1` のように ES standard analyzer が `-` で token を分割して無関係な `HIF*` 系エントリーを拾ってしまう挙動を回避する。

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
| `status` | ステータス別カウント。値: `public`, `private`, `suppressed`, `withdrawn` |
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

**`includeDbXrefs` パラメータ**:

`GET /entries/`, `GET /entries/{type}/`, `GET /entries/{type}/{id}`, `POST /entries/{type}/bulk` は `includeDbXrefs` boolean パラメータ (デフォルト: `true`) をサポートする。`false` の場合、DuckDB を一切参照せず、レスポンスから `dbXrefs` と `dbXrefsCount` を省略する。`dbXrefsLimit=0` との違いは以下の通り:

| パラメータ | `dbXrefs` | `dbXrefsCount` | DuckDB アクセス |
|-----------|-----------|---------------|---------------|
| `dbXrefsLimit=0` | 空配列 `[]` | あり (集計結果) | あり (カウントのみ) |
| `includeDbXrefs=false` | 省略 | 省略 | なし |

`includeDbXrefs=false` と `dbXrefsLimit` が同時に指定された場合、`includeDbXrefs=false` が優先される。

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

`bioproject`, `biosample`, `gea`, `geo`, `humandbs`, `insdc`, `insdc-assembly`, `insdc-master`, `jga-dac`, `jga-dataset`, `jga-policy`, `jga-study`, `metabobank`, `pubmed`, `sra-analysis`, `sra-experiment`, `sra-run`, `sra-sample`, `sra-study`, `sra-submission`, `taxonomy`

DbType (12 種) とは別の型。dblink 固有のタイプを含む。

#### `GET /dblink/`

利用可能なアクセッションタイプの一覧を返す (静的、DB 不要)。

Trailing slash 両対応 (`/dblink` と `/dblink/` は同じ結果)。

**レスポンス** (`DbLinksTypesResponse`):

```json
{
  "types": [
    "bioproject", "biosample", "gea", "geo", "humandbs",
    "insdc", "insdc-assembly", "insdc-master", "jga-dac", "jga-dataset",
    "jga-policy", "jga-study", "metabobank", "pubmed",
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
  "type": "humandbs",
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

### DB Portal API

db-portal フロントエンド専用の統合検索エンドポイント。`/entries/*` とは独立したレスポンススキーマ (`hits` envelope) を持ち、UI 向けにシンプル検索と Advanced Search (将来) を統合して提供する。

**実装ロードマップ**:

- AP1 (完了): 4 パターン分岐の骨組み、ES 対応 6 DB のシンプル検索、cursor/hits envelope
- AP4 (完了): Solr proxy で `trad` (ARSA 8-shard fan-out) / `taxonomy` (TXSearch) を有効化。cursor は未対応 (Solr 4.4.0 に PIT 相当なし) のため `db=trad`/`db=taxonomy` + `cursor` は 400 `cursor-not-supported`
- AP3 (予定): Advanced Search DSL パーサ (Lark LALR(1)) で `adv` を有効化
- AP5 (予定): 横断 count-only の並列 fan-out + 個別/全体 timeout

#### `GET /db-portal/search`

4 パターンに分岐する:

| # | クエリ | 処理 |
|---|-------|-----|
| 1 | `q` のみ | 横断シンプル検索 (count-only、8 DB に順次発行。`trad` は ARSA 8-shard fan-out、`taxonomy` は TXSearch、残り 6 DB は ES) |
| 2 | `q` + `db` (ES 対応 6 DB) | DB 指定シンプル検索 (`hits` envelope + cursor/offset pagination) |
| 3 | `q` + `db=trad` / `db=taxonomy` | DB 指定シンプル検索 (Solr proxy、offset-only、9 共通フィールド + DB 別 extra で返却) |
| 4 | `adv` (`db` 指定有無問わず) | 501 (`advanced-search-not-implemented`) |
| 5 | `cursor` + `db=trad` / `db=taxonomy` | 400 (`cursor-not-supported` — Solr proxy は offset-only) |

`q` と `adv` の同時指定は 400 (`invalid-query-combination`)。

Trailing slash なし (`/db-portal/search`) が canonical。

**クエリパラメータ** (`DbPortalQuery`):

| パラメータ | 型 | デフォルト | 説明 |
|----------|-----|-----------|------|
| `q` | string | — | シンプル検索キーワード。既存 `/entries/` と同じ auto-phrase (記号 `-` `/` `.` `+` `:` 含むと phrase match) が適用される |
| `adv` | string | — | Advanced Search DSL (AP3 で実装)。AP1 時点で指定すると 501 |
| `db` | enum | — | 検索対象 DB。値: `trad`, `sra`, `bioproject`, `biosample`, `jga`, `gea`, `metabobank`, `taxonomy`。省略時は横断 count-only |
| `page` | integer | `1` | ページ番号 (1 始まり) |
| `perPage` | integer | `20` | 1 ページあたりの件数。許容値: `20`, `50`, `100` のみ (他は 422) |
| `cursor` | string | — | カーソルトークン (HMAC 署名付き、PIT 5 分) |
| `sort` | string | — (relevance) | ソート順。許容値: `datePublished:desc`, `datePublished:asc`, または省略 (relevance = score desc + identifier tiebreaker)。他値は 422 |

`q` と `adv` は排他 (同時指定で 400 `invalid-query-combination`)。

**横断レスポンス** (`DbPortalCrossSearchResponse`、`db` 省略時):

```json
{
  "databases": [
    { "db": "trad",       "count": 15050, "error": null },
    { "db": "sra",        "count": 1234,  "error": null },
    { "db": "bioproject", "count": 567,   "error": null },
    { "db": "biosample",  "count": 890,   "error": null },
    { "db": "jga",        "count": 12,    "error": null },
    { "db": "gea",        "count": 34,    "error": null },
    { "db": "metabobank", "count": 5,     "error": null },
    { "db": "taxonomy",   "count": 12,    "error": null }
  ]
}
```

- `databases` は常に 8 件、順序は固定 (`trad → sra → bioproject → biosample → jga → gea → metabobank → taxonomy`)
- 各要素は `DbPortalCount`: `db` (enum 8 値)、`count` (int | null)、`error` (enum | null)
- `count` は `track_total_hits=true` (ES) または Solr の `numFound` (Solr-backed DB) に基づく正確値
- `error` 値: `timeout`, `upstream_5xx`, `connection_refused`, `unknown`
- 1 つ以上の DB で成功: HTTP 200 (部分失敗許容)
- 全 DB 失敗: HTTP 502 (`about:blank`)

**DB 指定レスポンス** (`DbPortalHitsResponse`、`db` が ES 対応 6 DB のいずれか):

```json
{
  "total": 1234,
  "hits": [
    {
      "identifier": "PRJDB1234",
      "type": "bioproject",
      "title": "Human Cancer Study",
      "description": "...",
      "organism": {"identifier": "9606", "name": "Homo sapiens"},
      "datePublished": "2023-05-01",
      "url": "https://ddbj.nig.ac.jp/search/entry/bioproject/PRJDB1234",
      "sameAs": [],
      "dbXrefs": null
    }
  ],
  "hardLimitReached": false,
  "page": 1,
  "perPage": 20,
  "nextCursor": "eyJwaXRfaWQi...",
  "hasNext": true
}
```

- `total`: マッチ総件数 (`track_total_hits=true`)
- `hardLimitReached`: `total >= 10000` のとき `true` (Solr 10,000 件上限と統一)
- `hits`: `DbPortalHit` の配列。共通フィールド (`identifier`, `type`, `title`, `description`, `organism`, `datePublished`, `url`, `sameAs`, `dbXrefs`) を返す。DB 別の追加フィールドは `extra="allow"` で透過
- `page` / `perPage`: offset mode で指定値。cursor mode では `page` が `null`
- `nextCursor` / `hasNext`: 既存 cursor ページネーションと同じ方式 (HMAC 署名、プロセス再起動で失効)

**AP1 注意**: `dbXrefs` は AP1 時点で DuckDB 注入しない (ES `_source.dbXrefs` があればそのまま返す、無ければ `null`)。UI 向け dbXrefs 統合は将来の phase で検討する。

**ページネーション**: 共通仕様「ページネーション」の cursor 排他ルールを db-portal 独自に適用:

- `cursor` 指定時、以下は指定不可 (400): `q`, `adv`, `sort`, `page` (デフォルト `1` 以外)
- `db` と `perPage` は `cursor` と併用可能 (cursor トークンには対象 index 情報が含まれないため、`db` は再指定必須)
- `cursor` を指定して `db` を省略すると 400 (cross モードはカウントのみのため cursor 概念がない)
- `page * perPage > 10000` は 400 (既存 `_DEEP_PAGING_LIMIT` と同じ)

**エラー** (type URI + HTTP status):

| type URI (prefix `https://ddbj.nig.ac.jp/problems/` + slug) | HTTP | 条件 | 廃止予定 |
|------|------|------|---------|
| `invalid-query-combination` | 400 | `q` と `adv` 同時指定 | — |
| `advanced-search-not-implemented` | 501 | `adv` 指定 | AP3 |
| `cursor-not-supported` | 400 | `db=trad` / `db=taxonomy` と `cursor` 同時指定 (Solr proxy は offset-only) | — |
| `about:blank` | 400 | Deep paging 超過、cursor 排他違反、不正な cursor、cursor 期限切れ | — |
| `about:blank` | 422 | `db` / `sort` / `perPage` 等の enum・Literal 違反、型不一致 | — |
| `about:blank` | 502 | 横断 count-only で全 DB 失敗、Solr DB 指定検索で upstream エラー | — |

URI prefix `https://ddbj.nig.ac.jp/problems/` は dereferenceable である必要はなく、識別子として機能する (RFC 7807 §3.1)。AP3 完了時に `advanced-search-not-implemented` は廃止される。AP3 で DSL パーサが加わる時点で `unexpected-token`, `unknown-field`, `field-not-available-in-cross-db`, `invalid-date-format`, `invalid-operator-for-field`, `nest-depth-exceeded`, `missing-value` の slug が追加される予定。

**CORS / rate limit**: 既存 API と同じ (`CORS: *`、rate limit は nginx レイヤ)。

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
| `GET /entries/bioproject/{accession}/umbrella-tree` | `TypeIdParams` (type=bioproject 固定、id=accession) | — | — | `UmbrellaTreeResponse` |
| `POST /entries/{type}/bulk` | `TypeParams` | `BulkQuery` | `BulkRequest` | `BulkResponse` |
| `GET /facets` | — | `FacetsQuery` | — | `FacetsResponse` |
| `GET /facets/{type}` | `TypeParams` | `FacetsTypeQuery` | — | `FacetsResponse` |
| `GET /facets/bioproject` | `TypeParams` | `FacetsBioProjectQuery` | — | `FacetsResponse` |
| `GET /dblink/` | — | — | — | `DbLinksTypesResponse` |
| `GET /dblink/{type}/{id}` | `DbLinksParams` | `DbLinksQuery` | — | `DbLinksResponse` |
| `POST /dblink/counts` | — | — | `DbLinksCountsRequest` | `DbLinksCountsResponse` |
| `GET /db-portal/search` | — | `DbPortalQuery` | — | `DbPortalCrossSearchResponse` \| `DbPortalHitsResponse` |
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

#### Endpoint Query (10 型)

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
| `DbPortalQuery` | — | `q`, `adv`, `db` (DbPortalDb enum), `page`, `perPage` (Literal 20/50/100、デフォルト: 20), `cursor`, `sort` (allowlist: null / `datePublished:desc` / `datePublished:asc`) |

#### Request (2 型)

| 型名 | フィールド |
|------|----------|
| `BulkRequest` | `ids` (最大 1000 件) |
| `DbLinksCountsRequest` | `items` (1-100 件、各要素: `type` (AccessionType), `id`) |

#### Response (24 型)

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
| `UmbrellaTreeResponse` | BioProject umbrella tree (query: `str`, roots: `list[str]`, edges: `list[UmbrellaTreeEdge]`) |
| `BulkResponse` | 一括取得レスポンス (entries: `list[*EntryResponse]` + notFound: `list[string]`) |
| `FacetsResponse` | ファセット集計 |
| `ServiceInfoResponse` | サービス情報 (name, version, description, elasticsearch) |
| `DbLinksTypesResponse` | アクセッションタイプ一覧 (types: `list[AccessionType]`) |
| `DbLinksResponse` | 関連 ID (identifier, type, dbXrefs: `list[Xref]`) |
| `DbLinksCountsResponse` | 一括カウント結果 (items: `list[DbLinksCountsResponseItem]`) |
| `DbLinksCountsResponseItem` | カウント結果の各要素 (identifier, type, counts: `Dict[str, int]`) |
| `DbPortalCrossSearchResponse` | db-portal 横断 count-only (`databases: list[DbPortalCount]`)。`databases` は常に 8 件、固定順序 |
| `DbPortalHitsResponse` | db-portal DB 指定検索結果 (total, hits: `list[DbPortalHit]`, hardLimitReached, page, perPage, nextCursor, hasNext) |

#### ドメインモデル (8 型)

| 型名 | 説明 |
|------|------|
| `Pagination` | ページネーション情報 (page, perPage, total) |
| `EntryListItem` | 検索結果リスト内の各エントリー (サマリー) |
| `Facets` | ファセット集計データ (フィールド名 → 値別カウント) |
| `DbXrefsCount` | dbXrefs のタイプ別カウント (`Dict[str, int]`)。キーはデータベースタイプ名、値は件数 (例: `{"biosample": 200, "sra-study": 50}`) |
| `ProblemDetails` | RFC 7807 エラーレスポンス |
| `UmbrellaTreeEdge` | umbrella tree の有向辺 (parent: `str`, child: `str`、BioProject accession) |
| `DbPortalCount` | db-portal 横断レスポンスの要素 (db: `DbPortalDb`, count: `int \| null`, error: `DbPortalCountError \| null`) |
| `DbPortalHit` | db-portal 検索結果のエントリー (identifier, type, title, description, organism, datePublished, url, sameAs, dbXrefs)。`extra="allow"` で DB 別追加フィールドを透過 |

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
