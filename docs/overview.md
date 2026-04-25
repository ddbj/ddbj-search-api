# 概要

ddbj-search-api は [DDBJ Search](https://ddbj.nig.ac.jp/search) の RESTful API サーバー。BioProject / BioSample / SRA / JGA など全 12 タイプのデータを検索・取得する。

このドキュメントは「コードを読んでも見えない設計判断の背景」を集めた SSOT。API の振る舞い詳細は [api-spec.md](api-spec.md)、開発・運用は [development.md](development.md) / [deployment.md](deployment.md) を参照。

## 位置付け

DDBJ の 4 つのリポジトリで役割が分かれている。

- [ddbj-search-converter](https://github.com/ddbj/ddbj-search-converter): ES へのデータ投入パイプライン (XML → Pydantic モデル → ES ドキュメント、dbXrefs DuckDB 構築)。スキーマ定義の SSOT
- **ddbj-search-api (本リポジトリ)**: ES と DuckDB を読む REST API サーバー。converter のスキーマを import して使う
- [ddbj-search-front](https://github.com/ddbj/ddbj-search-front): API を叩いて UI を出す
- [ddbj-search](https://github.com/ddbj/ddbj-search): nginx reverse proxy。`/search/api/*` を本 API に、`/search/*` を front に振り分ける

API リポジトリと converter リポジトリを分けているのは、データ投入バッチと API サーバーのライフサイクル・依存関係が大きく違うため (converter は重いライブラリを抱える、API は軽量な FastAPI)。

## システム構成

```
[Internet]
   |
   v
+-------------------+    /search/api/*    +----------------------+
| nginx (gateway)   | ------------------> | ddbj-search-api      |
| reverse proxy     |    /search/*        | (this repo)          |
|                   | ----+               | container            |
+-------------------+     |               +----------+-----------+
                          v                          | reads
                  +---------------+                  v
                  | ddbj-search-  |        +------------------+
                  | front         |        | Elasticsearch    |
                  +---------------+        | (managed by      |
                                           |  converter)      |
                                           +------------------+
```

API サーバーは converter と同一の Docker network (`ddbj-search-network-{env}`) 上で起動し、`ddbj-search-es-{env}` ホスト名で ES に接続する。物理ネットワーク構成は [ddbj-search/docs/network-architecture.md](https://github.com/ddbj/ddbj-search/blob/main/docs/network-architecture.md) を参照。

## ES と DuckDB の役割分担

ES は全文検索・フィルタ・ファセット集計・エントリー本体を担う。`dbXrefs` (関連 ID リスト) は別途 DuckDB に持つ。

理由: 1 エントリーあたり dbXrefs は数千〜数千万件にもなり、ES の nested フィールドで持つとインデックスサイズも検索負荷も悪化する。逆引き (関連 ID → 元エントリー) も必要なため、関連を専用 DuckDB に正規化して持たせている。

エンドポイント別の dbXrefs 扱い・切り詰めポリシー・tail injection の振る舞いは [api-spec.md § dbXrefs](api-spec.md) を参照。

## ddbj-search-converter コードガイド

API 開発時に converter 側のどこを見れば何があるかの索引。同 git 並びで `~/git/github.com/ddbj/ddbj-search-converter` をチェックアウトしておくと便利。

| 知りたいこと | 見るファイル |
|------------|------------|
| ES ドキュメントの構造 (フィールド名、型、必須/任意) | `ddbj_search_converter/schema.py` |
| ES マッピング (text/keyword/nested、検索可能か) | `ddbj_search_converter/es/mappings/{bioproject,biosample,sra,jga,common}.py` |
| インデックス名、エイリアス (`entries`, `sra`, `jga`) | `ddbj_search_converter/es/index.py` |
| データ生成の実装 (XML → Pydantic モデル) | `ddbj_search_converter/jsonl/{bp,bs,sra,jga}.py` |
| dbXrefs の構築 | `ddbj_search_converter/jsonl/utils.py` |
| ES インデックス設定 (analyzer, refresh interval) | `ddbj_search_converter/es/settings.py` |

API 側のテストデータは converter の Pydantic モデル (`BioProject`, `BioSample`, `SRA`, `JGA` 等) を信頼して `hypothesis.strategies.builds()` で生成する。converter のモデルが正しい前提でテストする方針。
