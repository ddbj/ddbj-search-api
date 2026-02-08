# API 仕様書

## 概要

DDBJ-Search API は、BioProject / BioSample / SRA / JGA データを検索・取得するための RESTful API サーバー。
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
- **`.json` 拡張子の規約**: `/{id}` と `/{id}.json` は異なるレスポンスを返す。`/{id}` はフロントエンド向け (dbXrefs 切り詰め + dbXrefsCount 付与)、`/{id}.json` はデータアクセス向け (ES ドキュメントそのまま)。つまり `.json` 拡張子は「**加工なしの生データ取得**」を意味する
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

### Bulk API (一括取得系: 1 エンドポイント)

| Method | Path | 説明 |
|--------|------|------|
| POST | `/entries/{type}/bulk` | 一括取得 |

### Facets API (ファセット集計: 2 エンドポイント)

| Method | Path | 説明 |
|--------|------|------|
| GET | `/facets` | 横断ファセット集計 |
| GET | `/facets/{type}` | タイプ別ファセット集計 |

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

コレクション (リスト) 系エンドポイントの canonical パスは trailing slash 付き (例: `/entries/`)。
trailing slash なし (`/entries`) でも同じレスポンスを返す (リダイレクトしない)。
個別リソース (例: `/entries/{type}/{id}`) には trailing slash を付けない。

### リクエスト追跡 (X-Request-ID)

全レスポンスに `X-Request-ID` ヘッダーを付与する。クライアントがリクエスト時に `X-Request-ID` を指定した場合はその値を使用し、指定がない場合はサーバーで UUID を生成する。
エラーレスポンスにも `requestId` フィールドとして含まれるため、ログとの突き合わせに使用できる。

### エラーレスポンス (RFC 7807)

