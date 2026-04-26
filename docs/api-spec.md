# API 仕様書

DDBJ Search API は BioProject / BioSample / SRA / JGA データを検索・取得する RESTful API サーバー。認証なしの public API として提供する。

エンドポイント・パラメータ・レスポンススキーマの詳細は Swagger UI (`/search/api/docs`) または raw spec (`/search/api/openapi.json`) で確認する。本仕様書はコードや openapi.json では表現しきれないロジック・規約・運用ルールを集める。

各エンドポイントは URL prefix `/search/api` の下にデプロイされ、本仕様書ではパスを prefix なしの相対表記 (例: `/entries/`) で書く。設計判断の背景 (ES と DuckDB の役割分担、converter との関係) は [overview.md](overview.md) を参照。

## sameAs による ID 解決

Entry Detail API の 4 エンドポイントは、`{id}` パスパラメータに対して以下の順序でエントリーを解決する:

1. **identifier 一致**: ES ドキュメント ID (`_id` = `identifier`) で直接取得を試みる
2. **sameAs フォールバック**: 1 で見つからない場合、`sameAs` フィールドを nested query で検索する。検索条件は `sameAs.identifier == {id} AND sameAs.type == {type}` (同一タイプのみ)
3. **404**: いずれでも見つからない場合は `404 Not Found` を返す

sameAs フォールバックでヒットした場合、レスポンスは identifier で直接取得した場合と同一形式 (リダイレクトはしない)。

**sameAs クエリのエラーハンドリング**: sameAs nested query が ES エラー (400 等) を返した場合、「見つからない」として扱い、ステップ 3 の 404 へフォールスルーする。これにより、`sameAs` フィールドのマッピングが存在しないインデックスに対するリクエストでも 500 ではなく 404 を返す。

**対象データ**: JGA エントリー (jga-study, jga-dataset, jga-dac) は XML の `IDENTIFIERS.SECONDARY_ID` を `sameAs` に格納しており、Secondary ID から Primary エントリーを取得できる。ロジック自体は全タイプ共通で、`sameAs` が空のタイプではフォールバックが発火しないだけである。

**Elasticsearch 要件**: `sameAs` フィールドは nested タイプとしてインデックスされている必要がある (ddbj-search-converter 側のマッピング定義)。

**alias ドキュメント (converter 連携)**: ddbj-search-converter は、エントリーの `sameAs` に含まれる Secondary ID を `_id` とする alias ドキュメントを ES に投入する (alias ドキュメントの `_source` は Primary ドキュメントと同一で、`identifier` フィールドは Primary ID のまま)。これにより、Secondary ID による直接取得 (ステップ 1) が alias ドキュメントにヒットし、sameAs nested query (ステップ 2) へのフォールバックが不要になる。API 側では、直接取得成功時に `_source.identifier` を確認し、リクエストの `{id}` と異なる場合は `identifier` を Primary ID として DuckDB 検索・JSON-LD `@id` に使用する。

## Umbrella Tree (BioProject 専用)

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

## 共通仕様

### CORS

すべてのオリジンからのリクエストを許可する。

```plain
Access-Control-Allow-Origin: *
Access-Control-Allow-Methods: *
Access-Control-Allow-Headers: *
```

### Trailing Slash

リスト系エンドポイント (`/entries/`, `/entries/{type}/`, `/dblink/`) は trailing slash 付きを canonical パスとする。trailing slash なし (`/entries`, `/dblink` 等) でも同じレスポンスを返す (リダイレクトしない)。

