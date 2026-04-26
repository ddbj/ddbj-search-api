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
            params={"db": "bioproject", "adv": "title:cancer*", "perPage": 20},
        )
        upper = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "adv": "title:Cancer*", "perPage": 20},
        )
        assert lower.status_code == 200
        assert upper.status_code == 200
        assert lower.json()["total"] == upper.json()["total"]


class TestCursorAdvExclusivityEs:
    """IT-DSL-02: cursor + adv on ES-backed DB → 400 ``cursor-not-supported``."""

    def test_cursor_and_adv_returns_400(self, app: TestClient) -> None:
        """IT-DSL-02: 400 with the slug when cursor accompanies adv."""
        resp = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "adv": "title:cancer",
                "cursor": "any-token",
            },
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "cursor-not-supported" in body.get("type", "")


class TestCursorAdvExclusivitySolr:
    """IT-DSL-03: cursor + adv on Solr (trad) → same slug."""

    @pytest.mark.staging_only
    def test_cursor_and_adv_on_trad_returns_400(self, app: TestClient) -> None:
        """IT-DSL-03: Solr DB also returns ``cursor-not-supported`` 400."""
        resp = app.get(
            "/db-portal/search",
            params={
                "db": "trad",
                "adv": "title:cancer",
                "cursor": "any-token",
            },
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "cursor-not-supported" in body.get("type", "")


class TestDbPortalParseAst:
    """IT-DSL-04: GET /db-portal/parse converts DSL → AST JSON."""

    def test_parse_returns_object(self, app: TestClient) -> None:
        """IT-DSL-04: parse output is a non-empty object (queryTree shape)."""
        resp = app.get(
            "/db-portal/parse",
            params={"adv": "title:cancer", "db": "bioproject"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, dict)
        assert len(body) > 0


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
    """IT-DSL-06: grammar accepts symbol-bearing wildcards (HIF-1*, COVID-19*)."""

    def test_hif1_wildcard_parses(self, app: TestClient) -> None:
        """IT-DSL-06: ``HIF-1*`` parses without a syntax error."""
        resp = app.get(
            "/db-portal/parse",
            params={"adv": "title:HIF-1*", "db": "bioproject"},
        )
        assert resp.status_code == 200

    def test_covid19_wildcard_parses(self, app: TestClient) -> None:
        """IT-DSL-06: ``COVID-19*`` parses without a syntax error."""
        resp = app.get(
            "/db-portal/parse",
            params={"adv": "title:COVID-19*", "db": "bioproject"},
        )
        assert resp.status_code == 200


class TestParseCrossModeTier3Rejection:
    """IT-DSL-07: cross-mode (db omitted) rejects Tier 3 fields."""

    def test_tier3_field_returns_400(self, app: TestClient) -> None:
        """IT-DSL-07: ``japanese_name`` (TXSearch-only) cross-mode → 400."""
        resp = app.get(
            "/db-portal/parse",
            params={"adv": "japanese_name:Homo"},
        )
        assert resp.status_code == 400


class TestParseSyntaxError:
    """IT-DSL-08: malformed DSL → 400 ``unexpected-token``."""

    def test_syntax_error_returns_400(self, app: TestClient) -> None:
        """IT-DSL-08: 400 with a parse-error type URI on a malformed adv."""
        resp = app.get(
            "/db-portal/parse",
            params={"adv": "title:::cancer", "db": "bioproject"},
        )
        assert resp.status_code == 400


class TestParseUnknownField:
    """IT-DSL-09: allowlist-外 fields → 400 ``unknown-field``."""

    def test_unknown_field_returns_400(self, app: TestClient) -> None:
        """IT-DSL-09: unrecognised field name → 400."""
        resp = app.get(
            "/db-portal/parse",
            params={"adv": "__not_a_field__:value", "db": "bioproject"},
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
                "adv": "date_published:[2020-01-01 TO 2024-12-31]",
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
                "adv": "date_published:[2020-01-01 TO 2024-12-31]",
                "perPage": 20,
            },
        ).json()["total"]
        narrow = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "adv": "date_published:[2020-01-01 TO 2020-01-01]",
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
            params={"db": "bioproject", "adv": 'title:"whole genome"', "perPage": 20},
        )
        assert resp.status_code == 200

    def test_phrase_at_most_as_broad_as_token(self, app: TestClient) -> None:
        """IT-DSL-11: phrase total <= individual token AND total."""
        phrase = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "adv": 'title:"whole genome"', "perPage": 20},
        ).json()["total"]
        ands = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "adv": "title:whole AND title:genome",
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
                "adv": "title:cancer AND title:brain",
                "perPage": 20,
            },
        ).json()["total"]
        or_total = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "adv": "title:cancer OR title:brain",
                "perPage": 20,
            },
        ).json()["total"]
        assert and_total <= or_total

    def test_and_not_subset_of_left(self, app: TestClient) -> None:
        """IT-DSL-12: ``A AND NOT B`` total <= ``A`` total."""
        a_only = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "adv": "title:cancer", "perPage": 20},
        ).json()["total"]
        a_not_b = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "adv": "title:cancer AND NOT title:brain",
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
                "adv": "(title:cancer OR title:brain) AND date_published:[2020-01-01 TO 2024-12-31]",
                "perPage": 20,
            },
        ).json()["total"]
        right_grouped = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "adv": "title:cancer OR (title:brain AND date_published:[2020-01-01 TO 2024-12-31])",
                "perPage": 20,
            },
        ).json()["total"]
        # Different parse trees ⇒ result sets must differ.
        assert left_grouped != right_grouped


