# API Specification

## 概要

DDBJ Search API は、BioProject / BioSample / SRA / JGA データを検索・取得するための RESTful API。

| 項目 | 値 |
|------|------|
| ベース URL | `/api` (設定で変更可能) |
| 認証 | なし（パブリック API） |
| レスポンス形式 | JSON / JSON-LD / NDJSON |

## データタイプ (DbType)

API で扱うデータベースタイプの一覧。

| タイプ | 説明 |
|--------|------|
| `bioproject` | プロジェクトレベルの情報を管理するフレームワーク |
| `biosample` | 生物学的ソース材料のサンプルメタデータ |
| `sra-submission` | SRA に提出されたメタデータとデータオブジェクトのコレクション |
| `sra-study` | シーケンシング実験の全体的な目標と設計 |
| `sra-experiment` | ライブラリ構築とシーケンシング方法 |
| `sra-run` | 実験で生成された実際のシーケンシングデータ |
| `sra-sample` | 実験で使用された生物学的検体 |
| `sra-analysis` | シーケンシングリードから派生した処理済みデータ |
| `jga-study` | JGA のアクセス制御研究プロジェクト |
| `jga-dataset` | JGA のアクセス制御データファイルのコレクション |
| `jga-dac` | データアクセス委員会（アクセス権限管理） |
| `jga-policy` | DAC が設定するデータアクセス条件 |

## 共通仕様

### Content-Type

| 形式 | Content-Type | 説明 |
|------|--------------|------|
| JSON | `application/json` | 標準レスポンス |
| JSON-LD | `application/ld+json` | RDF 対応レスポンス |
| NDJSON | `application/x-ndjson` | 一括取得時のストリーミングレスポンス |

### CORS 設定

すべてのオリジンからのリクエストを許可。

```
Access-Control-Allow-Origin: *
Access-Control-Allow-Methods: *
Access-Control-Allow-Headers: *
```

### エラーレスポンス (RFC 7807 ProblemDetails)

エラー時は RFC 7807 形式の JSON を返す。

```json
{
  "type": "about:blank",
  "title": "Not Found",
  "status": 404,
  "detail": "The requested BioProject 'INVALID' was not found.",
  "instance": "/entries/bioproject/INVALID"
}
```

| フィールド | 型 | 説明 |
|------------|------|------|
| `type` | string | 問題タイプ URI（通常 `about:blank`） |
| `title` | string | 問題の短い説明 |
| `status` | integer | HTTP ステータスコード |
| `detail` | string? | 詳細な説明 |
| `instance` | string? | 問題が発生したリクエストパス |

### ページネーション

リスト系エンドポイントは `Pagination` オブジェクトを含む。

```json
{
  "page": 1,
  "perPage": 10,
  "total": 10000
}
```

| フィールド | 型 | 説明 |
|------------|------|------|
| `page` | integer | 現在のページ番号（1 始まり） |
| `perPage` | integer | 1 ページあたりの件数 |
| `total` | integer | 総件数 |

### 日付形式

ISO 8601 形式を使用。

- 単一日付: `2020-01-01`
- 範囲指定: `2020-01-01,2024-12-31`（カンマ区切りで開始日,終了日）

## クエリパラメータリファレンス

### 検索パラメータ（共通）

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|----------|------|
| `keywords` | string | - | フリーテキスト検索。カンマ区切りで複数指定可（例: `cancer,genome`） |
| `keywords.fields` | string | - | キーワード検索対象フィールド（カンマ区切り、例: `title,description`） |
| `keywords.operator` | enum | - | キーワード結合論理演算子。`AND`: すべて一致 / `OR`: いずれか一致 |
| `organism` | string | - | NCBI Taxonomy ID でフィルタ（例: `9606` = Homo sapiens） |
| `datePublished` | string | - | 公開日範囲（例: `2020-01-01,2024-12-31`） |
| `dateUpdated` | string | - | 更新日範囲（例: `2020-01-01,2024-12-31`） |
| `sort` | string | - | ソート順（例: `datePublished:desc`） |

### BioProject 固有パラメータ