Facets API (`/facets`, `/facets/{type}`) と DB Portal API (`/db-portal/cross-search`, `/db-portal/search`, `/db-portal/parse`) は trailing slash なしのみをサポートする。
個別リソース (例: `/entries/{type}/{id}`, `/dblink/{type}/{id}`) にも trailing slash を付けない。

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
| 400 | Bad Request | Deep paging 制限超過 (`page * perPage > 10000`)、`cursor` と検索条件/`page` の同時指定、不正な cursor トークン、cursor 期限切れ (PIT 失効)、`facets` の type-mismatch (valid なフィールド名だが対象 endpoint で利用できない、例: `GET /facets/bioproject?facets=libraryStrategy`)、DB Portal API の `q`/`adv` 同時指定 |
| 404 | Not Found | エントリーが存在しない、withdrawn / private のエントリーへの直接アクセス (存在を秘匿するため存在しないものと同じ応答を返す)、不正な `{type}` |
| 422 | Unprocessable Entity | パラメータバリデーションエラー (`perPage` の範囲外、不正な日付形式 (`YYYY-MM-DD` 以外) や不正な日付 (`2024-02-30` 等)、不正な `types` 値、不正な `objectTypes` 値 (`BioProject` / `UmbrellaBioProject` 以外)、不正な `sort` フィールド、不正な `keywordFields` 値、不正な `facets` 値 (allowlist 外のフィールド名 typo)、cross-type endpoint (`GET /entries/`, `GET /facets`) に type-specific filter / 型グループ限定 nested 検索パラメータ (`externalLinkLabel` / `derivedFromId`) / text match パラメータが渡された場合 (`organization` / `publication` / `grant` は cross-type endpoint でも受け付け)、type-specific endpoint に対応する型グループ外のパラメータが渡された場合 (例: `GET /entries/biosample/?libraryStrategy=WGS` や `GET /entries/bioproject/?host=Homo+sapiens`) など) |
| 500 | Internal Server Error | ES 接続エラー、DuckDB ファイルが見つからない (Entries 検索/詳細/Bulk/DBLinks API)、その他サーバー内部エラー |
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
- `types`, `organization`, `publication`, `grant`, `objectTypes`, `externalLinkLabel`, `derivedFromId`
- `libraryStrategy`, `librarySource`, `librarySelection`, `platform`, `instrumentModel`, `libraryLayout`, `analysisType`, `experimentType`, `studyType`, `datasetType`, `submissionType` (type-specific term filter)
- `projectType`, `host`, `strain`, `isolate`, `geoLocName`, `collectionDate`, `libraryName`, `libraryConstructionProtocol`, `vendor` (type-specific text match)
- `includeFacets`, `includeProperties` (デフォルト値以外), `fields`, `facets`

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
| `keywords` | string | — | 検索キーワード (カンマ区切りで複数指定可)。ダブルクオートで囲むとフレーズマッチ (例: `"RNA-Seq"`)。記号 (`-` `/` `.` `+` `:`) を含むキーワードは自動フレーズマッチ (例: `HIF-1`)。`keywords` 全体が単一のアクセッション ID と完全一致する場合は `suppressed` ステータスも検索対象に含む (詳細は「データ可視性 (status 制御)」節) |
| `keywordFields` | string | — | 検索対象フィールドを限定 (カンマ区切り)。指定可能な値: `identifier`, `title`, `name`, `description`。不正な値は 422 |
| `keywordOperator` | string | `AND` | キーワードの結合条件。`AND` (すべて一致) / `OR` (いずれか一致) |
| `organism` | string | — | NCBI Taxonomy ID (例: `9606`) |
| `organization` | string | — | nested query で `organization.name` に match (詳細は § nested フィールド検索)。`GET /entries/`, `GET /facets` (cross-type) を含む全 endpoint で受け付け。converter スキーマで `organization` を持たない type に渡された場合は ES で match なしとなり結果 0 件化 |
| `publication` | string | — | nested query で `publication.title` に match (詳細は § nested フィールド検索)。同上 |
| `grant` | string | — | nested query で `grant.title` に match (詳細は § nested フィールド検索)。同上 |
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

`GET /entries/`, `GET /facets` (cross-type endpoint) は **`SearchFilterQuery` の共通パラメータ + `types`** を受け付ける。`SearchFilterQuery` の `organization` / `publication` / `grant` (nested) も全 endpoint 共通として cross-type endpoint で受け付け、対応 nested path を持たない index では match なしで自然に 0 件化される。一方、type-specific filter (term) / 型グループ限定 nested (`externalLinkLabel`, `derivedFromId`) / text match パラメータを cross-type endpoint に渡すと **422 Unprocessable Entity** を返す (型グループ限定 param は全 index で AND 制約として一律に適用できないため)。

