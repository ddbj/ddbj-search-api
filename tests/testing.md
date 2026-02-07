# テスト方針

## 目的

テストは**バグを見つけ、防止する**ために書く。
すべてのテストは「このテストが落ちたら、どんなバグが検出されたことになるか？」に答えられなければならない。

## 原則

### TDD

仕様書 (`docs/api-spec.md`) からテストケースを導出し、実装の前にテストを書く。

1. **Red**: 失敗するテストを書く
2. **Green**: テストを通す最小限のコードを書く
3. **Refactor**: テストが通ったまま改善する

### PBT

hypothesis を使い、入力空間を広く自動探索する。

### アンチパターン

- **通すためのテスト**: 実装の鏡像で、実装にバグがあればテストも同じバグを持つ
- **Happy path のみ**: 正常系だけテストして異常系を無視する
- **アサーションなし**: smoke test と称して何も検証しない
- **過剰な mock**: 内部実装の詳細を mock し、リファクタリングで壊れるテスト
- **魔法の定数**: テストデータに意味のない値を使い、なぜその値かが不明

## テスト分類

### Unit テスト (`tests/unit/`)

- **対象**: バリデーション、変換ロジック、クエリ構築、レスポンス整形
- **ES**: mock する (実 ES に接続しない)
- **実行**: `uv run pytest` (デフォルト)

### Integration テスト (`tests/integration/`)

- **対象**: 実 ES に対する検索・取得の E2E 検証
- **ES**: `localhost:9200` の ES (`ddbj-search-es-dev`) を前提
- **実行**: `uv run pytest tests/integration/` (明示指定)

pyproject.toml の `testpaths` を `["tests/unit"]` に設定し、`uv run pytest` では unit テストのみ実行する。

## ディレクトリ構成

`ddbj_search_api/` のディレクトリ構造をミラーする。

```
tests/
├── testing.md
├── unit/
│   ├── conftest.py         # shared fixtures (mock ES client, etc.)
│   ├── strategies.py       # hypothesis custom strategies
│   ├── test_config.py
│   ├── test_main.py
│   ├── test_utils.py
│   ├── schemas/
│   │   ├── test_common.py
│   │   ├── test_entries.py
│   │   ├── test_bulk.py
│   │   └── ...
│   ├── routers/
│   │   ├── test_entries.py
│   │   ├── test_entry_detail.py
│   │   ├── test_bulk.py
│   │   ├── test_facets.py
│   │   ├── test_service_info.py
│   │   └── ...
│   └── es/
│       └── test_client.py
└── integration/
    ├── conftest.py
    └── ...
```

## テストの書き方

### 命名規則

関数: `test_<対象>_<条件>_<期待結果>()`

クラス: 1 ファイル内で関心事ごとに分ける。

- `Test<Feature>`: 基本的な機能テスト
- `Test<Feature>PBT`: Property-based tests
- `Test<Feature>EdgeCases`: 境界値・異常系
- `TestBug<N><Description>`: バグ回帰テスト

### PBT の書き方

`@given` で書く。カスタムストラテジーは `tests/unit/strategies.py` に集約する。
テストデータの自動生成には `hypothesis.strategies.builds()` を使う (`from_type()` は Pydantic v2 との相性が悪い)。

```python
from hypothesis import given
from hypothesis import strategies as st

valid_per_page = st.integers(min_value=1, max_value=100)
invalid_per_page = st.integers().filter(lambda x: x < 1 or x > 100)


@given(per_page=valid_per_page)
def test_with_valid_per_page_accepts(per_page: int):
    query = PaginationQuery(perPage=per_page)
    assert query.per_page == per_page


@given(per_page=invalid_per_page)
def test_with_invalid_per_page_rejects(per_page: int):
    with pytest.raises(ValidationError):
        PaginationQuery(perPage=per_page)
```

### 境界値テスト

PBT と併用し、境界値は明示的にテストする。

| パラメータ | 境界値 |
|-----------|--------|
| `perPage` | 0 (NG), 1 (OK), 100 (OK), 101 (NG) |
| `page` | 0 (NG), 1 (OK) |
| Deep paging | `page=100, perPage=100` (OK: 10000), `page=101, perPage=100` (NG: 10100) |
| `dbXrefsLimit` | -1 (NG), 0 (OK: 空配列), 1000 (OK), 1001 (NG) |
| `BulkRequest.ids` | 空, 1件, 1000件, 1001件 |
| `keywords` | 空文字, 空白のみ, カンマのみ, 非常に長い文字列 |