`/entries/bioproject/` でのみ使用可能。

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|----------|------|
| `organization` | string | - | 組織名でフィルタ |
| `publication` | string | - | 出版物でフィルタ |
| `grant` | string | - | グラントでフィルタ |
| `umbrella` | enum | - | Umbrella BioProject ステータス。`TRUE` / `FALSE` |

### レスポンス制御パラメータ

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|----------|------|
| `page` | integer | `1` | ページ番号（1 以上） |
| `perPage` | integer | `10` | 1 ページあたりの件数（1-100） |
| `fields` | string | - | レスポンスに含めるフィールド（カンマ区切り、例: `identifier,title,organism`） |
| `trimProperties` | boolean | `false` | `true` で `properties` フィールドを除外 |
| `types` | string | - | データタイプでフィルタ（カンマ区切り、例: `bioproject,biosample`）。`/entries/` のみ使用可 |

## エンドポイント

### Entries（検索）

#### GET /entries/

全タイプ横断検索

| 項目 | 値 |
|------|------|
| メソッド | GET |
| パス | `/entries/` |
| レスポンス | `EntryListResponse` |

##### クエリパラメータ

| パラメータ | 型 | 必須 | デフォルト | 説明 |
|------------|----|----|----------|------|
| `keywords` | string | - | - | フリーテキスト検索 |
| `keywords.fields` | string | - | - | キーワード検索対象フィールド |
| `keywords.operator` | enum | - | - | `AND` / `OR` |
| `types` | string | - | - | データタイプでフィルタ（カンマ区切り） |
| `organism` | string | - | - | Taxonomy ID でフィルタ |
| `datePublished` | string | - | - | 公開日範囲 |
| `dateUpdated` | string | - | - | 更新日範囲 |
| `sort` | string | - | - | ソート順 |
| `page` | integer | - | `1` | ページ番号 |
| `perPage` | integer | - | `10` | 1 ページあたりの件数 |
| `fields` | string | - | - | 取得フィールド |
| `trimProperties` | boolean | - | `false` | properties 除外 |

##### レスポンス例

```json
{
  "pagination": {
    "page": 1,
    "perPage": 10,
    "total": 10000
  },
  "items": [
    {
      "identifier": "PRJNA16",
      "type": "bioproject",
      "title": "Cancer Genome Project",
      "organism": {
        "identifier": "9606",
        "name": "Homo sapiens"
      },
      "datePublished": "2013-05-31",
      "dbXrefs": [
        {
          "identifier": "SAMN123",
          "type": "biosample",
          "url": "/entries/biosample/SAMN123"
        }
      ]
    }
  ]
}
```

##### curl 例

```bash
# 基本検索
curl "https://ddbj.nig.ac.jp/search/api/entries/"

# キーワード検索
curl "https://ddbj.nig.ac.jp/search/api/entries/?keywords=cancer,genome"

# タイプとページネーション指定
curl "https://ddbj.nig.ac.jp/search/api/entries/?types=bioproject,biosample&page=2&perPage=20"
```

---

#### GET /entries/{type}/

タイプ別検索

| 項目 | 値 |
|------|------|
| メソッド | GET |
| パス | `/entries/{type}/` |
| レスポンス | `EntryListResponse` |

##### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|------------|----|----|------|
| `type` | DbType | Yes | データタイプ |

##### クエリパラメータ

共通検索パラメータ + レスポンス制御パラメータに加え、BioProject の場合は固有パラメータも使用可。

##### curl 例

```bash
# BioProject 検索
curl "https://ddbj.nig.ac.jp/search/api/entries/bioproject/"

# BioProject を Umbrella でフィルタ
curl "https://ddbj.nig.ac.jp/search/api/entries/bioproject/?umbrella=TRUE"

# BioSample を Organism でフィルタ
curl "https://ddbj.nig.ac.jp/search/api/entries/biosample/?organism=9606"
```

---

### Entry Detail（詳細取得）

#### GET /entries/{type}/{id}

エントリー詳細取得（JSON）

| 項目 | 値 |
|------|------|
| メソッド | GET |
| パス | `/entries/{type}/{id}` |
| レスポンス | `EntryDetail` |

##### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|------------|----|----|------|
| `type` | DbType | Yes | データタイプ |
| `id` | string | Yes | エントリー ID |

##### クエリパラメータ