`GET /entries/{type}/`, `GET /facets/{type}` (type-specific endpoint) は、DbType の **型グループ単位** で以下のパラメータを共通に受け付ける。型グループ内の各 type に同じパラメータセットが許可され、対応 field を持たない type に渡された場合は ES 側で match なしになり結果が 0 件化される (DbType ごとに細かく許可リストを分けず、型グループ全体で受け付ける方針)。

各パラメータには 3 種類のセマンティクスがある (詳細はそれぞれの節を参照):

- **term**: `*.keyword` への term filter (exact match、カンマ区切り値は OR)
- **nested**: nested query 経由の検索 (§ nested フィールド検索)
- **text**: text field への match query (analyzer 適用、auto-phrase 対応、§ text match フィールド検索)

| 型グループ (DbType) | パラメータ |
|---|---|
| `GET /entries/`, `GET /facets` (cross-type) | `types` (`organization` / `publication` / `grant` は § 検索パラメータ の `SearchFilterQuery` で全 endpoint 共通) |
| bioproject | `objectTypes` (term)、`externalLinkLabel` (nested)、`projectType` (text) |
| biosample | `derivedFromId` (nested)、`host` / `strain` / `isolate` / `geoLocName` / `collectionDate` (text) |
| sra-* (`sra-submission` / `sra-study` / `sra-experiment` / `sra-run` / `sra-sample` / `sra-analysis`) | `libraryStrategy` / `librarySource` / `librarySelection` / `platform` / `instrumentModel` / `libraryLayout` / `analysisType` (term)、`derivedFromId` (nested)、`libraryName` / `libraryConstructionProtocol` / `geoLocName` / `collectionDate` (text) |
| jga-* (`jga-study` / `jga-dataset` / `jga-policy` / `jga-dac`) | `studyType` / `datasetType` (term)、`externalLinkLabel` (nested)、`vendor` (text) |
| gea | `experimentType` (term) |
| metabobank | `studyType` / `experimentType` / `submissionType` (term) |

term filter / text match パラメータの値域は型グループ内で実際に当該 field を持つ type が SSOT (例: sra-* の `libraryStrategy` は sra-experiment、`libraryLayout` は sra-experiment、`analysisType` は sra-analysis、`libraryName` は sra-experiment、`geoLocName` / `collectionDate` は biosample / sra-sample、jga-* の `studyType` は jga-study、`datasetType` は jga-dataset、`vendor` は jga-study)。型グループ内の他 type に渡しても match なしで 0 件化される。

**`objectTypes` (bioproject)** の補足: `BioProject` / `UmbrellaBioProject` のカンマ区切り (1 つまたは 2 つ)。指定された値の OR 検索。未指定または両方指定はフィルタなしと等価。値域は `objectType` ファセットの bucket key と一致する。

### nested フィールド検索

ddbj-search-converter のスキーマで nested 型として定義されているフィールド (`organization`, `publication`, `grant`, `externalLink`, `derivedFrom`) は、`keywords` の `multi_match` 対象に含まれない (`multi_match` は nested ドキュメントに降りないため)。専用パラメータで nested query 経由の検索を提供する。

**API パラメータ**:

| パラメータ | nested path | match 対象 sub-field | 適用範囲 |
|---|---|---|---|
| `organization` | `organization` | `organization.name` | 全 endpoint (cross-type 含む、§ 検索パラメータ の `SearchFilterQuery`) |
| `publication` | `publication` | `publication.title` | 同上 |
| `grant` | `grant` | `grant.title` | 同上 |
| `externalLinkLabel` | `externalLink` | `externalLink.label` | bioproject / jga-* (型グループ限定、§ エンドポイント固有のパラメータ) |
| `derivedFromId` | `derivedFrom` | `derivedFrom.identifier` | biosample / sra-* (型グループ限定、§ エンドポイント固有のパラメータ) |

`organization` / `publication` / `grant` の 3 つは converter スキーマ上で多くの DB に nested として存在することから全 endpoint (cross-type 含む) の共通検索フィルタに置く。対応 nested path を持たない type / index に渡された場合は ES 側で match なしで結果 0 件化される (DbType 別の存在は converter mapping を参照)。`externalLinkLabel` / `derivedFromId` は使える型グループが限定的 (bioproject + jga-* / biosample + sra-*) なため、§ エンドポイント固有のパラメータ で型グループ単位の許可とする。

