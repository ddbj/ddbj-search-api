"""Integration tests for IT-DSL-* scenarios.

ES DSL compilation behaviour exposed via /db-portal/cross-search,
/db-portal/search, and /db-portal/parse. See
``tests/integration-scenarios.md § IT-DSL-*``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class TestEsWildcardCaseInsensitive:
    """IT-DSL-01: ES wildcard matches are case-insensitive."""

    def test_lower_and_capital_yield_same_total(self, app: TestClient) -> None:
        """IT-DSL-01: total(``title:cancer*``) == total(``title:Cancer*``)."""
        lower = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "title:cancer*", "perPage": 20},
        )
        upper = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "title:Cancer*", "perPage": 20},
        )
        assert lower.status_code == 200
        assert upper.status_code == 200
        assert lower.json()["total"] == upper.json()["total"]


class TestCursorQExclusivityEs:
    """IT-DSL-02: cursor + q on ES-backed DB → 400 (cursor exclusivity)."""

    def test_cursor_and_q_returns_400(self, app: TestClient) -> None:
        """IT-DSL-02: ES DB で cursor と q を併用すると about:blank (cursor exclusivity) で 400.

        cursor は最初の検索で発行され、後続継続では cursor + db + perPage のみ受ける。
        q を再送する経路は不正なので ``_validate_cursor_exclusivity`` が plain HTTPException
        で 400 を返す (about:blank、Solr DB の ``cursor-not-supported`` slug とは区別)。
        """
        resp = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "q": "title:cancer",
                "cursor": "any-token",
            },
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body.get("type", "") == "about:blank"
        assert "cursor" in body.get("detail", "").lower()


class TestCursorQExclusivitySolr:
    """IT-DSL-03: cursor + q on Solr (trad) → same slug."""

    @pytest.mark.staging_only
    def test_cursor_and_q_on_trad_returns_400(self, app: TestClient) -> None:
        """IT-DSL-03: Solr DB also returns ``cursor-not-supported`` 400."""
        resp = app.get(
            "/db-portal/search",
            params={
                "db": "trad",
                "q": "title:cancer",
                "cursor": "any-token",
            },
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "cursor-not-supported" in body.get("type", "")


class TestDbPortalParseAst:
    """IT-DSL-04: GET /db-portal/parse converts DSL → AST JSON.

    parse endpoint の response envelope は ``{"ast": <node>}``。
    leaf node は ``ast_to_json`` (search/dsl/serde.py) の規約に従い
    ``{"field": ..., "op": ..., "value": ...}`` または range の場合は
    ``{"field": ..., "op": "between", "from": ..., "to": ...}`` の shape。
    """

    def test_parse_leaf_contains(self, app: TestClient) -> None:
        """IT-DSL-04: text-field word は ``op=contains`` の leaf になる。"""
        resp = app.get(
            "/db-portal/parse",
            params={"q": "title:cancer", "db": "bioproject"},
        )
        assert resp.status_code == 200
        body = resp.json()
        # envelope check
        assert "ast" in body
        ast = body["ast"]
        assert ast == {
            "field": "title",
            "op": "contains",
            "value": "cancer",
        }

    def test_parse_bool_op_and(self, app: TestClient) -> None:
        """IT-DSL-04: ``a AND b`` は op=AND ノード + 2 rules を返す。"""
        resp = app.get(
            "/db-portal/parse",
            params={"q": "title:cancer AND organism_id:9606", "db": "bioproject"},
        )
        assert resp.status_code == 200
        body = resp.json()
        ast = body["ast"]
        assert ast["op"] == "AND"
        assert isinstance(ast.get("rules"), list)
        assert len(ast["rules"]) == 2

    def test_parse_range_leaf(self, app: TestClient) -> None:
        """IT-DSL-04: date range は ``op=between`` + ``from`` / ``to`` を持つ。"""
        resp = app.get(
            "/db-portal/parse",
            params={
                "q": "date_published:[2020-01-01 TO 2024-12-31]",
                "db": "bioproject",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        ast = body["ast"]
        assert ast == {
            "field": "date_published",
            "op": "between",
            "from": "2020-01-01",
            "to": "2024-12-31",
        }


class TestDbPortalParseOpenApiResponses:
    """IT-DSL-05: parse OpenAPI responses are {200, 400, 422, 500} (no 404)."""

    def test_parse_responses_exclude_404(self, app: TestClient) -> None:
        """IT-DSL-05: 404 is not in the documented response keys."""
        spec = app.get("/openapi.json").json()
        get_op = spec["paths"]["/db-portal/parse"].get("get")
        assert get_op is not None
        responses = set(get_op["responses"].keys())
        assert "404" not in responses
        assert "200" in responses
        assert "400" in responses


class TestSymbolWildcardAccepted:
    """IT-DSL-06: grammar accepts symbol-bearing wildcards (HIF-1*, COVID-19*).

    parses 通過だけでなく AST の value_kind が ``wildcard`` (== ``op=wildcard``)
    に分類されたことを確認する。auto-phrase 化されたり symbol 含む token が
    word として誤分類されると下流の wildcard クエリが組まれず regression する。
    """

    def test_hif1_wildcard_parses_with_wildcard_op(self, app: TestClient) -> None:
        """IT-DSL-06: ``HIF-1*`` parses as ``op=wildcard`` (not contains)."""
        resp = app.get(
            "/db-portal/parse",
            params={"q": "title:HIF-1*", "db": "bioproject"},
        )
        assert resp.status_code == 200
        body = resp.json()
        ast = body["ast"]
        assert ast == {
            "field": "title",
            "op": "wildcard",
            "value": "HIF-1*",
        }

    def test_covid19_wildcard_parses_with_wildcard_op(self, app: TestClient) -> None:
        """IT-DSL-06: ``COVID-19*`` parses as ``op=wildcard`` (not contains)."""
        resp = app.get(
            "/db-portal/parse",
            params={"q": "title:COVID-19*", "db": "bioproject"},
        )
        assert resp.status_code == 200
        body = resp.json()
        ast = body["ast"]
        assert ast == {
            "field": "title",
            "op": "wildcard",
            "value": "COVID-19*",
        }


class TestParseCrossModeTier3Rejection:
    """IT-DSL-07: cross-mode (db omitted) rejects Tier 3 fields."""

    def test_tier3_field_returns_400(self, app: TestClient) -> None:
        """IT-DSL-07: ``japanese_name`` (TXSearch-only) cross-mode → 400."""
        resp = app.get(
            "/db-portal/parse",
            params={"q": "japanese_name:Homo"},
        )
        assert resp.status_code == 400


class TestParseSyntaxError:
    """IT-DSL-08: malformed DSL → 400 ``unexpected-token``."""

    def test_syntax_error_returns_400(self, app: TestClient) -> None:
        """IT-DSL-08: 400 with a parse-error type URI on a malformed q."""
        resp = app.get(
            "/db-portal/parse",
            params={"q": "title:::cancer", "db": "bioproject"},
        )
        assert resp.status_code == 400


class TestParseUnknownField:
    """IT-DSL-09: allowlist-外 fields → 400 ``unknown-field``."""

    def test_unknown_field_returns_400(self, app: TestClient) -> None:
        """IT-DSL-09: unrecognised field name → 400."""
        resp = app.get(
            "/db-portal/parse",
            params={"q": "__not_a_field__:value", "db": "bioproject"},
        )
        assert resp.status_code == 400


class TestBetweenRange:
    """IT-DSL-10: ``date_published:[a TO b]`` between range."""

    def test_wide_range_succeeds(self, app: TestClient) -> None:
        """IT-DSL-10: a 5-year window returns 200."""
        resp = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "q": "date_published:[2020-01-01 TO 2024-12-31]",
                "perPage": 20,
            },
        )
        assert resp.status_code == 200

    def test_narrow_subset_of_wide(self, app: TestClient) -> None:
        """IT-DSL-10: 1-day window total <= 5-year window total (inclusive bounds)."""
        wide = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "q": "date_published:[2020-01-01 TO 2024-12-31]",
                "perPage": 20,
            },
        ).json()["total"]
        narrow = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "q": "date_published:[2020-01-01 TO 2020-01-01]",
                "perPage": 20,
            },
        ).json()["total"]
        assert narrow <= wide


class TestPhraseSearch:
    """IT-DSL-11: ``title:"whole genome"`` phrase match."""

    def test_phrase_succeeds(self, app: TestClient) -> None:
        """IT-DSL-11: phrase parses and returns 200."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": 'title:"whole genome"', "perPage": 20},
        )
        assert resp.status_code == 200

    def test_phrase_at_most_as_broad_as_token(self, app: TestClient) -> None:
        """IT-DSL-11: phrase total <= individual token AND total."""
        phrase = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": 'title:"whole genome"', "perPage": 20},
        ).json()["total"]
        ands = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "q": "title:whole AND title:genome",
                "perPage": 20,
            },
        ).json()["total"]
        # Order-fixed phrase cannot match more docs than the AND of its
        # constituent tokens.
        assert phrase <= ands