| パラメータ | 型 | 必須 | デフォルト | 説明 |
|------------|----|----|----------|------|
| `fields` | string | - | - | 取得フィールド（カンマ区切り） |
| `trimProperties` | boolean | - | `false` | properties 除外 |

##### レスポンス例

```json
{
  "identifier": "PRJNA16",
  "type": "bioproject",
  "title": "Cancer Genome Project",
  "description": "A comprehensive study of cancer genomes",
  "organism": {
    "identifier": "9606",
    "name": "Homo sapiens"
  },
  "dateCreated": "2013-01-15",
  "dateModified": "2024-06-01",
  "datePublished": "2013-05-31"
}
```

##### curl 例

```bash
curl "https://ddbj.nig.ac.jp/search/api/entries/bioproject/PRJNA16"
```

---

#### GET /entries/{type}/{id}.json

エントリー詳細取得（JSON）- 互換性エンドポイント

| 項目 | 値 |
|------|------|
| メソッド | GET |
| パス | `/entries/{type}/{id}.json` |
| レスポンス | `EntryDetail` |

`GET /entries/{type}/{id}` と同一の動作。明示的な `.json` 拡張子による互換性エンドポイント。

##### curl 例

```bash
curl "https://ddbj.nig.ac.jp/search/api/entries/bioproject/PRJNA16.json"
```

---

#### GET /entries/{type}/{id}.jsonld

エントリー詳細取得（JSON-LD）

| 項目 | 値 |
|------|------|
| メソッド | GET |
| パス | `/entries/{type}/{id}.jsonld` |
| Content-Type | `application/ld+json` |
| レスポンス | `EntryDetailJsonLd` |

##### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|------------|----|----|------|
| `type` | DbType | Yes | データタイプ |
| `id` | string | Yes | エントリー ID |

##### クエリパラメータ

| パラメータ | 型 | 必須 | デフォルト | 説明 |
|------------|----|----|----------|------|
| `fields` | string | - | - | 取得フィールド（カンマ区切り） |
| `trimProperties` | boolean | - | `false` | properties 除外 |

##### レスポンス例

```json
{
  "@context": "https://raw.githubusercontent.com/ddbj/rdf/main/context/bioproject.jsonld",
  "@id": "https://ddbj.nig.ac.jp/search/entries/bioproject/PRJNA16",
  "identifier": "PRJNA16",
  "type": "bioproject",
  "title": "Cancer Genome Project",
  "description": "A comprehensive study of cancer genomes",
  "organism": {
    "identifier": "9606",
    "name": "Homo sapiens"
  },
  "datePublished": "2013-05-31"
}
```

##### curl 例

```bash
curl "https://ddbj.nig.ac.jp/search/api/entries/bioproject/PRJNA16.jsonld"
```

---

### Bulk（一括取得）【未実装】

#### GET /entries/{type}/bulk

一括取得（GET）

| 項目 | 値 |
|------|------|
| メソッド | GET |
| パス | `/entries/{type}/bulk` |
| Content-Type | `application/x-ndjson` |
| レスポンス | NDJSON（1 行 1 エントリー） |

##### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|------------|----|----|------|
| `type` | DbType | Yes | データタイプ |

##### クエリパラメータ

| パラメータ | 型 | 必須 | デフォルト | 説明 |
|------------|----|----|----------|------|
| `ids` | string | Yes | - | エントリー ID（カンマ区切り） |
| `trimProperties` | boolean | - | `false` | properties 除外 |

##### レスポンス例

```
{"identifier":"PRJNA16","type":"bioproject","title":"Project 1"}
{"identifier":"PRJNA17","type":"bioproject","title":"Project 2"}
{"identifier":"PRJNA18","type":"bioproject","title":"Project 3"}
```

##### curl 例

```bash
curl "https://ddbj.nig.ac.jp/search/api/entries/bioproject/bulk?ids=PRJNA16,PRJNA17,PRJNA18"
```

---

#### POST /entries/{type}/bulk

一括取得（POST）

| 項目 | 値 |
|------|------|
| メソッド | POST |
| パス | `/entries/{type}/bulk` |
| リクエスト Content-Type | `application/json` |
| レスポンス Content-Type | `application/x-ndjson` |
| レスポンス | NDJSON（1 行 1 エントリー） |

##### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|------------|----|----|------|
| `type` | DbType | Yes | データタイプ |

##### クエリパラメータ

| パラメータ | 型 | 必須 | デフォルト | 説明 |
|------------|----|----|----------|------|
| `trimProperties` | boolean | - | `false` | properties 除外 |

##### リクエストボディ

```json
{
  "ids": ["PRJNA16", "PRJNA17", "PRJNA18"]
}
```

| フィールド | 型 | 必須 | 説明 |
|------------|----|----|------|
| `ids` | string[] | Yes | エントリー ID リスト（最大 1000 件） |

##### curl 例

```bash
curl -X POST "https://ddbj.nig.ac.jp/search/api/entries/bioproject/bulk" \
  -H "Content-Type: application/json" \
  -d '{"ids":["PRJNA16","PRJNA17","PRJNA18"]}'
```

---

### Count（件数取得）【未実装】

#### GET /count/types/

タイプ別件数取得

| 項目 | 値 |
|------|------|
| メソッド | GET |
| パス | `/count/types/` |
| レスポンス | `TypeCounts` |

##### クエリパラメータ

| パラメータ | 型 | 必須 | デフォルト | 説明 |
|------------|----|----|----------|------|
| `keywords` | string | - | - | フリーテキスト検索 |
| `keywords.fields` | string | - | - | キーワード検索対象フィールド |
| `keywords.operator` | enum | - | - | `AND` / `OR` |
| `organism` | string | - | - | Taxonomy ID でフィルタ |
| `datePublished` | string | - | - | 公開日範囲 |
| `dateUpdated` | string | - | - | 更新日範囲 |

##### レスポンス例

```json
{
  "bioproject": 500000,
  "biosample": 30000000,
  "sra-submission": 1000000,
  "sra-study": 800000,
  "sra-experiment": 5000000,
  "sra-run": 80000000,
  "sra-sample": 30000000,
  "sra-analysis": 500000,
  "jga-study": 1000,
  "jga-dataset": 2000,
  "jga-dac": 500,
  "jga-policy": 600
}
```

##### curl 例

```bash
# 全件数
curl "https://ddbj.nig.ac.jp/search/api/count/types/"

# キーワードでフィルタした件数
curl "https://ddbj.nig.ac.jp/search/api/count/types/?keywords=cancer"
```

---

### Service Info

#### GET /service-info

サービス情報取得

| 項目 | 値 |
|------|------|
| メソッド | GET |
| パス | `/service-info` |
| レスポンス | `ServiceInfo` |

##### レスポンス例

```json
{
  "app-version": "0.1.0"
}
```

##### curl 例

```bash
curl "https://ddbj.nig.ac.jp/search/api/service-info"
```

## レスポンススキーマ

### EntryListResponse

リスト取得時のレスポンス。

```typescript
{
  pagination: Pagination;
  items: EntryListItem[];
}
```

### EntryListItem

リスト内のエントリー。

| フィールド | 型 | 説明 |
|------------|------|------|
| `identifier` | string | エントリー ID |
| `type` | DbType | データタイプ |
| `title` | string | タイトル |
| `organism` | Organism? | 生物種情報 |
| `datePublished` | string | 公開日 |
| `dbXrefs` | DbXref[]? | 関連データベース参照 |

### EntryDetail

詳細取得時のレスポンス。タイプによりフィールドが異なる。

| フィールド | 型 | 説明 |
|------------|------|------|
| `identifier` | string? | エントリー ID |
| `type` | DbType? | データタイプ |
| `title` | string? | タイトル |
| `description` | string? | 説明 |
| `organism` | Organism? | 生物種情報 |
| `dateCreated` | string? | 作成日 |
| `dateModified` | string? | 更新日 |
| `datePublished` | string? | 公開日 |
| (その他) | any | タイプ固有のフィールド |

### EntryDetailJsonLd

JSON-LD 形式のレスポンス。`EntryDetail` に加え以下のフィールドを含む。

| フィールド | 型 | 説明 |
|------------|------|------|
| `@context` | string | JSON-LD コンテキスト URL |
| `@id` | string | エントリー URI |

