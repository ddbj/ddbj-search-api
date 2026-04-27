# Integration テストシナリオ

実 Elasticsearch (場合により ARSA / TXSearch Solr) に対する E2E 検証シナリオの一覧。各シナリオは「これが落ちたらどんなバグが検出されたことになるか」に答えられる粒度で書く。

このファイルは **シナリオ列挙の SSOT**。具体的なコードは `tests/integration/test_*.py` にあり、運用上の注意 (環境変数・fixture 戦略・件数 drift 対策・Solr 用 marker) は [integration-note.md](integration-note.md) を、テスト方針 (TDD / クラス分け規約) は [testing.md](testing.md) を参照。

## ID 体系

`IT-{機能}-{連番 2 桁}` の形式で振る。例: `IT-DSL-01`, `IT-STATUS-03`, `IT-DBPORTAL-12`。

- 機能ごとに連番をリセット
- 削除したシナリオの ID は **再利用しない** (履歴互換性)
- 機能名は固定リスト (下記カテゴリ): `CORE`, `SEARCH`, `DETAIL`, `BULK`, `FACETS`, `UMBRELLA`, `DSL`, `DBPORTAL`, `STATUS`, `DBLINK`
- IT 1 件 = test 関数 1 件 (parametrize で複数ケースを 1 関数に展開してよい)。test 関数の docstring に `IT-XXX-NN` を明記して双方向にトレース可能にする

## シナリオテンプレート

各シナリオは以下 4 項目で記述する。件数の実測値は書かない (構造的不変条件のみ。書き方は `integration-note.md § 件数 drift 対策`)。

```markdown
### IT-XXX-NN: <短いタイトル>

**endpoint**: HTTP method + path + 主要パラメータ

**不変条件**:
- 構造的に守るべき条件 1
- 構造的に守るべき条件 2

**回帰元**: 仕様根拠 (`docs/api-spec.md § ...` / `docs/db-portal-api-spec.md § ...`)

**関連 unit テスト**: SSOT としての unit ファイル。クラス・関数名まで分かれば `path::Class` で記述
```

## 観点 matrix (網羅チェック用)

各カテゴリ内で以下の観点を機械的に確認する。endpoint × 観点が成立する組み合わせは原則 1 IT 以上書く (該当しない組み合わせは IT を省略)。

| 観点 | 内容 | 主な対象カテゴリ |
|------|------|-----------------|
| 正常系 | 主要パス、sameAs フォールバック、alias ヒット、DSL 各演算子 | 全カテゴリ |
| 境界値 | ページネーション境界、cursor 期限、`perPage` 上下限、`topHits` 上下限、`hardLimitReached` 閾値、DSL ネスト深さ上限 | SEARCH / DBPORTAL / BULK / DSL |
| 異常系 | 422 (Pydantic) / 400 (業務エラー) / 404 / 500 / 502、RFC 7807 形式、DSL 7 slug | 全カテゴリ |
| status filter | `withdrawn` / `private` 常に除外、`suppressed` はアクセッション完全一致 (q) または single leaf `identifier:` eq (adv) のみ解放 | DETAIL / SEARCH / BULK / UMBRELLA / FACETS / STATUS / DBPORTAL |
| Solr 依存 | ARSA (8-shard fan-out) / TXSearch、cursor 非対応 | DBPORTAL / STATUS の Solr 部分のみ (`@pytest.mark.staging_only`) |

---

## IT-CORE-*: 共通仕様

全 endpoint 横断の HTTP レベル不変条件。代表 endpoint で 1 度通れば全体が守られる前提 (実装上 middleware で集約)。

### IT-CORE-01: X-Request-ID をリクエストヘッダーで指定すれば echo される

**endpoint**: `GET /service-info` (代表)

**不変条件**:
- リクエストヘッダー `X-Request-ID: <任意文字列>` 指定時、レスポンスヘッダー `X-Request-ID` に同値が入る
- レスポンス body のエラー (RFC 7807) にも同じ ID が反映される (エラー応答の場合)

**回帰元**: `docs/api-spec.md § リクエスト追跡 (X-Request-ID)`

**関連 unit テスト**: `tests/unit/test_main.py`

### IT-CORE-02: X-Request-ID 未指定時は UUID v4 が自動生成される

**endpoint**: `GET /service-info` (代表)

**不変条件**:
- リクエストに `X-Request-ID` が無い場合、レスポンスヘッダーに UUID v4 形式の文字列が入る
- 同一リクエストを 2 回叩くと毎回別の値になる (キャッシュされない)

**回帰元**: `docs/api-spec.md § リクエスト追跡 (X-Request-ID)`

**関連 unit テスト**: `tests/unit/test_main.py`

### IT-CORE-03: エラーレスポンスは RFC 7807 Problem Details 形式

**endpoint**: 不在 endpoint (`GET /entries/__does_not_exist__/X` 等)

**不変条件**:
- `Content-Type: application/problem+json`
- レスポンス body に `type` (URI), `title`, `status`, `detail` が必須キーとして存在
- `status` は HTTP ステータスコードと一致

**回帰元**: `docs/api-spec.md § エラーレスポンス (RFC 7807)`

**関連 unit テスト**: `tests/unit/test_main.py`

### IT-CORE-04: Trailing slash policy

**endpoint**:
- リスト系 (`/entries/`, `/entries/{type}/`, `/dblink/`) — canonical は with slash、no slash も alias で 200
- Facets / db-portal (`/facets`, `/facets/{type}`, `/db-portal/*`) — no slash のみ canonical、with slash は 404
- 個別リソース (`/entries/{type}/{id}`, `/dblink/{type}/{id}`) — slash なし

**不変条件**:
- `/entries` と `/entries/` で `total` が一致 (両方 200)
- `/dblink` と `/dblink/` で両方 200 (alias の挙動が一貫している)
- `/facets/` (with slash) は 404 (canonical は `/facets` のみ)
- どの form でも HTTP redirect (3xx) を返さない

**回帰元**: `docs/api-spec.md § Trailing Slash`

**関連 unit テスト**: `tests/unit/routers/test_entries.py`

### IT-CORE-05: Content-Type が path 拡張子で切り替わる

**endpoint**: `GET /entries/{type}/{id}.json` ↔ `.jsonld`

**不変条件**:
- `.json` → `Content-Type: application/json`
- `.jsonld` → `Content-Type: application/ld+json`
- body は valid な JSON / JSON-LD としてパース可能

**回帰元**: `docs/api-spec.md § sameAs による ID 解決` (4 variant 説明)

**関連 unit テスト**: `tests/unit/routers/test_entry_detail.py`

### IT-CORE-06: CORS ヘッダーが全 endpoint で `*`

**endpoint**: 任意 (代表として `GET /service-info`)

**不変条件**:
- レスポンスヘッダー `Access-Control-Allow-Origin: *`
- preflight (`OPTIONS`) でも同じ

**回帰元**: `docs/api-spec.md § CORS`

**関連 unit テスト**: `tests/unit/test_main.py`

### IT-CORE-07: 不在 endpoint は 404 + RFC 7807

**endpoint**: `GET /__not_a_route__`

**不変条件**:
- `status_code == 404`
- IT-CORE-03 と同じ RFC 7807 形式
- `detail` が「path が無い」旨を示す

**回帰元**: `docs/api-spec.md § エラーレスポンス`

**関連 unit テスト**: `tests/unit/test_main.py`

### IT-CORE-08: `/service-info` が ES 健康状態を含む

**endpoint**: `GET /service-info`

**不変条件**:
- `status_code == 200`
- body に `name`, `version`, `description`, `elasticsearch` キーが存在
- `elasticsearch` フィールドは `Literal["ok", "unavailable"]` の string 値 (`schemas.service_info.ElasticsearchStatus`)
- 実 ES に到達できる integration では `"ok"`

**回帰元**: `docs/api-spec.md § サービス情報` / `schemas/service_info.ElasticsearchStatus`

**関連 unit テスト**: `tests/unit/routers/test_service_info.py`

---

## IT-SEARCH-*: 検索とページネーション

`/entries/` (横断) と `/entries/{type}/` (タイプ別) の検索系。

### IT-SEARCH-01: 横断検索の正常系

**endpoint**: `GET /entries/?keywords=<word>`

**不変条件**:
- `status_code == 200`
- body に `total: int >= 0`, `items: list`, `facets` (default) が存在
- `len(items) <= perPage` (default 20)

**回帰元**: `docs/api-spec.md § 検索パラメータ`

**関連 unit テスト**: `tests/unit/routers/test_entries.py`

### IT-SEARCH-02: type 別検索の正常系

**endpoint**: `GET /entries/{type}/?keywords=<word>` (全 DbType)

**不変条件**:
- 全 type で `status_code == 200`
- `items` の各 entry の `type` field が path の type と一致 (横断と異なり単一 type のみ)

**回帰元**: `docs/api-spec.md § 検索パラメータ`
**関連 unit テスト**: `tests/unit/routers/test_entries.py`

### IT-SEARCH-03: ページネーション正常系 (`page * perPage <= 10000`)

**endpoint**: `GET /entries/?page={p}&perPage={pp}` (`p * pp <= 10000` の境界、例: `page=100&perPage=100`)

**不変条件**:
- `status_code == 200`
- `len(items) <= perPage`
- 同じパラメータで 2 度叩いて結果集合が一致 (実行順序非依存)

**回帰元**: `docs/api-spec.md § オフセットベースページネーション`

