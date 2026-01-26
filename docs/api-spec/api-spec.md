# DDBJ Search API 仕様書

## 概要

DDBJ Search API は、DDBJ（DNA Data Bank of Japan）に登録されている生物学データベースのエントリを検索・取得するための RESTful API です。Elasticsearch をバックエンドとして使用し、BioProject、BioSample、SRA、JGA の各データベースのデータを JSON および JSON-LD 形式で提供します。

### ベース URL

- 本番環境: `https://ddbj.nig.ac.jp/search`
- 開発環境: `http://localhost:8080`

### 認証

認証は不要です。すべてのエンドポイントは公開されています。

### レスポンス形式

- デフォルト: `application/json`
- JSON-LD: `application/ld+json`（`.jsonld` エンドポイント）
- Bulk: `application/x-ndjson`（JSON Lines 形式）

---

## データベースタイプ

API で使用可能なデータベースタイプ（`type`）は以下の通りです。

| タイプ | 説明 | objectType |
|--------|------|------------|
| `bioproject` | BioProject | BioProject / UmbrellaBioProject |
| `biosample` | BioSample | BioSample |
| `sra-submission` | SRA Submission | SraSubmission |
| `sra-study` | SRA Study | SraStudy |
| `sra-experiment` | SRA Experiment | SraExperiment |
| `sra-run` | SRA Run | SraRun |
| `sra-sample` | SRA Sample | SraSample |
| `sra-analysis` | SRA Analysis | SraAnalysis |
| `jga-study` | JGA Study | JgaStudy |
| `jga-dataset` | JGA Dataset | JgaDataset |
| `jga-dac` | JGA DAC | JgaDac |
| `jga-policy` | JGA Policy | JgaPolicy |

---

## エンドポイント一覧

| メソッド | パス | 説明 |
|----------|------|------|
| GET | `/api/entries/` | 全タイプ一覧（検索） |
| GET | `/api/entries/{type}/` | タイプ別一覧 |
| GET | `/api/entries/{type}/{id}` | 詳細（JSON） |
| GET | `/api/entries/{type}/{id}.json` | 詳細（JSON）※互換性 |
| GET | `/api/entries/{type}/{id}.jsonld` | 詳細（JSON-LD） |
| GET/POST | `/api/entries/{type}/bulk` | 一括取得（JSON Lines） |
| GET | `/api/count/types/` | タイプ別件数 |

---

## 共通検索パラメータ

すべての一覧 API（`/api/entries/` および `/api/entries/{type}/`）で使用可能なパラメータです。

### 検索パラメータ

| パラメータ | 型 | デフォルト | 説明 | 例 |
|------------|-----|----------|------|-----|
| `q` | string | - | フリーテキスト検索クエリ | `q=cancer` |
| `q.fields` | string | - | 検索対象フィールド（カンマ区切り） | `q.fields=title,description` |
| `q.operator` | string | `OR` | 複数キーワードの結合方法（AND/OR） | `q.operator=AND` |
| `organism` | string | - | 生物種での絞り込み（Taxonomy ID） | `organism=9606` |
| `datePublished` | string | - | 公開日範囲（開始,終了） | `datePublished=2020-01-01,2021-12-31` |
| `dateUpdated` | string | - | 更新日範囲（開始,終了） | `dateUpdated=2020-01-01,2021-12-31` |

### ページネーションパラメータ

| パラメータ | 型 | デフォルト | 最大値 | 説明 |
|------------|-----|----------|--------|------|
| `page` | integer | 1 | - | ページ番号 |
| `perPage` | integer | 10 | 100 | 1ページあたりの件数 |

### ソートパラメータ

| パラメータ | 型 | デフォルト | 説明 |
|------------|-----|----------|------|
| `sort` | string | - | ソート順（フィールド名:asc/desc） |

**ソート可能フィールド**（複数タイプ検索時）:

- `datePublished` - 公開日
- `dateModified` - 更新日
- `dateCreated` - 作成日
- `title` - タイトル
- `identifier` - 識別子

### その他

| パラメータ | 型 | 説明 | 例 |
|------------|-----|------|-----|
| `types` | string | タイプ絞り込み（カンマ区切り）※全タイプ検索時のみ | `types=bioproject,biosample` |
| `fields` | string | 取得フィールド指定（カンマ区切り） | `fields=identifier,title,organism` |

---

## タイプ固有パラメータ

### BioProject 固有パラメータ

`/api/entries/bioproject/` でのみ使用可能です。

| パラメータ | 型 | 説明 | 例 |
|------------|-----|------|-----|
| `organization` | string | 組織名での絞り込み | `organization=DDBJ` |
| `publication` | string | 出版物での絞り込み | `publication=Nature` |
| `grant` | string | 助成金での絞り込み | `grant=JSPS` |
| `umbrella` | string | UmbrellaBioProject か否か（TRUE/FALSE） | `umbrella=TRUE` |

---

## API 詳細

### GET /api/entries/

全データベースタイプを横断してエントリを検索します。

#### リクエスト例

```
GET /api/entries/?q=cancer&types=bioproject,biosample&page=1&perPage=10
```