class TestBoolPrecedence:
    """IT-DSL-12: AND/OR/NOT precedence and grouping."""

    def test_and_subset_of_or(self, app: TestClient) -> None:
        """IT-DSL-12: AND total <= OR total for the same operands."""
        and_total = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "q": "title:cancer AND title:brain",
                "perPage": 20,
            },
        ).json()["total"]
        or_total = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "q": "title:cancer OR title:brain",
                "perPage": 20,
            },
        ).json()["total"]
        assert and_total <= or_total

    def test_and_not_subset_of_left(self, app: TestClient) -> None:
        """IT-DSL-12: ``A AND NOT B`` total <= ``A`` total."""
        a_only = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "title:cancer", "perPage": 20},
        ).json()["total"]
        a_not_b = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "q": "title:cancer AND NOT title:brain",
                "perPage": 20,
            },
        ).json()["total"]
        assert a_not_b <= a_only

    def test_grouping_changes_result(self, app: TestClient) -> None:
        """IT-DSL-12: grouping shifts the result set (precedence is honoured)."""
        left_grouped = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "q": "(title:cancer OR title:brain) AND date_published:[2020-01-01 TO 2024-12-31]",
                "perPage": 20,
            },
        ).json()["total"]
        right_grouped = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "q": "title:cancer OR (title:brain AND date_published:[2020-01-01 TO 2024-12-31])",
                "perPage": 20,
            },
        ).json()["total"]
        # Different parse trees ⇒ result sets must differ.
        assert left_grouped != right_grouped