cross-type endpoint (`GET /entries/`, `GET /facets`) では型グループ限定の nested 検索パラメータ (`externalLinkLabel`, `derivedFromId`) を受け付けない (**422 Unprocessable Entity**、対応する型グループ外で AND 制約として適用できないため)。`organization` / `publication` / `grant` は前述のとおり cross-type endpoint でも受け付ける (全 type に nested あり、対応 path を持たない index は自然に 0 件化)。

各フィールドの DB 別存在の詳細は ddbj-search-converter の [es/mappings/](https://github.com/ddbj/ddbj-search-converter/tree/main/ddbj_search_converter/es/mappings) を参照。

`sameAs` も nested フィールドだが、detail エンドポイントの ID 解決 (§ sameAs による ID 解決) で内部利用しており、検索フィルタとしては露出しない。

### text match フィールド検索

ddbj-search-converter のスキーマで top-level の text 型フィールドとして定義されているもののうち、値域が wide・表記揺れあり・自由文で、term filter (`*.keyword` exact match) では実用性が低いフィールドは、専用の type-specific text match パラメータで検索する。`keywords` の `multi_match` と同じ analyzer 適用 + auto-phrase 機構を使う。

**API パラメータと適用範囲**:

| パラメータ | match 対象フィールド | 適用範囲 (型グループ) |
|---|---|---|
| `projectType` | `projectType` | bioproject |
| `host` | `host` | biosample |
| `strain` | `strain` | biosample |
| `isolate` | `isolate` | biosample |
| `geoLocName` | `geoLocName` | biosample / sra-* (sra-sample が SSOT、他 sra-* type では 0 件化) |
| `collectionDate` | `collectionDate` | biosample / sra-* (同上) |
| `libraryName` | `libraryName` | sra-* (sra-experiment が SSOT、他 sra-* type では 0 件化) |
| `libraryConstructionProtocol` | `libraryConstructionProtocol` | sra-* (同上) |
| `vendor` | `vendor` | jga-* (jga-study が SSOT、他 jga-* type では 0 件化) |

**検索 semantics** (`keywords` と共通の auto-phrase 機構を再利用):

- `host=Homo+sapiens`: analyzer で token 化、各 token の `match` query (default `keywordOperator=AND` に従い AND 結合)
- `host="Homo sapiens"`: phrase match (`match_phrase`、明示フレーズ)
- `host=HIF-1`: 記号 (`-` `/` `.` `+` `:`) を含むため自動 phrase 化
- カンマ区切りで複数値: 各値を OR 結合 (例: `host=Homo+sapiens,Mus+musculus` で Homo sapiens OR Mus musculus にマッチ)

**term filter との使い分け**:

- term filter (`*.keyword` exact match): 値域が限定的・固定値のフィールド (例: `libraryStrategy` の WGS / RNA-Seq 等)
- text match (analyzer 適用): 値域が wide・表記揺れあり・自由文のフィールド (例: bioproject の `projectType` は `Genome sequencing` / `genome sequencing` / `Genome Sequencing` 等の表記揺れがあり、term filter では実用性が低い。biosample の `host` / `strain` / `isolate` も自由形式テキスト)

cross-type endpoint (`GET /entries/`, `GET /facets`) では text match パラメータ 9 個を受け付けない (**422 Unprocessable Entity**、全 index で AND 制約として一律に適用できないため)。

各フィールドの DB 別存在の詳細は ddbj-search-converter の [es/mappings/](https://github.com/ddbj/ddbj-search-converter/tree/main/ddbj_search_converter/es/mappings) を参照。

### ファセット

検索結果のファセット集計。取得方法は 2 つ:

- `GET /facets` (または `GET /facets/{type}`): ファセットのみ取得。検索結果リストが不要な場合に使う
- `GET /entries/?includeFacets=true` (または `GET /entries/{type}/?includeFacets=true`): 検索結果リストとファセットを 1 リクエストで同時取得。フロントエンドで検索結果とファセットを同時に表示する場合に使う

ファセットのカウントは、検索クエリ (`SearchFilterQuery` のパラメータ) が適用された結果に対して集計される。例えば `keywords=cancer` を指定した場合、ファセットの各値のカウントは `cancer` にマッチするエントリーのみを対象とした件数になる。クエリを指定しない場合は全件が対象になる。ファセット集計は必ず `status:public` に絞り込んでから行う (詳細は「データ可視性 (status 制御)」節)。

**共通ファセットフィールド** (全タイプ共通):

| フィールド | 説明 |
|-----------|------|
| `organism` | 生物種別カウント |
| `accessibility` | アクセシビリティ別カウント |

レスポンス schema (`Facets`) 上はすべてのフィールドが optional (nullable)。`facets` パラメータで集計対象に含まれているフィールドのみ list として返り、対象外のフィールドは `null` で返る。SDK / クライアントは「集計したが 0 件」(空 list `[]`) と「集計対象外」(`null`) を区別できる。

**横断検索時の追加フィールド** (`GET /entries/`, `GET /facets`):

| フィールド | 説明 |
|-----------|------|
| `type` | データタイプ別カウント |

**タイプ固有フィールド**:

| タイプ | フィールド | 説明 |
|--------|----------|------|
| bioproject | `objectType` | Umbrella / 通常の区分。bucket key は `BioProject` / `UmbrellaBioProject`。同じ key を `objectTypes` filter に渡すと検索を絞り込める |
| sra-experiment | `libraryStrategy`, `librarySource`, `librarySelection`, `platform`, `instrumentModel` | 各 `*.keyword` の値域。同名の type-specific filter parameter で検索を絞り込める |
| gea | `experimentType` | `experimentType.keyword` の値域 |
| metabobank | `studyType`, `experimentType`, `submissionType` | 各 `*.keyword` の値域 |
| jga-study | `studyType` | `studyType.keyword` の値域 |

タイプ固有フィールドは、そのタイプのファセット (`GET /facets/{type}`, `GET /entries/{type}/?includeFacets=true`) でのみ返される。bucket key の値域は ES データから動的に決まるため (converter mapping の値域)、本仕様書には列挙しない。

#### ファセット集計対象の選択 (`facets` パラメータ)

ファセット集計はクエリパラメータ `facets` (カンマ区切り) で集計対象フィールドを opt-in で選ぶ。

**デフォルト挙動** (`facets` 未指定時): **共通ファセットのみ** を返す。

- `organism`, `accessibility`
- 横断検索 (`GET /entries/`, `GET /facets`) のときのみ `type` を追加

タイプ固有フィールド (上記表) および `objectType` は、デフォルトでは集計しない。

**明示指定時** (`facets=...` を渡した場合): **指定したフィールドのみ** を集計する (デフォルトの「共通ファセットを base に追加」ではなく、**完全置換**)。共通ファセット (`organism`, `accessibility`) や cross-type の `type` も同時に取得したい場合は `facets=organism,accessibility,type,objectType` のように共通分も明示する必要がある。集計対象から外れたフィールドはレスポンスで `null` になる。

**指定可能値**: `organism`, `accessibility`, `type` (cross-type endpoint のみ), `objectType` (bioproject endpoint のみ), およびタイプ固有フィールド (上記表)。

**適用箇所**: `GET /facets`, `GET /facets/{type}`, `GET /entries/*` の `includeFacets=true` 時。`includeFacets=false` (デフォルト) の `GET /entries/*` では `facets` を指定しても集計しない。

**`facets=` (空文字)**: ファセット 0 個 (`Facets` schema の全フィールドが `null`) を返す。

**エラー**:

- 共通フィールド・タイプ固有フィールドのいずれにも該当しない値 (typo): **422 Unprocessable Entity**
- valid なフィールド名だが対象 endpoint で利用できない (例: `GET /facets/bioproject?facets=libraryStrategy`): **400 Bad Request**

cross-type endpoint (`GET /facets`, `GET /entries/?includeFacets=true`) では、いずれかの index でそのフィールドが存在すれば許可し、該当 index のみで集計する。

背景・設計判断の詳細は [overview.md § ファセット default の設計](overview.md) を参照。

### データ可視性 (status 制御)

ES ドキュメントの `status` フィールドは INSDC の公開状態を示す 4 値 (`public`, `suppressed`, `withdrawn`, `private`) を取る。API は status に応じて検索・取得の可視性を制御する。

**可視性マトリクス**:

| ケース | suppressed | withdrawn | private |
|--------|:----------:|:---------:|:-------:|
| 自由文キーワード検索 (`/entries/`, `/entries/{type}/`) | 除外 | 除外 | 除外 |
| `keywords` がアクセッション ID 完全一致のキーワード検索 | 許可 (200) | 除外 | 除外 |
| 詳細取得 (`/entries/{type}/{id}`, `.json`, `.jsonld`, `/dbxrefs.json`) | 200 で返却 | 404 | 404 |
| 一括取得 (`POST /entries/{type}/bulk`) | 200 で返却 | `notFound` (JSON) / skip (NDJSON) | `notFound` (JSON) / skip (NDJSON) |
| umbrella-tree (`/entries/bioproject/{accession}/umbrella-tree`) | seed 該当で 404、中間 node 該当で edge から除外 | 同左 | 同左 |
| ファセット (`/facets`, `/facets/{type}`, `/entries/?includeFacets=true`) | カウント対象外 | カウント対象外 | カウント対象外 |

`public` は全エンドポイントで常に可視 (表には記載省略)。

**アクセッション ID 完全一致の判定ルール**:

`/entries/` 系のキーワード検索で、`keywords` が以下の条件を **全て** 満たす場合のみ「アクセッション ID 完全一致」とみなす:

- 単一トークン (カンマ区切りで複数指定していない)
- 前後の空白・タブを `strip` した結果、外側のクオート (ダブル `"` または シングル `'`) は剥がしてから判定する (クオート内が完全一致なら可)
- ワイルドカード (`*`, `?`) を含まない
- ddbj-search-converter の `ID_PATTERN_MAP` に定義された正規表現のいずれかに完全一致する (例: `^PRJ[DEN][A-Z]\d+\Z`, `^[SDE]RA\d+\Z`, `^JGAS\d+\Z` 等)

他の検索フィルタ (`organism`, `datePublishedFrom` など) と併用された場合でも、`keywords` が上記条件を満たせばアクセッション完全一致として扱い、`suppressed` を検索対象に含める。

**404 による存在秘匿**:

`withdrawn` および `private` のエントリーへの直接アクセス (`/entries/{type}/{id}` 系・umbrella-tree seed) は、存在しない ID と同一のレスポンス (`404 Not Found` + `The requested {type} '{id}' was not found.`) を返す。これは status の有無を外部から推測できないようにするため。

**`status` ファセットの廃止**:

ファセット集計は常に `status:public` に絞り込んでから行うため、`status` ファセットの値は常に `public` のみとなり意味をなさない。このため `Facets` スキーマには `status` フィールドが含まれない。

**dbXrefs 内の非 public エントリー** (Future work):

`public` なエントリーのレスポンスに含まれる `dbXrefs` のうち、`withdrawn` / `private` な accession を除外する仕様は Future work とする。巨大な dbXrefs を持つエントリー (数十万件) に対する ES `_mget` のコストが重いため、今回は実装を見送る。現状は DuckDB の edge をそのまま返すため、`withdrawn` / `private` な accession の ID が `dbXrefs` に現れる可能性がある。将来的には converter 側で DuckDB の edge テーブルに status 列を持たせる等で解決する予定。

**DB Portal API (`/db-portal/cross-search`, `/db-portal/search`) の status filter** (Future work):

DB Portal API の ES 経由検索 (`/db-portal/cross-search` の 6 ES DB 部分、および `/db-portal/search?db=bioproject|biosample|sra|jga|gea|metabobank`) に対する status filter は Future work とする。Solr proxy (`/db-portal/search?db=trad|taxonomy`) 側での status 相当の制御と対称性を取る必要があり、別途設計が必要なため。現状は 4 値いずれの status のエントリーも hit する可能性がある。

### 配列フィールド

エントリー詳細系の 3 endpoint (`/entries/{type}/{id}`、`/entries/{type}/{id}.json`、`/entries/{type}/{id}.jsonld`) のレスポンスに含まれる必須配列フィールドは、対象エントリーに該当する値がない場合でも JSON 上の key として常に空配列 `[]` で返却される。これにより SDK 利用者は optional chain なしで `response.dbXrefs.length` のように直接アクセスできる。

エントリー型別の必須配列フィールドの完全な一覧は ddbj-search-converter のスキーマ仕様 ([data-architecture.md § 配列フィールドの契約](https://github.com/ddbj/ddbj-search-converter/blob/main/docs/data-architecture.md#%E9%85%8D%E5%88%97%E3%83%95%E3%82%A3%E3%83%BC%E3%83%AB%E3%83%89%E3%81%AE%E5%A5%91%E7%B4%84)) を参照する。OpenAPI スキーマ上は API 側の以下すべての schema の `required` に列挙される。

- フロントエンド向け Detail: `BioProjectDetailResponse` / `BioSampleDetailResponse` / `SraDetailResponse` / `JgaDetailResponse` / `GeaDetailResponse` / `MetaboBankDetailResponse`
- raw レスポンス: `BioProject` / `BioSample` / `SRA` / `JGA` / `GEA` / `MetaboBank` (`*EntryResponse` は converter スキーマの type alias)
- JSON-LD レスポンス: `BioProjectEntryJsonLdResponse` / `BioSampleEntryJsonLdResponse` / `SraEntryJsonLdResponse` / `JgaEntryJsonLdResponse` / `GeaEntryJsonLdResponse` / `MetaboBankEntryJsonLdResponse`

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
- テーブル: `dbxref (accession_type, accession, linked_type, linked_accession)`
- DBLink は無向グラフ。converter が各無向 edge `{A, B}` を `(A → B)` と `(B → A)` の 2 行に半辺化して保存する。これにより、いずれの端点からの lookup も `WHERE accession_type = ? AND accession = ?` の point lookup で完結する (UNION ALL 不要)
- 物理 sort: `accession_type, accession, linked_type, linked_accession` + `idx_dbxref_accession (accession_type, accession)` により方向別の性能非対称性は解消されている
- API のレスポンス形式・意味論は本 schema 変更で変わらない (storage 層の最適化)

#### アクセッションタイプ (AccessionType, 21 種)

`bioproject`, `biosample`, `gea`, `geo`, `humandbs`, `insdc`, `insdc-assembly`, `insdc-master`, `jga-dac`, `jga-dataset`, `jga-policy`, `jga-study`, `metabobank`, `pubmed`, `sra-analysis`, `sra-experiment`, `sra-run`, `sra-sample`, `sra-study`, `sra-submission`, `taxonomy`

DbType とは別の型。dblink 固有のタイプを含む。

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

## DB Portal API

db-portal フロント専用の統合検索 API (`/db-portal/cross-search` / `/db-portal/search` / `/db-portal/parse`)。`/entries/*` 系とは別系統のレスポンススキーマと Advanced Search DSL を持つため、仕様は独立ファイルに分離している: [db-portal-api-spec.md](db-portal-api-spec.md)。

## サービス情報

`GET /service-info` でサービスのメタ情報と Elasticsearch の疎通状態を返す。死活監視・デプロイ確認用。

**レスポンス** (`ServiceInfoResponse`):

```json
{
  "name": "DDBJ Search API",
  "version": "0.x.y",
  "description": "RESTful API for searching and retrieving BioProject, BioSample, SRA, and JGA entries.",
  "elasticsearch": "ok"
}
```

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `name` | string | サービス名 |
| `version` | string | パッケージバージョン (`importlib.metadata` から取得) |
| `description` | string | サービスの説明 |
| `elasticsearch` | enum | `ok` (ES 疎通可) / `unavailable` (ES 不通) |

ES が落ちていてもこのエンドポイント自体は 200 を返し、`elasticsearch=unavailable` で状態を伝える。trailing slash なし (`/service-info`) のみをサポートする。