class TestEnumOperator:
    """IT-DSL-13: enum operator on ``project_type``."""

    def test_umbrella_bioproject_succeeds(self, app: TestClient) -> None:
        """IT-DSL-13: ``project_type:UmbrellaBioProject`` returns 200 with hits."""
        resp = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "adv": "project_type:UmbrellaBioProject",
                "perPage": 20,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["total"] > 0

    def test_two_buckets_disjoint(self, app: TestClient) -> None:
        """IT-DSL-13: BioProject + UmbrellaBioProject sums to OR(both)."""
        primary = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "adv": "project_type:BioProject", "perPage": 20},
        ).json()["total"]
        umbrella = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "adv": "project_type:UmbrellaBioProject",
                "perPage": 20,
            },
        ).json()["total"]
        either = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "adv": "project_type:BioProject OR project_type:UmbrellaBioProject",
                "perPage": 20,
            },
        ).json()["total"]
        assert either == primary + umbrella

    def test_unknown_enum_value_yields_zero(self, app: TestClient) -> None:
        """IT-DSL-13: a value outside the enum is parsed but matches 0 docs."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "adv": "project_type:Foobar", "perPage": 20},
        )
        # The validator allows any enum-typed value; the ES side returns 0.
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


class TestTwoLevelNestedGrantAgency:
    """IT-DSL-14: two-level nested ``grant_agency`` filter."""

    def test_grant_agency_returns_200(self, app: TestClient) -> None:
        """IT-DSL-14: 2-level nested DSL parses and runs (no silent 5xx)."""
        resp = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "adv": "grant_agency:NIH", "perPage": 20},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] >= 0

    def test_combined_with_title_narrows(self, app: TestClient) -> None:
        """IT-DSL-14: ``grant_agency:NIH AND title:cancer`` <= grant_agency alone."""
        ga = app.get(
            "/db-portal/search",
            params={"db": "bioproject", "adv": "grant_agency:NIH", "perPage": 20},
        ).json()["total"]
        ga_and_title = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "adv": "grant_agency:NIH AND title:cancer",
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
        "adv",
        [
            "date_published:[2024-02-30 TO 2024-12-31]",  # impossible day
            "date_published:[2024-13-01 TO 2024-12-31]",  # impossible month
        ],
    )
    def test_returns_400_with_slug(self, app: TestClient, adv: str) -> None:
        """IT-DSL-15: impossible dates surface as ``invalid-date-format``."""
        resp = app.get("/db-portal/parse", params={"adv": adv, "db": "bioproject"})
        assert resp.status_code == 400, adv
        assert "invalid-date-format" in resp.json().get("type", ""), adv


class TestInvalidOperatorForField:
    """IT-DSL-16: ``invalid-operator-for-field`` 400."""

    @pytest.mark.parametrize(
        "adv",
        [
            "date_published:cancer*",  # wildcard on date
            "identifier:[PRJDB1 TO PRJDB99]",  # between on identifier
            "organism:cancer*",  # wildcard on organism (eq-only)
        ],
    )
    def test_returns_400_with_slug(self, app: TestClient, adv: str) -> None:
        """IT-DSL-16: type/operator mismatch returns the correct slug."""
        resp = app.get("/db-portal/parse", params={"adv": adv, "db": "bioproject"})
        assert resp.status_code == 400, adv
        assert "invalid-operator-for-field" in resp.json().get("type", ""), adv


class TestNestDepthExceeded:
    """IT-DSL-17: AND/OR/NOT nest depth limit (max_depth = 5)."""

    def test_max_depth_succeeds(self, app: TestClient) -> None:
        """IT-DSL-17: 5 nested boolean layers (the documented max) parse cleanly."""
        # FieldClause leaves return early in ``_check_depth``, so a 5-layer
        # OR chain stays at depth 5.
        adv = "(((((title:a OR title:b) OR title:c) OR title:d) OR title:e) OR title:f)"
        resp = app.get("/db-portal/parse", params={"adv": adv, "db": "bioproject"})
        assert resp.status_code == 200

    def test_above_max_returns_400(self, app: TestClient) -> None:
        """IT-DSL-17: 6 nested boolean layers raise ``nest-depth-exceeded``."""
        adv = "((((((title:a OR title:b) OR title:c) OR title:d) OR title:e) OR title:f) OR title:g)"
        resp = app.get("/db-portal/parse", params={"adv": adv, "db": "bioproject"})
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
            params={"adv": 'title:""', "db": "bioproject"},
        )
        assert resp.status_code == 400
        assert "missing-value" in resp.json().get("type", "")

    def test_explicit_empty_single_quotes_returns_400(self, app: TestClient) -> None:
        """IT-DSL-18: ``title:''`` (single quote) も同 slug で reject される."""
        resp = app.get(
            "/db-portal/parse",
            params={"adv": "title:''", "db": "bioproject"},
        )
        assert resp.status_code == 400
        assert "missing-value" in resp.json().get("type", "")


class TestSingleQuotePhrase:
    """IT-DSL-19: single quote phrase は double quote と同等 AST を返す.

    `q=` 側 (``ddbj_search_api/search/phrase.py``) と整合させるため、
    ``adv`` でも ``field:'value'`` を ``field:"value"`` と同じ phrase として
    parse する。共有 URL や frontend からの query 渡しで quote を変換せずに
    済むようにするための互換性保証。
    """

    def test_single_quote_phrase_parses_as_phrase(self, app: TestClient) -> None:
        """IT-DSL-19: ``organism:'Homo sapiens'`` が 200 で eq AST を返す."""
        resp = app.get(
            "/db-portal/parse",
            params={"adv": "organism:'Homo sapiens'", "db": "bioproject"},
        )
        assert resp.status_code == 200
        ast = resp.json().get("ast", {})
        assert ast.get("op") == "eq"
        assert ast.get("field") == "organism"
        assert ast.get("value") == "Homo sapiens"

    def test_single_and_double_quote_yield_same_ast(self, app: TestClient) -> None:
        """IT-DSL-19: single / double quote phrase は同一 AST."""
        single = app.get(
            "/db-portal/parse",
            params={"adv": "organism:'Homo sapiens'", "db": "bioproject"},
        ).json()
        double = app.get(
            "/db-portal/parse",
            params={"adv": 'organism:"Homo sapiens"', "db": "bioproject"},
        ).json()
        assert single == double

    def test_single_quote_phrase_search_total_matches_double(self, app: TestClient) -> None:
        """IT-DSL-19: 検索 endpoint でも single / double quote で同じ ``total``."""
        single = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "adv": "title:'whole genome'",
                "perPage": 20,
            },
        )
        double = app.get(
            "/db-portal/search",
            params={
                "db": "bioproject",
                "adv": 'title:"whole genome"',
                "perPage": 20,
            },
        )
        assert single.status_code == 200
        assert double.status_code == 200
        assert single.json()["total"] == double.json()["total"]