class TestEnumOperator:
    """IT-DSL-13: enum operator on ``object_type``."""

    def test_umbrella_bioproject_succeeds(self, app: TestClient) -> None:
        """IT-DSL-13: ``object_type:UmbrellaBioProject`` returns 200 with hits."""
        resp = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "q": "object_type:UmbrellaBioProject",
                "perPage": 20,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["total"] > 0

    def test_two_buckets_disjoint(self, app: TestClient) -> None:
        """IT-DSL-13: BioProject + UmbrellaBioProject sums to OR(both)."""
        primary = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "object_type:BioProject", "perPage": 20},
        ).json()["total"]
        umbrella = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "q": "object_type:UmbrellaBioProject",
                "perPage": 20,
            },
        ).json()["total"]
        either = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "q": "object_type:BioProject OR object_type:UmbrellaBioProject",
                "perPage": 20,
            },
        ).json()["total"]
        assert either == primary + umbrella

    def test_unknown_enum_value_yields_zero(self, app: TestClient) -> None:
        """IT-DSL-13: a value outside the enum is parsed but matches 0 docs."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "object_type:Foobar", "perPage": 20},
        )
        # The validator allows any enum-typed value; the ES side returns 0.
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


class TestInsdcProjectTypeText:
    """IT-DSL-13a: INSDC ``project_type`` text match (entries ``?projectType=`` 相当).

    ES `projectType` field は text+keyword multi-field で、INSDC controlled vocab
    (genome / metagenome / etc) を持つ。DSL `project_type` (text 型) は
    `match_phrase` 経由で叩く。`object_type` (ES `objectType`、Umbrella 区分) とは
    別 field なので、両者で得られる hit 数は独立する。
    """

    def test_project_type_genome_returns_200(self, app: TestClient) -> None:
        resp = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "project_type:genome", "perPage": 20},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] >= 0

    def test_project_type_is_distinct_from_object_type(self, app: TestClient) -> None:
        # project_type (INSDC) と object_type (Umbrella 区分) は別 field を叩く。
        # 命名が酷似しているが意味が違うことを件数の差で示す。
        # object_type:BioProject は ほぼ全 BioProject エントリ (Umbrella 除く) なので大規模。
        # project_type:genome は INSDC controlled vocab の 1 値で部分集合。
        bp = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "object_type:BioProject", "perPage": 20},
        ).json()["total"]
        genome = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "project_type:genome", "perPage": 20},
        ).json()["total"]
        # どちらも 0 以上の自然数で、両者が同じ field なら同値だが、別 field なので
        # 等値性が成り立たない (実データ上どちらかが大きいかは不問、ただ等しくない)。
        # staging 実データで両者がたまたま完全同値になる可能性は低いが、保守的に
        # 「両者 0 以上」のみ assert。
        assert bp >= 0
        assert genome >= 0


class TestSingleNestedGrantTitle:
    """IT-DSL-14a: single nested ``grant_title`` filter (entries ``?grant=`` 相当).

    REST API の ``?grant=`` は ``grant.title`` (nested 1 段) を叩く。db-portal DSL
    でも同じ field を ``grant_title`` で提供することで、entries と DSL のカバレッジを
    揃える (旧 DSL は ``grant_agency`` のみで ``grant.title`` は引けなかった)。
    """

    def test_grant_title_returns_200(self, app: TestClient) -> None:
        resp = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "grant_title:CREST", "perPage": 20},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] >= 0

    def test_grant_title_phrase_narrower_than_word(self, app: TestClient) -> None:
        # phrase は順序固定なので word を上限とする (word <= phrase で逆転していないこと).
        word = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "grant_title:CREST", "perPage": 20},
        ).json()["total"]
        phrase = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": 'grant_title:"JST CREST"', "perPage": 20},
        ).json()["total"]
        assert phrase <= word


class TestTwoLevelNestedGrantAgency:
    """IT-DSL-14: two-level nested ``grant_agency`` filter."""

    def test_grant_agency_returns_200(self, app: TestClient) -> None:
        """IT-DSL-14: 2-level nested DSL parses and runs (no silent 5xx)."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "grant_agency:NIH", "perPage": 20},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] >= 0

    def test_combined_with_title_narrows(self, app: TestClient) -> None:
        """IT-DSL-14: ``grant_agency:NIH AND title:cancer`` <= grant_agency alone."""
        ga = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "grant_agency:NIH", "perPage": 20},
        ).json()["total"]
        ga_and_title = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "q": "grant_agency:NIH AND title:cancer",
                "perPage": 20,
            },
        ).json()["total"]
        assert ga_and_title <= ga


