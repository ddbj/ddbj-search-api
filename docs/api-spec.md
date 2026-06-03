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

**Case-sensitivity**: accession は ES に投入された正規形 (典型的には大文字。例: `PRJDB1234`) と完全一致した場合のみ解決する。API 側では case-folding を行わず、ES `_id` lookup と `sameAs.identifier` の term match はいずれも大小区別。lowercase / mixedcase で渡された accession はステップ 1・2 とも一致しないため 404 となり、レスポンスは「不在の accession」と区別不能 (`§ データ可視性` § 404 による存在秘匿 と同じ固定 detail)。

**sameAs クエリのエラーハンドリング**: sameAs nested query が ES エラー (400 等) を返した場合、「見つからない」として扱い、ステップ 3 の 404 へフォールスルーする。これにより、`sameAs` フィールドのマッピングが存在しないインデックスに対するリクエストでも 500 ではなく 404 を返す。

**対象データ**: JGA エントリー (jga-study, jga-dataset, jga-dac) は XML の `IDENTIFIERS.SECONDARY_ID` を `sameAs` に格納しており、Secondary ID から Primary エントリーを取得できる。ロジック自体は全タイプ共通で、`sameAs` が空のタイプではフォールバックが発火しないだけである。BioProject の `sameAs` には GEO 等の外部 DB cross-ref のみが格納されており (`sameAs.type` が `bioproject` 以外)、Secondary ID による Primary 解決経路は事実上 JGA 系のみで効く。

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

**depth 1 (umbrella → leaf のみ、典型的なケース)**:

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

**multi-parent DAG (子が複数親を持つケース)**:

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
  "detail": "The requested bioproject entry was not found.",
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
| 400 | Bad Request | Deep paging 制限超過 (`page * perPage > 10000`)、`cursor` と検索条件/`page` の同時指定、不正な cursor トークン、cursor 期限切れ (PIT 失効)、`facets` の type-mismatch (valid なフィールド名だが対象 endpoint で利用できない、例: `GET /facets/bioproject?facets=libraryStrategy`) |
| 404 | Not Found | エントリーが存在しない、withdrawn / private のエントリーへの直接アクセス (存在を秘匿するため存在しないものと同じ応答を返す)、不正な `{type}` |
| 422 | Unprocessable Entity | パラメータバリデーションエラー (`perPage` の範囲外、不正な日付形式 (`YYYY-MM-DD` 以外) や不正な日付 (`2024-02-30` 等)、不正な `types` 値、不正な `objectTypes` 値 (`BioProject` / `UmbrellaBioProject` 以外)、不正な `sort` フィールド、不正な `keywordFields` 値、不正な `facets` 値 (allowlist 外のフィールド名 typo)、`facetsSize` の範囲外 (1〜1000 以外) または非整数、cross-type endpoint (`GET /entries/`, `GET /facets`) に type-specific filter / 型グループ限定 nested 検索パラメータ (`externalLinkLabel` / `derivedFromId`) / text match パラメータが渡された場合 (`organization` / `publication` / `grant` は cross-type endpoint でも受け付け)、type-specific endpoint に対応する型グループ外のパラメータが渡された場合 (例: `GET /entries/biosample/?libraryStrategy=WGS`、`GET /entries/bioproject/?host=Homo+sapiens`、`GET /entries/biosample/?grant=AMED` (`grant` は bioproject / jga-* のみ、`publication` は biosample 以外でのみ受け付け)) など) |
| 500 | Internal Server Error | ES 接続エラー、DuckDB ファイルが見つからない (Entries 検索/詳細/Bulk/DBLinks API)、その他サーバー内部エラー |
| 502 | Bad Gateway | DB Portal API の横断 fan-out で全 DB への問い合わせが失敗 |

400 と 422 の使い分け: リクエストのパラメータ型・形式・制約のバリデーションは 422、アプリケーションのビジネスルール違反 (deep paging 制限、cursor 排他など) は 400 を返す。

### ページネーション

リスト系エンドポイントは 2 種類のページネーション (オフセットベース・カーソルベース) をサポートする。パラメータ名・型・デフォルトは Swagger UI (`/search/api/docs`) もしくは [openapi.json](openapi.json) を参照。本節では振る舞いと規約だけを残す。

#### オフセットベースページネーション (デフォルト)

`page` (1 始まり) と `perPage` (1-100) でページを指定する。`total` は常に正確な総件数 (`track_total_hits=true`)。

**Deep paging 制限**: `page * perPage` が 10000 を超えるリクエストは `400 Bad Request` を返す (Elasticsearch の `index.max_result_window` に基づく)。10000 件を超える結果を取得する必要がある場合は、カーソルベースを使う。

#### カーソルベースページネーション

10,000 件を超える検索結果を順次取得するためのページネーション方式。Elasticsearch の `search_after` + PIT (Point in Time) を内部で使用する。