**関連 unit テスト**: `tests/unit/routers/test_entries.py`

### IT-SEARCH-04: ページネーション境界超過は 400

**endpoint**: `GET /entries/?page=101&perPage=100` (`page * perPage = 10100 > 10000`)

**不変条件**:
- `status_code == 400`
- RFC 7807 形式、`detail` が deep paging 制限を示す
- `cursor` の使用を促す案内が含まれる (もしくは `type` URI で識別可能)

**回帰元**: `docs/api-spec.md § オフセットベースページネーション` (10000 件上限) / `docs/api-spec.md § カーソルベースページネーション`

**関連 unit テスト**: `tests/unit/routers/test_entries.py`

### IT-SEARCH-05: cursor pagination で 10000 件超を取得できる

**endpoint**: `GET /entries/?cursor=<token>` (deep paging)

**不変条件**:
- 1 ページ目で `nextCursor` が返る
- `cursor` を渡した 2 ページ目以降で同条件の検索が継続
- `total` が 10000 を超えても `items` が連続して取得される

**回帰元**: `docs/api-spec.md § カーソルベースページネーション`
**関連 unit テスト**: `tests/unit/test_cursor.py`

### IT-SEARCH-06: cursor の HMAC 改ざん検出

**endpoint**: `GET /entries/?cursor=<改ざん token>`

**不変条件**:
- `status_code == 400`
- RFC 7807 形式、`detail` が cursor invalid を示す
- 元の検索結果は漏れない (情報量を増やさない)

**回帰元**: `docs/api-spec.md § カーソルベースページネーション`
**関連 unit テスト**: `tests/unit/test_cursor.py`

### IT-SEARCH-07: cursor 期限切れ (5 分超) で 400

**endpoint**: `GET /entries/?cursor=<5 分以上前の token>`

**不変条件**:
- `status_code == 400`
- `detail` が cursor expired を示す
- IT-SEARCH-06 とは type URI が区別される (改ざん vs 期限切れ)

**回帰元**: `docs/api-spec.md § カーソルベースページネーション` (TTL 5 分)

**関連 unit テスト**: `tests/unit/test_cursor.py`

### IT-SEARCH-08: sort パース (正常 / 異常)

**endpoint**: `GET /entries/?sort=<field>:<direction>`

**不変条件**:
- `sort=datePublished:desc` で 200、`items` が降順 (`item[i].datePublished >= item[i+1].datePublished`)
- 不正な direction (`sort=datePublished:foo`) で 422
- 不正な field (`sort=__not_a_field__:asc`) で 422

**回帰元**: `docs/api-spec.md § 検索パラメータ`

**関連 unit テスト**: `tests/unit/schemas/test_queries.py`

### IT-SEARCH-09: fields フィルタで指定フィールドのみ返る

**endpoint**: `GET /entries/?fields=identifier,title`

**不変条件**:
- `items` の各要素のキー集合が指定 fields のサブセット (システム必須キーは除く)
- 未指定フィールドは含まれない

**回帰元**: `docs/api-spec.md § 検索パラメータ` (`fields`)

**関連 unit テスト**: `tests/unit/routers/test_entries.py`

### IT-SEARCH-10: types カンマ区切りで複数 type を絞り込み

**endpoint**: `GET /entries/?types=bioproject,biosample`

**不変条件**:
- `items` の各 entry の `type` が `{bioproject, biosample}` に含まれる
- `types=bioproject` (単一) の `total` <= `types=bioproject,biosample` の `total`

**回帰元**: `docs/api-spec.md § 検索パラメータ`

**関連 unit テスト**: `tests/unit/schemas/test_queries.py`

### IT-SEARCH-11: keywords 演算子 (AND / OR / NOT、フレーズ)

**endpoint**: `GET /entries/?keywords=<expr>`

**不変条件**:
- `keywords=cancer AND brain` の `total` <= `keywords=cancer` の `total`
- `keywords=cancer OR brain` の `total` >= `keywords=cancer` の `total`
- `keywords=cancer NOT brain` の `total` <= `keywords=cancer` の `total`
- `keywords="exact phrase"` でフレーズ検索 (空白区切り AND と区別)

**回帰元**: `docs/api-spec.md § 検索パラメータ`
**関連 unit テスト**: `tests/unit/search/test_phrase.py`

### IT-SEARCH-12: 配列フィールド常時 key 返却契約 (検索結果)

**endpoint**: `GET /entries/?perPage=10` (default fields、全 type で個別にも検証)

**不変条件**:
- `items` の各 entry が converter 必須 list field (`grantList`, `publicationList`, `dbXrefs` 等) を空配列でも key として持つ (`fields` フィルタを使わない default ケース)
- 全 type で成立

**回帰元**: `docs/api-spec.md § 配列フィールド`
**関連 unit テスト**: `tests/unit/schemas/test_converter_contract.py`

### IT-SEARCH-13: type-specific filter (BioProject `objectTypes`)

**endpoint**: `GET /entries/bioproject/?objectTypes=<bucket>`

**不変条件**:
- `objectType` facet bucket key と同じ値で絞り込み可能 (例: `objectTypes=Umbrella,Primary Submission`)
- 該当しない type 横断 endpoint (`/entries/?objectTypes=...`) では 422

**回帰元**: `docs/api-spec.md § 検索パラメータ`
**関連 unit テスト**: `tests/unit/schemas/test_queries.py`

### IT-SEARCH-14: facets パラメータの allowlist 制御

**endpoint**: `GET /entries/?facets=organization,publication`

**不変条件**:
- `facets` 明示指定で集計対象が完全置換 (allowlist 内のみ)
- allowlist 外フィールド (例: `facets=__not_a_facet__`) で 422
- `facets=` 空指定で集計を完全に無効化 (response の `facets` が空 or null)

**回帰元**: `docs/api-spec.md § ファセット集計対象の選択`
**関連 unit テスト**: `tests/unit/schemas/test_queries.py`, `tests/unit/routers/test_facets.py`

### IT-SEARCH-15: nested フィールド検索 (`organization` / `publication` / `grant`)

**endpoint**: `GET /entries/?organization=DDBJ` / `?publication=<token>` / `?grant=<token>` (cross-type 含む全 endpoint)

**不変条件**:
- cross-type (`/entries/`) でも type 別 (`/entries/{type}/`) でも 200
- ES nested query 経由で `organization.name` / `publication.title` / `grant.title` に match
- 同じ token を keywords と組み合わせると、両条件の AND になり `total <= keywords 単独`
- 対応 nested path を持たない index に渡された場合は 0 件化 (ES 側で no match)

**回帰元**: `docs/api-spec.md § nested フィールド検索`
**関連 unit テスト**: `tests/unit/es/test_query.py`, `tests/unit/schemas/test_queries.py`

### IT-SEARCH-16: nested フィールド検索の型グループ限定 (`externalLinkLabel` / `derivedFromId`)

**endpoint**: `GET /entries/{type}/?externalLinkLabel=...` (bioproject / jga-* で適用、それ以外で 422) / `?derivedFromId=...` (biosample / sra-* で適用、それ以外で 422)

**不変条件**:
- cross-type endpoint (`/entries/`, `/facets`) で 422
- 適用範囲内 endpoint で 200
- 型グループ外 endpoint で 422

**回帰元**: `docs/api-spec.md § nested フィールド検索`
**関連 unit テスト**: `tests/unit/schemas/test_queries.py`

### IT-SEARCH-17: text match フィールド検索 (9 個、type-specific)

**endpoint**: `GET /entries/{type}/?<param>=<token>` (spec L347-369 の 9 (type, field) ペアを parametrize で網羅)

**不変条件**:
- 適用範囲内 endpoint で 200 かつ `total > 0` (代表 token は conftest 定数)
- 記号含み値で自動 phrase 化が効く (`host=HIF-1` の `total <= host=HIF` の `total`)

**回帰元**: `docs/api-spec.md § text match フィールド検索`
**関連 unit テスト**: `tests/unit/search/test_phrase.py`, `tests/unit/schemas/test_queries.py`

### IT-SEARCH-18: text match の cross-type 拒否

**endpoint**: `GET /entries/?host=Homo+sapiens` / `GET /facets?host=...` (cross-type で 9 個全て)

**不変条件**:
- cross-type endpoint で 9 個全ての text match パラメータが 422
- 型グループ外 type endpoint (例: `/entries/jga-study/?host=...` のような無関係 type) でも 422

**回帰元**: `docs/api-spec.md § text match フィールド検索`
**関連 unit テスト**: `tests/unit/schemas/test_queries.py`

### IT-SEARCH-19: type-specific term filter (sra-* / gea / metabobank / jga-*)

**endpoint**: `GET /entries/{type}/?<param>=<bucket>` (spec L309-318 の term 行を parametrize で網羅)

**不変条件**:
- 適用範囲内 endpoint で 200 かつ `total > 0`
- cross-type endpoint で 422
- bioproject `objectTypes` のみ排他検証: `BioProject` + `UmbrellaBioProject` 単独 total の和が OR 指定の total と一致 (term filter の OR セマンティクス確認)

**回帰元**: `docs/api-spec.md § 検索パラメータ § エンドポイント固有のパラメータ`
**関連 unit テスト**: `tests/unit/schemas/test_queries.py`

### IT-SEARCH-20: organism (NCBI Taxonomy ID) 単独フィルタ

**endpoint**: `GET /entries/?organism=9606`