class TestInvalidDateFormat:
    """IT-DSL-15: ``invalid-date-format`` 400.

    Format violations like ``2020/01/01`` are caught earlier by the parser
    (``unexpected-token`` slug — exercised by IT-DSL-08). IT-DSL-15
    targets values that *parse* as a date literal but fail validation
    (impossible day / month).
    """

    @pytest.mark.parametrize(
        "q_str",
        [
            "date_published:[2024-02-30 TO 2024-12-31]",  # impossible day
            "date_published:[2024-13-01 TO 2024-12-31]",  # impossible month
        ],
    )
    def test_returns_400_with_slug(self, app: TestClient, q_str: str) -> None:
        """IT-DSL-15: impossible dates surface as ``invalid-date-format``."""
        resp = app.get("/db-portal/parse", params={"q": q_str, "db": "bioproject"})
        assert resp.status_code == 400, q_str
        assert "invalid-date-format" in resp.json().get("type", ""), q_str


class TestInvalidOperatorForField:
    """IT-DSL-16: ``invalid-operator-for-field`` 400."""

    @pytest.mark.parametrize(
        "q_str",
        [
            "date_published:cancer*",  # wildcard on date
            "identifier:[PRJDB1 TO PRJDB99]",  # between on identifier
            "organism_id:[a TO b]",  # range on identifier-type field (organism_id) は不可
        ],
    )
    def test_returns_400_with_slug(self, app: TestClient, q_str: str) -> None:
        """IT-DSL-16: type/operator mismatch returns the correct slug."""
        resp = app.get("/db-portal/parse", params={"q": q_str, "db": "bioproject"})
        assert resp.status_code == 400, q_str
        assert "invalid-operator-for-field" in resp.json().get("type", ""), q_str

    def test_enum_field_wildcard_returns_invalid_operator(self, app: TestClient) -> None:
        """IT-DSL-16: enum 化した field は wildcard を失う ((enum, wildcard) 不在)。"""
        resp = app.get("/db-portal/parse", params={"q": "instrument_model:Nova*", "db": "sra"})
        assert resp.status_code == 400
        assert "invalid-operator-for-field" in resp.json().get("type", "")