**使い方**:

1. 通常のオフセットベース検索を実行する
2. レスポンスの `nextCursor` を取得する
3. `cursor` パラメータに `nextCursor` の値を指定してリクエストする
4. 以降、`nextCursor` が `null` になるまで繰り返す

**排他ルール**: `cursor` を指定した場合、以下のパラメータは指定不可 (`400 Bad Request`):

- `page` (デフォルト値 `1` 以外)
- `keywords`, `keywordFields`, `keywordOperator` (デフォルト値 `OR` 以外)
- `organism`, `accessibility`, `datePublishedFrom`, `datePublishedTo`, `dateModifiedFrom`, `dateModifiedTo`
- `sort`
- `types`, `organization`, `publication`, `grant`, `objectTypes`, `externalLinkLabel`, `derivedFromId`
- `libraryStrategy`, `librarySource`, `librarySelection`, `platform`, `instrumentModel`, `libraryLayout`, `analysisType`, `experimentType`, `studyType`, `datasetType`, `submissionType`, `relevance`, `package`, `model` (type-specific term filter)
- `projectType`, `host`, `strain`, `isolate`, `geoLocName`, `collectionDate`, `libraryName`, `libraryConstructionProtocol`, `vendor` (type-specific text match)
- `includeFacets`, `includeProperties` (デフォルト値以外), `fields`, `facets`, `facetsSize`

`perPage`、`dbXrefsLimit`、`includeDbXrefs` は `cursor` と併用可能。

**レスポンス**: `page` はカーソルモードでは `null` (ページ番号の概念がないため)。`nextCursor` は次ページ用トークン (最終ページでは `null`)。`hasNext` は次ページの有無を示す boolean。

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

`GET /entries/`, `GET /entries/{type}/`, `GET /facets`, `GET /facets/{type}` で共通の検索パラメータは、内部的に 2 つの mixin (`SearchFilterQuery` と `ResponseControlQuery`) に分けて定義される。Entries API は両方を合成、Facets API は `SearchFilterQuery` のみを使用する。各パラメータの名前・型・デフォルト・例は Swagger UI (`/search/api/docs`) もしくは [openapi.json](openapi.json) を参照。本節では Swagger UI に書かれていない振る舞いと規約だけを残す。

#### 検索 query parameter のセマンティクス共通ルール

`keywords` / nested 5 param (`organization` / `publication` / `grant` / `externalLinkLabel` / `derivedFromId`) / text match 9 param (`host` / `strain` / `isolate` / `geoLocName` / `collectionDate` / `libraryName` / `libraryConstructionProtocol` / `vendor` / `projectType`) は以下の統一ルールで動作する (`derivedFromId` のみ keyword field の特性上、phrase / auto-phrase が適用されない)。

| 入力 | 意味 |
|---|---|
| 値内のスペース | **AND** 結合 (例: `organization=National Institute` → `National` AND `Institute` を含む `organization.name`) |
| カンマ区切り | **OR** 結合 (例: `organization=DDBJ,EBI` → `DDBJ` OR `EBI`) |
| ダブルクオート `"..."` | **phrase 検索** (順序固定。例: `organization="National Institute"`) |
| 記号 `-` `/` `.` `+` `:` を含む値 | **自動 phrase 化** (例: `HIF-1`, `COVID-19`, `SARS-CoV-2`, `GSE12345/analysis`、`Pasteur-Lille`) |

`derivedFromId` のみ: 値全体を accession ID として `term` 完全一致 (大小区別)。カンマ区切りで OR (`terms`)。スペース AND / phrase / auto-phrase は適用なし。

`keywords` の場合: カンマ区切り全体を AND するか OR するかは `keywordOperator` パラメータで上書きできる (default **OR**)。値内スペース AND は常に有効 (上書き不可。AND にしたくない場合はクオートで phrase 化する)。

text match 9 param と nested 4 text param (`organization` / `publication` / `grant` / `externalLinkLabel`) は `keywordOperator` の影響を受けない (常に値内空白 = AND、カンマ = OR)。

**フレーズマッチ** (`keywords`): 明示フレーズ (ダブルクオート) と自動フレーズ化 (記号含み) は ES `multi_match` の `type: "phrase"` で検索する (トークン順序を保持した完全一致、analyzer による token 分割で精度が下がるのを防ぐ)。混在可能: `keywords=HIF-1,"whole genome",cancer` → 前 2 つは phrase、最後はトークンベースマッチ (値内空白なし)。自動フレーズ化は `HIF-1` のように ES standard analyzer が `-` で token を分割して無関係な `HIF*` 系エントリーを拾ってしまう挙動を回避する。