#### レスポンス

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
          "url": "/api/entries/biosample/SAMN123"
        }
      ]
    }
  ]
}
```

#### 制約事項

複数タイプをまたぐ検索では以下の制約があります。

- **ソート**: 共通フィールドのみ使用可能
- **検索パラメータ**: 共通パラメータのみ使用可能（タイプ固有パラメータは使用不可）
- **ページネーション**: `from + size <= 10000`（Elasticsearch の制限）

---

### GET /api/entries/{type}/

指定したタイプのエントリ一覧を取得します。

#### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|------------|-----|-----|------|
| `type` | string | Yes | データベースタイプ |

#### リクエスト例

```
GET /api/entries/bioproject/?q=genome&organization=DDBJ&page=1&perPage=20
```

#### レスポンス

`GET /api/entries/` と同様の形式です。

---

### GET /api/entries/{type}/{id}

指定したエントリの詳細情報を JSON 形式で取得します。

#### パスパラメータ

| パラメータ | 型 | 必須 | 説明 |
|------------|-----|-----|------|
| `type` | string | Yes | データベースタイプ |
| `id` | string | Yes | エントリ ID |

#### リクエスト例

```
GET /api/entries/bioproject/PRJNA16
```

#### レスポンス

`ddbj-search-converter` パッケージで定義されたスキーマに従った完全な JSON オブジェクトが返されます。

---

### GET /api/entries/{type}/{id}.json

`GET /api/entries/{type}/{id}` と同じです。後方互換性のために提供されています。

---

### GET /api/entries/{type}/{id}.jsonld

指定したエントリの詳細情報を JSON-LD 形式で取得します。

#### レスポンス

通常の詳細レスポンスに `@context` と `@id` フィールドが追加されます。

```json
{
  "@context": "https://raw.githubusercontent.com/ddbj/rdf/main/context/bioproject.jsonld",
  "@id": "https://ddbj.nig.ac.jp/search/api/entries/bioproject/PRJNA16",
  "identifier": "PRJNA16",
  "type": "bioproject",
  "title": "Cancer Genome Project",
  ...
}
```

---

### GET/POST /api/entries/{type}/bulk

複数のエントリを一括で取得します。

#### リクエスト

**GET メソッド**:

```
GET /api/entries/bioproject/bulk?ids=PRJNA16,PRJNA17,PRJNA18
```

**POST メソッド**:

```
POST /api/entries/bioproject/bulk
Content-Type: application/json

{
  "ids": ["PRJNA16", "PRJNA17", "PRJNA18"]
}
```

#### レスポンス

`Content-Type: application/x-ndjson`（JSON Lines 形式）

```
{"identifier": "PRJNA16", "type": "bioproject", ...}
{"identifier": "PRJNA17", "type": "bioproject", ...}
{"identifier": "PRJNA18", "type": "bioproject", ...}
```

#### 制限

- 最大 1000 件/リクエスト
- 見つからない ID はスキップされます（見つかったエントリのみ返却）

---

### GET /api/count/types/

各データベースタイプのエントリ件数を取得します。

#### クエリパラメータ

検索条件を指定すると、その条件に一致するエントリの件数を返します。

| パラメータ | 型 | 説明 |
|------------|-----|------|
| `q` | string | フリーテキスト検索 |
| `datePublished` | string | 公開日範囲 |
| `dateUpdated` | string | 更新日範囲 |

#### リクエスト例

```
GET /api/count/types/?q=cancer
```

#### レスポンス

```json
{
  "bioproject": 1234,
  "biosample": 5678,
  "sra-submission": 100,
  "sra-study": 200,
  "sra-experiment": 500,
  "sra-run": 800,
  "sra-sample": 600,
  "sra-analysis": 50,
  "jga-study": 10,
  "jga-dataset": 20,
  "jga-dac": 5,
  "jga-policy": 5
}
```

---

## エラーレスポンス

エラー時は RFC 7807（Problem Details for HTTP APIs）形式でレスポンスを返します。

### 形式

```json
{
  "type": "about:blank",
  "title": "Not Found",
  "status": 404,
  "detail": "The requested BioProject 'INVALID' was not found.",
  "instance": "/api/entries/bioproject/INVALID"
}
```

### 主なエラーコード

| ステータスコード | 説明 |
|------------------|------|
| 400 | Bad Request - リクエストパラメータが不正 |
| 404 | Not Found - 指定されたエントリが存在しない |
| 422 | Unprocessable Entity - バリデーションエラー |
| 500 | Internal Server Error - サーバー内部エラー |

---

## 使用例

### Python

```python
import requests

# エントリ一覧を取得
response = requests.get(
    "https://ddbj.nig.ac.jp/search/api/entries/",
    params={"q": "cancer", "types": "bioproject", "perPage": 10}
)
data = response.json()

for item in data["items"]:
    print(f"{item['identifier']}: {item['title']}")
```

### curl

```bash
# BioProject の詳細を取得
curl "https://ddbj.nig.ac.jp/search/api/entries/bioproject/PRJNA16"

# JSON-LD 形式で取得
curl "https://ddbj.nig.ac.jp/search/api/entries/bioproject/PRJNA16.jsonld"

# 一括取得
curl -X POST "https://ddbj.nig.ac.jp/search/api/entries/bioproject/bulk" \
  -H "Content-Type: application/json" \
  -d '{"ids": ["PRJNA16", "PRJNA17"]}'
```

---

## 関連ドキュメント

- [OpenAPI 仕様](./openapi.yaml) - 機械可読な API 定義
- [ddbj-search-converter](https://github.com/ddbj/ddbj-search-converter) - スキーマ定義パッケージ