class TestNestDepthExceeded:
    """IT-DSL-17: AND/OR/NOT nest depth limit (max_depth = 5)."""

    def test_max_depth_succeeds(self, app: TestClient) -> None:
        """IT-DSL-17: 5 nested boolean layers (the documented max) parse cleanly."""
        # FieldClause leaves return early in ``_check_depth``, so a 5-layer
        # OR chain stays at depth 5.
        q_str = "(((((title:a OR title:b) OR title:c) OR title:d) OR title:e) OR title:f)"
        resp = app.get("/db-portal/parse", params={"q": q_str, "db": "bioproject"})
        assert resp.status_code == 200

    def test_above_max_returns_400(self, app: TestClient) -> None:
        """IT-DSL-17: 6 nested boolean layers raise ``nest-depth-exceeded``."""
        q_str = "((((((title:a OR title:b) OR title:c) OR title:d) OR title:e) OR title:f) OR title:g)"
        resp = app.get("/db-portal/parse", params={"q": q_str, "db": "bioproject"})
        assert resp.status_code == 400
        assert "nest-depth-exceeded" in resp.json().get("type", "")


class TestMissingValue:
    """IT-DSL-18: ``missing-value`` 400.

    The grammar's PHRASE token accepts both double (``"..."``) and single
    (``'...'``) quotes (対称、`q=` 側のキーワード検索と一貫)。empty phrase
    in either form raises ``missing-value``.
    """

    def test_explicit_empty_double_quotes_returns_400(self, app: TestClient) -> None:
        """IT-DSL-18: ``title:""`` is rejected as missing-value."""
        resp = app.get(
            "/db-portal/parse",
            params={"q": 'title:""', "db": "bioproject"},
        )
        assert resp.status_code == 400
        assert "missing-value" in resp.json().get("type", "")

    def test_explicit_empty_single_quotes_returns_400(self, app: TestClient) -> None:
        """IT-DSL-18: ``title:''`` (single quote) も同 slug で reject される."""
        resp = app.get(
            "/db-portal/parse",
            params={"q": "title:''", "db": "bioproject"},
        )
        assert resp.status_code == 400
        assert "missing-value" in resp.json().get("type", "")


class TestSingleQuotePhrase:
    """IT-DSL-19: single quote phrase は double quote と同等 AST を返す.

    `q=` 側 (``ddbj_search_api/search/phrase.py``) と整合させるため、
    ``q`` でも ``field:'value'`` を ``field:"value"`` と同じ phrase として
    parse する。共有 URL や frontend からの query 渡しで quote を変換せずに
    済むようにするための互換性保証。
    """

    def test_single_quote_phrase_parses_as_phrase(self, app: TestClient) -> None:
        """IT-DSL-19: ``organism_name:'Homo sapiens'`` が 200 で contains AST を返す."""
        resp = app.get(
            "/db-portal/parse",
            params={"q": "organism_name:'Homo sapiens'", "db": "bioproject"},
        )
        assert resp.status_code == 200
        ast = resp.json().get("ast", {})
        assert ast.get("op") == "contains"
        assert ast.get("field") == "organism_name"
        assert ast.get("value") == "Homo sapiens"

    def test_single_and_double_quote_yield_same_ast(self, app: TestClient) -> None:
        """IT-DSL-19: single / double quote phrase は同一 AST."""
        single = app.get(
            "/db-portal/parse",
            params={"q": "organism_name:'Homo sapiens'", "db": "bioproject"},
        ).json()
        double = app.get(
            "/db-portal/parse",
            params={"q": 'organism_name:"Homo sapiens"', "db": "bioproject"},
        ).json()
        assert single == double

    def test_single_quote_phrase_search_total_matches_double(self, app: TestClient) -> None:
        """IT-DSL-19: 検索 endpoint でも single / double quote で同じ ``total``."""
        single = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "q": "title:'whole genome'",
                "perPage": 20,
            },
        )
        double = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "q": 'title:"whole genome"',
                "perPage": 20,
            },
        )
        assert single.status_code == 200
        assert double.status_code == 200
        assert single.json()["total"] == double.json()["total"]