**前方一致** (`keywords` のみ): クオートなし・記号なしの bare word トークンは、完全トークン一致に加えて**末尾トークンの前方一致**でも検索する (打ちかけ・部分入力に対応)。各 bare word トークンを `bool.should` (`minimum_should_match: 1`) に展開し、`multi_match{operator:"and"}` (完全語、語順不問) と `multi_match{type:"phrase_prefix"}` (末尾トークン前方一致) を OR で並べる。これで `keywords=Huma` が `Human` に、`keywords=Homo sap` が `Homo sapiens` にヒットする。完全一致は両 should を満たしてスコアが高くなるため、relevance 順 (sort 未指定時) では完全一致が自然に上位に来る (明示 boost は付けない。`sort` 明示時は boost が効かず「ヒット範囲が広がる」だけになる)。前方一致 (`phrase_prefix`) は **text 型フィールド専用**で、`identifier` (keyword 型) には適用しない (ES が keyword field 上の phrase prefix を拒否するため)。`identifier` の完全一致は `operator:"and"` 側で維持される。前方一致の展開数には ES の `max_expansions` (default 50) が効くため、極端に短い prefix では展開が頭打ちになる。

前方一致の境界条件: クオートで囲んだトークンと記号含みトークン (自動フレーズ化) は前方一致せず完全一致 (`type: "phrase"`) のまま (クオート = 厳密一致の意図を尊重)。**末尾語が 1 文字のトークンは前方一致しない** (全 term スキャン回避のため最小 2 文字、field-scoped wildcard `value*` の最小長と同基準)。インデックスは standard analyzer のみ (ngram / edge_ngram 不使用) のため、中間一致 (`uman` → `Human`) はサポートしない。**`keywords` が単一 accession ID と完全一致して `suppressed` を解禁した場合は前方一致を抑止する** (§ データ可視性。解禁した accession の prefix で別 accession の `suppressed` を漏らさないため)。

前方一致が効くのは `keywords` (キーワード検索窓) のみ。nested 5 param / text match 9 param は完全トークン一致のままで前方一致しない (filter 用途のため精密側に倒す。前方一致が必要な場合は値域に応じて term filter や別パラメータを使う)。

**`keywordFields` allowlist**: `identifier`, `title`, `name`, `description`, `organism.name` の 5 値のみ受け付ける (allowlist 外は 422)。`organism.name` は学名のテキストマッチで、term filter `organism` (TaxID 完全一致) とは独立して動作する。

**`organism`**: `^\d+$` の NCBI Taxonomy ID (例: `9606`) のみ受け付ける。`/facets` の `organism` bucket の `value` をそのまま再注入できる (§ ファセット)。学名 (例: `Homo sapiens`) は 422 になる。

**`accessibility`**: enum `public-access` / `controlled-access` の 2 値のみ受け付ける (allowlist 外は 422)。term filter として `accessibility` field (全 DB 共通) を絞り込む。`/facets` の `accessibility` bucket の `value` をそのまま再注入できる (§ ファセット)。

**nested フィールド検索** (`organization` / `publication` / `grant`): 詳細は § nested フィールド検索 を参照。`organization` は全 type に実在するため全 endpoint (cross-type 含む) で共通に受け付ける。`publication` / `grant` は実在する型グループでのみ受け付け (対象外の型グループに渡すと 422)、cross-type endpoint では 3 つとも受け付ける。対応 nested path を持たない index (型グループ内の非実在 subtype や cross-type の非対応 index) では ES 側で match なしで結果 0 件化される。

**日付パラメータ**: `datePublishedFrom` / `datePublishedTo` / `dateModifiedFrom` / `dateModifiedTo` は ISO 8601 (`YYYY-MM-DD`) 形式で範囲指定する。形式違反や実在しない日付 (例: `2024-02-30`) は 422。`From > To` は Pydantic を通り ES 側で `total == 0` になる。

**`sort`**: 形式 `{field}:{direction}`。許容 field は `datePublished` / `dateModified`、direction は `asc` / `desc`。未指定時は relevance (検索スコア) 順。

**`fields`**: ES ドキュメントのトップレベルフィールド名をカンマ区切りで指定。指定外のフィールドは ES から取得しないが、レスポンス schema 上のキー自体は保持され値は `null` で返る (Pydantic レスポンスモデルが必須キーを維持するため)。

**`includeFacets`**: `/entries/*` 系で `true` にすると検索結果リストとファセット集計を 1 リクエストで取得できる (`GET /facets` 等を別途叩く必要がない)。デフォルト `false`。

**エンドポイント固有のパラメータ**:

`GET /entries/`, `GET /facets` (cross-type endpoint) は **`SearchFilterQuery` の共通パラメータ (`organization` を含む) + `types` + `publication` / `grant`** を受け付ける。`organization` / `publication` / `grant` (nested) は cross-type endpoint で受け付け、対応 nested path を持たない index では match なしで自然に 0 件化される。一方、type-specific filter (term) / 型グループ限定 nested (`externalLinkLabel`, `derivedFromId`) / text match パラメータを cross-type endpoint に渡すと **422 Unprocessable Entity** を返す (型グループ限定 param は全 index で AND 制約として一律に適用できないため)。

