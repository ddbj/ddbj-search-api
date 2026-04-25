# テスト方針

## 目的

テストは **バグを見つけ、防ぐため** に書く。すべてのテストは「これが落ちたらどんなバグが検出されたことになるか」に答えられなければならない。通すために書くテスト、Happy path だけのテスト、アサーションのない smoke テストは書かない。

## 原則

- **TDD**: 仕様書 ([docs/api-spec.md](../docs/api-spec.md)) からテストを導出し、実装の前に書く。Red → Green → Refactor
- **PBT (Property-Based Testing)**: hypothesis で入力空間を広く探索する。`@given` で書き、カスタムストラテジーは `tests/unit/strategies.py` に集約。テストデータは `hypothesis.strategies.builds()` で組み立てる (Pydantic v2 と相性が良い)
- **境界値・エッジケース・異常系を必ず書く**: 正常系だけでは脆い
- **mock は外部境界だけ**: ES client / DuckDB client のような外部 I/O を境界として mock し、内部 (Pydantic、レスポンス変換、FastAPI ルーティング) は実物を通す
- **テスト間の独立性**: 状態を共有しない、実行順序に依存しない

## テスト分類

2 バケツに分ける。基準は「実 ES に接続するか」。

- **Unit** (`tests/unit/`): 実 ES に接続しない。mock した ES client を `TestClient` 経由で叩く。`pyproject.toml` の `testpaths = ["tests/unit"]` でデフォルト実行対象になる
- **Integration** (`tests/integration/`): 実 ES に接続する。`DDBJ_SEARCH_INTEGRATION_ES_URL` (デフォルト `http://localhost:9200`) で接続先を切替。ES に到達できなければ session 全体を skip

実行コマンドは [docs/development.md § 日常コマンド](../docs/development.md) を参照。

## ディレクトリ構成

`tests/` は `ddbj_search_api/` のディレクトリ構造をミラーする。共通 fixture は `unit/conftest.py` と `integration/conftest.py`、PBT カスタムストラテジーは `unit/strategies.py` に集約。新しいモジュールを作ったら同じ階層にテストファイルを追加する。

## 命名規則

関数: `test_<対象>_<条件>_<期待結果>()`。読むだけで何を検証しているか分かる粒度。

クラスで関心ごとに分ける:

| クラス名 | 用途 |
|---------|------|
| `Test<Feature>` | 基本機能 |
| `Test<Feature>PBT` | Property-based |
| `Test<Feature>EdgeCases` | 境界値・異常系 |
| `TestBug<N><Description>` | バグ回帰 |

## Mock 戦略

外部境界 (ES client、DuckDB client) のレスポンスを mock し、内部実装は実物を通す。

- **mock する**: ES の検索レスポンス・エラー、DuckDB の関数 (`iter_linked_ids`, `get_linked_ids_limited`, `count_linked_ids` など)
- **mock しない**: Pydantic バリデーション、レスポンス変換ロジック、FastAPI ルーティング、`TestClient` の HTTP 経路

router テストでは `TestClient` で HTTP リクエスト → レスポンスの全体を検証する。内部関数を個別に mock しない (リファクタで壊れるため)。

テストデータは converter の Pydantic モデル (`BioProject`, `BioSample`, `SRA`, `JGA`) を信頼して `builds()` で生成する。converter スキーマの位置は [docs/overview.md § ddbj-search-converter コードガイド](../docs/overview.md) を参照。

## レイヤー別観点

テストを書くときの「どこに重点を置くか」の方針。具体的な Property や境界値はテストコード自身が SSOT なので、ここには書かない。

- **schemas/**: PBT を最も活用する。pydantic バリデーションの境界、Enum 受入/拒否、カンマ区切り文字列のパース、デフォルト値
- **routers/**: `TestClient` で HTTP レベル。ステータスコード、レスポンス構造、Trailing slash、`Content-Type`、CORS、`X-Request-ID` echo、RFC 7807 エラー形式
- **es/**: ES クエリ DSL の構造を検証。実 ES に接続せず、構築された dict を比較する
- **dblink/**: 実 DuckDB を `tmp_path` に作って実 SQL で検証する。DuckDB クエリ自体は mock しない (DuckDB の振る舞いが SSOT)
- **solr/**: Solr query string と mapper の出力を検証。実 Solr には接続しない
- **search/dsl/**: lark grammar のパース、ES / Solr へのコンパイラ出力構造、validator の allowlist
- **config.py**: 環境変数からの読み込みとデフォルト値

## Integration テスト

実 ES (場合により Solr) に対する E2E 検証。件数スナップショットは記録せず、構造的不変条件 (set 一致、相対比較、`>= 0`、`detail` 文字列一致) のみを assert する方針。

- シナリオ列挙: [tests/integration-scenarios.md](integration-scenarios.md)
- 環境構築・運用: [tests/integration-note.md](integration-note.md)

## バグ回帰テスト

修正したバグは `TestBug<N><Description>` クラスで再発防止テストを書く。コミットや PR の URL を docstring に残し、なぜそのテストがあるかを後から辿れるようにする。
