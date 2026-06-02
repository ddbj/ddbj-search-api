"""DSL compiler for Elasticsearch (Stage 3a: AST → ES bool query dict).

SSOT: search-backends.md §バックエンド変換.

- Tier 1 は 10 flat (identifier/title/name/description/organism_id/organism_name/date_published/
  date_modified/date_created/accessibility) + 1 or_flat (date alias)。
- Tier 2 は 2 nested (submitter: organization, publication: publication)。
- Tier 3 (ES 対象) は 24 flat (BioProject object_type/project_type/relevance / BioSample 7
  (host/strain/isolate/geo_loc_name/collection_date/package/model) / SRA 8
  (library_strategy/source/layout/selection/platform/instrument_model/library_name/
  library_construction_protocol; analysis_type は別 path) / JGA 3 / MetaboBank shared 3 /
  SRA+JGA shared 1 (type)) + 3 nested (grant_title: grant.title, external_link_label:
  externalLink.label, derived_from_id: derivedFrom.identifier) + 1 double-nested
  (grant_agency: grant → grant.agency)。
- Trad / Taxonomy 系 Tier 3 は compiler_solr 側で扱うため、本 module の allowlist には含めない。

前提: validator で ``(field_type, value_kind)`` 互換性および cross-mode Tier 3 拒否は担保済。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ddbj_search_api.search.dsl.allowlist import FIELD_TYPES, OPERATOR_BY_KIND
from ddbj_search_api.search.dsl.ast import FieldClause, FreeText, Node, Range
from ddbj_search_api.search.phrase import ES_AUTO_PHRASE_CHARS, parse_keywords_with_autophrase

# シンプル検索 (q) を multi_match に展開するときのデフォルトフィールド集合.
# db-portal handler は keyword_fields を渡さず常にこのデフォルトで検索する.
# entries / facets は ``validate_keyword_fields`` 経由で同じ 5 field を明示渡しする
# (entries 側 ``_DEFAULT_KEYWORD_FIELDS`` (``ddbj_search_api/es/query.py``) と一致).
_FREE_TEXT_DEFAULT_FIELDS: tuple[str, ...] = (
    "identifier",
    "title",
    "name",
    "description",
    "organism.name",
)


@dataclass(frozen=True, slots=True)
class _ESStrategy:
    """DSL field 名 → ES query 構築方針。

    `kind` に応じて使うフィールドが異なる:
    - ``flat``     : ``path`` (単一 top-level) に basic leaf を直接投げる。
    - ``or_flat``  : ``paths`` (複数 top-level) に OR (bool should) で投げる。
    - ``nested``   : ``path`` の nested wrapper + ``sub`` に basic leaf。
    - ``nested2``  : ``path`` → ``inner_path`` の 2 段 nested + ``sub`` に basic leaf。
    """

    kind: Literal["flat", "or_flat", "nested", "nested2"]
    path: str | None = None
    paths: tuple[str, ...] | None = None
    sub: str | None = None
    inner_path: str | None = None


_ES_FIELD_STRATEGY: dict[str, _ESStrategy] = {
    # === Tier 1 ===
    "identifier": _ESStrategy(kind="flat", path="identifier"),
    "title": _ESStrategy(kind="flat", path="title"),
    # ES common の name は text+keyword だが、text 型 (contains = match_phrase) で開放するため
    # analyzed の top-level `name` に当てる (.keyword suffix 不要)。
    "name": _ESStrategy(kind="flat", path="name"),
    "description": _ESStrategy(kind="flat", path="description"),
    # 生物種は taxID と学名で別 field に分割。organism_id は identifier 型 (keyword に term)、
    # organism_name は text 型 (text に match_phrase 経由で standard analyzer を通す)。
    "organism_id": _ESStrategy(kind="flat", path="organism.identifier"),
    "organism_name": _ESStrategy(kind="flat", path="organism.name"),
    "date_published": _ESStrategy(kind="flat", path="datePublished"),
    "date_modified": _ESStrategy(kind="flat", path="dateModified"),
    "date_created": _ESStrategy(kind="flat", path="dateCreated"),
    "date": _ESStrategy(kind="or_flat", paths=("datePublished", "dateModified", "dateCreated")),
    # 全 ES backed 6 DB 共通 controlled vocab。Solr backed (Trad / Taxonomy) には field 不在で
    # cross-mode で degenerate (0 件) 自然に。
    "accessibility": _ESStrategy(kind="flat", path="accessibility"),
    # === Tier 2 ===
    "submitter": _ESStrategy(kind="nested", path="organization", sub="organization.name"),
    "publication": _ESStrategy(kind="nested", path="publication", sub="publication.title"),
    # === Tier 3 flat ===
    # SRA / JGA / GEA / MetaboBank の enum 系 (libraryStrategy / instrumentModel /
    # analysisType / datasetType / experimentType / submissionType 等) は ES mapping が
    # text+keyword multi-field のため、term query には `.keyword` サブフィールドを使う
    # 必要がある (analyzer 適用後の lowercase token と uppercase 値が一致しないため)。
    # text 型 (libraryName / libraryConstructionProtocol / vendor / projectType 等) は
    # match_phrase で analyzer 経由するので suffix 不要。keyword 単独 (objectType /
    # relevance) も suffix 不要。
    #
    # NOTE: DSL の `object_type` は ES `objectType` field を叩く
    # (BioProject / UmbrellaBioProject の Umbrella 区分。REST API の `?objectTypes=` と同じ field)。
    "object_type": _ESStrategy(kind="flat", path="objectType"),
    # DSL `project_type` は ES `projectType` text+keyword field を match_phrase
    # (INSDC controlled vocab: genome / metagenome / 等)。REST `?projectType=` と同じ field。
    # `object_type` (ES `objectType`、Umbrella 区分) とは別 field なので混同注意。
    "project_type": _ESStrategy(kind="flat", path="projectType"),
    "relevance": _ESStrategy(kind="flat", path="relevance"),
    "library_strategy": _ESStrategy(kind="flat", path="libraryStrategy.keyword"),
    "library_source": _ESStrategy(kind="flat", path="librarySource.keyword"),
    "library_layout": _ESStrategy(kind="flat", path="libraryLayout.keyword"),
    # library_selection は sra-experiment のみ field 存在 (INSDC controlled vocab)
    "library_selection": _ESStrategy(kind="flat", path="librarySelection.keyword"),
    "platform": _ESStrategy(kind="flat", path="platform.keyword"),
    "instrument_model": _ESStrategy(kind="flat", path="instrumentModel.keyword"),
    "library_name": _ESStrategy(kind="flat", path="libraryName"),
    "library_construction_protocol": _ESStrategy(kind="flat", path="libraryConstructionProtocol"),
    "analysis_type": _ESStrategy(kind="flat", path="analysisType.keyword"),
    "study_type": _ESStrategy(kind="flat", path="studyType.keyword"),
    "vendor": _ESStrategy(kind="flat", path="vendor"),
    "dataset_type": _ESStrategy(kind="flat", path="datasetType.keyword"),
    "experiment_type": _ESStrategy(kind="flat", path="experimentType.keyword"),
    "submission_type": _ESStrategy(kind="flat", path="submissionType.keyword"),
    # BioSample 7 (converter 0.3.0 で top-level 化、geo_loc_name / collection_date は SRA-sample と共通、
    # package は object{name:keyword, displayName:keyword} で `package.name` keyword 単独に解決)
    "host": _ESStrategy(kind="flat", path="host"),
    "strain": _ESStrategy(kind="flat", path="strain"),
    "isolate": _ESStrategy(kind="flat", path="isolate"),
    "geo_loc_name": _ESStrategy(kind="flat", path="geoLocName"),
    "collection_date": _ESStrategy(kind="flat", path="collectionDate"),
    "package": _ESStrategy(kind="flat", path="package.name"),
    "model": _ESStrategy(kind="flat", path="model"),
    # SRA + JGA 共通 (subtype 識別子。SRA: sra-submission..sra-analysis、JGA: jga-study..jga-policy)
    "type": _ESStrategy(kind="flat", path="type"),
    # === Tier 3 double-nested ===
    # BioProject / JGA 共通: grant[].title (単一 nested)。REST API の `?grant=` と同じ field。
    "grant_title": _ESStrategy(
        kind="nested",
        path="grant",
        sub="grant.title",
    ),
    # BioProject / JGA 共通: grant[].agency[].name (2 段 nested)。DSL 名は `grant_agency`。
    "grant_agency": _ESStrategy(
        kind="nested2",
        path="grant",
        inner_path="grant.agency",
        sub="grant.agency.name",
    ),
    # === Tier 3 nested ===
    # BioProject / JGA 共通: externalLink[].label。converter mapping は text
    # (common.py externalLink.label)。allowlist でも text 型なので contains 経由で
    # match_phrase (順序保持) になる。普通 search 側の `externalLinkLabel` は match
    # (順序非保持) で、どちらも analyzer 経由だが phrase か否かが異なる。portal DSL の
    # 演算子セマンティクス (contains = phrase) を優先する。
    "external_link_label": _ESStrategy(
        kind="nested",
        path="externalLink",
        sub="externalLink.label",
    ),
    # BioSample / SRA (sra-sample) 共通: derivedFrom[].identifier。
    # identifier 型なので eq (term) / wildcard 経路。普通 search 側 `derivedFromId` は
    # match (analyzer 経由) なので analyzer 差はあるが、accession ID は大小区別なしで
    # 一致するため実害は小さい。
    "derived_from_id": _ESStrategy(
        kind="nested",
        path="derivedFrom",
        sub="derivedFrom.identifier",
    ),
    # Trad / Taxonomy 系 Tier 3 は ES 対象外 (compiler_solr で処理)。本 map には入れない。
}


def compile_free_text(
    value: str,
    *,
    operator: Literal["AND", "OR"] = "AND",
    fields: list[str] | tuple[str, ...] | None = None,
    is_phrase: bool = False,
) -> dict[str, Any]:
    """Convert a raw search keyword string (``q``) to an ES bool query.

    ``value`` を auto-phrase 適用付きでトークン化し、各トークンを ``multi_match``
    (記号含みは ``type=phrase``) に展開、``operator`` で ``bool.must`` /
    ``bool.should`` を選ぶ。db-portal / entries / facets / AST 経路 (FreeText
    ノード) で同じロジックを共有する。

    ``fields`` を省略すると ``_FREE_TEXT_DEFAULT_FIELDS`` (5 field、
    ``identifier`` / ``title`` / ``name`` / ``description`` / ``organism.name``) を使う。
    entries / facets 系は ``build_search_query`` が ``_DEFAULT_KEYWORD_FIELDS`` から
    組み立てた ``fields`` を明示渡しするが、両者の集合は同期させてある (`organism.name`
    込みの 5 field)。``value`` がトークン化後に空となる場合は ``ValueError`` を
    raise する (呼び出し側で空入力を弾く前提)。

    ``is_phrase=True`` の場合、ユーザーが DSL 上で値全体をクオートで囲んだことを
    示す (AST 経路から ``FreeText.is_phrase`` を引き継ぐ)。このときコンマ分割 /
    auto-phrase 判定を bypass し、``value`` 全体を 1 phrase token として
    ``multi_match.type=phrase`` (順序保持) で出力する。引用符内のコンマも phrase
    の一部として保持される (parser の PHRASE terminal と整合)。``is_phrase=False``
    (default) は string-based 経路 (entries / facets 系) と従来 AST 経路で同じ.
    """
    if fields is None:
        used_fields: list[str] = list(_FREE_TEXT_DEFAULT_FIELDS)
    else:
        used_fields = list(fields)
    if is_phrase:
        # ユーザーが明示的に "..." / '...' で囲んだ FreeText: value 全体を 1 phrase token
        # として match_phrase (= multi_match.type=phrase) に展開. コンマ分割は bypass.
        if not value:
            raise ValueError(f"empty free-text value (after tokenization): {value!r}")
        multi_matches: list[dict[str, Any]] = [
            {"multi_match": {"query": value, "fields": used_fields, "type": "phrase"}},
        ]
    else:
        tokens = parse_keywords_with_autophrase(value, ES_AUTO_PHRASE_CHARS)
        if not tokens:
            raise ValueError(f"empty free-text value (after tokenization): {value!r}")
        multi_matches = []
        for text, token_is_phrase in tokens:
            mm: dict[str, Any] = {"query": text, "fields": used_fields}
            if token_is_phrase:
                mm["type"] = "phrase"
            else:
                # 1 multi_match 内 (= 1 keyword 値内) の空白を AND 結合する.
                # multi_match の ES default は OR で、stop word に近い token を
                # 含む長めの keyword で誤爆が大きいため明示する.
                # phrase 系は順序固定なので operator は意味を持たない (ES 仕様で
                # phrase に対する operator は無視される) ため付けない.
                mm["operator"] = "and"
            multi_matches.append({"multi_match": mm})
    if operator == "OR":
        return {"bool": {"should": multi_matches, "minimum_should_match": 1}}
    return {"bool": {"must": multi_matches}}


def compile_to_es(
    ast: Node,
    *,
    free_text_operator: Literal["AND", "OR"] = "AND",
) -> dict[str, Any]:
    """Convert a validated AST to an ES query body (value of the ``query`` key).

    Returns a bool / leaf dict suitable for embedding as ``{"query": <result>, "size": ...}``
    — matches the shape produced by :func:`ddbj_search_api.es.query.build_search_query` so
    the router can route all queries through the same helpers.

    ``free_text_operator`` controls the boolean connection of multiple bare-word /
    phrase tokens inside a ``FreeText`` node (``cancer tumor`` → ``cancer AND tumor``
    or ``cancer OR tumor``).  ``AND`` (default) emits ``bool.must``; ``OR`` emits
    ``bool.should`` + ``minimum_should_match=1``.  The explicit ``AND`` / ``OR`` /
    ``NOT`` BoolOps inside the AST are unaffected.

    トップレベル AND の直下に FreeText が混じった AST (``cancer AND organism_id:9606`` 等)
    では、``free_text_operator=AND`` のときに限り FreeText 子の ``bool.must`` 中身を
    上位 ``bool.must`` に flatten して単一 bool 句にまとめる。OR の場合は FreeText の
    ``bool.should`` が semantics 上 inline 化できないので、入れ子の bool 句として残す。
    """
    return _compile_node(ast, free_text_operator=free_text_operator)


def _compile_node(node: Node, *, free_text_operator: Literal["AND", "OR"] = "AND") -> dict[str, Any]:
    if isinstance(node, FreeText):
        return compile_free_text(
            node.value,
            operator=free_text_operator,
            is_phrase=node.is_phrase,
        )
    if isinstance(node, FieldClause):
        return _compile_leaf(node)
    if node.op == "AND":
        return {
            "bool": {
                "must": _compile_and_children(node.children, free_text_operator=free_text_operator),
            },
        }
    if node.op == "OR":
        return {
            "bool": {
                "should": [_compile_node(c, free_text_operator=free_text_operator) for c in node.children],
                "minimum_should_match": 1,
            },
        }
    # NOT
    return {
        "bool": {
            "must_not": [_compile_node(c, free_text_operator=free_text_operator) for c in node.children],
        },
    }


def _compile_and_children(
    children: tuple[Node, ...],
    *,
    free_text_operator: Literal["AND", "OR"] = "AND",
) -> list[dict[str, Any]]:
    """AND の子ノードを compile し、FreeText 由来の ``bool.must`` を flatten する.

    ``free_text_operator=AND`` のときのみ FreeText が生成する ``bool.must=[multi_match_1, ...]``
    をそのまま親 AND の clauses に展開し、入れ子の bool wrapper を 1 段減らす。
    OR では FreeText が ``bool.should`` を生成するため、AND clauses に直接展開すると
    semantics が崩れる (token 間 OR ではなくなる)。そのため OR 時は flatten しない。
    OR / NOT 配下 (子の側) でも flatten しない (bool 構造の意味が変わるため)。
    """
    clauses: list[dict[str, Any]] = []
    for child in children:
        compiled = _compile_node(child, free_text_operator=free_text_operator)
        if isinstance(child, FreeText) and free_text_operator == "AND":
            inner = compiled.get("bool", {}).get("must")
            if isinstance(inner, list):
                clauses.extend(inner)
                continue
        clauses.append(compiled)
    return clauses


def _compile_leaf(clause: FieldClause) -> dict[str, Any]:
    strategy = _ES_FIELD_STRATEGY[clause.field]
    if strategy.kind == "flat":
        assert strategy.path is not None
        return _basic_leaf(strategy.path, clause)
    if strategy.kind == "or_flat":
        assert strategy.paths is not None
        return _or_over_fields(clause, strategy.paths)
    if strategy.kind == "nested":
        assert strategy.path is not None
        assert strategy.sub is not None
        # ``ignore_unmapped`` で、対応 nested path を持たない index (db=sra / db=jga が
        # 展開する非実在 subtype など) を shard exception でなく 0 件化する
        # (docs/db-portal-api-spec.md § フィールド allowlist).
        return {
            "nested": {
                "path": strategy.path,
                "query": _basic_leaf(strategy.sub, clause),
                "ignore_unmapped": True,
            },
        }
    # nested2: 外側・内側どちらの path が unmapped でも shard exception になるため両方に付ける
    assert strategy.path is not None
    assert strategy.inner_path is not None
    assert strategy.sub is not None
    return {
        "nested": {
            "path": strategy.path,
            "ignore_unmapped": True,
            "query": {
                "nested": {
                    "path": strategy.inner_path,
                    "query": _basic_leaf(strategy.sub, clause),
                    "ignore_unmapped": True,
                },
            },
        },
    }


def _or_over_fields(clause: FieldClause, es_fields: tuple[str, ...]) -> dict[str, Any]:
    return {
        "bool": {
            "should": [_basic_leaf(f, clause) for f in es_fields],
            "minimum_should_match": 1,
        },
    }


def _basic_leaf(es_field: str, clause: FieldClause) -> dict[str, Any]:
    field_type = FIELD_TYPES[clause.field]
    op = OPERATOR_BY_KIND[(field_type, clause.value_kind)]
    value = clause.value
    if op == "eq":
        return {"term": {es_field: value}}
    if op == "contains":
        return {"match_phrase": {es_field: value}}
    if op == "wildcard":
        # ES wildcard does not apply the analyzer to the value, so text-type
        # fields (tokenized lowercase) miss any uppercase letter in the
        # pattern and keyword fields miss values that do not match case
        # exactly.  ``case_insensitive`` restores the symmetric behaviour
        # users expect (staging probe 2026-04-24: ``title:Cancer*`` 0 → 10k).
        return {"wildcard": {es_field: {"value": value, "case_insensitive": True}}}
    if op == "between" and isinstance(value, Range):
        return {"range": {es_field: {"gte": value.from_, "lte": value.to}}}
    # 構造上ここに到達しない (validator が弾いている) が、mypy 安全のため
    raise ValueError(f"unsupported (field={clause.field!r}, op={op!r}) in compile_to_es")