`GET /entries/{type}/`, `GET /facets/{type}` (type-specific endpoint) は、DbType の **型グループ単位** で以下のパラメータを共通に受け付ける。型グループ内の各 type に同じパラメータセットが許可され、対応 field を持たない type に渡された場合は ES 側で match なしになり結果が 0 件化される (DbType ごとに細かく許可リストを分けず、型グループ全体で受け付ける方針)。

各パラメータには 3 種類のセマンティクスがある (詳細はそれぞれの節を参照):

- **term**: `*.keyword` への term filter (exact match、カンマ区切り値は OR)
- **nested**: nested query 経由の検索 (§ nested フィールド検索)
- **text**: text field への match query (analyzer 適用、auto-phrase 対応、§ text match フィールド検索)

| 型グループ (DbType) | パラメータ |
|---|---|
| `GET /entries/`, `GET /facets` (cross-type) | `types`、`organization` / `publication` / `grant` (nested、cross-type は対応 index で 0 件化。`organization` は § 検索パラメータ の `SearchFilterQuery` 共通) |
| bioproject | `objectTypes` / `relevance` (term)、`publication` / `grant` / `externalLinkLabel` (nested)、`projectType` (text) |
| biosample | `package` / `model` (term)、`derivedFromId` (nested)、`host` / `strain` / `isolate` / `geoLocName` / `collectionDate` (text) |
| sra-* (`sra-submission` / `sra-study` / `sra-experiment` / `sra-run` / `sra-sample` / `sra-analysis`) | `libraryStrategy` / `librarySource` / `librarySelection` / `platform` / `instrumentModel` / `libraryLayout` / `analysisType` (term)、`publication` / `derivedFromId` (nested)、`libraryName` / `libraryConstructionProtocol` / `geoLocName` / `collectionDate` (text) |
| jga-* (`jga-study` / `jga-dataset` / `jga-policy` / `jga-dac`) | `studyType` / `datasetType` (term)、`publication` / `grant` / `externalLinkLabel` (nested)、`vendor` (text) |
| gea | `experimentType` (term)、`publication` (nested) |
| metabobank | `studyType` / `experimentType` / `submissionType` (term)、`publication` (nested) |

term filter / text match パラメータの値域は型グループ内で実際に当該 field を持つ type が SSOT (例: sra-* の `libraryStrategy` は sra-experiment、`libraryLayout` は sra-experiment、`analysisType` は sra-analysis、`libraryName` は sra-experiment、`geoLocName` / `collectionDate` は biosample / sra-sample、jga-* の `studyType` は jga-study、`datasetType` は jga-dataset、`vendor` は jga-study)。型グループ内の他 type に渡しても match なしで 0 件化される。

**`objectTypes` (bioproject)** の補足: `BioProject` / `UmbrellaBioProject` のカンマ区切り (1 つまたは 2 つ)。指定された値の OR 検索。未指定または両方指定はフィルタなしと等価。値域は `objectType` ファセットの bucket key と一致する。

**`relevance` (bioproject) / `package` (biosample) / `model` (biosample)** の補足: それぞれ同名ファセット (`relevance` / `package` / `model`) の bucket value をそのまま再注入できる。カンマ区切りで OR (`terms`)、単一値で `term`。値域のクライアント側 allowlist は行わず、ES 側に存在しない値は match なしで 0 件化される (`relevance` の INSDC 7 値、`package` の BioSample package 名、`model` のモデル名はいずれも controlled vocab 寄りだが、新値追加に api 側の regex 更新を要さない設計)。`package` は ES mapping 上は object 配下の `package.name` (keyword) に対して term filter を組むが、API parameter 名は object 表現を隠して `package` を使う。

### nested フィールド検索

ddbj-search-converter のスキーマで nested 型として定義されているフィールド (`organization`, `publication`, `grant`, `externalLink`, `derivedFrom`) は、`keywords` の `multi_match` 対象に含まれない (`multi_match` は nested ドキュメントに降りないため)。専用パラメータで nested query 経由の検索を提供する。

**API パラメータ**:

| パラメータ | nested path | match 対象 sub-field | 適用範囲 |
|---|---|---|---|
| `organization` | `organization` | `organization.name` | 全 endpoint (cross-type 含む、§ 検索パラメータ の `SearchFilterQuery`) |
| `publication` | `publication` | `publication.title` | bioproject / sra-* / jga-* / gea / metabobank + cross-type (型グループ限定、§ エンドポイント固有のパラメータ) |
| `grant` | `grant` | `grant.title` | bioproject / jga-* + cross-type (型グループ限定、§ エンドポイント固有のパラメータ) |
| `externalLinkLabel` | `externalLink` | `externalLink.label` | bioproject / jga-* (型グループ限定、§ エンドポイント固有のパラメータ) |
| `derivedFromId` | `derivedFrom` | `derivedFrom.identifier` | biosample / sra-* (型グループ限定、§ エンドポイント固有のパラメータ) |

