# API 仕様書

## 概要

DDBJ Search API は、BioProject / BioSample / SRA / JGA データを検索・取得するための RESTful API。

| 項目 | 値 |
|------|------|
| ベース URL | `/api` (設定で変更可能) |
| 認証 | なし (パブリック API) |
| レスポンス形式 | JSON / JSON-LD / NDJSON |

### OpenAPI ドキュメント

インタラクティブな API ドキュメント (Swagger UI) は `/docs` で確認できる。

| 環境 | URL |
|------|-----|
| Staging | `https://ddbj-staging.nig.ac.jp/search/api/docs` |
| Production | `https://ddbj.nig.ac.jp/search/api/docs` |

### 主要な設計ポイント

- **横断検索とタイプ別検索**: 全 12 タイプを横断検索可能、タイプを絞り込んでの検索も可能
- **JSON-LD 対応**: RDF 対応の JSON-LD 形式でエントリー詳細を取得可能
- **一括取得 (Bulk API)**: 複数 ID を指定して一括取得。JSON Array / NDJSON 形式を選択可能
- **スキーマ定義**: エントリーのスキーマは [ddbj-search-converter](https://github.com/ddbj/ddbj-search-converter) で定義

## エンドポイント一覧

### Entries API (検索系)

| Method | Path | 説明 |
|--------|------|------|
| GET | `/entries/` | 全タイプ横断検索 |
| GET | `/entries/{type}/` | タイプ別検索 |

### Entry Detail API (詳細取得系)

| Method | Path | 説明 |
|--------|------|------|
| GET | `/entries/{type}/{id}` | エントリー詳細取得 (JSON) |
| GET | `/entries/{type}/{id}.json` | エントリー詳細取得 (JSON、互換性) |
| GET | `/entries/{type}/{id}.jsonld` | エントリー詳細取得 (JSON-LD) |

### Bulk API (一括取得系)

| Method | Path | 説明 |
|--------|------|------|
| GET | `/entries/{type}/bulk` | 一括取得 (GET) |
| POST | `/entries/{type}/bulk` | 一括取得 (POST) |

### Count API (件数取得系)

| Method | Path | 説明 |
|--------|------|------|
| GET | `/count/types/` | タイプ別件数取得 |

### Service Info API

| Method | Path | 説明 |
|--------|------|------|
| GET | `/service-info` | サービス情報取得 |

## 使用例 (ユースケース)

### 基本的な検索フロー

```plaintext
1. GET /entries/?keywords=cancer
   -> 全タイプ横断でキーワード検索
   -> pagination.total で総件数を確認

2. GET /count/types/?keywords=cancer (オプション)
   -> タイプ別の件数を確認
   -> どのタイプに多くヒットしているか把握

3. GET /entries/bioproject/?keywords=cancer
   -> BioProject に絞り込んで検索
   -> items[] から目的のエントリーを探す

4. GET /entries/bioproject/PRJNA12345
   -> 詳細情報を取得

5. GET /entries/bioproject/PRJNA12345.jsonld (RDF 利用時)
   -> JSON-LD 形式で取得
```

### 一括取得のユースケース

```plaintext
# 少数 ID の場合: GET で ID をカンマ区切り指定
GET /entries/bioproject/bulk?ids=PRJNA1,PRJNA2,PRJNA3

# 多数 ID の場合: POST でリクエストボディに指定
POST /entries/bioproject/bulk
Content-Type: application/json
{"ids":["PRJNA1","PRJNA2",...]}  # 最大 1000 件

# レスポンス形式の選択
# - format=json (デフォルト): 通常の JSON 配列形式
# - format=ndjson: 1 行 1 エントリーの NDJSON 形式
GET /entries/bioproject/bulk?ids=PRJNA1,PRJNA2&format=ndjson
```

### ページネーションの利用

```plaintext
# 1 ページ目を取得 (デフォルト: page=1, perPage=10)
GET /entries/biosample/?keywords=human

# レスポンスの pagination を確認
{
  "pagination": { "page": 1, "perPage": 10, "total": 1500 },
  "items": [...]
}

# 次のページを取得
GET /entries/biosample/?keywords=human&page=2

# 1 ページあたりの件数を変更 (最大 100)
GET /entries/biosample/?keywords=human&page=1&perPage=50
```

## 共通仕様

### データタイプ (DbType)

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
| `jga-dac` | データアクセス委員会 (アクセス権限管理) |
| `jga-policy` | DAC が設定するデータアクセス条件 |

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

### エラーレスポンス (RFC 7807)

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

### ページネーション

リスト系エンドポイントは `Pagination` オブジェクトを含む。

```json
{
  "page": 1,
  "perPage": 10,
  "total": 10000
}
```

### 日付形式

ISO 8601 形式を使用。

- 単一日付: `2020-01-01`
- 範囲指定: `2020-01-01,2024-12-31` (カンマ区切りで開始日,終了日)

## クエリパラメータリファレンス

### 検索パラメータ (共通)

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|----------|------|
| `keywords` | string | - | フリーテキスト検索。カンマ区切りで複数指定可 (例: `cancer,genome`) |
| `keywords.fields` | string | - | キーワード検索対象フィールド (カンマ区切り、例: `title,description`) |
| `keywords.operator` | enum | - | キーワード結合論理演算子。`AND`: すべて一致 / `OR`: いずれか一致 |
| `organism` | string | - | NCBI Taxonomy ID でフィルタ (例: `9606` = Homo sapiens) |
| `datePublished` | string | - | 公開日範囲 (例: `2020-01-01,2024-12-31`) |
| `dateUpdated` | string | - | 更新日範囲 (例: `2020-01-01,2024-12-31`) |
| `sort` | string | - | ソート順 (例: `datePublished:desc`) |

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
| `page` | integer | `1` | ページ番号 (1 以上) |
| `perPage` | integer | `10` | 1 ページあたりの件数 (1-100) |
| `fields` | string | - | レスポンスに含めるフィールド (カンマ区切り、例: `identifier,title,organism`) |
| `trimProperties` | boolean | `false` | `true` で `properties` フィールドを除外 |
| `types` | string | - | データタイプでフィルタ (カンマ区切り、例: `bioproject,biosample`)。`/entries/` のみ使用可 |

## Entries API 詳細

### GET /entries/

全タイプ横断検索。

**クエリパラメータ**:

検索パラメータ (共通) + レスポンス制御パラメータが使用可能。

**レスポンス例**:

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

**curl 例**:

```bash
# 基本検索
curl "https://ddbj.nig.ac.jp/search/api/entries/"

# キーワード検索
curl "https://ddbj.nig.ac.jp/search/api/entries/?keywords=cancer,genome"

# タイプとページネーション指定
curl "https://ddbj.nig.ac.jp/search/api/entries/?types=bioproject,biosample&page=2&perPage=20"
```

---

### GET /entries/{type}/

タイプ別検索。

**パスパラメータ**:

| パラメータ | 型 | 必須 | 説明 |
|------------|----|----|------|
| `type` | DbType | Yes | データタイプ |

**クエリパラメータ**:

検索パラメータ (共通) + レスポンス制御パラメータが使用可能。BioProject の場合は固有パラメータも使用可。

**curl 例**:

```bash
# BioProject 検索
curl "https://ddbj.nig.ac.jp/search/api/entries/bioproject/"

# BioProject を Umbrella でフィルタ
curl "https://ddbj.nig.ac.jp/search/api/entries/bioproject/?umbrella=TRUE"

# BioSample を Organism でフィルタ
curl "https://ddbj.nig.ac.jp/search/api/entries/biosample/?organism=9606"
```

## Entry Detail API 詳細

### GET /entries/{type}/{id}

エントリー詳細取得 (JSON)。

**パスパラメータ**:

| パラメータ | 型 | 必須 | 説明 |
|------------|----|----|------|
| `type` | DbType | Yes | データタイプ |
| `id` | string | Yes | エントリー ID |

**クエリパラメータ**:

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|----------|------|
| `fields` | string | - | 取得フィールド (カンマ区切り) |
| `trimProperties` | boolean | `false` | properties 除外 |

**レスポンス例**:

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

**curl 例**:

```bash
curl "https://ddbj.nig.ac.jp/search/api/entries/bioproject/PRJNA16"
```

---

### GET /entries/{type}/{id}.json

エントリー詳細取得 (JSON) - 互換性エンドポイント。

`GET /entries/{type}/{id}` と同一の動作。明示的な `.json` 拡張子による互換性エンドポイント。

**curl 例**:

```bash
curl "https://ddbj.nig.ac.jp/search/api/entries/bioproject/PRJNA16.json"
```

---

### GET /entries/{type}/{id}.jsonld

エントリー詳細取得 (JSON-LD)。

**パスパラメータ**:

| パラメータ | 型 | 必須 | 説明 |
|------------|----|----|------|
| `type` | DbType | Yes | データタイプ |
| `id` | string | Yes | エントリー ID |

**クエリパラメータ**:

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|----------|------|
| `fields` | string | - | 取得フィールド (カンマ区切り) |
| `trimProperties` | boolean | `false` | properties 除外 |

**備考**:

- Content-Type: `application/ld+json`
- `@context` と `@id` フィールドが追加される

**レスポンス例**:

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

**curl 例**:

```bash
curl "https://ddbj.nig.ac.jp/search/api/entries/bioproject/PRJNA16.jsonld"
```

## Bulk API 詳細

### GET /entries/{type}/bulk

一括取得 (GET)。

**パスパラメータ**:

| パラメータ | 型 | 必須 | 説明 |
|------------|----|----|------|
| `type` | DbType | Yes | データタイプ |

**クエリパラメータ**:

| パラメータ | 型 | 必須 | デフォルト | 説明 |
|------------|----|----|----------|------|
| `ids` | string | Yes | - | エントリー ID (カンマ区切り) |
| `format` | enum | - | `json` | レスポンス形式。`json`: JSON Array / `ndjson`: NDJSON (JSON Lines) |
| `trimProperties` | boolean | - | `false` | properties 除外 |

**備考**:

- `format=json` (デフォルト): Content-Type は `application/json`、通常の JSON 配列
- `format=ndjson`: Content-Type は `application/x-ndjson`、1 行 1 エントリー

**レスポンス例 (JSON Array)**:

```json
[
  {"identifier":"PRJNA16","type":"bioproject","title":"Project 1"},
  {"identifier":"PRJNA17","type":"bioproject","title":"Project 2"},
  {"identifier":"PRJNA18","type":"bioproject","title":"Project 3"}
]
```

**レスポンス例 (NDJSON)**:

```
{"identifier":"PRJNA16","type":"bioproject","title":"Project 1"}
{"identifier":"PRJNA17","type":"bioproject","title":"Project 2"}
{"identifier":"PRJNA18","type":"bioproject","title":"Project 3"}
```

**curl 例**:

```bash
# JSON Array 形式 (デフォルト)
curl "https://ddbj.nig.ac.jp/search/api/entries/bioproject/bulk?ids=PRJNA16,PRJNA17,PRJNA18"

# NDJSON 形式
curl "https://ddbj.nig.ac.jp/search/api/entries/bioproject/bulk?ids=PRJNA16,PRJNA17,PRJNA18&format=ndjson"
```

---

### POST /entries/{type}/bulk

一括取得 (POST)。

**パスパラメータ**:

| パラメータ | 型 | 必須 | 説明 |
|------------|----|----|------|
| `type` | DbType | Yes | データタイプ |

**クエリパラメータ**:

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|----------|------|
| `format` | enum | `json` | レスポンス形式。`json`: JSON Array / `ndjson`: NDJSON (JSON Lines) |
| `trimProperties` | boolean | `false` | properties 除外 |

**備考**:

- リクエスト Content-Type: `application/json`
- レスポンス Content-Type: `format` パラメータにより `application/json` または `application/x-ndjson`
- 最大 1000 件まで指定可能

**curl 例**:

```bash
# JSON Array 形式 (デフォルト)
curl -X POST "https://ddbj.nig.ac.jp/search/api/entries/bioproject/bulk" \
  -H "Content-Type: application/json" \
  -d '{"ids":["PRJNA16","PRJNA17","PRJNA18"]}'

# NDJSON 形式
curl -X POST "https://ddbj.nig.ac.jp/search/api/entries/bioproject/bulk?format=ndjson" \
  -H "Content-Type: application/json" \
  -d '{"ids":["PRJNA16","PRJNA17","PRJNA18"]}'
```

## Count API 詳細

### GET /count/types/

タイプ別件数取得。

**クエリパラメータ**:

検索パラメータ (共通) が使用可能 (`sort` 以外)。

**レスポンス例**:

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

**curl 例**:

```bash
# 全件数
curl "https://ddbj.nig.ac.jp/search/api/count/types/"

# キーワードでフィルタした件数
curl "https://ddbj.nig.ac.jp/search/api/count/types/?keywords=cancer"
```

## Service Info API 詳細

### GET /service-info

サービス情報取得。

**レスポンス例**:

```json
{
  "app-version": "0.1.0"
}
```

**curl 例**:

```bash
curl "https://ddbj.nig.ac.jp/search/api/service-info"
```

## スキーマ定義

### DbType

データベースタイプの Enum。

```python
class DbType(str, Enum):
    BIOPROJECT = "bioproject"
    BIOSAMPLE = "biosample"
    SRA_SUBMISSION = "sra-submission"
    SRA_STUDY = "sra-study"
    SRA_EXPERIMENT = "sra-experiment"
    SRA_RUN = "sra-run"
    SRA_SAMPLE = "sra-sample"
    SRA_ANALYSIS = "sra-analysis"
    JGA_STUDY = "jga-study"
    JGA_DATASET = "jga-dataset"
    JGA_DAC = "jga-dac"
    JGA_POLICY = "jga-policy"
```

### Pagination

ページネーション情報。

```python
class Pagination(BaseModel):
    page: int       # 現在のページ番号 (1 始まり)
    perPage: int    # 1 ページあたりの件数
    total: int      # 総件数
```

### Organism

生物種情報。

```python
class Organism(BaseModel):
    identifier: str | None = None  # NCBI Taxonomy ID (例: "9606")
    name: str | None = None        # 生物種名 (例: "Homo sapiens")
```

### DbXref

データベース参照。

```python
class DbXref(BaseModel):
    identifier: str           # 参照先 ID (例: "SAMN123")
    type: DbType              # 参照先タイプ (例: "biosample")
    url: str | None = None    # 参照先 URL (例: "/entries/biosample/SAMN123")
```

### ProblemDetails

RFC 7807 形式のエラーレスポンス。

```python
class ProblemDetails(BaseModel):
    type: str = "about:blank"       # 問題タイプ URI
    title: str                      # 問題の短い説明 (例: "Not Found")
    status: int                     # HTTP ステータスコード (例: 404)
    detail: str | None = None       # 詳細な説明
    instance: str | None = None     # 問題が発生したリクエストパス
```

### EntryListItem

検索結果リスト内の各エントリー。

```python
class EntryListItem(BaseModel):
    identifier: str                     # エントリー ID (例: "PRJNA16")
    type: DbType                        # データタイプ (例: "bioproject")
    title: str                          # タイトル
    organism: Organism | None = None    # 生物種情報
    datePublished: str                  # 公開日 (ISO 8601)
    dbXrefs: list[DbXref] | None = None # 関連データベース参照
```

### EntryListResponse

検索結果のレスポンス。

```python
class EntryListResponse(BaseModel):
    pagination: Pagination      # ページネーション情報
    items: list[EntryListItem]  # エントリーリスト
```

### EntryDetail

エントリー詳細のレスポンス。タイプによりフィールドが異なる。

```python
class EntryDetail(BaseModel):
    identifier: str | None = None           # エントリー ID
    type: DbType | None = None              # データタイプ
    title: str | None = None                # タイトル
    description: str | None = None          # 説明
    organism: Organism | None = None        # 生物種情報
    dateCreated: str | None = None          # 作成日 (ISO 8601)
    dateModified: str | None = None         # 更新日 (ISO 8601)
    datePublished: str | None = None        # 公開日 (ISO 8601)
    # ... タイプ固有のフィールドは ddbj-search-converter スキーマを参照
```

### EntryDetailJsonLd

JSON-LD 形式のエントリー詳細。

```python
class EntryDetailJsonLd(EntryDetail):
    context: str = Field(alias="@context")  # JSON-LD コンテキスト URL
    id: str = Field(alias="@id")            # エントリー URI
```

### TypeCounts

タイプ別件数のレスポンス。

```python
class TypeCounts(BaseModel):
    bioproject: int
    biosample: int
    sra_submission: int = Field(alias="sra-submission")
    sra_study: int = Field(alias="sra-study")
    sra_experiment: int = Field(alias="sra-experiment")
    sra_run: int = Field(alias="sra-run")
    sra_sample: int = Field(alias="sra-sample")
    sra_analysis: int = Field(alias="sra-analysis")
    jga_study: int = Field(alias="jga-study")
    jga_dataset: int = Field(alias="jga-dataset")
    jga_dac: int = Field(alias="jga-dac")
    jga_policy: int = Field(alias="jga-policy")
```

### ServiceInfo

サービス情報のレスポンス。

```python
class ServiceInfo(BaseModel):
    app_version: str = Field(alias="app-version")  # アプリケーションバージョン
```

### BulkRequest

POST Bulk のリクエストボディ。

```python
class BulkRequest(BaseModel):
    ids: list[str]  # エントリー ID リスト (最大 1000 件)
```

### EntryDetail のタイプ別フィールド

`EntryDetail` の追加フィールドはデータタイプによって異なる。
詳細は [ddbj-search-converter のスキーマ定義](https://github.com/ddbj/ddbj-search-converter) を参照。

| タイプ | スキーマクラス |
|--------|---------------|
| bioproject | `BioProject` |
| biosample | `BioSample` |
| sra-* | `SRA` |
| jga-* | `JGA` |