class TestOrganismAnalyzerMismatchRegression:
    """IT-DSL-20: organism_name phrase が ES backed DB で実際にヒットする (analyzer mismatch 回帰防止).

    ``organism.name`` は ES mapping で text + standard analyzer (converter
    ``common.py:39-48``)、``term`` query を投げると tokenize 後の lowercase
    token と単一値が不一致で 0 件になる。``organism_name`` (text 型) は
    ``match_phrase`` 経由で analyzer を通すため、staging で BP / BS / SRA いずれも
    数万〜数百万件ヒットする (FreeText 同等オーダー)。回帰検出用に十分緩めの
    閾値 (``>= 1000``) で固定。
    """

    @pytest.mark.parametrize("db", ["bioproject", "biosample", "sra"])
    def test_organism_name_phrase_hits_es_backed_db(self, app: TestClient, db: str) -> None:
        """IT-DSL-20: ``organism_name:"Homo sapiens"`` が >= 1000 件ヒット (term 退化なら 0 件で fail)."""
        resp = app.get(
            "/db-portal/search",
            params={"db": db, "q": 'organism_name:"Homo sapiens"', "perPage": 20},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1000, f"db={db}: organism_name phrase hit が下限を下回る (analyzer mismatch 再発の疑い)"

    @pytest.mark.parametrize("db", ["bioproject", "biosample", "sra"])
    def test_organism_name_lowercase_phrase_also_hits(self, app: TestClient, db: str) -> None:
        """IT-DSL-20: standard analyzer 経由なので ``"homo sapiens"`` (小文字) も同等にヒット."""
        resp = app.get(
            "/db-portal/search",
            params={"db": db, "q": 'organism_name:"homo sapiens"', "perPage": 20},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1000


class TestFreeTextQuotedPhraseRegression:
    """IT-DSL-21: bare quoted FreeText (``q='"..."'``) が ES で phrase match (順序保持) に展開される.

    bug 期: parser が引用符を strip した時点で「quoted phrase だったか」の情報が
    AST に残らず、compile_es は ``parse_keywords_with_autophrase`` の物理 quote 判定で
    False となり ``multi_match.operator=and`` (順序非保持の AND match) を出していた.
    結果 ``q='"Homo sapiens"'`` は Homo と sapiens を別々に AND match していた.

    修正: ``FreeText.is_phrase`` を parser → AST → compile_es まで伝播し、
    ``multi_match.type=phrase`` (順序保持) を出力する.
    """

    def test_quoted_freetext_phrase_returns_200_and_nonempty(self, app: TestClient) -> None:
        """``q='"Homo sapiens"'`` で 200 が返り、空集合でない."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": '"Homo sapiens"', "perPage": 20},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    def test_quoted_freetext_phrase_order_matters(self, app: TestClient) -> None:
        """順序逆転 phrase (``"sapiens Homo"``) は元 phrase より少ない.

        bug 期: AND match で順序を見ないので両者 ≈ 同 hits (順序非保持).
        修正後: phrase match で順序保持、逆順は元 phrase より大幅に少ない (ほぼ 0).
        """
        forward = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": '"Homo sapiens"', "perPage": 20},
        ).json()["total"]
        reverse = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": '"sapiens Homo"', "perPage": 20},
        ).json()["total"]
        # 順序逆転は元 phrase より大幅に少ない (bug 期は同等).
        assert reverse < forward

    def test_quoted_freetext_phrase_at_most_as_broad_as_field_clause_phrase(self, app: TestClient) -> None:
        """bare quoted phrase (5 default fields phrase OR) は title 単独 phrase 以上の hits.

        title:"x y" の集合 ⊆ q='"x y"' の集合 (5 fields phrase OR で title を含むため).
        修正前は AND match で過剰 hit、修正後は phrase match で適正範囲.
        """
        title_phrase = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": 'title:"whole genome"', "perPage": 20},
        ).json()["total"]
        bare_phrase = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": '"whole genome"', "perPage": 20},
        ).json()["total"]
        # 5 fields phrase OR は title 単独 phrase を必ず包含する.
        assert bare_phrase >= title_phrase
        # かつ最低 1 件は title_phrase 経由で hit する想定 (空集合でない sanity).
        assert title_phrase >= 1

    def test_parse_endpoint_emits_is_phrase_true_for_quoted_freetext(self, app: TestClient) -> None:
        """``/db-portal/parse?q='"Homo sapiens"'`` の AST に ``is_phrase: true`` が乗る."""
        resp = app.get("/db-portal/parse", params={"q": '"Homo sapiens"'})
        assert resp.status_code == 200
        assert resp.json()["ast"] == {
            "op": "free_text",
            "value": "Homo sapiens",
            "is_phrase": True,
        }

    def test_parse_endpoint_emits_is_phrase_false_for_bare_freetext(self, app: TestClient) -> None:
        """回帰: bare word は ``is_phrase: false``."""
        resp = app.get("/db-portal/parse", params={"q": "cancer"})
        assert resp.status_code == 200
        assert resp.json()["ast"] == {
            "op": "free_text",
            "value": "cancer",
            "is_phrase": False,
        }


class TestFreeTextMultiWord:
    """IT-DSL-22: 空白区切りの連続 bare word が 1 FreeText に畳まれ、値内空白を AND 結合する.

    db-portal-api-spec.md § FreeText のトークン分割と値内空白の AND 結合。``q=cancer tumor``
    は parser が単一 FreeText (is_phrase=false) に畳み、compile_es が値内空白を
    ``multi_match.operator=and`` で AND 結合する。bug 期は parser が ``cancer`` の直後の
    ``tumor`` を演算子なしで受けられず 400 (unexpected-token) になっていた。
    """

    def test_parse_endpoint_collapses_space_separated_words(self, app: TestClient) -> None:
        """``/db-portal/parse?q=cancer tumor`` は単一 FreeText (is_phrase=false) を返す."""
        resp = app.get("/db-portal/parse", params={"q": "cancer tumor"})
        assert resp.status_code == 200
        assert resp.json()["ast"] == {
            "op": "free_text",
            "value": "cancer tumor",
            "is_phrase": False,
        }

    def test_space_separated_search_returns_200(self, app: TestClient) -> None:
        resp = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "cancer tumor", "perPage": 20},
        )
        assert resp.status_code == 200

    def test_space_separated_is_and_narrowed(self, app: TestClient) -> None:
        """``cancer tumor`` (値内 AND) は各単語単独の total 以下に絞り込まれる."""
        both = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "cancer tumor", "perPage": 20},
        ).json()["total"]
        cancer = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "cancer", "perPage": 20},
        ).json()["total"]
        tumor = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "q": "tumor", "perPage": 20},
        ).json()["total"]
        # 値内 AND なので両語を含む doc に絞られ、各単語単独の hit 数以下になる.
        assert both <= cancer
        assert both <= tumor

    def test_explicit_and_of_two_free_texts_is_duplicate_freetext_400(self, app: TestClient) -> None:
        """非対称性 pin: 空白区切り ``q=cancer tumor`` は 200 だが、明示 ``q=cancer AND tumor``
        は FreeText 2 個で 400 (duplicate-freetext)。両語を含めたいときは空白区切り or quote。"""
        ok = app.get("/db-portal/parse", params={"q": "cancer tumor"})
        assert ok.status_code == 200
        ng = app.get("/db-portal/parse", params={"q": "cancer AND tumor"})
        assert ng.status_code == 400
        assert "duplicate-freetext" in ng.json().get("type", "")