**不変条件**:
- 200 かつ `total > 0`
- `keywords` と AND 結合: `?keywords=cancer&organism=9606` の `total` <= `?keywords=cancer` の `total`

**回帰元**: `docs/api-spec.md § 検索パラメータ`
**関連 unit テスト**: `tests/unit/schemas/test_queries.py`

### IT-SEARCH-21: 日付範囲と不正日付 422

**endpoint**: `GET /entries/?datePublishedFrom=...&datePublishedTo=...`

**不変条件**:
- 妥当範囲・単側指定で 200
- 形式違反 (`2020/01/01` 等) と実在しない日付 (`2024-02-30`) で 422
- From > To は Pydantic を通り ES で `total == 0` になる

**回帰元**: `docs/api-spec.md § 検索パラメータ`
**関連 unit テスト**: `tests/unit/schemas/test_queries.py`

### IT-SEARCH-22: keywordFields allowlist と不正値 422

**endpoint**: `GET /entries/?keywords=<token>&keywordFields=<value>`

**不変条件**:
- 4 値 (`identifier` / `title` / `name` / `description`) と複数カンマ区切りで 200
- allowlist 外で 422
- `keywordFields=title` の `total` <= 未指定 (全フィールド) の `total` (絞り込みの方向)

**回帰元**: `docs/api-spec.md § 検索パラメータ`
**関連 unit テスト**: `tests/unit/schemas/test_queries.py`

### IT-SEARCH-23: keywordOperator AND と OR の挙動差

**endpoint**: `GET /entries/?keywords=cancer,brain&keywordOperator={AND|OR}`

**不変条件**:
- `AND` の `total` <= `OR` の `total`
- `OR` の `total` >= 単独 `keywords=cancer` の `total`
- allowlist 外 (`XOR` 等) で 422

**回帰元**: `docs/api-spec.md § 検索パラメータ`
**関連 unit テスト**: `tests/unit/schemas/test_queries.py`

### IT-SEARCH-24: dbXrefsLimit リスト系 (type ごとに切り詰め)

**endpoint**: `GET /entries/?perPage=5&dbXrefsLimit=N`

**不変条件**:
- 各 entry の `dbXrefs` を `type` で group-by すると、各 type の長さ <= N
- N=0 で `dbXrefs == []`、`dbXrefsCount` は present (実数)
- N=1000 で 200、N=1001 で 422

**回帰元**: `docs/api-spec.md § dbXrefs`
**関連 unit テスト**: `tests/unit/routers/test_entries.py`

### IT-SEARCH-25: includeProperties 効果 (検索結果)

**endpoint**: `GET /entries/?perPage=5&includeProperties={true|false}`

**不変条件**:
- default で `items[*].properties` キーが present
- `false` で `properties` キーが items から落ちる
- `total` は両者で一致

**回帰元**: `docs/api-spec.md § 検索パラメータ § ResponseControlQuery`
**関連 unit テスト**: `tests/unit/routers/test_entries.py`

### IT-SEARCH-26: includeDbXrefs=false で dbXrefs/dbXrefsCount 省略

**endpoint**: `GET /entries/?perPage=5&includeDbXrefs=false`

**不変条件**:
- 各 entry から `dbXrefs` / `dbXrefsCount` キーが両方落ちる
- `dbXrefsLimit=0` の場合は `dbXrefs == []` + `dbXrefsCount` が dict (集計結果) で挙動が異なる

**回帰元**: `docs/api-spec.md § dbXrefs § includeDbXrefs パラメータ`
**関連 unit テスト**: `tests/unit/routers/test_entries.py`

---

## IT-DETAIL-*: Entry Detail / sameAs / dbXrefs

`/entries/{type}/{id}` の 4 variant: `/{id}` (フロント向け切り詰め), `.json` (raw streaming), `.jsonld` (JSON-LD), `/dbxrefs.json` (dbXrefs 全件)。

### IT-DETAIL-01: 4 variant それぞれの正常系

**endpoint**: `GET /entries/{type}/{id}` (4 variant、全 DbType)

**不変条件**:
- 各 variant で `status_code == 200`
- `/{id}` は Pydantic validated レスポンス、`.json` は streaming raw、`.jsonld` は JSON-LD context 付き、`/dbxrefs.json` は dbXrefs リスト

**回帰元**: `docs/api-spec.md § sameAs による ID 解決` (variant 説明)
**関連 unit テスト**: `tests/unit/routers/test_entry_detail.py`

### IT-DETAIL-02: 4 variant の内容差異 (フロント向け vs 全データ)

**endpoint**: `/entries/{type}/{id}` (`/{id}` ↔ `.json`)

**不変条件**:
- `/{id}` は dbXrefs を `dbXrefsLimit` で切り詰め、`dbXrefsCount` を併記
- `.json` は ES の `_source` をそのまま返し、dbXrefs 切り詰めなし
- 両者の identifier 等の主要フィールドは一致

**回帰元**: `docs/api-spec.md § dbXrefs`
**関連 unit テスト**: `tests/unit/routers/test_entry_detail.py`

### IT-DETAIL-03: sameAs Secondary ID フォールバック

**endpoint**: `GET /entries/bioproject/<Secondary_ID>` (Secondary ID 直打ち)

**不変条件**:
- `status_code == 200`
- response の `identifier` は Primary ID (Secondary でない)
- sameAs nested query が機能している

**回帰元**: `docs/api-spec.md § sameAs による ID 解決`
**関連 unit テスト**: `tests/unit/routers/test_entry_detail.py`

### IT-DETAIL-04: alias ドキュメントヒット

**endpoint**: `GET /entries/{type}/<alias>` (converter が alias として投入したドキュメント)

**不変条件**:
- alias でも 200 で詳細が返る
- response の Primary は alias ではなく canonical ID

**回帰元**: `docs/api-spec.md § sameAs による ID 解決`

**関連 unit テスト**: `tests/unit/routers/test_entry_detail.py`

### IT-DETAIL-05: 不在 entry の 404

**endpoint**: `GET /entries/bioproject/PRJDB_DOES_NOT_EXIST_99999`

**不変条件**:
- `status_code == 404`
- IT-CORE-03 と同じ RFC 7807 形式
- 4 variant 全てで挙動が同じ

**回帰元**: `docs/api-spec.md § エラーレスポンス`

**関連 unit テスト**: `tests/unit/routers/test_entry_detail.py`

### IT-DETAIL-06: dbXrefs 切り詰め (`dbXrefsLimit`)

**endpoint**: `GET /entries/{type}/{id}?dbXrefsLimit=N`

**不変条件**:
- `len(response.dbXrefs) <= N`
- `response.dbXrefsCount` は切り詰め前の総数 (N 超でも実数)
- N=0 で空配列、`dbXrefsCount` は実数

**回帰元**: `docs/api-spec.md § dbXrefs`

**関連 unit テスト**: `tests/unit/routers/test_entry_detail.py`

### IT-DETAIL-07: JSON-LD `@id` が Primary ID URI

**endpoint**: `GET /entries/bioproject/{accession}.jsonld`

**不変条件**:
- `Content-Type: application/ld+json`
- `@id` が Primary ID 由来の URI
- `@context` 必須

**回帰元**: `docs/api-spec.md § sameAs による ID 解決`

**関連 unit テスト**: `tests/unit/routers/test_entry_detail.py`

### IT-DETAIL-08: 配列フィールド常時 key 返却契約 (詳細 全 DbType × 3 variant)

**endpoint**: `GET /entries/{type}/{id}` (`/{id}`, `.json`, `.jsonld`) × 全 DbType

**不変条件**:
- 各 type の converter 必須 list field (例: BioProject `grantList`, `publicationList`) が空配列でも response の key として present
- `.json` は streaming で Pydantic を経由しないので独立に検証 (回帰しやすい経路)

**回帰元**: `docs/api-spec.md § 配列フィールド`
**関連 unit テスト**: `tests/unit/schemas/test_converter_contract.py`, `tests/unit/schemas/test_entries.py`

### IT-DETAIL-09: `/dbxrefs.json` の全件取得 (DuckDB stream)

**endpoint**: `GET /entries/{type}/{id}/dbxrefs.json`

**不変条件**:
- `status_code == 200`
- `Content-Type: application/json`
- `dbXrefs` の長さが切り詰めなし (`/{id}` の `dbXrefsCount` と一致)
- 大規模 entry (dbXrefs 数千〜数万件) でも streaming で完走

**回帰元**: `docs/api-spec.md § dbXrefs` / `docs/api-spec.md § DBLinks API`
**関連 unit テスト**: `tests/unit/routers/test_entry_detail.py`, `tests/unit/dblink/test_client.py`

### IT-DETAIL-10: sameAs 検索失敗時の 404 への安全な fall-through

**endpoint**: `GET /entries/{type}/<不正な形の Secondary>`

**不変条件**:
- ES 側の sameAs nested query が失敗しても 500 にならず 404 で帰す
- IT-DETAIL-05 と detail 文字列が一致

**回帰元**: `docs/api-spec.md § sameAs による ID 解決`
**関連 unit テスト**: `tests/unit/routers/test_entry_detail.py`

### IT-DETAIL-11: lowercase / mixedcase accession は 404

**endpoint**: `GET /entries/{type}/{lowercase_or_mixedcase_accession}` (4 variant)

**不変条件**:
- `status_code == 404` (case-folding しないので Primary とも sameAs alias とも一致しない)
- 4 variant 全てで 404
- `body.detail` が `IT-DETAIL-05` (不在 ID) と完全一致 (status 推測防止に使う固定文字列を再利用)
- 元 accession を大文字に揃えれば 200 で同じ entry に解決される (case 違いの理由が「正規化 off」だと示すために対比で確認)

