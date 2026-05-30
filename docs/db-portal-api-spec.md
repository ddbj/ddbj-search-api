# DB Portal API 仕様書

[ddbj-search-front の db-portal 画面](https://github.com/ddbj/db-portal) 専用の統合検索 API。`/entries/*` 系の汎用 API ([api-spec.md](api-spec.md)) とは別系統で、UI 向けの `hits` envelope とクエリ言語を提供する。

エンドポイント・パラメータ・レスポンススキーマの raw spec は Swagger UI (`/search/api/docs`) または `/search/api/openapi.json` で確認する。本仕様書はコードや openapi.json では表現しきれないロジック・規約を集める。設計判断の背景は [overview.md](overview.md) を参照。

## 主要機能

- **2 endpoint 構成**: `/db-portal/cross-search` (横断 fan-out、count + 上位ヒット) と `/db-portal/search` (DB 指定 hits) の 2 系統に分離。両者は operation セマンティクスが別物 (横断は 8 DB fan-out + 部分失敗許容 + 全体タイムアウト、DB 指定は単一 backend + 5xx でフェイル + ページネーション可) のため endpoint も分けた。NCBI EUtils の `eGquery` / `esearch?db=...` と同型
- ES 6 DB + Solr 2 DB (`trad` = ARSA 8-shard fan-out、`taxonomy` = TXSearch) に対応
- 横断 fan-out は asyncio で並列実行、per-backend timeout と全体 timeout で早期打切り (部分完了許容)
- クエリは Lark LALR(1) パーサ → allowlist validator → ES/Solr compiler の pipeline で処理 (Solr 用と ES 用で別 compiler)
- フィールド allowlist は 3 段構造: Tier 1 / Tier 2 (横断・単一 DB の両方で使用可、converter 側正規化済の共通 field 中心) / Tier 3 (単一 DB 指定必須、DB 別の特殊 field)
- `DbPortalHit` は `type` discriminator を持つ discriminated union (variant は DB 別の追加フィールドを持つ。`extra="ignore"` で converter 側の新 field は silently drop)
- `GET /db-portal/parse`: クエリを JSON tree に逆変換 (共有 URL からの GUI state 復元用)
- Solr proxy (`db=trad` / `db=taxonomy`) は offset-only (Solr 4.4.0 に PIT 相当なし)、`cursor` 併用は 400 `cursor-not-supported`
- 横断モードで Tier 3 field を使用すると 400 `field-not-available-in-cross-db` (detail に候補 DB を列挙)
- `/db-portal/cross-search` / `/db-portal/search` は `facets` パラメータで `q` 連動の facet 集計 (値 + 件数) をレスポンスに同梱できる。集計母集団は検索ヒットと一致 (同じ compiled query + status filter)。ES 6 DB + Solr 2 DB の両方に対応 ([§ facet 集計](#facet-集計))

## 内部モデル

`/db-portal/*` の handler は `q` クエリをパーサで AST に変換してから backend query にコンパイルする。`/db-portal/cross-search` と `/db-portal/search` は同じパーサ・同じ AST・同じ compiler を共有し、結果を 8 DB fan-out / 単一 DB に分配するだけの違いに留める (両 endpoint の文法が乖離しない設計)。db-portal 側 [`docs/search.md § 検索の内部モデル`](https://github.com/ddbj/db-portal/blob/main/docs/search.md) / [`docs/search-backends.md § クエリ変換`](https://github.com/ddbj/db-portal/blob/main/docs/search-backends.md) と整合する SSOT。

### AST ノードと外向き契約

`q` は AST に変換される。AST のノード種は次の 3 種:

- **FreeText**: フィールド指定なしの全文検索 (bare word / quoted phrase)
- **FieldClause**: `field:value` 形式の leaf
- **BoolOp**: `AND` / `OR` / `NOT` (`(...)` グルーピング含む)

`q` 省略時は AST なし (handler 側で `match_all` (ES) / `*:*` (Solr) と等価に扱う)。

[`/db-portal/parse`](#get-db-portalparse) は AST 構造を JSON tree として返す (レスポンス schema は `DbPortalParseResponse`)。

### FreeText の位置制約

`FreeText` ノードは AST 上で次のいずれかの位置にのみ出現可能 (validator が enforce):

- AST root が `FreeText` 単独
- AST root が `BoolOp(AND, ...)` で、`FreeText` がその直下子に最大 1 つ

`OR` / `NOT` 配下や、ネスト深部 (root 直下でない) AND 配下に `FreeText` が現れると 400 `invalid-freetext-position`。AND 直下に `FreeText` 子が 2 個以上で 400 `duplicate-freetext`。検索意味論として bare text を OR で別条件と並べる意図は曖昧、ネスト AND 配下も UI の表現範囲外、というのが理由。

### FreeText の auto-phrase 処理

`FreeText` の値に含まれる記号 (`-` `/` `.` `+` `:`) を含む **bare** トークンは backend に応じて自動 phrase 化される (例: `HIF-1` → `"HIF-1"` の phrase match)。これは AST 構築時ではなく backend compiler 内で行う (ES は standard analyzer の挙動回避、Solr は edismax メタ文字回避、と backend ごとに事情が異なる)。AST 上は入力文字列を保持する。

**明示的にクオートで囲んだ FreeText** (例: `q='"Homo sapiens"'`) は記号の有無に関わらず常に phrase match (順序保持) として扱う。parser は当該 `FreeText` ノードに phrase フラグを立てて AST に記録し、compiler 段で ES では `multi_match.type=phrase` (= `match_phrase`)、Solr では各 token quote (既存仕様) に展開する。`/db-portal/parse` のレスポンス JSON では `op="free_text"` ノードに `is_phrase: true` / `is_phrase: false` (bare) が常に明示的に付与される。

### FreeText のトークン分割と値内空白の AND 結合

クエリ中で空白区切りに並べた連続 bare word (例 `q=cancer tumor`) は parser が 1 つの `FreeText` 値 (`value="cancer tumor"`) に畳む (`/db-portal/parse` のレスポンスでも単一 `op="free_text"` ノードになる)。明示 `AND` で区切った `cancer AND tumor` は 2 つの `FreeText` になり、[§ FreeText の位置制約](#freetext-の位置制約) の `duplicate-freetext` で 400 になる点に注意 (両方含めたいときは空白区切り or quote phrase を使う)。

`FreeText` の値はまず **カンマ** で複数 token に分割される (引用符内のカンマは保持)。各 token は ES の `multi_match` 1 件に展開される (Solr では `("token1" OR/AND "token2")` の各句にマップ)。

- **トークン間 (カンマ区切り)** の連結演算子は `keywordOperator` パラメータで切替え (default **OR**、AND も指定可)
- **bare token 内 (= クオートなしの 1 multi_match 内) のスペース** は常に **AND 固定** (ES では `multi_match.operator=and` 明示。`q=cancer tumor` は「cancer AND tumor 両方含む」を意味する)
- 値内スペースを OR にしたい場合は **カンマ区切り** にする (`q=cancer,tumor` + `keywordOperator=OR`)
- 値内スペースを「順序固定 phrase」として扱いたい場合は **クオート** (`q="cancer tumor"` で phrase match。AST 上で当該 FreeText に phrase フラグが立ち、ES では `multi_match.type=phrase` に展開される。引用符内のコンマはトークン分割対象から外れ、phrase の一部として保持)

この方針は `/entries/*` 系 (`keywords` パラメータ) と統一されている (両 API で同じ `compile_free_text` 経路)。

DSL 側の default fields は `identifier` / `title` / `name` / `description` / `organism.name` の 5 field (`/entries/*` 系の `keywordFields` 省略時と同じ集合)。db-portal API では `keywordFields` 相当の絞り込みパラメータは公開していない (常に default 5 field で multi_match)。

### status filter (suppressed 解禁) は AST と独立

`status` filter は ES `bool.filter` / Solr `fq` で AST と別レーンに注入する (compiler は AST しか触らない)。accession 完全一致による `suppressed` 解禁の判定ロジック ([§ データ可視性](#データ可視性-status-制御)) は AST 全体を見るが、生成された ES / Solr query への注入は filter 経由。

### `/db-portal/parse` への影響

[`/db-portal/parse`](#get-db-portalparse) は `q` パラメータを受けて AST を JSON tree に逆変換する。`FreeText` も AST の正規ノードとして登場し、レスポンス schema (`DbPortalParseResponse`) の variant に `op="free_text"` を含む。

## `GET /db-portal/cross-search`

8 DB を横断したカウント + 上位ヒット検索。レスポンスは `DbPortalCrossSearchResponse` (常に 8 件、固定順序の `databases` 配列。各要素に count と上位ヒット (`hits`) を nested) のみ。ページネーション概念は持たない (DB 指定の本格検索は `/db-portal/search`)。

| クエリ | 処理 |
|-------|-----|
| `q` 指定 | クエリをパース → validator → ES/Solr にコンパイルして 8 DB 並列発行 (per-backend timeout + 全体 timeout で早期打切り。`trad` は ARSA 8-shard fan-out、`taxonomy` は TXSearch、残り 6 DB は ES)。Tier 1/2 フィールドのみ許容 (Tier 3 は 400 `field-not-available-in-cross-db`) |
| `q` 省略 | 全件 `match_all` 横断カウント |

パラメータルール:

- `q` は省略可。省略時は当該 endpoint の全件カウントになる
- `db` / `cursor` / `page` / `perPage` / `sort` は受け付けない (指定すると 400 `unexpected-parameter`)。横断はページネーションも DB 指定も持たないため、利用者の typo を早期に表面化させる
- `keywordOperator` (`AND` / `OR`、default **`OR`**) は受け付ける (FreeText のカンマ区切り token 連結演算子、[§ クエリ文法](#クエリ文法)参照)
- `facets` / `facetsSize` (任意) を受け付ける ([§ facet 集計](#facet-集計))。横断の facet は organism / accessibility / type のみ
- 横断モードで Tier 3 field を `q` に含めると 400 `field-not-available-in-cross-db` (detail に候補 DB を列挙)

Trailing slash なし (`/db-portal/cross-search`) が canonical。

### クエリパラメータ (`DbPortalCrossSearchQuery`)

パラメータ名・型・デフォルトは Swagger UI (`/search/api/docs`) もしくは [openapi.json](openapi.json) を参照。

- `q`: Tier 1/2 フィールドのみ許容 (Tier 3 は 400 `field-not-available-in-cross-db`)。文法は本ページ「クエリ文法」節
- `topHits`: 値域 0-50。`topHits=0` で count-only モード (各 `databases[i].hits=null`)、`1`-`50` で各 DB から最大 N 件返却。51 以上 / 負数で 422
- `facets`: 集計する facet 名のカンマ区切り (任意、省略時は集計なし)。許容値は organism / accessibility / type のみ。それ以外を要求すると 400 `facet-not-applicable` ([§ facet 集計](#facet-集計))
- `facetsSize`: facet ごとの最大 bucket 数 (任意、1–1000、既定 100)

### レスポンス (`DbPortalCrossSearchResponse`)

```json
{
  "databases": [
    {
      "db": "trad",
      "count": 295259692,
      "error": null,
      "hits": [
        {
          "identifier": "GL589895",
          "type": "trad",
          "url": "https://getentry.ddbj.nig.ac.jp/getentry/na/GL589895/",
          "title": "Mus musculus strain C57BL/6J unplaced genomic scaffold scaffold_765, whole genome shotgun sequence.",
          "description": null,
          "organism": {"identifier": null, "name": "Mus musculus"},
          "status": "public",
          "accessibility": "public-access",
          "dateCreated": null,
          "dateModified": null,
          "datePublished": "2015-03-13",
          "isPartOf": "trad"
        }
      ]
    },
    { "db": "sra",        "count": 1234, "error": null, "hits": [/* ... */] },
    { "db": "bioproject", "count": 567,  "error": null, "hits": [/* ... */] },
    { "db": "biosample",  "count": 890,  "error": null, "hits": [/* ... */] },
    { "db": "jga",        "count": 12,   "error": null, "hits": [/* ... */] },
    { "db": "gea",        "count": 34,   "error": null, "hits": [/* ... */] },
    { "db": "metabobank", "count": 5,    "error": null, "hits": [/* ... */] },
    { "db": "taxonomy",   "count": 12,   "error": null, "hits": [/* ... */] }
  ]
}
```

- `databases` は常に 8 件、順序は固定 (`trad → sra → bioproject → biosample → jga → gea → metabobank → taxonomy`)
- 各要素は `DbPortalCount`: `db` (enum 8 値)、`count` (int | null)、`error` (enum | null)、`hits` (`DbPortalHit[]` | null)
- `count` は `track_total_hits=true` (ES) または Solr の `numFound` (Solr-backed DB) に基づく正確値
- `error` 値: `timeout`, `upstream_5xx`, `connection_refused`, `unknown`
- `hits` 仕様:
  - `topHits=0` のとき `null` (count-only モード)
  - `topHits>=1` で per-DB に最大 `topHits` 件 (relevance 順、`_score` desc + `identifier` asc tiebreaker)
  - `q` を省略した場合 (`match_all`) はすべての `_score` が同点になり、tiebreaker により実質 `identifier` 昇順の最初の N 件になる
  - per-DB 内で `(identifier, type)` の組は unique。subtype 違い (例: 同一 entity が `jga-study` と `jga-dataset` の両方で hit) は別 hit として並びうる
  - `count` は raw 値 (上記 unique 化前の件数) なので、常に `count >= len(hits)` が成立する。極端な重複 (Tier 3 単一 DB 集計の重複源など) があると `count` と `len(hits)` の乖離が顕著になることがある
  - per-DB error 時は `[]` (空配列、`error` と整合)
- 1 つ以上の DB で成功: HTTP 200 (部分失敗許容)
- 全 DB 失敗: HTTP 502 (`about:blank`)
- `facets`: `facets` パラメータ指定時のみ非 null ([§ facet 集計](#facet-集計))。トップレベル 1 セット (per-DB ではない)。ES 6 DB (entries alias) の union 集計のみで organism / accessibility / type に限る (trad / taxonomy は含まれない)。集計リクエストが失敗 / timeout した場合は `facets=null` を返し、count fan-out の結果で 200 を維持する

### `hits` lightweight schema

cross-search の `hits` は `DbPortalLightweightHit` (12 field 固定) で返す。`/db-portal/search` の `DbPortalHit` (8 variant、DB 別追加 field を含む) とは別 schema。横断 UI は「DB 別の上位例」だけを並べる前提なので、`projectType` / `libraryStrategy` / `division` / `rank` 等の DB 別追加 field は cross-search 側のレスポンスには含めない。

| 12 field | 内容 |
|---|---|
| `identifier` | エントリ識別子 |
| `type` | hit 種別 16 値 (`bioproject` / `biosample` / `sra-*` / `jga-*` / `gea` / `metabobank` / `trad` / `taxonomy`) |
| `url` | エントリ canonical URL |
| `title` | タイトル |
| `description` | 説明 |
| `organism` | `{identifier, name}` |
| `status` | `public` / `private` / `suppressed` / `withdrawn` |
| `accessibility` | `public-access` / `controlled-access` |
| `dateCreated`, `dateModified`, `datePublished` | ISO 8601 日付 |
| `isPartOf` | 所属識別子 (例: BioProject なら `"bioproject"`、SRA なら `"sra"`) |

ES 6 DB (`bioproject` / `biosample` / `sra-*` / `jga-*` / `gea` / `metabobank`) は ES index に格納された 12 field をそのまま返す (`/entries/*` と同じ source)。

Solr 2 DB (`trad`, `taxonomy`) は外部 NIG Solr cluster を proxy しており、status / accessibility / 一部日付 / `isPartOf` 相当の field を持たない。下表のとおり実 source を持たない field は固定値 (Solr 側は public 前提) または `null` で埋める。

| field | `trad` (ARSA) | `taxonomy` (TXSearch) |
|---|---|---|
| `identifier` | `PrimaryAccessionNumber` | `tax_id` |
| `type` | 固定 `"trad"` | 固定 `"taxonomy"` |
| `url` | `https://getentry.ddbj.nig.ac.jp/getentry/na/{accession}/` | `https://ddbj.nig.ac.jp/tx_search/{tax_id}?view=info` |
| `title` | `Definition` | `scientific_name` |
| `description` | `null` | `null` |
| `organism` | `Organism` (name) + `Feature` の `db_xref="taxon:..."` (identifier) | `scientific_name` (name) + `tax_id` (identifier) |
| `status` | 固定 `"public"` | 固定 `"public"` |
| `accessibility` | 固定 `"public-access"` | 固定 `"public-access"` |
| `dateCreated` | `null` | `null` |
| `dateModified` | `null` | `null` |
| `datePublished` | `Date` (`YYYYMMDD` → ISO) | `null` |
| `isPartOf` | 固定 `"trad"` | 固定 `"taxonomy"` |

### データ可視性 (status 制御)

ES 6 DB (`bioproject` / `biosample` / `sra-*` / `jga-*` / `gea` / `metabobank`) は ES ドキュメントの `status` フィールド (`public` / `suppressed` / `withdrawn` / `private`) を判定軸として可視性を制御する。仕様は `/entries/*` 系 ([api-spec.md § データ可視性 (status 制御)](api-spec.md#データ可視性-status-制御)) と揃える。

- `withdrawn` / `private` は常に検索結果から除外
- accession 完全一致による `suppressed` 解禁は、パーサが生成した AST ([§ 内部モデル](#内部モデル)) を走査して判定する。以下のいずれかに該当すると `include_suppressed`、それ以外は `public_only` 固定:
  - AST のトップが `FreeText` 単独で、値がアクセッション ID 完全一致
  - AST のトップが `FieldClause(identifier, eq, v)` で `v` がアクセッション ID 完全一致 (ワイルドカード非含有)
  - AST のトップが `BoolOp(AND, ...)` で、**直下** 子のいずれかが上記 2 条件のどちらかを満たす (`q` 内で `FreeText AND field:...` のように書いた場合に相当)
- アクセッション ID の判定は ddbj-search-converter の `ID_PATTERN_MAP` 完全一致を用いる。`/entries/*` 系と同じ判定を共有
- `BoolOp(OR, ...)` / `BoolOp(NOT, ...)` 配下、およびネスト AND の更に下に accession ID が現れても解禁対象外 (誤検出回避)

Solr 2 DB (`trad`, `taxonomy`) は外部 NIG Solr cluster を proxy しており、index に non-public エントリーを含まない前提。status filter は注入せず、レスポンスの `status` / `accessibility` は固定値 `"public"` / `"public-access"` で埋める ([§ `hits` lightweight schema](#hits-lightweight-schema))。

cursor pagination (ES 6 DB) は cursor token に最初の offset リクエスト時点の status filter 込み query を焼き込む方式のため、後続 cursor 継続でも同じ status_mode が引き継がれる。

### タイムアウト挙動

- 8 DB は asyncio で並列 fan-out、全体 timeout 付き wait で集約。順序は task 完了順に依存せず常に上記固定順
- 個別 timeout は各 DB 関数内で適用。超過した DB は `error=timeout` (`hits=[]`) でレスポンスに含まれる。`topHits>=1` でも同じ deadline で運用 (`_source` 絞りで延びが小さい前提)
- 全体 timeout 超過時、未完了の task は cancel され、対象 DB は `error=timeout` で補完される (部分完了分は維持、C2 パターン)
- 呼び出し側は個別/全体どちらで切れたかを区別しない (内訳は X-Request-ID + サーバログで追える)
- per-backend timeout と全体 timeout は env 経由で上書き可能 (具体的な変数名・初期値は `compose.yml` および `env.*` を SSOT とする)

## `GET /db-portal/search`

特定 DB に対する hits envelope 検索。`db` 必須、ES 6 DB と Solr 2 DB のいずれか。

| クエリ | 処理 |
|-------|-----|
| `q` + `db` (ES 対応 6 DB) | DB 指定検索 (`hits` envelope + cursor/offset pagination)。クエリは Lark でパース → validator → ES bool query にコンパイル |
| `q` + `db=trad` / `db=taxonomy` | DB 指定検索 (Solr proxy、offset-only、9 共通フィールド + DB 別 extra で返却)。クエリは edismax `q` 文字列にコンパイル |
| `q` 省略 + `db` | 当該 `db` の全件 `match_all` ヒット |
| `cursor` + `db=trad` / `db=taxonomy` | 400 (`cursor-not-supported` — Solr proxy は cursor 非対応、offset-only) |
| `cursor` + `q` / `sort` / `page>1` (ES 6 DB) | 400 `about:blank` (cursor 排他違反、[§ ページネーション](#ページネーション)参照) |

パラメータルール:

- `db` 必須、未指定で 400 `missing-db`
- `q` は省略可。省略時は当該 `db` の全件ヒット
- `cursor` 指定時の併用制限は本ページ「ページネーション」節を参照

Trailing slash なし (`/db-portal/search`) が canonical。

### クエリパラメータ (`DbPortalSearchQuery`)

パラメータ名・型・デフォルトは Swagger UI (`/search/api/docs`) もしくは [openapi.json](openapi.json) を参照。

- `db` (required): 値は `trad`, `sra`, `bioproject`, `biosample`, `jga`, `gea`, `metabobank`, `taxonomy` のいずれか。未指定で 400 `missing-db`
- `q`: Tier 1/2/3 全フィールド許容。文法は本ページ「クエリ文法」節
- `perPage`: 許容値は `20`, `50`, `100` のみ (他は 422)
- `cursor`: HMAC 署名付き opaque トークン、PIT 5 分。ES 6 DB のみ対応。Solr proxy 2 DB (`db=trad` / `db=taxonomy`) は cursor 非対応: 400 `cursor-not-supported`。ES DB で `q` / `sort` / `page>1` と同時指定した場合は cursor 排他違反: 400 `about:blank` ([§ ページネーション](#ページネーション))
- `sort`: 許容値は `datePublished:desc`, `datePublished:asc`, または省略 (relevance = score desc + identifier tiebreaker)。他値は 422
- `facets`: 集計する facet 名のカンマ区切り (任意、省略時は集計なし)。許容値は `db` の scope に依存する ([§ facet 集計](#facet-集計))。scope 外の facet は 400 `facet-not-applicable`、allowlist 外の名前は 422。cursor 排他の対象外 (cursor と併用可)
- `facetsSize`: facet ごとの最大 bucket 数 (任意、1–1000、既定 100)。cursor 排他の対象外

### レスポンス (`DbPortalHitsResponse`)

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
- `hits`: `DbPortalHit` discriminated union (8 variant、`type` が discriminator) の配列。`extra="ignore"` で converter 側の新 field は silently drop
- `page` / `perPage`: offset mode で指定値。cursor mode では `page` が `null`
- `nextCursor` / `hasNext`: 既存 cursor ページネーションと同じ方式 (HMAC 署名、プロセス再起動で失効)
- `facets`: `facets` パラメータ指定時のみ非 null ([§ facet 集計](#facet-集計))。`DbPortalFacets` (`Facets` 拡張) 型。集計母集団は hits と同一 query (cursor mode では cursor token に焼き込んだ query)

### `DbPortalHit` 8 variant

| variant | `type` 値 | DB 別追加 field |
|---------|-----------|----------------|
| `DbPortalHitBioProject` | `bioproject` | `projectType` (Literal: BioProject / UmbrellaBioProject) / `organization` / `publication` / `grant` / `externalLink` / `relevance` (INSDC enum 配列: Agricultural / Medical / Industrial / Environmental / Evolution / ModelOrganism / Other) |
| `DbPortalHitBioSample` | `biosample` | `organization` / `package` / `model` / `host` / `strain` / `isolate` / `geoLocName` / `collectionDate` |
| `DbPortalHitSra` | `sra-submission` / `sra-study` / `sra-experiment` / `sra-run` / `sra-sample` / `sra-analysis` | `organization` / `publication` / `libraryStrategy` / `librarySource` / `librarySelection` / `libraryLayout` / `platform` / `instrumentModel` / `libraryName` / `libraryConstructionProtocol` / `analysisType` / `geoLocName` / `collectionDate` (subtype により一部 `null`。`libraryName` / `libraryConstructionProtocol` は sra-experiment、`analysisType` は sra-analysis、`geoLocName` / `collectionDate` は sra-sample のみ populate) |
| `DbPortalHitJga` | `jga-study` / `jga-dataset` / `jga-dac` / `jga-policy` | `organization` / `publication` / `grant` / `externalLink` / `studyType` / `datasetType` / `vendor` |
| `DbPortalHitGea` | `gea` | `organization` / `publication` / `experimentType` |
| `DbPortalHitMetabobank` | `metabobank` | `organization` / `publication` / `studyType` / `experimentType` / `submissionType` |
| `DbPortalHitTrad` | `trad` | `division` / `molecularType` / `sequenceLength` |
| `DbPortalHitTaxonomy` | `taxonomy` | `rank` / `commonName` / `japaneseName` / `lineage` |

共通フィールド (全 variant の base `DbPortalHitBase`): `identifier` / `title` / `description` / `organism` / `datePublished` / `dateModified` / `dateCreated` / `url` / `sameAs` / `dbXrefs` / `status` (Literal: public / private / suppressed / withdrawn) / `accessibility` (Literal: public-access / controlled-access)

OpenAPI schema では `DbPortalHit` が `oneOf` 8 member として表現される。db-portal 側は `openapi-typescript` で TypeScript discriminated union に展開可能。

**`dbXrefs` 注意**: DuckDB 注入しない (ES `_source.dbXrefs` があればそのまま返す、無ければ `null`)。UI 向け dbXrefs 統合は将来検討する。

### ページネーション

共通仕様「ページネーション」 ([api-spec.md](api-spec.md)) の cursor 排他ルールを `/db-portal/search` 専用に適用 (`/db-portal/cross-search` にはページネーション概念がない):

- `cursor` 指定時、以下は指定不可 (400): `q`, `sort`, `page` (デフォルト `1` 以外)、`keywordOperator` (デフォルト `OR` 以外)
- `db` と `perPage` は `cursor` と併用可能 (cursor トークンには対象 index 情報が含まれないため、`db` は再指定必須)
- `facets` / `facetsSize` も `cursor` と併用可能 (cursor 排他の対象外)。cursor 継続時の facet 集計は cursor token に焼き込んだ query を母集団にする
- `page * perPage > 10000` は 400 (`/entries/*` 系の deep paging 制限と同じ閾値)

## facet 集計

`/db-portal/cross-search` と `/db-portal/search` は、`q` で絞った検索ヒットと同一母集団の facet 集計 (値 + 件数) をレスポンスに同梱できる。db-portal の結果ページ Sidebar (NCBI 風の「値 + 件数」フィルタ) が候補値と件数を実データから得るための経路 ([db-portal/docs/search.md § Sidebar facet](https://github.com/ddbj/db-portal/blob/main/docs/search.md))。

`/entries/*` 系の `/facets` ([api-spec.md § ファセット](api-spec.md)) とは別系統:

- `/facets` は flat param (`keywords` / `organism` 等) 専用で DSL `q` を受け取れず、母集団を `status:public` 固定で集計する。
- db-portal の facet は DSL `q` で絞り、**母集団を検索ヒットと一致させる** (同じ compiled query + 同じ `status_mode`)。accession 完全一致で `suppressed` が解禁される場合 ([§ データ可視性](#データ可視性-status-制御))、facet 集計にも同じ解禁が反映される (`/facets` のような public_only 固定にしない)。

### リクエストパラメータ

両 endpoint 共通の任意パラメータ (型・既定は openapi.json を参照):

- `facets`: 集計する facet 名のカンマ区切り。**省略時は集計しない** (`facets` が `null`)。空文字も集計なし。`/facets` の「省略時 organism+accessibility」とは挙動が異なる (db-portal は明示 opt-in)。
- `facetsSize`: facet ごとの最大 bucket 数 (1–1000、既定 100)。全 facet に一律適用。`organism` のラベル sub-aggregation は常に size 1 で不変。

`facets` / `facetsSize` は cursor とも併用可 (cursor 排他の対象外)。cursor 継続時は cursor token に焼き込んだ query を母集団に集計する。

### scope 別 facet 集合

API が受け付ける facet は scope (cross / 各 DB) ごとに決まる。allowlist 外の scope で要求すると 400 `facet-not-applicable`、allowlist 自体に無い名前は 422。

| scope | backend | 受け付ける facet |
|---|---|---|
| cross | ES (entries alias) | organism, accessibility, type |
| bioproject | ES | organism, accessibility, objectType, relevance, projectType |
| biosample | ES | organism, accessibility, package, model, host |
| sra | ES | organism, accessibility, libraryStrategy, librarySource, librarySelection, platform, instrumentModel, libraryLayout, analysisType, type |
| jga | ES | organism, accessibility, studyType, datasetType, vendor, type |
| gea | ES | organism, accessibility, experimentType |
| metabobank | ES | organism, accessibility, experimentType, studyType, submissionType |
| trad | Solr (ARSA) | division, molecularType |
| taxonomy | Solr (TXSearch) | rank, kingdom |

ES facet の scope 判定は `_FACET_AGG_SPECS` / `_TYPE_SPECIFIC_FACET_SCOPE` を SSOT とする (db-portal の `db` 値 → ES subtype 集合に展開して照合)。Solr facet (division / molecularType / rank / kingdom) は db-portal 専用の scope 表で trad / taxonomy のみ許容する。

注:

- **cross は organism / accessibility / type のみ** (type-specific facet を要求すると 400 `facet-not-applicable`)。クエリ field の Tier 1/2 制約と同じ思想。cross facet は **ES 6 DB (entries alias) の union 集計のみ**で、trad / taxonomy (Solr) は含まれない。`type` facet は ES subtype 14 値。
- **`type` facet は per-db `sra` / `jga` でも集計可** (`db=sra` → sra-* subtype 別、`db=jga` → jga-* subtype 別)。複数 subtype を跨ぐ DB だけが対象で、単一 subtype の `bioproject` / `biosample` / `gea` / `metabobank` は `facets=type` を要求すると 400 (subtype 分解の意味が無い)。`type` の scope は `_TYPE_SPECIFIC_FACET_SCOPE` 上で sra+jga の subtypes として定義し、cross では `_CROSS_TYPE_ONLY_FACET_NAMES` 経由で従来どおり union を集計する。
- **submitter は facet にしない**。organization.name は高 cardinality のため集計せず、portal 側で text 入力として扱う。
- **taxonomy の organism は facet にしない** (tax_id が doc 同一性で degenerate)。taxonomy facet は rank / kingdom のみ。
- `host` (biosample) は cardinality が高い。API としては許容するが大きい `facetsSize` は避ける (既定 100 推奨)。
- subtype scope (SRA): `db=sra` は subtype 横断のため、`libraryStrategy` 等 (sra-experiment 専属) / `analysisType` (sra-analysis 専属) は該当 subtype を持たない doc から自然に空 bucket で脱落する。

### レスポンス

`DbPortalHitsResponse` (単一 DB) と `DbPortalCrossSearchResponse` (横断) に `facets` フィールドを追加する (optional、既定 `null`)。型は `DbPortalFacets` (`/facets` の `Facets` を継承し、Solr 用に `division` / `molecularType` / `rank` / `kingdom` を追加したもの)。

- 各 facet は「集計対象外 = `null`」「集計したが 0 件 = `[]`」を区別する (`Facets` と同じ規約)。
- `organism` の bucket は `{value, count, label}` (value = NCBI TaxID、label = 学名)。他の facet は `{value, count}`。
- 横断レスポンスの `facets` はトップレベル 1 セット (per-DB ではない)。

### facet 値の DSL 再注入

facet の bucket `value` は DSL `field:value` として再注入できる (portal が選択を絞り込みに反映する経路)。facet 名と DSL field 名が異なるものがある:

| facet 名 | 再注入する DSL field | 備考 |
|---|---|---|
| organism | `organism_id:<TaxID>` | value は TaxID。表示は `label` (学名) |
| objectType | `object_type:<value>` | |
| projectType | `project_type:<value>` | bucket は `projectType.keyword` 完全値。`project_type` は analyzed match なので `project_type:"<value>"` (phrase) 推奨 |
| libraryStrategy ほか enum | `library_strategy:<value>` 等 | enum keyword |
| molecularType | `molecular_type:<value>` | Solr `MolecularType` |
| division / rank / kingdom | `division:<value>` 等 | Solr field 同名 |

(ES facet の再注入規約は [api-spec.md § ファセット](api-spec.md) と共通。)

### 集計の発行と失敗時挙動

- **単一 DB (ES)**: hits 検索と同一の ES リクエストに aggs を相乗りさせる (母集団 = hits と同一 query)。
- **単一 DB (Solr)**: hits 検索と同一の Solr リクエストに `facet=true` + `facet.field` + `facet.mincount=1` + `facet.limit=<facetsSize>` を付与し `facet_counts` をパースする。ARSA は 8 shard 分散集計。
- **横断 (cross)**: 8 DB count fan-out とは別に、entries alias へ size=0 の集計リクエストを 1 本追加発行する (fan-out と同じ compiled ES query + status filter)。この集計が失敗 / timeout した場合は `facets=null` を返し、cross-search 自体は 200 (count fan-out の結果) を維持する。

## クエリ文法

`/db-portal/cross-search?q=...` (横断、Tier 1/2 のみ) と `/db-portal/search?q=...&db=<id>` (DB 指定、Tier 1/2/3) で共通の文法。

- **文法** (Lark LALR(1), Lucene サブセット):
  - bare word / quoted phrase: フィールド指定なしの全文検索。AST 上は `FreeText` ノード。例: `cancer`、`"Homo sapiens"`、`HIF-1` (記号含み bare word も WORD トークンとして通る)
  - `field:value` / `field:"phrase"` / `field:'phrase'` / `field:[a TO b]` / `field:value*` / `field:value?`
  - phrase は double quote と single quote のどちらでも記述可 (対称)。escape は `\"` / `\'` / `\\`
  - `AND` / `OR` / `NOT` (大文字必須)、優先度 `AND > OR`、`(...)` でグルーピング
  - 非対応構文 (boost `^` / fuzzy `~` / 正規表現 `/.../`、bare wildcard `HIF*`) は構文エラー (`unexpected-token`)。bare wildcard が必要な場合は `field:value*` 形式で
  - `field:value*` 形式でも、(a) leading wildcard (`field:*foo` / `field:?abc`)、(b) 単独 wildcard (`field:*`)、(c) wildcard 前の literal が短すぎる (`field:f*`) は拒否 (`invalid-operator-for-field`)。leading wildcard と過度に短い prefix は ES の全件 wildcard スキャンを誘発するため、最低 2 文字の literal prefix を必須にする
  - wildcard 値に使える文字は `[A-Za-z0-9_\-.]` + `*` / `?` のみ。Lucene/Solr の特殊文字 (`\` / `+` / `-` 先頭 / `!` / `|` / `&` / `<` / `>` / `=`) は構文エラー (`unexpected-token`)。バックエンド escape の漏れを root で防ぐ
  - ネスト深さ上限と AST ノード総数上限を超えると `nest-depth-exceeded` (横幅 `a OR b OR ... OR z` で同 slug)、クエリ長さ上限超過は `unexpected-token` (具体的な閾値は config を SSOT とする)
  - **FreeText 位置制約**: bare word / phrase は (a) クエリ全体が単一の FreeText、または (b) トップレベル AND 直下の子に最大 1 つ の位置にのみ書ける。OR / NOT 配下、ネスト AND 配下、AND 直下の重複は禁止 ([§ 内部モデル FreeText の位置制約](#freetext-の位置制約))
  - **FreeText 内部の複数トークン**: 1 つの FreeText 値内でカンマ区切りで複数トークンを指定できる (例: `q=cancer,tumor`)。トークン間の連結演算子はクエリパラメータ `keywordOperator` (`AND` / `OR`、default **`OR`**) で切替える。`/entries/*` 系の `keywordOperator` と同じセマンティクスで揃えてあり、DSL の明示 `AND` / `OR` / `NOT` BoolOp は影響を受けない (DSL 内で書いた `cancer AND title:tumor` は常に AND)。**bare token 内**のスペース (例: `q=cancer tumor` で `cancer tumor` を 1 token として渡した場合) は常に AND 結合 (`multi_match.operator=and` 固定、`keywordOperator` の影響を受けない)。**クオートで囲んだトークン** (例: `q="cancer tumor"` または `q='"Homo sapiens"'` のように FreeText 全体を quote) は 1 つの `match_phrase` (順序保持) に展開され、コンマ分割対象から除外される (引用符内のコンマも phrase の一部として保持)。`keywordOperator` は `/db-portal/cross-search` と `/db-portal/search` で受け付け、`/db-portal/parse` では受け付けない (parse の AST には operator state を含まない)。cursor 指定時に `keywordOperator` をデフォルト以外にすると 400 (排他、[§ ページネーション](#ページネーション))
- **フィールド allowlist (Tier 1/2/3)**: Tier 1 (横断可) / Tier 2 (横断可、converter 側正規化済の共通 field) / Tier 3 (単一 DB 指定必須) の 3 段構造。フィールド一覧は実装 (`allowlist.py`) を SSOT とし、本仕様では各 Tier に属する代表的なフィールドを列挙する。横断 (cross) モードで Tier 3 を使うと 400 `field-not-available-in-cross-db` (detail に候補 DB を列挙)。
  - **Tier 1 (横断可)**:
    - 識別子: `identifier` (`eq` / `wildcard`)
    - テキスト: `title` / `description` / `name` (`contains` / `wildcard` — `name` は ES common の text+keyword、free-text 既定 5 field の 1 つでもあるが field-scoped でも検索可)
    - 生物種 (taxID): `organism_id` (`eq` / `wildcard` — ES `organism.identifier` (keyword) に `term`、数値 taxID exact。Solr TXSearch は `tax_id` に直接マップ、Solr ARSA は対応 field 不在のため degenerate)
    - 生物種 (学名): `organism_name` (`contains` / `wildcard` — ES `organism.name` (text) に `match_phrase`、standard analyzer 経由で大文字小文字の揺れに寛容。Solr ARSA は `(Organism, Lineage)` の OR phrase、TXSearch は `scientific_name` にマップ)
    - 日付: `date_published` / `date_modified` / `date_created` (`eq` / `between`)
    - 日付エイリアス: `date` (ES 側で 3 日付フィールドの OR 展開、ARSA は `Date` に集約、TXSearch は degenerate)
    - アクセシビリティ: `accessibility` (`eq` — enum: `public-access` / `controlled-access`。全 ES backed 6 DB 共通、Solr backed (Trad / Taxonomy) では field 不在のため degenerate)
  - **Tier 2 (横断可、converter 正規化済の共通 field)**:
    - `submitter` (text; ES nested on `organization.name`、ARSA/TXSearch degenerate)
    - `publication` (text; ES nested on `publication.title` を `match_phrase`、ARSA / TXSearch ともに degenerate。`/entries/*` 系の `publication=` パラメータと意味が揃う)
  - **Tier 3 (単一 DB 指定必須)**:
    - BioProject: `object_type` (enum={BioProject, UmbrellaBioProject}、ES top-level keyword `objectType` に `term`。REST API の `?objectTypes=` と同じ field)、`project_type` (text、ES `projectType` text+keyword に `match_phrase`。INSDC controlled vocab: genome / metagenome 等。REST API の `?projectType=` と同じ field。`object_type` とは別 field なので混同注意)、`grant_title` (text、nested `grant → grant.title`; JGA と共通)、`grant_agency` (text、2 段 nested `grant → grant.agency.name`; JGA と共通)、`relevance` (enum, top-level keyword)、`external_link_label` (text, nested `externalLink → externalLink.label`; JGA と共通)
    - BioSample: `host` / `strain` / `isolate` (text)、`geo_loc_name` / `collection_date` (text; SRA-sample と共通)、`package` / `model` (enum、controlled vocab、`package` は ES `package.name.keyword` を見る)、`derived_from_id` (identifier, nested `derivedFrom → derivedFrom.identifier`; SRA-sample と共通)
    - SRA (subtype 別に分散ヒット): `library_strategy` / `library_source` / `library_layout` / `library_selection` / `platform` / `instrument_model` (enum、sra-experiment のみ; controlled vocab を facet bucket の `.keyword` exact と `eq` で揃える)、`library_name` / `library_construction_protocol` (text、sra-experiment のみ)、`analysis_type` (enum、sra-analysis のみ)、`geo_loc_name` / `collection_date` (text、sra-sample のみ; BioSample と共通)、`derived_from_id` (identifier, nested、sra-sample のみ; BioSample と共通)
    - JGA (subtype 別に分散ヒット): `study_type` (enum、jga-study)、`grant_title` (text、jga-study; BioProject と共通)、`grant_agency` (text、jga-study; BioProject と共通)、`vendor` (text、jga-study)、`dataset_type` (enum、jga-dataset)、`external_link_label` (text、jga-study; BioProject と共通)
    - SRA + JGA 共通: `type` (enum、subtype 識別子。SRA scope では `sra-submission` / `sra-study` / `sra-experiment` / `sra-run` / `sra-sample` / `sra-analysis`、JGA scope では `jga-study` / `jga-dataset` / `jga-dac` / `jga-policy`。db-portal の sidebar UI で SRA / JGA の subtype 絞込みに使う。値域 validation は ES 側で実施 (allowlist は値域を持たない)、未知の値は 0 件で返る)
    - GEA: `experiment_type` (enum)
    - MetaboBank: `study_type` / `experiment_type` / `submission_type` (enum)
    - Trad / ARSA: `division` / `molecular_type` (enum)、`sequence_length` (number; range + eq)、`feature_gene_name` / `reference_journal` (text)
    - Taxonomy / TXSearch: `rank` (enum)、`lineage` / `kingdom` / `phylum` / `class` / `order` / `family` / `genus` / `species` / `common_name` (text)。`japanese_name` は staging TXSearch の schema に field 不在のため対応外
  - 許容外フィールドは 400 `unknown-field`、型と演算子の非互換は 400 `invalid-operator-for-field`
- **演算子マトリクス** (型 → 許容演算子):
  - `identifier`: `eq` / `wildcard`
  - `text`: `contains` / `wildcard`
  - `date`: `eq` / `between`
  - `enum`: `eq` (word / phrase、phrase は空白含み値 e.g. `"VIRAL RNA"` 用)
  - `number`: `eq` / `between` (digit のみ、非 digit は `invalid-operator-for-field` に流用)
  - GUI の `not_equals` は `NOT field:value` で表現 (Operator Literal 拡張なし)
  - GUI の `starts_with` は wildcard `value*` で表現
- **バックエンド変換**:
  - ES: フィールド型に応じて flat keyword / OR で複数 keyword field / nested / 2 段 nested の 4 pattern に分岐。`submitter` / `publication` / `grant_title` / `external_link_label` / `derived_from_id` は nested、`grant_agency` は 2 段 nested (`grant.agency.name` を `match_phrase` で叩く)、その他 Tier 3 は flat
  - ARSA: AST → edismax `q` 文字列 (フィールド名マッピング、日付は `YYYYMMDD`、number range はそのまま、対応外 field は `(-*:*)` degenerate、`uf` で allowlist 制御)
  - TXSearch: AST → edismax `q` 文字列 (Tier 1 + Taxonomy Tier 3 のみ対応、他は `(-*:*)` degenerate、`uf` で allowlist 制御)
- **横断モードでの Tier 3 拒否**: 400 `field-not-available-in-cross-db`、detail に候補 DB を列挙 (例: `field 'library_strategy' is only available in single-DB mode at column 1. use db=sra.`)。複数 DB に乗る field は ` or ` で連結 (例: `field 'geo_loc_name' is only available in single-DB mode at column 1. use db=biosample or db=sra.`)
- **エラー位置情報**: `ProblemDetails` スキーマは無変更、`detail` 文字列に自然言語で `at column N (length M)` を埋め込む (機械判別は type URI slug のみ)

例 (bare word / フィールド条件混在):

```
/db-portal/search?db=bioproject&q=cancer+AND+organism_name%3A%22Homo+sapiens%22+AND+date_published%3A%5B2020-01-01+TO+2024-12-31%5D
```

URL デコード後:

```
cancer AND organism_name:"Homo sapiens" AND date_published:[2020-01-01 TO 2024-12-31]
```

例 (フィールド条件のみ、複雑なグルーピング):

```
/db-portal/search?db=bioproject&q=organism_name%3A%22Homo+sapiens%22+AND+date_published%3A%5B2020-01-01+TO+2024-12-31%5D+AND+(title%3Acancer+OR+title%3Atumor)
```

URL デコード後:

```
organism_name:"Homo sapiens" AND date_published:[2020-01-01 TO 2024-12-31] AND (title:cancer OR title:tumor)
```

例 (横断、Tier 1/2 のみ):

```
/db-portal/cross-search?q=organism_name%3A%22Homo+sapiens%22+AND+date%3A%5B2020-01-01+TO+2024-12-31%5D
```

## エラー (type URI + HTTP status)

| type URI (prefix `https://ddbj.nig.ac.jp/problems/` + slug) | HTTP | 条件 | 適用 endpoint | 備考 |
|------|------|------|----------------|------|
| `unexpected-parameter` | 400 | `/db-portal/cross-search` に `db` / `cursor` / `page` / `perPage` / `sort` を指定 | cross-search | detail に余剰パラメータ名を埋め込み |
| `missing-db` | 400 | `/db-portal/search` で `db` 未指定 | search | detail に許容 DB 一覧と「横断検索は `/db-portal/cross-search`」案内を埋め込み |
| `cursor-not-supported` | 400 | `db=trad` / `db=taxonomy` と `cursor` 同時指定 (Solr proxy は cursor 非対応、offset-only) | search | — |
| `unexpected-token` | 400 | 構文エラー (非対応構文 / 過長クエリ / 空入力 含む) | 両 | クエリ |
| `unknown-field` | 400 | allowlist 外フィールド。`detail` に column 位置と候補一覧を埋め込み | 両 | クエリ |
| `field-not-available-in-cross-db` | 400 | 横断モードで Tier 3 フィールド使用。`detail` に候補 DB を列挙 (例: `use db=sra or db=gea`) | cross-search | クエリ |
| `facet-not-applicable` | 400 | 当該 scope で利用できない facet を `facets` に指定 (例: cross で `facets=libraryStrategy`、`db=bioproject` で `facets=package`)。`detail` に対象 facet 名を埋め込み。allowlist 自体に無い名前は 422 (`about:blank`) | 両 | `facets` |
| `invalid-date-format` | 400 | `YYYY-MM-DD` 以外、実在しない日付 | 両 | クエリ |
| `invalid-operator-for-field` | 400 | フィールド型と演算子の非互換 (例: `date:cancer*`, `identifier:[a TO b]`) | 両 | クエリ |
| `nest-depth-exceeded` | 400 | AND/OR/NOT ネスト深さの上限超過、または AST ノード総数の上限超過 (具体的な閾値は config を SSOT とする) | 両 | クエリ |
| `missing-value` | 400 | `field:""` 等の空値 | 両 | クエリ |
| `invalid-freetext-position` | 400 | bare word / phrase が OR / NOT 配下、もしくはネスト深部 AND 配下に出現 ([§ FreeText の位置制約](#freetext-の位置制約)) | 両 | クエリ |
| `duplicate-freetext` | 400 | トップレベル AND 直下に bare word / phrase が 2 つ以上 ([§ FreeText の位置制約](#freetext-の位置制約)) | 両 | クエリ |
| `about:blank` | 400 | Deep paging 超過、cursor 排他違反 (q/sort/page と同時)、不正な cursor、cursor 期限切れ | search | — |
| `about:blank` | 422 | `db` / `sort` / `perPage` 等の enum・Literal 違反、型不一致 | search | — |
| `about:blank` | 502 | 横断 fan-out で全 DB 失敗、Solr DB 指定検索で upstream エラー | 両 | — |

URI prefix `https://ddbj.nig.ac.jp/problems/` は dereferenceable である必要はなく、識別子として機能する (RFC 7807 §3.1)。

## `GET /db-portal/parse`

クエリを SSOT の JSON tree に変換し、GUI state を復元できる形で返す。共有 URL (`?q=...`) を開いたユーザが GUI の条件ツリー / 検索ボックスを再構築するためのサーバ側エントリポイント。クライアント側に独自パーサを持たず、パース結果の構造化 JSON を GUI state に流し込むだけで済むようにする ([db-portal/docs/search.md §GUI ↔ クエリの方向性](https://github.com/ddbj/db-portal/blob/main/docs/search.md))。

内部処理は `/db-portal/cross-search?q=...` / `/db-portal/search?q=...&db=<id>` のクエリ分岐と同一: パース → validate (allowlist + mode + 深さ / 日付 / 値 / FreeText 位置制約) → JSON tree 化。3 endpoint で同じ pipeline を共有し、エラー契約も両 endpoint と同一の 9 slug を共有する。

Trailing slash なし (`/db-portal/parse`) が canonical。

例:

```
/db-portal/parse?q=cancer+AND+date%3A%5B2020-01-01+TO+2024-12-31%5D
```

URL デコード後:

```
cancer AND date:[2020-01-01 TO 2024-12-31]
```

### クエリパラメータ

パラメータ名・型・デフォルトは Swagger UI (`/search/api/docs`) もしくは [openapi.json](openapi.json) を参照。

- `q` (required): `/db-portal/cross-search` / `/db-portal/search` と同一文法。未指定で 422
- `db`: validator mode 切替。省略で横断 (`cross`、Tier 1/2 のみ) / 指定で単一 DB (当該 DB の allowlist、Tier 1/2/3)
- `page` / `perPage` / `cursor` / `sort` は受け取らない (OpenAPI 上に現れず、指定されても無視)

### レスポンス (`DbPortalParseResponse`、`db-portal/docs/search-backends.md §スキーマ仕様` 準拠)

```json
{
  "ast": {
    "op": "AND",
    "rules": [
      { "op": "free_text", "value": "cancer" },
      { "field": "organism_name", "op": "contains", "value": "Homo sapiens" },
      {
        "field": "date",
        "op": "between",
        "from": "2020-01-01",
        "to": "2024-12-31"
      }
    ]
  }
}
```

- ノード判別は `op` (Pydantic v2 discriminated union)。全 8 値 (`AND` / `OR` / `NOT` / `eq` / `contains` / `wildcard` / `between` / `free_text`) が重複なしで単一 discriminator 成立
- BoolOp (`op ∈ {AND, OR, NOT}`): `rules` に子ノード配列 (`NOT` は 1 件のみ)
- FieldClause 値型 (`op ∈ {eq, contains, wildcard}`): `field` + `op` + `value`
- FieldClause 範囲型 (`op = between`): `field` + `op` + `from` + `to` (日付フィールドのみ、Python 予約語回避のため Pydantic 内部は `from_` だが JSON key は `from`)
- FreeText (`op = free_text`): `op` + `value` のみ (フィールド指定なしの全文検索、bare word / phrase から生成)

### エラー

`/db-portal/cross-search` / `/db-portal/search` と同一の 9 slug を共有する (`unexpected-token` / `unknown-field` / `field-not-available-in-cross-db` / `invalid-date-format` / `invalid-operator-for-field` / `nest-depth-exceeded` / `missing-value` / `invalid-freetext-position` / `duplicate-freetext`、すべて 400 + `application/problem+json`)。`field-not-available-in-cross-db` は cross モードで Tier 3 field を使用した場合に発動する (単一 DB 指定必須のため)。`q` 未指定 / `db` 値不正は FastAPI 標準の 422 (`about:blank`)。

## `POST /db-portal/serialize`

`GET /db-portal/parse` の逆経路。AST JSON tree を受け取り、正規化された DSL 文字列を返す。GUI でユーザが advanced builder / sidebar filter で組んだ条件をサーバ側で文字列化し、共有 URL の `?q=<dsl>` に流し込むためのエントリポイント。クライアント側で grammar を再実装する必要がなくなる ([db-portal/docs/search.md §GUI ↔ クエリの方向性](https://github.com/ddbj/db-portal/blob/main/docs/search.md))。

`/parse` との対称性:

```
DSL string ─GET /parse──────> parse(grammar) → AST → JSON tree
JSON tree  ─POST /serialize─> AST → validate  → DSL string
```

GET でなく POST を採る理由は、大きな AST を URL に載せると長さ制限に当たるため。

Trailing slash なし (`/db-portal/serialize`) が canonical。

### リクエスト (`DbPortalSerializeRequest`)

```json
{
  "ast": {
    "op": "AND",
    "rules": [
      { "op": "free_text", "value": "cancer" },
      { "field": "organism_name", "op": "contains", "value": "Homo sapiens" }
    ]
  }
}
```

`ast` フィールドの schema は `GET /db-portal/parse` のレスポンス `ast` フィールド (`DbPortalParseNode`) を再利用する。つまり parse 結果をそのまま serialize に投げ返せる (型重複なし)。

### クエリパラメータ

- `db`: `/db-portal/parse` と同一 semantics の validator mode 切替。省略で横断 (`cross`、Tier 1/2 のみ) / 指定で単一 DB (`single`、当該 DB の Tier 1/2/3 allowlist)。レスポンスには影響しない (DSL 文字列の生成は mode に依存しない)

### レスポンス (`DbPortalSerializeResponse`)

```json
{ "dsl": "cancer AND organism_name:\"Homo sapiens\"" }
```

`dsl` は `GET /db-portal/parse?q=<dsl>` の入力としてそのまま使える正規化済 DSL 文字列。`parse(serialize(ast)) == ast` (Position を除く構造的等価) が保証される。

正規化ルール (`grammar.lark` に従う):

- `value_kind="phrase"` の値は常に `"..."` で quote、内部 `\` / `"` をエスケープ
- `value_kind="word" / "date" / "wildcard"` の値は bare (wildcard を quote すると `*` / `?` が literal 化するため)
- 空白・特殊文字を含む `FreeText` 値は必ず quote (parser が複数 token に分解しないように)
- BoolOp は precedence (AND > OR、NOT 単項) に従い冗長括弧を排除。AND/OR の子 chain は flat (`a AND b AND c`)
- `NOT` の子が `BoolOp` (AND/OR/NOT) のときは括弧化 (grammar `not_op: NOT atom` の制約)

### エラー

| `type` URI suffix | status | 発生条件 |
|---|---|---|
| `invalid-ast` | 400 | request body が `DbPortalParseNode` schema に合わない (`op` 不明、必須 key 欠落、value 型違い 等)。Pydantic の RequestValidationError を 400 + RFC 7807 にラップする |
| `unknown-field` / `field-not-available-in-cross-db` / `invalid-operator-for-field` / `invalid-date-format` / `missing-value` / `nest-depth-exceeded` / `invalid-freetext-position` / `duplicate-freetext` | 400 | AST → validator で reject。`/db-portal/parse` と同一エラー slug を共有 |

`db` query parameter の enum 値違反のみ FastAPI 標準の 422 (`about:blank`) を返す (body 由来ではないため `invalid-ast` 化しない)。

`invalid-ast` の error detail は Pydantic の `loc` を `body.ast.<path>` 形式で連結したもの。元の DSL 文字列を持たないため、parser 系エラーと違い `column` 情報は意味を持たない。