エラー時は [RFC 7807 Problem Details](https://tools.ietf.org/html/rfc7807) 形式の JSON を返す。

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
| 422 | Unprocessable Entity | パラメータバリデーションエラー (`perPage` の範囲外、不正な日付形式 (`YYYY-MM-DD` 以外)、不正な `umbrella` 値 (`TRUE`/`FALSE` 以外)、不正な `sort` フィールド、不正な `keywordFields` 値など) |
| 500 | Internal Server Error | ES 接続エラー、その他サーバー内部エラー |

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
| `fields` | string | — | レスポンスに含めるフィールドを限定 (カンマ区切り)。ES ドキュメントのトップレベルフィールド名を指定 (例: `identifier,organism,datePublished`) |
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

エントリーの `dbXrefs` (データベース間参照) はエントリーによっては数百万件になるため、エンドポイントによってレスポンスの扱いが異なる。

**エンドポイント別の dbXrefs 扱い**:

| エンドポイント | dbXrefs の扱い | dbXrefsCount |
|--------------|---------------|--------------|
| `GET /entries/`, `GET /entries/{type}/` | `dbXrefsLimit` で切り詰め | あり |
| `GET /entries/{type}/{id}` | `dbXrefsLimit` で切り詰め | あり |
| `GET /entries/{type}/{id}.json` | ES ドキュメントそのまま (全件) | なし |
| `GET /entries/{type}/{id}.jsonld` | ES ドキュメントそのまま (全件) | なし |
| `POST /entries/{type}/bulk` | ES ドキュメントそのまま (全件) | なし |

フロントエンド向けエンドポイント (リスト API・`/{id}`) では `dbXrefsLimit` クエリパラメータ (デフォルト: 100, 範囲: 0-1000) で `dbXrefs` を切り詰め、`dbXrefsCount` (タイプ別の参照総数) をレスポンスに付与する。`dbXrefsLimit=0` の場合は `dbXrefs` を空配列で返すが、`dbXrefsCount` は常に返す。クライアントは `dbXrefs` の件数と `dbXrefsCount` の合計を比較し、差分がある場合は専用エンドポイントへのリンクを表示する。

データアクセス向けエンドポイント (`.json`, `.jsonld`, `/bulk`) では ES ドキュメントをそのまま返し、切り詰めや `dbXrefsCount` の付与は行わない。

**専用エンドポイント**:

- `GET /entries/{type}/{id}/dbxrefs.json`: 全件を一括取得 (JSON 配列)

### Bulk API

`POST /entries/{type}/bulk` で複数エントリーを一括取得する。パラメータの詳細は型カタログの `BulkQuery` / `BulkRequest` を参照。

指定された ID のうち見つからなかったものは、レスポンスの `notFound` フィールドで返す。

- `format=json`: `{ "entries": [...], "notFound": ["ID_1", "ID_2"] }`
- `format=ndjson`: エントリーを 1 行 1 件で出力 (`notFound` は含まない)

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
| `GET /service-info` | — | — | — | `ServiceInfoResponse` |

**レスポンスのタイプ別展開**:

| converter 型 | 対象 DB タイプ | DetailResponse | EntryResponse | EntryJsonLdResponse |
|-------------|--------------|----------------|---------------|---------------------|
| `BioProject` | bioproject | `BioProjectDetailResponse` | `BioProjectEntryResponse` | `BioProjectEntryJsonLdResponse` |
| `BioSample` | biosample | `BioSampleDetailResponse` | `BioSampleEntryResponse` | `BioSampleEntryJsonLdResponse` |
| `SRA` | sra-* (6 タイプ) | `SraDetailResponse` | `SraEntryResponse` | `SraEntryJsonLdResponse` |
| `JGA` | jga-* (4 タイプ) | `JgaDetailResponse` | `JgaEntryResponse` | `JgaEntryJsonLdResponse` |

- `*DetailResponse`: フロントエンド向け。`dbXrefs` を切り詰め、`dbXrefsCount` を付与
- `*EntryResponse`: ES ドキュメントそのまま。converter 型の別名
- `*EntryJsonLdResponse`: ES ドキュメント + `@context`, `@id`
- Bulk は `BulkResponse` (`entries` + `notFound`) を返す。NDJSON 形式ではエントリーのみ出力 (`notFound` は含まない)

### 型カタログ

#### Path Params (2 型)

| 型名 | フィールド |
|------|----------|
| `TypeParams` | `type` |
| `TypeIdParams` | `type`, `id` |

#### Mixin Query (3 型)

| 型名 | フィールド |
|------|----------|
| `PaginationQuery` | `page`, `perPage` |
| `SearchFilterQuery` | `keywords`, `keywordFields`, `keywordOperator`, `organism`, `datePublishedFrom`, `datePublishedTo`, `dateModifiedFrom`, `dateModifiedTo` |
| `ResponseControlQuery` | `sort`, `fields`, `includeProperties`, `includeFacets` |

#### Endpoint Query (9 型)

| 型名 | 合成元 | 追加フィールド |
|------|-------|--------------|
| `EntriesQuery` | Pagination + SearchFilter + ResponseControl | `types`, `dbXrefsLimit` (0-1000, デフォルト: 100) |
| `EntriesTypeQuery` | Pagination + SearchFilter + ResponseControl | `dbXrefsLimit` (0-1000, デフォルト: 100) |
| `EntriesBioProjectQuery` | EntriesTypeQuery を拡張 | `organization`, `publication`, `grant`, `umbrella` |
| `EntryDetailQuery` | — | `dbXrefsLimit` (0-1000, デフォルト: 100) |
| `BulkQuery` | — | `format` (`json` / `ndjson`, デフォルト: `json`) |
| `FacetsQuery` | SearchFilter | `types` |
| `FacetsTypeQuery` | SearchFilter | — |
| `FacetsBioProjectQuery` | FacetsTypeQuery を拡張 | `organization`, `publication`, `grant`, `umbrella` |

#### Request (1 型)

| 型名 | フィールド |
|------|----------|
| `BulkRequest` | `ids` (最大 1000 件) |

#### Response (17 型)

| 型名 | 説明 |
|------|------|
| `EntryListResponse` | 検索結果リスト (pagination + items: `list[EntryListItem]`) |
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
| `DbXrefsFullResponse` | dbXrefs 全件取得 |
| `BulkResponse` | 一括取得レスポンス (entries: `list[*EntryResponse]` + notFound: `list[string]`) |
| `FacetsResponse` | ファセット集計 |
| `ServiceInfoResponse` | サービス情報 (name, version, description, elasticsearch) |

#### ドメインモデル (5 型)

| 型名 | 説明 |
|------|------|
| `Pagination` | ページネーション情報 (page, perPage, total) |
| `EntryListItem` | 検索結果リスト内の各エントリー (サマリー) |
| `Facets` | ファセット集計データ (フィールド名 → 値別カウント) |
| `DbXrefsCount` | dbXrefs のタイプ別カウント |
| `ProblemDetails` | RFC 7807 エラーレスポンス |

#### ddbj-search-converter 由来 (6 型)

エントリーのスキーマは [ddbj-search-converter](https://github.com/ddbj/ddbj-search-converter) で定義されている。API はこれらの型を再定義せず、直接使用する。

| 型名 | 対象タイプ | 説明 |
|------|----------|------|
| `BioProject` | bioproject | BioProject エントリー |
| `BioSample` | biosample | BioSample エントリー |
| `SRA` | sra-* (6 タイプ共通) | SRA エントリー |
| `JGA` | jga-* (4 タイプ共通) | JGA エントリー |
| `Organism` | 全タイプ共通 | 生物種情報 (identifier, name) |
| `Xref` | 全タイプ共通 | 外部データベース参照 |