**回帰元**: `docs/api-spec.md § sameAs による ID 解決 § Case-sensitivity`
**関連 unit テスト**: `tests/unit/routers/test_entry_detail.py`

### IT-DETAIL-12: includeDbXrefs=false で dbXrefs/dbXrefsCount 省略 (`/{id}`)

**endpoint**: `GET /entries/{type}/{id}?includeDbXrefs=false`

**不変条件**:
- response から `dbXrefs` / `dbXrefsCount` キーが両方落ちる
- `?dbXrefsLimit=0` は `dbXrefs == []` + `dbXrefsCount` present で挙動が異なる
- `includeDbXrefs=false&dbXrefsLimit=100` で `includeDbXrefs=false` が優先

**回帰元**: `docs/api-spec.md § dbXrefs § includeDbXrefs パラメータ`
**関連 unit テスト**: `tests/unit/routers/test_entry_detail.py`

---

## IT-BULK-*: Bulk API

`POST /entries/{type}/bulk` (JSON Array / NDJSON)。

### IT-BULK-01: JSON Array 形式の正常系

**endpoint**: `POST /entries/{type}/bulk` (body: `{ids: [...], format: "json"}`)

**不変条件**:
- `status_code == 200`
- body は `{entries: [...], notFound: [...]}` 構造
- `entries` の各要素が detail スキーマを満たす

**回帰元**: `docs/api-spec.md § Bulk API`

**関連 unit テスト**: `tests/unit/routers/test_bulk.py`, `tests/unit/schemas/test_bulk.py`

### IT-BULK-02: NDJSON 形式の正常系

**endpoint**: `POST /entries/{type}/bulk` (body: `{ids: [...], format: "ndjson"}`)

**不変条件**:
- `Content-Type: application/x-ndjson`
- 各行が独立して valid な JSON
- 行数 = `len(set(ids)) - len(notFound)` (= entries の長さ)

**回帰元**: `docs/api-spec.md § Bulk API`

**関連 unit テスト**: `tests/unit/routers/test_bulk.py`

### IT-BULK-03: 不変式 `len(entries) + len(notFound) == len(set(ids))`

**endpoint**: `POST /entries/{type}/bulk` (mix of existing + non-existing IDs)

**不変条件**:
- 重複 ID 込みのリクエストでも、入力の `set(ids)` の数と (entries + notFound) の合計が一致
- entries と notFound に同じ ID が両方現れない (排他)

**回帰元**: `docs/api-spec.md § Bulk API`

**関連 unit テスト**: `tests/unit/routers/test_bulk.py`

### IT-BULK-04: 重複 ID set 化 (1 度だけ返る)

**endpoint**: `POST /entries/{type}/bulk` (body: `{ids: ["X", "X", "X"]}`)

**不変条件**:
- entries 内に同じ ID は 1 度だけ
- notFound 内に同じ ID は 1 度だけ

**回帰元**: `docs/api-spec.md § Bulk API`

**関連 unit テスト**: `tests/unit/routers/test_bulk.py`

### IT-BULK-05: 1000 件境界 (1000 OK / 1001 NG)

**endpoint**: `POST /entries/{type}/bulk` (`len(ids)` = 1000 / 1001)

**不変条件**:
- 1000 件で 200
- 1001 件で 422 (Pydantic validation)

**回帰元**: `docs/api-spec.md § Bulk API`

**関連 unit テスト**: `tests/unit/schemas/test_bulk.py`

### IT-BULK-06: 空 IDs で 422

**endpoint**: `POST /entries/{type}/bulk` (body: `{ids: []}`)

**不変条件**:
- `status_code == 422`
- RFC 7807 形式

**回帰元**: `docs/api-spec.md § Bulk API`

**関連 unit テスト**: `tests/unit/schemas/test_bulk.py`

### IT-BULK-07: notFound リストに不在 ID が分類される

**endpoint**: `POST /entries/{type}/bulk` (body: `{ids: ["EXISTING", "PRJDB_DOES_NOT_EXIST_99999"]}`)

**不変条件**:
- `notFound = ["PRJDB_DOES_NOT_EXIST_99999"]`
- `entries` に EXISTING が含まれる

**回帰元**: `docs/api-spec.md § Bulk API`

**関連 unit テスト**: `tests/unit/routers/test_bulk.py`

### IT-BULK-08: 配列フィールド常時 key 返却契約 (両形式)

**endpoint**: `POST /entries/{type}/bulk` (`format=json` / `format=ndjson`、全 DbType)

**不変条件**:
- entries の各要素が converter 必須 list field を空配列でも key として持つ (両形式とも)
- 全 type で成立

**回帰元**: `docs/api-spec.md § 配列フィールド`
**関連 unit テスト**: `tests/unit/schemas/test_converter_contract.py`

---

## IT-FACETS-*: Facets

`/facets`、`/facets/{type}`、`/entries/?includeFacets=true`。

### IT-FACETS-01: cross-type facet の構造 (`/facets`)

**endpoint**: `GET /facets`

**不変条件**:
- response に `organization`, `publication`, `accessibility` 等の cross 共通 facet が存在
- 各 facet bucket は `{key, count}` 形式
- type 固有 facet (`objectType` 等) は **含まれない**

**回帰元**: `docs/api-spec.md § ファセット`
**関連 unit テスト**: `tests/unit/routers/test_facets.py`, `tests/unit/schemas/test_facets.py`

### IT-FACETS-02: type 別 facet の構造 (`/facets/bioproject`)

**endpoint**: `GET /facets/bioproject`

**不変条件**:
- response に `objectType` (bioproject 固有) が含まれる
- bucket key が `b3db3ef` 以降の `objectTypes` パラメータと整合

**回帰元**: `docs/api-spec.md § ファセット`
**関連 unit テスト**: `tests/unit/routers/test_facets.py`

### IT-FACETS-03: cross-type で type-specific facet を要求すると 200 で該当 index のみ集計

**endpoint**: `GET /facets?facets=objectType` (`objectType` は bioproject 固有)

**不変条件**:
- `status_code == 200`
- response に `objectType` キーが存在し、bucket が non-empty (bioproject index のみで集計)
- 同 endpoint で `?facets=organism,objectType` に拡張すると `organism` も同時に返る
- 同 endpoint で `?facets=libraryStrategy` (sra-experiment 固有) も同様に 200 (該当 index のみで集計)

**回帰元**: `docs/api-spec.md § ファセット集計対象の選択` (cross-type endpoint は該当 index のみで集計する条項)
**関連 unit テスト**: `tests/unit/routers/test_facets.py`, `tests/unit/schemas/test_queries.py`

### IT-FACETS-04: `includeFacets=true` で検索結果 + facet 一括取得

**endpoint**: `GET /entries/?keywords=<word>&includeFacets=true`

**不変条件**:
- response に `items` と `facets` の両方が含まれる
- `includeFacets=false` (default) では `facets` が空 or null

**回帰元**: `docs/api-spec.md § ファセット`

**関連 unit テスト**: `tests/unit/routers/test_entries.py`

### IT-FACETS-05: facet 集計が `status:public` のみ

**endpoint**: `GET /facets` (suppressed / withdrawn / private が一定数存在する前提)

**不変条件**:
- response の facet bucket count に suppressed / withdrawn / private が含まれない
- `/entries/?keywords=<accession>` で suppressed が見えても、`/facets` の集計には反映されない

**回帰元**: `docs/api-spec.md § データ可視性 (status 制御)`
**関連 unit テスト**: `tests/unit/es/test_query.py`, `tests/unit/routers/test_facets.py`

### IT-FACETS-06: OpenAPI Facets schema 整合 (status キー無し、organism / accessibility 必須)

**endpoint**: `GET /openapi.json` (実体は `/facets`)

**不変条件**:
- Facets レスポンス schema に `status` キーが**存在しない** (status facet 廃止)
- `organism`, `accessibility` 等が必須

**回帰元**: `docs/api-spec.md § ファセット`
**関連 unit テスト**: `tests/unit/schemas/test_facets.py`

### IT-FACETS-07: facets allowlist 外で 422

**endpoint**: `GET /entries/?facets=__not_a_facet__`

**不変条件**:
- `status_code == 422`
- RFC 7807 形式

**回帰元**: `docs/api-spec.md § ファセット集計対象の選択`

**関連 unit テスト**: `tests/unit/schemas/test_queries.py`

### IT-FACETS-08: type-specific facet (sra-experiment / gea / metabobank / jga-study)

**endpoint**: `GET /facets/{type}?facets=<field>` (spec L405-411 のタイプ固有フィールドを parametrize で網羅)

**不変条件**:
- 対応 (type, field) ペアで 200、対応 facet が non-empty bucket
- 各 bucket は `{key, count: int >= 0}` 形式

**回帰元**: `docs/api-spec.md § ファセット § タイプ固有フィールド`
**関連 unit テスト**: `tests/unit/routers/test_facets.py`

### IT-FACETS-09: type-specific endpoint で対象外 facet 400 (allowlist 外 422 と区別)

**endpoint**: `GET /facets/bioproject?facets=libraryStrategy`

**不変条件**:
- valid な field 名だが対象 endpoint で利用不可 → 400
- typo (allowlist 外) → 422 (IT-FACETS-07 と区別される異なる failure class)

