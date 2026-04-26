# DB Portal API 仕様書

[ddbj-search-front の db-portal 画面](https://github.com/ddbj/db-portal) 専用の統合検索 API。`/entries/*` 系の汎用 API ([api-spec.md](api-spec.md)) とは別系統で、UI 向けの `hits` envelope と Advanced Search DSL を提供する。

エンドポイント・パラメータ・レスポンススキーマの raw spec は Swagger UI (`/search/api/docs`) または `/search/api/openapi.json` で確認する。本仕様書はコードや openapi.json では表現しきれないロジック・規約を集める。設計判断の背景は [overview.md](overview.md) を参照。

## 主要機能

- **2 endpoint 構成**: `/db-portal/cross-search` (横断 fan-out、count + 上位ヒット) と `/db-portal/search` (DB 指定 hits) の 2 系統に分離。両者は operation セマンティクスが別物 (横断は 8 DB fan-out + 部分失敗許容 + 全体タイムアウト 20s、DB 指定は単一 backend + 5xx でフェイル + ページネーション可) のため endpoint も分けた。NCBI EUtils の `eGquery` / `esearch?db=...` と同型
- ES 6 DB + Solr 2 DB (`trad` = ARSA 8-shard fan-out、`taxonomy` = TXSearch) に対応
- 横断 fan-out は `asyncio.create_task` + `asyncio.wait(ALL_COMPLETED)` で並列実行、per-backend timeout (ES 10s / ARSA 15s / TXSearch 5s) + 全体 20s で早期打切り (部分完了許容)
- Advanced Search DSL (`ddbj_search_api/search/dsl/*`): Lark LALR(1) パーサ → allowlist validator → ES/Solr compiler の pipeline。`grammar` / `ast` / `allowlist` / `errors` / `parser` / `validator` / `compiler_es` / `compiler_solr` / `serde` の 9 module 構成
- フィールド allowlist は 3 段構造: Tier 1 (横断可、8 field) / Tier 2 (横断可、converter 側正規化済の共通 field、2 field) / Tier 3 (単一 DB 指定必須、25 unique / per-DB 集計 28 field)
- `FieldType` は `identifier` / `text` / `organism` / `date` / `enum` / `number` の 6 種、ES 側は `_ES_FIELD_STRATEGY` で `flat` / `or_flat` / `nested` / `nested2` の 4 pattern に分岐
- `DbPortalHit` は `type` discriminator を持つ discriminated union 8 variant (`extra="ignore"` で converter 側の新 field は silently drop)
- `GET /db-portal/parse`: DSL を JSON tree に逆変換 (共有 URL からの GUI state 復元用、`serde.ast_to_json` を endpoint 経由で公開)
- Solr proxy (`db=trad` / `db=taxonomy`) は offset-only (Solr 4.4.0 に PIT 相当なし)、`cursor` 併用は 400 `cursor-not-supported`
- 横断モードで Tier 3 field を使用すると 400 `field-not-available-in-cross-db` (detail に候補 DB を列挙)

## `GET /db-portal/cross-search`

8 DB を横断したカウント + 上位ヒット検索。レスポンスは `DbPortalCrossSearchResponse` (常に 8 件、固定順序の `databases` 配列。各要素に count と上位ヒット (`hits`) を nested) のみ。ページネーション概念は持たない (DB 指定の本格検索は `/db-portal/search`)。

| クエリ | 処理 |
|-------|-----|
| `q` のみ | 横断シンプル検索 (8 DB に並列発行。個別 timeout ES 10s / ARSA 15s / TXSearch 5s、全体 20s で早期打切り。`trad` は ARSA 8-shard fan-out、`taxonomy` は TXSearch、残り 6 DB は ES) |
| `adv` のみ | 横断 Advanced Search (DSL を Lark でパース → validator → ES/Solr にコンパイルして 8 DB 並列発行、Tier 1/2 のみ許容) |

排他ルール:

- `q` / `adv` のいずれか必須、両方指定で 400 `invalid-query-combination`
- `db` / `cursor` / `page` / `perPage` / `sort` は受け付けない (指定すると 400 `unexpected-parameter`)。横断はページネーションも DB 指定も持たないため、利用者の typo を早期に表面化させる
- 横断モードで Tier 3 field を `adv` に含めると 400 `field-not-available-in-cross-db` (detail に候補 DB を列挙)

Trailing slash なし (`/db-portal/cross-search`) が canonical。

### クエリパラメータ (`DbPortalCrossSearchQuery`)

| パラメータ | 型 | デフォルト | 説明 |
|----------|-----|-----------|------|
| `q` | string | — | シンプル検索キーワード。既存 `/entries/` と同じ auto-phrase (記号 `-` `/` `.` `+` `:` 含むと phrase match) が適用される |
| `adv` | string | — | Advanced Search DSL。Tier 1/2 フィールドのみ許容 (Tier 3 は 400 `field-not-available-in-cross-db`)。文法詳細は本ページ「Advanced Search DSL」節 |
| `topHits` | integer | `10` | 各 DB の上位ヒット件数。値域 `0`-`50` (51 以上 / 負数で 422)。`topHits=0` で count-only モード (各 `databases[i].hits=null`)。`topHits=N` (1-50) で各 DB から最大 N 件返却 |

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
  - per-DB error 時は `[]` (空配列、`error` と整合)
- 1 つ以上の DB で成功: HTTP 200 (部分失敗許容)
- 全 DB 失敗: HTTP 502 (`about:blank`)

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
| `url` | `https://getentry.ddbj.nig.ac.jp/getentry/na/{accession}/` | `https://ddbj.nig.ac.jp/resource/taxonomy/{tax_id}` |
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
- `q` (シンプル検索) のキーワードが単一のアクセッション ID と完全一致するとき、対象 DB の `suppressed` を許可。判定ルール (単一トークン、ワイルドカードなし、外側クオート剥がし、ddbj-search-converter の `ID_PATTERN_MAP` 完全一致) は `/entries/*` の判定関数 `detect_accession_exact_match` をそのまま再利用する
- `adv` (Advanced Search DSL) は AST のトップが単一 `FieldClause` (`identifier` フィールド、`op=eq`) かつ value がアクセッション ID 完全一致のときのみ `suppressed` を許可。AND / OR / NOT でラップされたクエリ、ワイルドカード、`identifier` 以外のフィールドはすべて対象外 (`public_only` 固定)

Solr 2 DB (`trad`, `taxonomy`) は外部 NIG Solr cluster を proxy しており、index に non-public エントリーを含まない前提。status filter は注入せず、レスポンスの `status` / `accessibility` は固定値 `"public"` / `"public-access"` で埋める ([§ `hits` lightweight schema](#hits-lightweight-schema))。

cursor pagination (ES 6 DB) は cursor token に最初の offset リクエスト時点の status filter 込み query を焼き込む方式のため、後続 cursor 継続でも同じ status_mode が引き継がれる。

### タイムアウト挙動

- 8 DB は `asyncio.create_task` で並列 fan-out、`asyncio.wait(return_when=ALL_COMPLETED, timeout=20s)` で集約。順序は task 完了順に依存せず常に上記固定順
- 個別 timeout (ES 10s / ARSA 15s / TXSearch 5s) は各 DB 関数内の `asyncio.wait_for` で適用。超過した DB は `error=timeout` (`hits=[]`) でレスポンスに含まれる。`topHits>=1` でも同じ deadline で運用 (`_source` 絞りで delta 数百 ms 程度に収まる前提)
- 全体 timeout (20s) 超過時、未完了の task は cancel され、対象 DB は `error=timeout` で補完される (部分完了分は維持、C2 パターン)
- 呼び出し側は個別/全体どちらで切れたかを区別しない (内訳は X-Request-ID + サーバログで追える)
- 初期値は `AppConfig` の `es_search_timeout` / `arsa_timeout` / `txsearch_timeout` / `cross_search_total_timeout` で env 経由に上書き可能

## `GET /db-portal/search`

特定 DB に対する hits envelope 検索。`db` 必須、ES 6 DB と Solr 2 DB のいずれか。

| クエリ | 処理 |
|-------|-----|
| `q` + `db` (ES 対応 6 DB) | DB 指定シンプル検索 (`hits` envelope + cursor/offset pagination) |
| `q` + `db=trad` / `db=taxonomy` | DB 指定シンプル検索 (Solr proxy、offset-only、9 共通フィールド + DB 別 extra で返却) |
| `adv` + `db` | DB 指定 Advanced Search (DSL を対象バックエンドにコンパイル、hits envelope を返却) |
| `cursor` + `db=trad` / `db=taxonomy` | 400 (`cursor-not-supported` — Solr proxy は offset-only) |
| `cursor` + `adv` | 400 (`cursor-not-supported` — adv は offset-only。`db` の値を問わず常に同じ slug) |

排他ルール:

- `db` 必須、未指定で 400 `missing-db`
- `q` / `adv` のいずれか必須、両方指定で 400 `invalid-query-combination`
- `cursor` 指定時の併用制限は本ページ「ページネーション」節を参照

Trailing slash なし (`/db-portal/search`) が canonical。

### クエリパラメータ (`DbPortalSearchQuery`)

| パラメータ | 型 | デフォルト | 説明 |
|----------|-----|-----------|------|
| `db` | enum | — (required) | 検索対象 DB。値: `trad`, `sra`, `bioproject`, `biosample`, `jga`, `gea`, `metabobank`, `taxonomy`。未指定で 400 `missing-db` |
| `q` | string | — | シンプル検索キーワード。既存 `/entries/` と同じ auto-phrase (記号 `-` `/` `.` `+` `:` 含むと phrase match) が適用される |
| `adv` | string | — | Advanced Search DSL。Tier 1/2/3 全フィールドが許容。文法詳細は本ページ「Advanced Search DSL」節 |
| `page` | integer | `1` | ページ番号 (1 始まり) |
| `perPage` | integer | `20` | 1 ページあたりの件数。許容値: `20`, `50`, `100` のみ (他は 422) |
| `cursor` | string | — | カーソルトークン (HMAC 署名付き、PIT 5 分) |
| `sort` | string | — (relevance) | ソート順。許容値: `datePublished:desc`, `datePublished:asc`, または省略 (relevance = score desc + identifier tiebreaker)。他値は 422 |

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

### `DbPortalHit` 8 variant

| variant | `type` 値 | DB 別追加 field |
|---------|-----------|----------------|
| `DbPortalHitBioProject` | `bioproject` | `projectType` (Literal: BioProject / UmbrellaBioProject) / `organization` / `publication` / `grant` / `externalLink` |
| `DbPortalHitBioSample` | `biosample` | `organization` / `package` / `model` |
| `DbPortalHitSra` | `sra-submission` / `sra-study` / `sra-experiment` / `sra-run` / `sra-sample` / `sra-analysis` | `organization` / `publication` / `libraryStrategy` / `librarySource` / `librarySelection` / `libraryLayout` / `platform` / `instrumentModel` / `analysisType` (subtype により一部 `null`) |
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

- `cursor` 指定時、以下は指定不可 (400): `q`, `adv`, `sort`, `page` (デフォルト `1` 以外)
- `db` と `perPage` は `cursor` と併用可能 (cursor トークンには対象 index 情報が含まれないため、`db` は再指定必須)
- `page * perPage > 10000` は 400 (既存 `_DEEP_PAGING_LIMIT` と同じ)

## Advanced Search DSL

`/db-portal/cross-search?adv=...` (横断、Tier 1/2 のみ) と `/db-portal/search?adv=...&db=<id>` (DB 指定、Tier 1/2/3) で共通の文法。

- **文法** (Lark LALR(1), Lucene サブセット、実装は `ddbj_search_api/search/dsl/grammar.lark`):
  - `field:value` / `field:"phrase"` / `field:[a TO b]` / `field:value*` / `field:value?`
  - `AND` / `OR` / `NOT` (大文字必須)、優先度 `AND > OR`、`(...)` でグルーピング
  - 非対応構文 (boost `^` / fuzzy `~` / 正規表現 `/.../`) は構文エラー (`unexpected-token`)
  - ネスト深さ上限 5 (`dsl_max_depth`)、DSL 長さ上限 4096 文字 (`dsl_max_length`) 超過は `unexpected-token`
- **フィールド allowlist (Tier 1/2/3)**: Tier 1 (横断可、8 field) / Tier 2 (横断可、converter 側正規化済の共通 field、2 field) / Tier 3 (単一 DB 指定必須、25 unique / per-DB 集計 28) の 3 段構造。横断 (cross) モードで Tier 3 を使うと 400 `field-not-available-in-cross-db` (detail に候補 DB を列挙)。
  - **Tier 1 (横断可)**:
    - 識別子: `identifier` (`eq` / `wildcard`)
    - テキスト: `title` / `description` (`contains` / `wildcard`)
    - 生物種: `organism` (`eq` — ES 側で `organism.name` / `organism.identifier` の OR 展開)
    - 日付: `date_published` / `date_modified` / `date_created` (`eq` / `between`)
    - 日付エイリアス: `date` (ES 側で 3 日付フィールドの OR 展開、ARSA は `Date` に集約、TXSearch は degenerate)
  - **Tier 2 (横断可、converter 正規化済の共通 field)**:
    - `submitter` (text; ES nested on `organization.name`、ARSA/TXSearch degenerate)
    - `publication` (identifier; ES nested on `publication.id`、ARSA は `ReferencePubmedID`、TXSearch degenerate)
  - **Tier 3 (単一 DB 指定必須)**:
    - BioProject (2): `project_type` (enum={BioProject, UmbrellaBioProject} → `objectType`)、`grant_agency` (text, 2 段 nested `grant → grant.agency.name`)
    - SRA (5、実質 sra-experiment のみヒット): `library_strategy` / `library_source` / `library_layout` / `platform` (enum)、`instrument_model` (text)
    - JGA (2、実質 jga-study のみヒット): `study_type` (enum)、`grant_agency` (text; BioProject と共通)
    - GEA (1): `experiment_type` (text)
    - MetaboBank (3): `study_type` / `experiment_type` / `submission_type` (text)
    - Trad / ARSA (5): `division` / `molecular_type` (enum)、`sequence_length` (number; range + eq)、`feature_gene_name` / `reference_journal` (text)
    - Taxonomy / TXSearch (10): `rank` (enum)、`lineage` / `kingdom` / `phylum` / `class` / `order` / `family` / `genus` / `species` / `common_name` (text)。`japanese_name` は staging TXSearch の schema に field 不在のため対応外 (TXSearch 側の enrichment 待ち)
  - 許容外フィールドは 400 `unknown-field`、型と演算子の非互換は 400 `invalid-operator-for-field`
- **演算子マトリクス** (型 → 許容演算子):
  - `identifier`: `eq` / `wildcard`
  - `text`: `contains` / `wildcard`
  - `organism`: `eq`
  - `date`: `eq` / `between`
  - `enum`: `eq` (word / phrase、phrase は空白含み値 e.g. `"VIRAL RNA"` 用)
  - `number`: `eq` / `between` (digit のみ、非 digit は `invalid-operator-for-field` に流用)
  - GUI の `not_equals` は `NOT field:value` で表現 (Operator Literal 拡張なし)
  - GUI の `starts_with` は wildcard `value*` で表現
- **バックエンド変換**:
  - ES: `_ES_FIELD_STRATEGY` で `flat` / `or_flat` / `nested` / `nested2` の 4 pattern に分岐。`submitter` / `publication` が nested、`grant_agency` が 2 段 nested (`grant` → `grant.agency` → `match_phrase(grant.agency.name)`)、その他 Tier 3 は flat
  - ARSA: AST → edismax `q` 文字列 (フィールド名マッピング、日付は `YYYYMMDD`、number range はそのまま、対応外 field は `(-*:*)` degenerate、`uf` で allowlist 制御)
  - TXSearch: AST → edismax `q` 文字列 (Tier 1 + Taxonomy Tier 3 のみ対応、他は `(-*:*)` degenerate、`uf` で allowlist 制御)
- **横断モードでの Tier 3 拒否**: 400 `field-not-available-in-cross-db`、detail に候補 DB を列挙 (例: `field 'library_strategy' is only available in single-DB mode at column 1. use db=sra.`)
- **エラー位置情報**: `ProblemDetails` スキーマは無変更、`detail` 文字列に自然言語で `at column N (length M)` を埋め込む (機械判別は type URI slug のみ)

例 (DB 指定 Advanced Search):

```
/db-portal/search?db=bioproject&adv=organism%3A%22Homo+sapiens%22+AND+date_published%3A%5B2020-01-01+TO+2024-12-31%5D+AND+(title%3Acancer+OR+title%3Atumor)
```

URL デコード後:

```
organism:"Homo sapiens" AND date_published:[2020-01-01 TO 2024-12-31] AND (title:cancer OR title:tumor)
```

例 (横断 Advanced Search、Tier 1/2 のみ):

```
/db-portal/cross-search?adv=organism%3A%22Homo+sapiens%22+AND+date%3A%5B2020-01-01+TO+2024-12-31%5D
```

## エラー (type URI + HTTP status)

| type URI (prefix `https://ddbj.nig.ac.jp/problems/` + slug) | HTTP | 条件 | 適用 endpoint | 備考 |
|------|------|------|----------------|------|
| `invalid-query-combination` | 400 | `q` と `adv` 同時指定 | 両 | — |
| `unexpected-parameter` | 400 | `/db-portal/cross-search` に `db` / `cursor` / `page` / `perPage` / `sort` を指定 | cross-search | detail に余剰パラメータ名を埋め込み |
| `missing-db` | 400 | `/db-portal/search` で `db` 未指定 | search | detail に許容 DB 一覧と「横断検索は `/db-portal/cross-search`」案内を埋め込み |
| `advanced-search-not-implemented` | — | (未使用) | — | DSL 実装後は emit されない (enum は backward compat のため残置) |
| `cursor-not-supported` | 400 | `db=trad` / `db=taxonomy` と `cursor` 同時指定 (Solr proxy は offset-only)。`adv` + `cursor` も `db` の値を問わず常にこの slug | search | — |
| `unexpected-token` | 400 | DSL 構文エラー (非対応構文 / 過長 DSL / 空入力 含む) | 両 | DSL |
| `unknown-field` | 400 | allowlist 外フィールド。`detail` に column 位置と候補一覧を埋め込み | 両 | DSL |
| `field-not-available-in-cross-db` | 400 | 横断モードで Tier 3 フィールド使用。`detail` に候補 DB を列挙 (例: `use db=sra or db=gea`) | cross-search | DSL |
| `invalid-date-format` | 400 | `YYYY-MM-DD` 以外、実在しない日付 | 両 | DSL |
| `invalid-operator-for-field` | 400 | フィールド型と演算子の非互換 (例: `date:cancer*`, `identifier:[a TO b]`) | 両 | DSL |
| `nest-depth-exceeded` | 400 | AND/OR/NOT ネスト深さ > 5 (`dsl_max_depth`) | 両 | DSL |
| `missing-value` | 400 | `field:""` 等の空値 | 両 | DSL |
| `about:blank` | 400 | Deep paging 超過、cursor 排他違反 (adv/q/sort/page と同時)、不正な cursor、cursor 期限切れ | search | — |
| `about:blank` | 422 | `db` / `sort` / `perPage` 等の enum・Literal 違反、型不一致 | search | — |
| `about:blank` | 502 | 横断 fan-out で全 DB 失敗、Solr DB 指定検索で upstream エラー | 両 | — |

URI prefix `https://ddbj.nig.ac.jp/problems/` は dereferenceable である必要はなく、識別子として機能する (RFC 7807 §3.1)。DSL 関連 7 slug は DSL 実装時に enum へ追加済。`advanced-search-not-implemented` は router からは emit されなくなったが、OpenAPI 契約の互換性のため enum に残置している (将来の cleanup PR で物理削除予定)。

## `GET /db-portal/parse`

Advanced Search DSL を SSOT の JSON tree に変換し、GUI state を復元できる形で返す。共有 URL (`?adv=...`) を開いたユーザが Advanced Search GUI の条件ツリーを再構築するためのサーバ側エントリポイント。クライアント側に独自パーサを持たず、パース結果の構造化 JSON を GUI state に流し込むだけで済むようにする ([db-portal/docs/search.md §GUI ↔ DSL の方向性](https://github.com/ddbj/db-portal/blob/main/docs/search.md))。

内部処理は `/db-portal/cross-search?adv=...` / `/db-portal/search?adv=...&db=<id>` の DSL 分岐と同一: `parse` (Lark LALR(1)) → `validate` (allowlist + mode + 深さ / 日付 / 値) → `ast_to_json` で JSON tree 化。既存 DSL 実装 (`ddbj_search_api/search/dsl/*`) を完全再利用し、エラー契約は DSL 関連 7 slug をそのまま共有する (新 slug 追加なし)。

Trailing slash なし (`/db-portal/parse`) が canonical。

例:

```
/db-portal/parse?adv=title%3Acancer+AND+date%3A%5B2020-01-01+TO+2024-12-31%5D
```

URL デコード後:

```
title:cancer AND date:[2020-01-01 TO 2024-12-31]
```

### クエリパラメータ

| パラメータ | 型 | デフォルト | 説明 |
|----------|-----|-----------|------|
| `adv` | string (required) | — | Advanced Search DSL。`/db-portal/cross-search` / `/db-portal/search` の `adv` と同一文法。未指定時は 422 |
| `db` | enum | — | validator mode 切替。省略 → 横断 (`cross`, Tier 1/2 のみ) / 指定 → `single` (当該 DB の allowlist)。値は `DbPortalDb` (`trad` / `sra` / `bioproject` / `biosample` / `jga` / `gea` / `metabobank` / `taxonomy`) |

`q` / `page` / `perPage` / `cursor` / `sort` は受け取らない (OpenAPI 上に現れず、指定されても無視)。

### レスポンス (`DbPortalParseResponse`、`db-portal/docs/search-backends.md §スキーマ仕様` 準拠)

```json
{
  "ast": {
    "op": "AND",
    "rules": [
      { "field": "organism", "op": "eq", "value": "Homo sapiens" },
      {
        "field": "date",
        "op": "between",
        "from": "2020-01-01",
        "to": "2024-12-31"
      },
      {
        "op": "OR",
        "rules": [
          { "field": "title", "op": "contains", "value": "cancer" },
          { "field": "title", "op": "contains", "value": "tumor" }
        ]
      }
    ]
  }
}
```

- ノード判別は `op` (Pydantic v2 discriminated union)。全 7 値 (`AND` / `OR` / `NOT` / `eq` / `contains` / `wildcard` / `between`) が重複なしで単一 discriminator 成立
- BoolOp (`op ∈ {AND, OR, NOT}`): `rules` に子ノード配列 (`NOT` は 1 件のみ)
- FieldClause 値型 (`op ∈ {eq, contains, wildcard}`): `field` + `op` + `value`
- FieldClause 範囲型 (`op = between`): `field` + `op` + `from` + `to` (日付フィールドのみ、Python 予約語回避のため Pydantic 内部は `from_` だが JSON key は `from`)

### エラー

`/db-portal/cross-search` / `/db-portal/search` の DSL 関連 7 slug をそのまま共有する (`unexpected-token` / `unknown-field` / `field-not-available-in-cross-db` / `invalid-date-format` / `invalid-operator-for-field` / `nest-depth-exceeded` / `missing-value`、すべて 400 + `application/problem+json`)。`field-not-available-in-cross-db` は cross モードで Tier 3 field を使用した場合に発動する (単一 DB 指定必須のため)。`adv` 未指定 / `db` 値不正は FastAPI 標準の 422 (`about:blank`)。