### Mock 戦略

Mock は**境界**で行う。内部の関数を個別に mock しない。

```
Router (テスト対象)
  └── ES Client (ここを mock)
        └── Elasticsearch (テストでは接続しない)
```

- **mock する**: ES client のレスポンス (検索結果、エラー)
- **mock しない**: Pydantic バリデーション、レスポンス変換ロジック、FastAPI のルーティング

FastAPI の `TestClient` を使い、HTTP リクエスト → レスポンスの全体を検証する。

### テストデータ

- converter の Pydantic モデルを信頼し、`builds()` でテストデータを生成する
- 特定のエッジケース (巨大な dbXrefs、特殊文字を含む ID 等) は明示的に fixture として定義する

## レイヤー別テスト方針

### schemas/

PBT を最も積極的に適用する。

- バリデーションの境界値 (有効/無効の分岐点)
- デフォルト値の正しさ
- カンマ区切り文字列のパース (keywords, keywordFields, types, fields)
- Enum 値の受け入れ/拒否

テストすべき性質:

| 対象 | Property |
|------|----------|
| ページネーション | `page >= 1`, `1 <= perPage <= 100`, `page * perPage > 10000` は 400 |
| dbXrefs 切り詰め | `len(result) <= limit`、`count` は常に正確 |
| fields フィルタ | レスポンスに指定フィールドのみ含まれる |
| sort パース | `{field}:{direction}` のみ有効、それ以外は 422 |
| Bulk API | `len(entries) + len(notFound) == len(set(request.ids))` |

### routers/

TestClient で HTTP レベルの振る舞いをテストする。

- ステータスコード (200, 400, 404, 422, 500)
- レスポンスボディの構造 (必須フィールドの存在)
- Trailing slash の扱い (`/entries` と `/entries/` が同じ結果)

全エンドポイント共通の性質:

| Property | 検証内容 |
|----------|---------|
| RFC 7807 | エラーレスポンスに `type`, `title`, `status`, `detail` が存在 |
| X-Request-ID | 常にレスポンスヘッダーに存在。リクエスト指定時はエコー |
| Content-Type | `.json` → `application/json`、`.jsonld` → `application/ld+json` |
| CORS | `Access-Control-Allow-Origin: *` |

### es/

ES client が構築するクエリの構造をテストする。

- 検索パラメータ → ES クエリ DSL の変換
- フィルタ条件の組み合わせ (keywords + organism + date range)
- ページネーションの from/size 計算
- ファセット集計クエリの構造

### config.py

- 環境変数からの設定読み込み
- デフォルト値

## ddbj-search-converter リファレンス

API は ES を読む側。書く側である [ddbj-search-converter](https://github.com/ddbj/ddbj-search-converter) (`~/git/github.com/ddbj/ddbj-search-converter`) が ES データ構造の唯一の情報源。

converter の Pydantic モデル (`BioProject`, `BioSample`, `SRA`, `JGA` 等) は信頼して使う。API 側のテストではこれらの型が正しい前提でテストデータを生成する。

| 知りたいこと | 見るファイル |
|------------|------------|
| ES ドキュメントの構造 (フィールド名、型、必須/任意) | `ddbj_search_converter/schema.py` |
| ES マッピング (text/keyword/nested、検索可能か) | `ddbj_search_converter/es/mappings/{bioproject,biosample,sra,jga,common}.py` |
| インデックス名、エイリアス (`entries`, `sra`, `jga`) | `ddbj_search_converter/es/index.py` |
| データ生成の実装 (XML → Pydantic モデル) | `ddbj_search_converter/jsonl/{bp,bs,sra,jga}.py` |
| dbXrefs の構築 | `ddbj_search_converter/jsonl/utils.py` |
| ES インデックス設定 | `ddbj_search_converter/es/settings.py` |

## 実行コマンド

```bash
# Unit テストのみ (デフォルト)
uv run pytest

# Integration テスト (実 ES 必要)
uv run pytest tests/integration/

# 全テスト
uv run pytest tests/

# PBT の再現 (失敗シードを指定)
uv run pytest --hypothesis-seed=12345
```