**回帰元**: `docs/api-spec.md § ファセット集計対象の選択 § エラー`
**関連 unit テスト**: `tests/unit/routers/test_facets.py`

---

## IT-UMBRELLA-*: Umbrella Tree

`GET /entries/bioproject/{accession}/umbrella-tree`。BioProject の親子関係 (DAG) を返す。

### IT-UMBRELLA-01: orphan で `roots = [self], edges = []`

**endpoint**: `GET /entries/bioproject/<orphan_accession>/umbrella-tree`

**不変条件**:
- `status_code == 200`
- `roots == [<orphan_accession>]`
- `edges == []`
- `query == <orphan_accession>`

**回帰元**: `docs/api-spec.md § Umbrella Tree`
**関連 unit テスト**: `tests/unit/routers/test_umbrella_tree.py`

### IT-UMBRELLA-02: depth 1 (umbrella → leaf) の典型構造

**endpoint**: `GET /entries/bioproject/<umbrella_accession>/umbrella-tree`

**不変条件**:
- `roots` に umbrella accession が含まれる
- `edges` に少なくとも 1 件 (parent → child) が含まれる
- 全 edge の `parent` / `child` が `nodes` (もしくは roots) に存在 (整合性)

**回帰元**: `docs/api-spec.md § Umbrella Tree`
**関連 unit テスト**: `tests/unit/routers/test_umbrella_tree.py`

### IT-UMBRELLA-03: multi-parent DAG で edge 重複排除

**endpoint**: `GET /entries/bioproject/<multi_parent_accession>/umbrella-tree`

**不変条件**:
- 同一 (parent, child) ペアの edge が 1 件しか現れない
- `edges` 内で `(parent, child)` がユニーク

**回帰元**: `docs/api-spec.md § Umbrella Tree`

**関連 unit テスト**: `tests/unit/routers/test_umbrella_tree.py`

### IT-UMBRELLA-05: 中間 node 参照切れで edge 除外、API 全体は 200

**endpoint**: `GET /entries/bioproject/<has_dangling_child_accession>/umbrella-tree`

**不変条件**:
- API は `status_code == 200`
- 参照切れの child を含む edge は `edges` から除外される
- 残りの整合性 (parent / child が node に存在) は保たれる

**回帰元**: `docs/api-spec.md § Umbrella Tree`

**関連 unit テスト**: `tests/unit/routers/test_umbrella_tree.py`

### IT-UMBRELLA-07: seed 不在で 404

**endpoint**: `GET /entries/bioproject/PRJDB_DOES_NOT_EXIST_99999/umbrella-tree`

**不変条件**:
- `status_code == 404`
- RFC 7807 形式
- `detail` がアクセッション値を含まない固定文字列 (status 推測防止)

**回帰元**: `docs/api-spec.md § Umbrella Tree` / `docs/api-spec.md § データ可視性`

**関連 unit テスト**: `tests/unit/routers/test_umbrella_tree.py`

---

## IT-DSL-*: ES DSL コンパイル動作

`/db-portal/cross-search`, `/db-portal/search`, `/db-portal/parse` の DSL 関連。

### IT-DSL-01: ES wildcard が `case_insensitive: true` で大文字小文字を吸収

**endpoint**: `GET /db-portal/search?db=bioproject&adv=title:cancer*` ↔ `?adv=title:Cancer*`

**不変条件**:
- 両クエリの `total` が一致 (case 違いに依存しない)
- `keyword` 系フィールドでも tokenized text でも対称に動く

**回帰元**: `docs/db-portal-api-spec.md § Advanced Search DSL`
**関連 unit テスト**: `tests/unit/search/dsl/test_compiler_es.py`

### IT-DSL-02: cursor + adv 同時指定で `cursor-not-supported` 400 (ES DB)

**endpoint**: `GET /db-portal/search?db=bioproject&adv=title:cancer&cursor=<token>`

**不変条件**:
- `status_code == 400`
- `type` URI が `cursor-not-supported` slug を含む
- adv は offset-only

**回帰元**: `docs/db-portal-api-spec.md § エラー`
**関連 unit テスト**: `tests/unit/routers/test_db_portal.py`

### IT-DSL-03: cursor + adv 同時指定で `cursor-not-supported` 400 (Solr DB) — staging_only

**endpoint**: `GET /db-portal/search?db=trad&adv=title:cancer&cursor=<token>` (`@pytest.mark.staging_only`)

**不変条件**:
- ES と同じ slug `cursor-not-supported`
- Solr DB は cursor 非対応 (offset-only)

**回帰元**: `docs/db-portal-api-spec.md § エラー`
**関連 unit テスト**: `tests/unit/routers/test_db_portal.py`

### IT-DSL-04: `/db-portal/parse` が DSL を AST JSON に変換

**endpoint**: `GET /db-portal/parse?adv=title:cancer AND organism.name:human&db=bioproject`

**不変条件**:
- `status_code == 200`
- response が `{"queryTree": ...}` 形式の JSON tree
- AND/OR/NOT/leaf の構造が DSL と一致 (GUI state restore 用 SSOT)

**回帰元**: `docs/db-portal-api-spec.md § /db-portal/parse`
**関連 unit テスト**: `tests/unit/routers/test_db_portal_parse.py`, `tests/unit/search/dsl/test_serde.py`

### IT-DSL-05: `/db-portal/parse` OpenAPI responses は `{200, 400, 422, 500}` (404 不含)

**endpoint**: `GET /openapi.json` (実体は `/db-portal/parse`)

**不変条件**:
- parse の responses key 集合 = `{"200", "400", "422", "500"}`
- 404 が含まれない (`db` を必須にしないので path resolve には失敗しない)

**回帰元**: `docs/db-portal-api-spec.md § /db-portal/parse § エラー`
**関連 unit テスト**: `tests/unit/routers/test_db_portal_parse.py`

### IT-DSL-06: grammar が symbol 含み wildcard を受理

**endpoint**: `GET /db-portal/search?db=bioproject&adv=title:HIF-1*` / `?adv=title:COVID-19*`

**不変条件**:
- `status_code == 200` (parse error にならない)
- `total >= 0` で検索が動く
- 同じく `?` (single char) も受理

**回帰元**: `docs/db-portal-api-spec.md § Advanced Search DSL`
**関連 unit テスト**: `tests/unit/search/dsl/test_grammar.py`

### IT-DSL-07: `/db-portal/parse` cross-mode (db 省略) で Tier 3 拒否

**endpoint**: `GET /db-portal/parse?adv=<Tier 3 field>:value` (db 省略 = cross-mode)

**不変条件**:
- `status_code == 400`
- `type` URI が `field-not-available-in-cross-db` slug

**回帰元**: `docs/db-portal-api-spec.md § Advanced Search DSL § Tier`
**関連 unit テスト**: `tests/unit/search/dsl/test_validator.py`

### IT-DSL-08: DSL syntax error で 400 `unexpected-token`

**endpoint**: `GET /db-portal/parse?adv=title:::cancer` (壊れた DSL)

**不変条件**:
- `status_code == 400`
- `type` URI が `unexpected-token` slug を含む
- `detail` に位置情報が含まれる (列番号など)

**回帰元**: `docs/db-portal-api-spec.md § エラー`

**関連 unit テスト**: `tests/unit/search/dsl/test_errors.py`

### IT-DSL-09: allowlist 外フィールドで 400 `unknown-field`

**endpoint**: `GET /db-portal/parse?adv=__not_a_field__:value&db=bioproject`

**不変条件**:
- `status_code == 400`
- `type` URI が `unknown-field` slug

**回帰元**: `docs/db-portal-api-spec.md § Advanced Search DSL`

**関連 unit テスト**: `tests/unit/search/dsl/test_allowlist.py`

### IT-DSL-10: between 範囲検索 (date)

**endpoint**: `GET /db-portal/search?db=bioproject&adv=date_published:[a TO b]`

**不変条件**:
- 200 で 1 日範囲 `total` <= 5 年範囲 `total` (drift 非依存の相対不変)

**回帰元**: `docs/db-portal-api-spec.md § Advanced Search DSL § 演算子マトリクス`
**関連 unit テスト**: `tests/unit/search/dsl/test_compiler_es.py`

### IT-DSL-11: phrase 検索 (text)

**endpoint**: `GET /db-portal/search?db=bioproject&adv=title:"whole genome"`

**不変条件**:
- 200 で phrase の `total` <= `title:whole AND title:genome` の `total` (順序固定で厳しい)

**回帰元**: `docs/db-portal-api-spec.md § Advanced Search DSL § 演算子マトリクス`
**関連 unit テスト**: `tests/unit/search/dsl/test_grammar.py`

### IT-DSL-12: AND/OR/NOT 優先度・grouping

**endpoint**: `GET /db-portal/search?db=bioproject&adv=<expr>`

**不変条件**:
- `A AND B` の `total` <= `A OR B` の `total`
- `A AND NOT B` の `total` <= `A` 単独の `total`
- 異なる grouping (`(A OR B) AND C` vs `A OR (B AND C)`) で `total` が異なる

**回帰元**: `docs/db-portal-api-spec.md § Advanced Search DSL § 文法`
**関連 unit テスト**: `tests/unit/search/dsl/test_grammar.py`, `tests/unit/search/dsl/test_compiler_es.py`

### IT-DSL-13: enum 演算子 (`project_type`)

**endpoint**: `GET /db-portal/search?db=bioproject&adv=project_type:<value>`