`organization` は全 type に nested として存在するため全 endpoint (cross-type 含む) の共通検索フィルタに置く。`publication` (biosample 以外) / `grant` (bioproject + jga-*) は実在する型グループが限定的なため、`externalLinkLabel` / `derivedFromId` と同様に § エンドポイント固有のパラメータ で型グループ単位の許可とする (対象外の型グループに渡すと 422)。ただし cross-type endpoint では 3 つとも受け付ける。型グループ内で実在しない subtype (例: `grant` を jga-dataset に) や cross-type の非対応 index に渡された場合は ES 側で match なしで結果 0 件化される (DbType 別の存在は converter mapping を参照)。

cross-type endpoint (`GET /entries/`, `GET /facets`) では型グループ限定の nested 検索パラメータ (`externalLinkLabel`, `derivedFromId`) を受け付けない (**422 Unprocessable Entity**、対応する型グループ外で AND 制約として適用できないため)。`organization` / `publication` / `grant` は cross-type endpoint でも受け付ける (対応 path を持たない index は自然に 0 件化)。一方、単一 type endpoint では `publication` / `grant` も型グループ scope の対象で、実在しない型グループ (例: `GET /entries/biosample/?grant=...`) に渡すと 422 になる。

各フィールドの DB 別存在の詳細は ddbj-search-converter の [es/mappings/](https://github.com/ddbj/ddbj-search-converter/tree/main/ddbj_search_converter/es/mappings) を参照。

**検索 semantics**:

`organization` / `publication` / `grant` / `externalLinkLabel` は § セマンティクス共通ルール に従う (値内スペース AND、カンマ OR、クオート phrase、記号 auto-phrase)。内部的に nested wrapper + `match` (`operator=and` 明示) または `match_phrase` query を組み立てる。値内空白 AND は `keywordOperator` の影響を受けない (常に AND 固定)。

- `organization=National+Institute`: 各 token の AND 結合 (`match.operator=and`)
- `organization="National Institute"`: 順序固定の phrase (`match_phrase`)
- `organization=Pasteur-Lille`: 記号 `-` 含みで自動 phrase 化
- `organization=DDBJ,EBI`: 各値を OR 結合 (`bool.should + minimum_should_match=1`)

`derivedFromId` は accession ID の term 完全一致 (`derivedFrom.identifier` は keyword field、analyzer 走らず大小区別)。**カンマ区切り以外のパース処理 (クオート除去・スペース解釈・auto-phrase) は一切しない** ため、値中にスペース・クオートが含まれると 0 件確定。クライアントは生 accession ID のみを comma で連結すること。

- `derivedFromId=SAMD00012345`: 単一 ID の `term`
- `derivedFromId=SAMD00012345,SAMD00067890`: `terms` (OR)
- `derivedFromId=SAMD00012345 SAMD00067890` (スペース区切り) や `derivedFromId="SAMD00012345"` (クオート付き): **0 件**

**scoring 副作用**: nested 5 param はすべて `bool.filter` 配下に置かれる (filter context)。内側の `match` / `match_phrase` / `term` / `terms` / `bool.should` のいずれも `_score` に寄与せず、検索結果の relevance ソート (`sort` 未指定時) に影響しない。`sort=datePublished:desc` 等の明示ソートを併用するか、`keywords` を併用して `keywords` 側で relevance を出すこと。

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

**検索 semantics**:

§ セマンティクス共通ルール に従う (値内スペース AND、カンマ OR、クオート phrase、記号 auto-phrase)。値内空白 AND は `keywordOperator` の影響を受けず常に AND 固定 (`match.operator=and` 明示)。

- `host=Homo+sapiens`: 各 token の AND 結合 (`match.operator=and`、`Homo` AND `sapiens` を含む `host` のみマッチ)
- `host="Homo sapiens"`: 順序固定の phrase (`match_phrase`)
- `host=HIF-1`: 記号含みで自動 phrase 化
- `host=Homo+sapiens,Mus+musculus`: 各値を OR 結合 (`bool.should + minimum_should_match=1`、`Homo sapiens` または `Mus musculus`)

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
| `organism` | 生物種別カウント。bucket は `OrganismFacetBucket` (`{value, count, label}`) 形式。後述 |
| `accessibility` | アクセシビリティ別カウント |

レスポンス schema (`Facets`) 上はすべてのフィールドが optional (nullable)。`facets` パラメータで集計対象に含まれているフィールドのみ list として返り、対象外のフィールドは `null` で返る。SDK / クライアントは「集計したが 0 件」(空 list `[]`) と「集計対象外」(`null`) を区別できる。

**bucket 形式**:

`organism` を除く全 facet は `FacetBucket` (`{value, count}` の 2 フィールド) を返す。`value` の再注入経路は **ペアになる search parameter のタイプ** によって 2 通りある:

- **term filter parameter とペア** (= bucket value を `?<facet>=<value>` に再注入すると完全一致、bucket 件数 = 検索結果件数): `accessibility`, `objectType` (→ `objectTypes`), `libraryStrategy`, `librarySource`, `librarySelection`, `platform`, `instrumentModel`, `libraryLayout`, `analysisType`, `experimentType`, `studyType`, `submissionType`, `datasetType`, `relevance`, `package`, `model`。例: `libraryStrategy` facet の value `"WGS"` を `?libraryStrategy=WGS` に再注入。
- **text match parameter とペア** (= bucket value は `.keyword` の exact、再注入先は analyzed match なので **bucket の docs ⊆ 検索結果**、トークン共有の docs が混ざる): `projectType`, `host`, `vendor`。例: `host` facet の value `"Homo sapiens"` を `?host=Homo+sapiens` に再注入すると analyzed match で `"Homo sapiens domesticus"` 等の追加 docs が混ざりうる。bucket 件数と完全に揃えたければ phrase 化 (`?host="Homo+sapiens"`) を使う。

DB Portal API (`/db-portal/*`) の `q` 経由でも同様に再注入可 (Tier 3 allowlist の snake_case field 名、例: `q=library_strategy:"WGS"` / `q=relevance:"Medical"` / `q=package:"MIGS.ba"` / `q=host:"Homo sapiens"`)。

`organism` のみ例外で `OrganismFacetBucket` (`{value, count, label}` の 3 フィールド) を返す:

| サブフィールド | 型 | 説明 |
|--------------|----|------|
| `value` | string | NCBI Taxonomy ID (例: `"9606"`)。検索 API の `?organism=` は `^\d+$` の TaxID のみ受け付ける (§ 検索パラメータ) ため、bucket の `value` をそのまま再注入できる |
| `count` | integer | 当該 TaxID にマッチするエントリー件数 |
| `label` | string | NCBI Taxonomy の scientific name (例: `"Homo sapiens"`)。表示用の人間向け文字列 |

例:

```json
{
  "facets": {
    "organism": [
      {"value": "9606", "count": 12345, "label": "Homo sapiens"},
      {"value": "562",  "count": 6789,  "label": "Escherichia coli"}
    ],
    "accessibility": [
      {"value": "public-access", "count": 19000}
    ]
  }
}
```

ES の terms aggregation は `organism.identifier` を bucket key にし、各 bucket 内で sub-aggregation により `organism.name.keyword` の代表値 (doc_count 最頻値) を `label` として 1 つ取る。同一 TaxID 内で `organism.name` に表記揺れ (例: `"Homo sapiens"` と `"Homo Sapiens"`) があれば、コーパス内で最も多く使われている表記が選ばれる。`organism.identifier` がある一方 `organism.name` が欠損するレアな entry のみで bucket が構成される場合は、`label` に TaxID (`value` と同値) をフォールバックとして格納する。

**横断検索時の追加フィールド** (`GET /entries/`, `GET /facets`):

| フィールド | 説明 |
|-----------|------|
| `type` | データタイプ別カウント |

**タイプ固有フィールド**:

| タイプ | フィールド | 説明 |
|--------|----------|------|
| bioproject | `objectType`, `relevance`, `projectType` | `objectType`: Umbrella / 通常の区分 (`BioProject` / `UmbrellaBioProject`、同じ key を `objectTypes` filter に渡すと検索を絞り込める)。`relevance`: INSDC 7 値 (Agricultural / Medical / Industrial / Environmental / Evolution / ModelOrganism / Other、同じ key を `relevance` filter に渡すと検索を絞り込める)。`projectType`: `projectType.keyword` の値域 (text match `?projectType=` とペア、再注入は analyzed match) |
| biosample | `package`, `model`, `host` | `package`: BioSample package 名 (`package.name` の値域、controlled vocab、同じ key を `package` filter に渡すと検索を絞り込める)。`model`: モデル名 (`model` の値域、同じ key を `model` filter に渡すと検索を絞り込める)。`host`: `host.keyword` の値域 (cardinality ~134K、text match `?host=` とペア、再注入は analyzed match)。**高 cardinality のため `host` facet は大きな `facetsSize` (~1000) で叩くと shard 集計コストが重い。default の `facetsSize=100` 推奨、必要なら事前に `keywords` / `organism` 等で検索範囲を絞ること** |
| sra-experiment | `libraryStrategy`, `librarySource`, `librarySelection`, `platform`, `instrumentModel`, `libraryLayout` | 各 `*.keyword` の値域。同名の type-specific filter parameter で検索を絞り込める |
| sra-analysis | `analysisType` | `analysisType.keyword` の値域 |
| gea | `experimentType` | `experimentType.keyword` の値域 |
| metabobank | `studyType`, `experimentType`, `submissionType` | 各 `*.keyword` の値域 |
| jga-study | `studyType`, `vendor` | `studyType.keyword` の値域。`vendor`: `vendor.keyword` の値域 (text match `?vendor=` とペア、再注入は analyzed match) |
| jga-dataset | `datasetType` | `datasetType.keyword` の値域 |

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

`/facets` は flat param (`keywords` / `organism` 等) 専用で、母集団を `status:public` 固定で集計する。DSL `q` で絞った facet 集計 (母集団を検索ヒットと一致させ、ES + Solr 両 backend に対応) が必要な場合は db-portal 系を使う ([db-portal-api-spec.md § facet 集計](db-portal-api-spec.md))。

背景・設計判断の詳細は [overview.md § ファセット default の設計](overview.md) を参照。

#### ファセット bucket 数の指定 (`facetsSize` パラメータ)

各 facet が返す bucket 数の上限はクエリパラメータ `facetsSize` で指定する。型 integer、デフォルト `100`、範囲 `1`–`1000`。`facets` で集計対象に含まれた全 facet 共通の `size` として ES の terms aggregation に渡る (facet ごとの個別指定はできない)。

**適用箇所**: `facets` と同じく `GET /facets`, `GET /facets/{type}`, `GET /entries/*` の `includeFacets=true` 時。`includeFacets=false` (デフォルト) の `GET /entries/*` では指定しても無視される。

**エラー**: 範囲外 (`facetsSize=0` / `facetsSize=1001` 等) や非整数値は **422 Unprocessable Entity**。

`organism` facet の bucket に付く `label` は別の sub-aggregation (`organism.name.keyword` の最頻 1 件) で取得しており、`facetsSize` の影響を受けない (常に 1 件のままで bucket 表示用ラベルとして機能する)。

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

アクセッション完全一致で `suppressed` を解禁する場合、`keywords` の**前方一致 (§ 前方一致) は抑止する** (完全トークン一致のみで検索する)。前方一致を効かせたままだと、解禁した accession (例 `PRJDB1234`) の prefix が別の accession (例 `PRJDB12345`) にも当たり、その `suppressed` エントリーを意図せず露出させうるため。`identifier:` を使った field-scoped 完全一致 (db-portal の `identifier:PRJDB1234`) は元から `term` 完全一致で prefix 展開されないため影響しない。

**404 による存在秘匿**:

`withdrawn` および `private` のエントリーへの直接アクセス (`/entries/{type}/{id}` 系・umbrella-tree seed) は、存在しない ID と同一のレスポンス (`404 Not Found` + `The requested {type} entry was not found.` (アクセッション ID を含めない固定文字列)) を返す。これは status の有無を外部から推測できないようにするため。

**`withdrawn` の実態に関する注釈**:

ddbj-search-converter は livelist の `*.{bioproject,biosample}.ddbj.withdrawn.txt` および DRA / SRA accession テーブルの `Status` カラムから `withdrawn` ステータスを正規化して保持するが、対応する元 XML レコードが converter の入力に含まれないため、`withdrawn` なエントリーは ES に投入されない。結果として `/entries/{type}/{withdrawn_id}` への直接アクセスは「存在しない accession」と区別不能な 404 になる。API 側は `status not in ("public", "suppressed")` を 404 化する防衛ロジックを備えており、可視性マトリクスの `withdrawn` 列はそのロジックを満たす際の振る舞いを示す。

**`status` ファセットの廃止**:

ファセット集計は常に `status:public` に絞り込んでから行うため、`status` ファセットの値は常に `public` のみとなり意味をなさない。このため `Facets` スキーマには `status` フィールドが含まれない。

**dbXrefs と非 public エントリー**:

`public` なエントリーのレスポンスに含まれる `dbXrefs` には、DuckDB の edge をそのまま返す方針のため、`withdrawn` / `private` な accession の ID が含まれることがある。dbXrefs 側での status filter は適用しない (巨大な dbXrefs を持つエントリーに対する ES `_mget` のコストが重いため)。

**DB Portal API (`/db-portal/cross-search`, `/db-portal/search`) の status filter**:

DB Portal API の ES 経由検索 (`/db-portal/cross-search` の 6 ES DB 部分、および `/db-portal/search?db=bioproject|biosample|sra|jga|gea|metabobank`) には `/entries/*` 系と同等の status filter (`withdrawn` / `private` は常に除外、`q` の AST トップが free text 単独もしくは `identifier:` 単一 leaf の eq でアクセッション ID 完全一致のときのみ `suppressed` を許可) を適用する。詳細は [db-portal-api-spec.md § データ可視性 (status 制御)](db-portal-api-spec.md#データ可視性-status-制御) を参照。Solr proxy (`/db-portal/search?db=trad|taxonomy`) は外部 NIG Solr cluster を proxy しており、index に non-public エントリーを含まない前提のため status filter は注入しない。

### 配列フィールド

エントリー詳細系の 3 endpoint (`/entries/{type}/{id}`、`/entries/{type}/{id}.json`、`/entries/{type}/{id}.jsonld`) のレスポンスに含まれる必須配列フィールドは、対象エントリーに該当する値がない場合でも JSON 上の key として常に空配列 `[]` で返却される。これにより SDK 利用者は optional chain なしで `response.dbXrefs.length` のように直接アクセスできる。

エントリー型別の必須配列フィールドの完全な一覧は ddbj-search-converter のスキーマ仕様 ([data-architecture.md § 配列フィールドの契約](https://github.com/ddbj/ddbj-search-converter/blob/main/docs/data-architecture.md#%E9%85%8D%E5%88%97%E3%83%95%E3%82%A3%E3%83%BC%E3%83%AB%E3%83%89%E3%81%AE%E5%A5%91%E7%B4%84)) を参照する。OpenAPI スキーマ上は API 側の以下すべての schema の `required` に列挙される。

- フロントエンド向け Detail: `BioProjectDetailResponse` / `BioSampleDetailResponse` / `SraDetailResponse` / `JgaDetailResponse` / `GeaDetailResponse` / `MetaboBankDetailResponse`
- raw レスポンス: `BioProject` / `BioSample` / `SRA` / `JGA` / `GEA` / `MetaboBank` (`*EntryResponse` は converter スキーマの type alias)
- JSON-LD レスポンス: `BioProjectEntryJsonLdResponse` / `BioSampleEntryJsonLdResponse` / `SraEntryJsonLdResponse` / `JgaEntryJsonLdResponse` / `GeaEntryJsonLdResponse` / `MetaboBankEntryJsonLdResponse`

### dbXrefs

エントリーの `dbXrefs` (データベース間参照) はエントリーによっては非常に大規模になるため、ES ドキュメントには含めず DuckDB (dblink.duckdb) から取得する。

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

- DuckDB ファイル: ddbj-search-converter が deploy 環境に配置する dblink DuckDB を読む (具体的な path は config 経由、運用詳細は [deployment.md](deployment.md))
- テーブル: `dbxref (accession_type, accession, linked_type, linked_accession)`
- DBLink は無向グラフ。converter が各無向 edge `{A, B}` を `(A → B)` と `(B → A)` の 2 行に半辺化して保存する。これにより、いずれの端点からの lookup も `WHERE accession_type = ? AND accession = ?` の point lookup で完結する (UNION ALL 不要)
- 物理 sort: `accession_type, accession, linked_type, linked_accession` + `idx_dbxref_accession (accession_type, accession)` により方向別の性能非対称性が無い

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

指定アクセッションに関連する ID を返す。パスパラメータ・クエリパラメータ・レスポンス schema は Swagger UI (`/search/api/docs`) を参照。本節ではコードで表現しきれない振る舞いだけを残す。

**振る舞い**:

- `dbXrefs` の各要素は converter 由来の `Xref` (identifier, type, url)
- `dbXrefs` は決定論的にソート: タイプ昇順 → アクセッション昇順
- 該当なしの場合: 200 + 空の `dbXrefs: []`
- `target` は AccessionType allowlist 外の値で 422

**ストリーミング**: 関連 ID が非常に大規模になり得るため、DuckDB から chunk 単位で読み出してストリーミングレスポンスで返す。

**エラー**:

| ステータス | 発生条件 |
|-----------|---------|
| 422 | 無効な `{type}` (AccessionType 以外)、無効な `target` 値 (AccessionType 以外) |
| 500 | DuckDB ファイルが見つからない |

#### `POST /dblink/counts`

複数アクセッションの関連 ID タイプ別カウントを一括取得する。フロントエンドで dbXrefs のサマリー表示に使用する。リクエスト・レスポンス schema は Swagger UI (`/search/api/docs`) を参照。

**振る舞い**:

- 不在 accession (DuckDB に見つからない) は `counts == {}` を返す (404 ではない)
- `items` は最小 1 件・最大 100 件 (Pydantic で enforce)
- response の `items` 配列順はリクエスト順と一致する

**エラー**:

| ステータス | 発生条件 |
|-----------|---------|
| 422 | `items` が空、101 件以上、または無効な `type` を含む item (atomic に全体 422) |
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