### Organism

| フィールド | 型 | 説明 |
|------------|------|------|
| `identifier` | string? | Taxonomy ID |
| `name` | string? | 生物種名 |

### DbXref

| フィールド | 型 | 説明 |
|------------|------|------|
| `identifier` | string | 参照先 ID |
| `type` | DbType | 参照先タイプ |
| `url` | string? | 参照先 URL |

### Pagination

| フィールド | 型 | 説明 |
|------------|------|------|
| `page` | integer | 現在のページ番号（1 始まり） |
| `perPage` | integer | 1 ページあたりの件数 |
| `total` | integer | 総件数 |

### ProblemDetails

RFC 7807 形式のエラーレスポンス。

| フィールド | 型 | 説明 |
|------------|------|------|
| `type` | string | 問題タイプ URI |
| `title` | string | 問題の短い説明 |
| `status` | integer | HTTP ステータスコード |
| `detail` | string? | 詳細な説明 |
| `instance` | string? | 問題が発生したリクエストパス |

### TypeCounts

タイプ別件数。

| フィールド | 型 | 説明 |
|------------|------|------|
| `bioproject` | integer | BioProject 件数 |
| `biosample` | integer | BioSample 件数 |
| `sra-submission` | integer | SRA Submission 件数 |
| `sra-study` | integer | SRA Study 件数 |
| `sra-experiment` | integer | SRA Experiment 件数 |
| `sra-run` | integer | SRA Run 件数 |
| `sra-sample` | integer | SRA Sample 件数 |
| `sra-analysis` | integer | SRA Analysis 件数 |
| `jga-study` | integer | JGA Study 件数 |
| `jga-dataset` | integer | JGA Dataset 件数 |
| `jga-dac` | integer | JGA DAC 件数 |
| `jga-policy` | integer | JGA Policy 件数 |

### ServiceInfo

| フィールド | 型 | 説明 |
|------------|------|------|
| `app-version` | string | アプリケーションバージョン |

### BulkRequest

POST Bulk リクエストボディ。

| フィールド | 型 | 説明 |
|------------|------|------|
| `ids` | string[] | エントリー ID リスト（最大 1000 件） |

## 使用例

### 基本的な検索フロー

```bash
# 1. キーワードで全タイプ横断検索
curl "https://ddbj.nig.ac.jp/search/api/entries/?keywords=cancer"

# 2. 件数を確認
curl "https://ddbj.nig.ac.jp/search/api/count/types/?keywords=cancer"

# 3. BioProject に絞り込み
curl "https://ddbj.nig.ac.jp/search/api/entries/bioproject/?keywords=cancer"

# 4. 詳細を取得
curl "https://ddbj.nig.ac.jp/search/api/entries/bioproject/PRJNA12345"

# 5. JSON-LD 形式で取得（RDF 利用時）
curl "https://ddbj.nig.ac.jp/search/api/entries/bioproject/PRJNA12345.jsonld"
```

### 複数エントリーの一括取得

```bash
# GET（少数の ID）
curl "https://ddbj.nig.ac.jp/search/api/entries/bioproject/bulk?ids=PRJNA1,PRJNA2,PRJNA3"

# POST（多数の ID）
curl -X POST "https://ddbj.nig.ac.jp/search/api/entries/bioproject/bulk" \
  -H "Content-Type: application/json" \
  -d '{"ids":["PRJNA1","PRJNA2","PRJNA3","PRJNA4","PRJNA5"]}'
```

### ページネーションの利用

```bash
# 2 ページ目、1 ページ 50 件
curl "https://ddbj.nig.ac.jp/search/api/entries/biosample/?page=2&perPage=50"
```

### 日付範囲でフィルタ

```bash
# 2023 年に公開されたエントリー
curl "https://ddbj.nig.ac.jp/search/api/entries/?datePublished=2023-01-01,2023-12-31"
```

### 特定フィールドのみ取得

```bash
# identifier と title のみ
curl "https://ddbj.nig.ac.jp/search/api/entries/bioproject/?fields=identifier,title"

# properties を除外（軽量化）
curl "https://ddbj.nig.ac.jp/search/api/entries/bioproject/?trimProperties=true"
```