**不変条件**:
- `BioProject` / `UmbrellaBioProject` 単独 `total` の和が OR 結合 `total` と一致 (enum 排他)
- enum 違反値 (`Foobar` 等) はパース成功 + ES 側で `total == 0`

**回帰元**: `docs/db-portal-api-spec.md § Advanced Search DSL § Tier 3`
**関連 unit テスト**: `tests/unit/search/dsl/test_compiler_es.py`

### IT-DSL-14: 2 段 nested (`grant_agency`)

**endpoint**: `GET /db-portal/search?db=bioproject&adv=grant_agency:NIH`

**不変条件**:
- 200 (silent 5xx 化しない)
- `grant_agency:NIH AND title:cancer` の `total` <= `grant_agency:NIH` 単独

**回帰元**: `docs/db-portal-api-spec.md § Advanced Search DSL § Tier 3 § BioProject`
**関連 unit テスト**: `tests/unit/search/dsl/test_compiler_es.py`

### IT-DSL-15: invalid-date-format で 400

**endpoint**: `GET /db-portal/parse?adv=<impossible date>`

**不変条件**:
- 構文上は date リテラルだが日付として無効な値 (例: `2024-02-30`) で 400 + `type` URI に `invalid-date-format` slug
- 区切り違反 (`2020/01/01` 等) は parser 段階で `unexpected-token` 化 (IT-DSL-08 でカバー)

**回帰元**: `docs/db-portal-api-spec.md § エラー`
**関連 unit テスト**: `tests/unit/search/dsl/test_validator.py`

### IT-DSL-16: invalid-operator-for-field で 400

**endpoint**: `GET /db-portal/parse?adv=<type/operator mismatch>`

**不変条件**:
- date に wildcard、identifier に between 等の不整合で 400 + `type` URI に `invalid-operator-for-field` slug

**回帰元**: `docs/db-portal-api-spec.md § Advanced Search DSL § 演算子マトリクス`
**関連 unit テスト**: `tests/unit/search/dsl/test_validator.py`

### IT-DSL-17: nest-depth-exceeded で 400

**endpoint**: `GET /db-portal/parse?adv=<深さ N のネスト>`

**不変条件**:
- 深さ 5 で 200、深さ 6 で 400 + `type` URI に `nest-depth-exceeded` slug
- ノード総数上限 (`dsl_max_nodes`、default 512) 超過も同 slug を流用 (深さは OK でも横幅 `a OR b OR ... OR z` で爆発する経路をガード)。default config では parser の DSL 長さ上限 4096 文字制限内に収まり再現困難なため、ノード総数経路は unit でカバー

**回帰元**: `docs/db-portal-api-spec.md § Advanced Search DSL § 文法`
**関連 unit テスト**: `tests/unit/search/dsl/test_validator.py` (TestNestDepth, TestNestNodes)

### IT-DSL-18: missing-value で 400

**endpoint**: `GET /db-portal/parse?adv=title:""` / `?adv=title:''`

**不変条件**:
- 明示空ダブルクオート (`title:""`) と明示空シングルクオート (`title:''`) のどちらも 400 + `type` URI に `missing-value` slug
- grammar の PHRASE は double / single quote 両対応 (対称、`q=` 側のキーワード検索と一貫)

**回帰元**: `docs/db-portal-api-spec.md § エラー`
**関連 unit テスト**: `tests/unit/search/dsl/test_grammar.py`, `tests/unit/search/dsl/test_validator.py`

### IT-DSL-19: single quote phrase は double quote と同等

**endpoint**: `GET /db-portal/parse?adv=organism:'Homo sapiens'` ↔ `?adv=organism:"Homo sapiens"`

**不変条件**:
- `/db-portal/parse`: single / double quote の phrase が同一の AST (`{op: eq, field, value}`) を返す
- `/db-portal/search`: 同一クエリで `total` が一致 (quote 種別はバックエンドの match に影響しない)
- `q=` 側 (キーワード検索) との一貫性: `phrase.py` の `tokenize_keywords` が `'...'` も phrase として扱うのと同等

**回帰元**: `docs/db-portal-api-spec.md § Advanced Search DSL § 文法`
**関連 unit テスト**: `tests/unit/search/dsl/test_grammar.py` (TestFieldClauseValueKinds, TestPhraseEscaping)

---

## IT-DBPORTAL-*: db-portal 横断 / DB 指定

`/db-portal/cross-search` と `/db-portal/search` の動作シナリオ。Solr (ARSA / TXSearch) 経由のシナリオには `@pytest.mark.staging_only` を付与 (Solr は staging のみ)、ES 6 DB のみのシナリオは marker なし。

### IT-DBPORTAL-01: ARSA `molecularType` field がレスポンスに含まれる

**endpoint**: `GET /db-portal/search?db=trad&q=*&perPage=20`

**不変条件**:
- `hits[*].molecularType` (Pydantic alias `molecularType`、Python attr `molecular_type`) が response に出る
- 一定割合の hit で値が non-null

**回帰元**: `docs/db-portal-api-spec.md § DbPortalHit (trad)`
**関連 unit テスト**: `tests/unit/solr/test_mappers.py`

### IT-DBPORTAL-02: ARSA `sequenceLength` field がレスポンスに含まれる

**endpoint**: `GET /db-portal/search?db=trad&q=*&perPage=20`

**不変条件**:
- `hits[*].sequenceLength` (Pydantic alias、Python attr `sequence_length`) が response に出る
- 一定割合の hit で値が non-null

**回帰元**: `docs/db-portal-api-spec.md § DbPortalHit (trad)`
**関連 unit テスト**: `tests/unit/solr/test_mappers.py`

### IT-DBPORTAL-03: ARSA `organism.identifier` が Feature `db_xref="taxon:..."` から抽出

**endpoint**: `GET /db-portal/search?db=trad&q=cancer&perPage=20`

**不変条件**:
- `hits[*].organism.identifier` が `taxon:` 接頭辞無しの数値 ID で埋まる (一定割合の hit で)
- 元 ARSA Feature の `db_xref="taxon:9606"` が `9606` として正しく抽出されている

**回帰元**: `docs/db-portal-api-spec.md § DbPortalHit (trad)`
**関連 unit テスト**: `tests/unit/solr/test_mappers.py`

### IT-DBPORTAL-04: trad / taxonomy `description` が常に null

**endpoint**: `GET /db-portal/search?db=trad&q=*&perPage=20` / `?db=taxonomy&q=*&perPage=20`

**不変条件**:
- 全 hit で `description == null` (機械連結廃止)

**回帰元**: `docs/db-portal-api-spec.md § DbPortalHit`
**関連 unit テスト**: `tests/unit/solr/test_mappers.py`

### IT-DBPORTAL-05: TXSearch lineage の自身除去

**endpoint**: `GET /db-portal/search?db=taxonomy&q=Homo&perPage=20`

**不変条件**:
- 各 hit で `lineage[0] != scientific_name` (自身重複が除去されている)
- もし `lineage[0] == scientific_name` のドキュメントが ES から来ても、API レイヤーで除去

**回帰元**: `docs/db-portal-api-spec.md § DbPortalHit (taxonomy)`
**関連 unit テスト**: `tests/unit/solr/test_mappers.py`

### IT-DBPORTAL-06: adv Tier 3 field の uf allowlist 完全性

**endpoint**: `GET /db-portal/search?db=trad&adv=division:BCT` (compile_to_solr 経由)

**不変条件**:
- `status_code == 200` かつ `total > 0` (allowlist を通って division で実フィルタが効いている)
- 同 `q=*` (フィルタなし) との `total` 比で `adv=division:BCT` の方が小さい (silent wrong-field match で全件 fallback すると等しくなる)

**回帰元**: `docs/db-portal-api-spec.md § Advanced Search DSL § Tier 3`
**関連 unit テスト**: `tests/unit/search/dsl/test_compiler_solr.py`, `tests/unit/solr/test_query.py`

### IT-DBPORTAL-07: cross-search の 8 DB fan-out (count + topHits)

**endpoint**: `GET /db-portal/cross-search?q=cancer&topHits=10`

**不変条件**:
- response に 8 DB (`bioproject`, `biosample`, `sra`, `jga`, `gea`, `metabobank`, `trad`, `taxonomy`) すべての count が含まれる
- 各 DB に対し `topHits` 個までの hit (12-field shared `DbPortalLightweightHit` schema) が含まれる
- 全 DB が並列に呼ばれている (個別 timeout 内で完走)

**回帰元**: `docs/db-portal-api-spec.md § /db-portal/cross-search`
**関連 unit テスト**: `tests/unit/routers/test_db_portal.py`

### IT-DBPORTAL-08: cross-search `topHits` 境界 (0 / 50 / 51)

**endpoint**: `GET /db-portal/cross-search?q=cancer&topHits={0|50|51}`

**不変条件**:
- `topHits=0` で 200、各 DB の hits が空配列、count のみ
- `topHits=50` で 200、`len(hits) <= 50`
- `topHits=51` で 422

**回帰元**: `docs/db-portal-api-spec.md § DbPortalCrossSearchQuery`
**関連 unit テスト**: `tests/unit/schemas/test_db_portal.py`

### IT-DBPORTAL-09: search?db=trad の cursor 不可

**endpoint**: `GET /db-portal/search?db=trad&q=cancer&cursor=<token>`

**不変条件**:
- `status_code == 400`
- `type` URI が `cursor-not-supported` slug
- Solr DB は offset-only

**回帰元**: `docs/db-portal-api-spec.md § ページネーション`
**関連 unit テスト**: `tests/unit/routers/test_db_portal.py`

### IT-DBPORTAL-10: search?db=taxonomy の cursor 不可

**endpoint**: `GET /db-portal/search?db=taxonomy&q=Homo&cursor=<token>`

**不変条件**:
- IT-DBPORTAL-09 と同じ挙動 (`cursor-not-supported` 400)

**回帰元**: `docs/db-portal-api-spec.md § ページネーション`
**関連 unit テスト**: `tests/unit/routers/test_db_portal.py`

### IT-DBPORTAL-11: search?db=trad/taxonomy の `perPage` allowlist (20/50/100 のみ)

**endpoint**: `GET /db-portal/search?db=trad&q=cancer&perPage={20|50|100|30}`

**不変条件**:
- `perPage in {20, 50, 100}` で 200
- それ以外 (例: 30) で 422

**回帰元**: `docs/db-portal-api-spec.md § ページネーション`
**関連 unit テスト**: `tests/unit/schemas/test_db_portal_hits.py`

### IT-DBPORTAL-12: per-backend timeout (個別 DB 失敗で他 DB は返る)

**endpoint**: `GET /db-portal/cross-search?q=<高負荷クエリ>` (timeout 誘発)

**不変条件**:
- 個別 DB が timeout してもレスポンス全体は 200 (他 DB の結果が返る)
- 失敗した DB は count / hits が `null` または error 表示
- 全 DB 失敗で初めて 502

**回帰元**: `docs/db-portal-api-spec.md § タイムアウト挙動`
**関連 unit テスト**: `tests/unit/routers/test_db_portal.py`

### IT-DBPORTAL-13: cross-search の `unexpected-parameter` で 400

**endpoint**: `GET /db-portal/cross-search?q=cancer&<extra>=...` (`db` / `page` / `perPage` / `cursor` / `sort` を parametrize)

**不変条件**:
- どの余剰パラメータでも 400 + `type` URI に `unexpected-parameter` slug

**回帰元**: `docs/db-portal-api-spec.md § /db-portal/cross-search § 排他ルール`
**関連 unit テスト**: `tests/unit/routers/test_db_portal.py`

### IT-DBPORTAL-14: search の `missing-db` で 400

**endpoint**: `GET /db-portal/search?q=cancer`

**不変条件**:
- 400 + `type` URI に `missing-db` slug

**回帰元**: `docs/db-portal-api-spec.md § /db-portal/search § 排他ルール`
**関連 unit テスト**: `tests/unit/routers/test_db_portal.py`

### IT-DBPORTAL-15: q + adv 同時指定で `invalid-query-combination` 400

**endpoint**: `GET /db-portal/cross-search?q=...&adv=...` / `GET /db-portal/search?db=...&q=...&adv=...`

**不変条件**:
- 両 endpoint で 400 + `type` URI に `invalid-query-combination` slug (同 slug 共有)

**回帰元**: `docs/db-portal-api-spec.md § エラー`
**関連 unit テスト**: `tests/unit/routers/test_db_portal.py`

### IT-DBPORTAL-16: search の sort allowlist

**endpoint**: `GET /db-portal/search?db=bioproject&q=*&sort=<value>`

**不変条件**:
- `datePublished:desc` / `:asc` で 200、`hits` が指定方向にソート
- allowlist 外で 422

**回帰元**: `docs/db-portal-api-spec.md § /db-portal/search § クエリパラメータ`
**関連 unit テスト**: `tests/unit/schemas/test_db_portal.py`

### IT-DBPORTAL-17: hardLimitReached フラグ

**endpoint**: `GET /db-portal/search?db=bioproject&q=...`

**不変条件**:
- `total >= 10000` で `hardLimitReached == true`
- `total < 10000` で `hardLimitReached == false`

**回帰元**: `docs/db-portal-api-spec.md § /db-portal/search § レスポンス`
**関連 unit テスト**: `tests/unit/schemas/test_db_portal.py`

### IT-DBPORTAL-18: search?db=ES_DB の cursor 正常系

**endpoint**: 1 ページ目 `?db=bioproject&q=*&perPage=20` → 2 ページ目 `?db=bioproject&cursor=<token>&perPage=20`

**不変条件**:
- 2 ページ目 `hits` の identifier 集合が 1 ページ目と排他
- `cursor` + 検索条件 (`q` / `adv` / `sort` / `page`) 併用で 400 (排他ルール)

**回帰元**: `docs/db-portal-api-spec.md § /db-portal/search § ページネーション`
**関連 unit テスト**: `tests/unit/test_cursor.py`, `tests/unit/routers/test_db_portal.py`

### IT-DBPORTAL-19: BioSample Tier 3 (geo_loc_name) の uf allowlist 完全性

**endpoint**: `GET /db-portal/search?db=biosample&adv=geo_loc_name:Japan&perPage=20` (compile_to_es 経由)

**不変条件**:
- `status_code == 200` かつ `total > 0` (allowlist を通って `geoLocName` で実フィルタが効いている)
- 同 `q=*` (フィルタなし) との `total` 比で `adv=geo_loc_name:Japan` の方が小さい (silent wrong-field match で全件 fallback すると等しくなる)

**回帰元**: `docs/db-portal-api-spec.md § Advanced Search DSL § Tier 3`
**関連 unit テスト**: `tests/unit/search/dsl/test_compiler_es.py`, `tests/unit/search/dsl/test_allowlist.py`

### IT-DBPORTAL-20: SRA Tier 3 (analysis_type) の uf allowlist 完全性

**endpoint**: `GET /db-portal/search?db=sra&adv=analysis_type:variation&perPage=20` (compile_to_es 経由、sra-analysis のみヒット)

**不変条件**:
- `status_code == 200` かつ `total > 0` (allowlist を通って `analysisType` で実フィルタが効いている)
- 同 `q=*` (フィルタなし) との `total` 比で `adv=analysis_type:variation` の方が小さい

**回帰元**: `docs/db-portal-api-spec.md § Advanced Search DSL § Tier 3`
**関連 unit テスト**: `tests/unit/search/dsl/test_compiler_es.py`, `tests/unit/search/dsl/test_allowlist.py`

---

## IT-STATUS-*: status filter

ES `status` フィールド (`public` / `suppressed` / `withdrawn` / `private`) のアクセス制御。`/entries/*` と `/db-portal/*` (ES 6 DB) で同等のロジック、`/db-portal/*` の Solr 2 DB は no-op。

### IT-STATUS-01: 自由文検索で hidden (withdrawn / private) がヒットしない

**endpoint**: `GET /entries/?keywords=<自由文>` (suppressed / withdrawn / private が一定数ある前提)

**不変条件**:
- 全 `items[*].status` が `"public"`
- `total` に hidden が含まれない

**回帰元**: `docs/api-spec.md § データ可視性`
**関連 unit テスト**: `tests/unit/es/test_query.py`, `tests/unit/search/test_accession.py`

### IT-STATUS-02: アクセッション完全一致 keywords で suppressed がヒット (UX 維持)

**endpoint**: `GET /entries/?keywords=<suppressed_accession>`

**不変条件**:
- `total >= 1`
- 該当 entry の `status == "suppressed"`
- 同 accession + ワイルドカード (`<acc>*`) では suppressed が出ない (完全一致のみ解放)

**回帰元**: `docs/api-spec.md § アクセッション ID 完全一致の判定ルール`
**関連 unit テスト**: `tests/unit/search/test_accession.py`

### IT-STATUS-03: detail 4 variant で `private` が 404、`suppressed` が 200

**endpoint**: `GET /entries/{type}/{accession}` 4 variant (`/{id}`, `.json`, `.jsonld`, `/dbxrefs.json`)

**不変条件**:
- private: 4 variant 全てで 404
- suppressed: 4 variant 全てで 200 (direct access は許可)

`withdrawn` は ES に存在しないため検証対象外 (`docs/api-spec.md § データ可視性` 参照)。防衛ロジックは unit でカバー。

**回帰元**: `docs/api-spec.md § データ可視性`
**関連 unit テスト**: `tests/unit/routers/test_entry_detail.py`

### IT-STATUS-04: 404 detail 文字列が hidden ↔ 不在で完全一致 (status 推測防止)

**endpoint**: `GET /entries/{type}/{private_id}` ↔ `GET /entries/{type}/PRJDB_DOES_NOT_EXIST_99999`

**不変条件**:
- `status_code` 一致 (404)
- `body.detail` 完全一致 (status 推測材料を漏らさない)
- private / 不在の 2 通りで `detail` が同じ (`withdrawn` は ES に投入されないため対象外)
- 4 variant 全てで成立

**回帰元**: `docs/api-spec.md § データ可視性`
**関連 unit テスト**: `tests/unit/routers/test_entry_detail.py`

### IT-STATUS-05: bulk で混在 IDs が `entries` (public + suppressed) と `notFound` (private + 不在) に分類

**endpoint**: `POST /entries/{type}/bulk` (body: 3 status + 不在の混在 IDs)

**不変条件**:
- `entries` に public + suppressed が含まれる
- `notFound` に private + 不在が含まれる (`withdrawn` は ES 不在で `notFound` 経路と同一)
- 不変式 IT-BULK-03 を満たす

**回帰元**: `docs/api-spec.md § データ可視性`
**関連 unit テスト**: `tests/unit/routers/test_bulk.py`

### IT-STATUS-08: facets が `status:public` のみで集計

**endpoint**: `GET /facets`、`GET /facets/{type}`、`GET /entries/?includeFacets=true`

**不変条件**:
- IT-FACETS-05 を再掲: facet bucket count に hidden / suppressed が含まれない
- accession exact match keywords でも facets は public のみ (suppressed の解放対象外)

**回帰元**: `docs/api-spec.md § データ可視性` / `docs/api-spec.md § ファセット`

**関連 unit テスト**: `tests/unit/es/test_query.py`

### IT-STATUS-09: `/db-portal/cross-search?q=<自由文>` で 6 ES DB の hits に hidden / suppressed が出ない

**endpoint**: `GET /db-portal/cross-search?q=<自由文>&topHits=10`

**不変条件**:
- ES 経由の各 DB (bioproject, biosample, sra, jga, gea, metabobank) の hits 全てで `status == "public"`
- count にも hidden / suppressed が含まれない

**回帰元**: `docs/db-portal-api-spec.md § データ可視性`
**関連 unit テスト**: `tests/unit/routers/test_db_portal.py`, `tests/unit/search/test_accession.py`

### IT-STATUS-10: `/db-portal/cross-search?q=<accession>` で対象 ES DB に suppressed が出る

**endpoint**: `GET /db-portal/cross-search?q=<suppressed_accession>&topHits=10`

**不変条件**:
- 対象 ES DB の hits に suppressed accession が含まれる
- 他の ES DB の count / hits は普通に public のみ
- アクセッション完全一致判定が `q` でも `adv` でも同じ規則

**回帰元**: `docs/db-portal-api-spec.md § データ可視性`
**関連 unit テスト**: `tests/unit/search/test_accession.py`

### IT-STATUS-11: `/db-portal/cross-search?adv=identifier:<accession>` (single leaf eq) で suppressed が出る

**endpoint**: `GET /db-portal/cross-search?adv=identifier:<suppressed_accession>&topHits=10`

**不変条件**:
- 対象 ES DB の hits に suppressed が含まれる
- adv AST のトップが単一 `FieldClause` (`identifier`, `op=eq`) のときのみ解放
- 他の field (例: `title:`) では解放されない

**回帰元**: `docs/db-portal-api-spec.md § データ可視性` (AST 判定ルール)
**関連 unit テスト**: `tests/unit/search/dsl/test_accession_exact_match.py`

### IT-STATUS-12: `/db-portal/cross-search?adv=identifier:<acc> AND title:<word>` (AND ラップ) で suppressed が出ない

**endpoint**: `GET /db-portal/cross-search?adv=identifier:<suppressed_accession> AND title:<word>&topHits=10`

**不変条件**:
- AND ラップは解放対象外なので、suppressed は hits に出ない
- OR / NOT も同様
- ワイルドカード (`identifier:<acc>*`) も対象外

**回帰元**: `docs/db-portal-api-spec.md § データ可視性`
**関連 unit テスト**: `tests/unit/search/dsl/test_accession_exact_match.py`

### IT-STATUS-13: `/db-portal/search?db=<es_db>&q=<accession>` cursor 2 ページ目で status filter 継承

**endpoint**: 1 ページ目: `?db=bioproject&q=<accession>` → 2 ページ目: `?cursor=<token>` (token に query state 焼き込み)

**不変条件**:
- 2 ページ目でも対象 DB に suppressed が出る (cursor token に accession exact match 状態が継承される)
- token を別 DB に流し込んでも継承されない (cursor + db の整合性)

**回帰元**: `docs/db-portal-api-spec.md § データ可視性`
**関連 unit テスト**: `tests/unit/test_cursor.py`

### IT-STATUS-14: `/db-portal/search?db=<es_db>&adv=identifier:<accession>` で suppressed (offset 経路、cursor 不可)

**endpoint**: `GET /db-portal/search?db=bioproject&adv=identifier:<suppressed_accession>`

**不変条件**:
- 1 ページ目に suppressed が出る (offset 経路)
- cursor + adv 同時指定で 400 (IT-DSL-02)
- 2 ページ目以降は `page` で取得 (deep paging 制限内)

**回帰元**: `docs/db-portal-api-spec.md § データ可視性`
**関連 unit テスト**: `tests/unit/search/dsl/test_accession_exact_match.py`

### IT-STATUS-15: `/db-portal/search?db=trad|taxonomy` (Solr proxy) は public のみが見える — staging_only

**endpoint**: `GET /db-portal/search?db=trad&q=*&perPage=20` / `?db=taxonomy&q=*&perPage=20` (`@pytest.mark.staging_only`)

**不変条件**:
- レスポンス `hits[*].status` は `null` または `"public"` のいずれか
- hidden な status (`suppressed` / `withdrawn` / `private`) は決して出ない

**回帰元**: `docs/db-portal-api-spec.md § データ可視性` (Solr no-op)
**関連 unit テスト**: `tests/unit/solr/test_query.py`

### IT-STATUS-16: アクセッション完全一致のクオート剥がしと否認パターン

**endpoint**: `GET /entries/?keywords=<variant>` (suppressed accession を変奏)

**不変条件**:
- 外側ダブルクオート (`"acc"`) / シングルクオート (`'acc'`) は判定前に剥がされ suppressed が出る
- ワイルドカード付き (`acc*`) / カンマ区切り複数トークン (`acc,foo`) で suppressed が出ない (単一・非ワイルドカード縛り)

**回帰元**: `docs/api-spec.md § データ可視性 § アクセッション ID 完全一致の判定ルール`
**関連 unit テスト**: `tests/unit/search/test_accession.py`

---

## IT-DBLINK-*: DBLinks

`/dblink/`、`/dblink/{type}/{id}`、`POST /dblink/counts`。

### IT-DBLINK-01: `/dblink/` で AccessionType が網羅返却

**endpoint**: `GET /dblink/`

**不変条件**:
- response の type 集合 = `docs/api-spec.md § アクセッションタイプ` で列挙された全 AccessionType と完全一致 (set 一致)
- 各エントリーが name / description 等の必須キーを持つ

**回帰元**: `docs/api-spec.md § アクセッションタイプ`

**関連 unit テスト**: `tests/unit/routers/test_dblink.py`, `tests/unit/schemas/test_dblink.py`

### IT-DBLINK-02: target フィルタ (単一 / 複数 / 不在)

**endpoint**: `GET /dblink/{type}/{id}?target={t1|t1,t2|__not_a_type__}`

**不変条件**:
- 単一 target で `dbXrefs[*].type == t1`
- 複数 target で `dbXrefs[*].type ∈ {t1, t2}`
- 存在しない target で `status_code == 200`、`dbXrefs == []`
- target=空 (フィルタなし) で全 type が混在

**回帰元**: `docs/api-spec.md § GET /dblink/{type}/{id}`

**関連 unit テスト**: `tests/unit/routers/test_dblink.py`

### IT-DBLINK-03: ソート順 (タイプ昇順 → アクセッション昇順)

**endpoint**: `GET /dblink/{type}/{id}` (関連が多い entry)

**不変条件**:
- `dbXrefs` がタイプ昇順、同一 type 内でアクセッション昇順
- ソートキーが安定 (同入力で常に同順)

**回帰元**: `docs/api-spec.md § GET /dblink/{type}/{id}`

**関連 unit テスト**: `tests/unit/routers/test_dblink.py`, `tests/unit/dblink/test_client.py`

### IT-DBLINK-04: 関連なしで 200 + `dbXrefs: []`

**endpoint**: `GET /dblink/{type}/{accession_with_no_links}`

**不変条件**:
- `status_code == 200`
- `dbXrefs == []`
- 5xx にならない (DuckDB が空クエリで安定動作)

**回帰元**: `docs/api-spec.md § GET /dblink/{type}/{id}`

**関連 unit テスト**: `tests/unit/routers/test_dblink.py`, `tests/unit/dblink/test_client.py`

### IT-DBLINK-05: `POST /dblink/counts` の bulk count 上限件数

**endpoint**: `POST /dblink/counts` (body: `{items: [{type, id}, ...]}`)

**不変条件**:
- 1-100 件で 200、各 `{type, id}` に対する `counts` (タイプ別件数 dict) を返す
- 0 件で 422、101 件で 422
- 不在 entry は `counts == {}` (404 ではない)
- response の `items[*].identifier` / `items[*].type` がリクエスト順と一致

**回帰元**: `docs/api-spec.md § POST /dblink/counts`
**関連 unit テスト**: `tests/unit/routers/test_dblink.py`, `tests/unit/dblink/test_client.py`

### IT-DBLINK-06: path `{type}` の AccessionType 違反で 422

**endpoint**: `GET /dblink/__not_a_type__/{id}` / `GET /dblink/BioProject/{id}` (case-sensitive)

**不変条件**:
- 不正 type / 大文字違いで 422
- IT-DBLINK-02 の `target` 422 とは別経路 (path vs query)

**回帰元**: `docs/api-spec.md § GET /dblink/{type}/{id}`
**関連 unit テスト**: `tests/unit/routers/test_dblink.py`, `tests/unit/schemas/test_dblink.py`

### IT-DBLINK-07: `POST /dblink/counts` の不正 payload は atomic に 422

**endpoint**: `POST /dblink/counts`

**不変条件**:
- AccessionType 外の `type` を含む item が 1 件でもあれば全体 422
- `id` 欠落 item で 422

**回帰元**: `docs/api-spec.md § POST /dblink/counts`
**関連 unit テスト**: `tests/unit/schemas/test_dblink.py`

