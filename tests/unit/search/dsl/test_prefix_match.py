"""前方一致 (prefix match) の仕様検証テスト.

SSOT: docs/api-spec.md § フレーズマッチ / 前方一致 / データ可視性、
docs/db-portal-api-spec.md § FreeText の前方一致 / 演算子マトリクス。

keyword box (free-text ``q`` / ``keywords``) と field-scoped ``contains`` で、
記号なし・クオートなしの simple word (末尾語 2 文字以上) だけが「完全一致 + 末尾前方
一致」に展開され、quoted 値・記号含み語・1 文字語・enum/identifier 型は完全一致のまま、
という不変条件を ES / Solr 両バックエンドで検証する。さらに accession 完全一致で
suppressed を解禁したクエリでは前方一致を抑止する (別 accession の suppressed 漏洩防止)。
実装の出力を写経するのではなく、仕様 (打ちかけ対応 / quote=厳密 / 最小 prefix 長 /
edismax メタ文字安全 / 可視性) から導いた性質を assert する。
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ddbj_search_api.es.query import build_search_query, es_query_from_ast
from ddbj_search_api.search.dsl import parse
from ddbj_search_api.search.dsl.compiler_es import compile_free_text, compile_to_es
from ddbj_search_api.search.dsl.compiler_solr import compile_free_text_solr, compile_to_solr

_DEFAULT_FIELDS = ["identifier", "title", "name", "description", "organism.name"]


def _iter_subdicts(node: Any) -> list[dict[str, Any]]:
    """dict ツリーを再帰的に走査して全 dict ノードを返す (キー存在判定用)."""
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        found.append(node)
        for v in node.values():
            found.extend(_iter_subdicts(v))
    elif isinstance(node, list):
        for v in node:
            found.extend(_iter_subdicts(v))
    return found


def _has_clause(node: Any, key: str) -> bool:
    """ツリー内に ``key`` を持つ dict ノードが 1 つでもあるか."""
    return any(key in d for d in _iter_subdicts(node))


def _multi_match_types(node: Any) -> list[str]:
    """ツリー内の全 multi_match について type を返す (operator:and は 'and' と表現)."""
    types: list[str] = []
    for d in _iter_subdicts(node):
        mm = d.get("multi_match")
        if isinstance(mm, dict):
            types.append(mm.get("type", "and" if mm.get("operator") == "and" else "default"))
    return types


# === ES: keyword box ===


class TestKeywordBoxPrefix:
    def test_bare_word_expands_to_exact_and_prefix(self) -> None:
        # bare word は should に「完全語 (operator:and)」と「前方一致 (phrase_prefix)」を
        # 必ず両方含む (打ちかけ Huma→Human はこの phrase_prefix が担う)。
        result = compile_free_text("Huma")
        token_clause = result["bool"]["must"][0]
        assert token_clause == {
            "bool": {
                "should": [
                    {"multi_match": {"query": "Huma", "fields": _DEFAULT_FIELDS, "operator": "and"}},
                    {"multi_match": {"query": "Huma", "fields": _DEFAULT_FIELDS, "type": "phrase_prefix"}},
                ],
                "minimum_should_match": 1,
            },
        }

    def test_prefix_clause_query_is_the_full_input(self) -> None:
        # phrase_prefix の query は入力そのまま (前方一致対象)。これが空や切り詰めだと
        # Homo sap→Homo sapiens が壊れる。
        result = compile_free_text("Homo sap")
        token_clause = result["bool"]["must"][0]
        prefix = next(
            c["multi_match"] for c in token_clause["bool"]["should"]
            if c["multi_match"].get("type") == "phrase_prefix"
        )
        assert prefix["query"] == "Homo sap"

    def test_exact_clause_always_present_for_scoring(self) -> None:
        # 完全語 (operator:and) clause が常に残る (完全一致をスコア上位に保つため)。
        result = compile_free_text("cancer")
        token_clause = result["bool"]["must"][0]
        operators = [
            c["multi_match"].get("operator")
            for c in token_clause["bool"]["should"]
            if "multi_match" in c
        ]
        assert "and" in operators

    def test_quoted_token_is_exact_no_prefix(self) -> None:
        # クオート = 厳密一致。phrase_prefix を含めてはならない。
        result = compile_free_text('"RNA Seq"')
        assert not _has_clause(result, "match_phrase_prefix")
        assert "phrase_prefix" not in _multi_match_types(result)
        assert _multi_match_types(result) == ["phrase"]

    def test_symbol_token_is_exact_no_prefix(self) -> None:
        # 記号含み (auto-phrase) も完全一致のまま (HIF-1*→HIF-10... の暴発回避)。
        result = compile_free_text("HIF-1")
        assert "phrase_prefix" not in _multi_match_types(result)
        assert _multi_match_types(result) == ["phrase"]

    def test_whole_value_quote_is_exact_no_prefix(self) -> None:
        # 値全体クオート (is_phrase=True) も前方一致しない。
        result = compile_to_es(parse('"Homo sapiens"'))
        assert "phrase_prefix" not in _multi_match_types(result)

    def test_or_operator_nests_each_token_should(self) -> None:
        result = compile_free_text("cancer, human", operator="OR")
        assert result["bool"]["minimum_should_match"] == 1
        # 各カンマトークンが should[operator:and, phrase_prefix] のラッパになる。
        for token_clause in result["bool"]["should"]:
            inner = token_clause["bool"]["should"]
            assert {c["multi_match"].get("type", "and") for c in inner} == {"and", "phrase_prefix"}

    def test_prefix_covers_identifier_field_for_accession_input(self) -> None:
        # default fields に identifier (keyword) を含むので、phrase_prefix が identifier
        # にもかかる → PRJDB→PRJDB123 のアクセッション前方入力に対応。
        result = compile_free_text("PRJDB")
        token_clause = result["bool"]["must"][0]
        prefix = next(
            c["multi_match"] for c in token_clause["bool"]["should"]
            if c["multi_match"].get("type") == "phrase_prefix"
        )
        assert "identifier" in prefix["fields"]

    @given(
        word=st.text(
            alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")),
            min_size=2,
            max_size=15,
        ),
    )
    @settings(max_examples=40, deadline=None)
    def test_pbt_bare_word_2plus_always_has_both_clauses(self, word: str) -> None:
        result = compile_free_text(word)
        token_clause = result["bool"]["must"][0]
        types = {c["multi_match"].get("type", "and") for c in token_clause["bool"]["should"]}
        assert types == {"and", "phrase_prefix"}

    @given(
        word=st.text(
            alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")),
            min_size=1,
            max_size=15,
        ),
    )
    @settings(max_examples=40, deadline=None)
    def test_pbt_quoted_word_never_prefixes(self, word: str) -> None:
        result = compile_free_text(f'"{word}"')
        assert "phrase_prefix" not in _multi_match_types(result)
        assert not _has_clause(result, "match_phrase_prefix")


class TestKeywordBoxMinPrefixLength:
    """末尾語が 1 文字のトークンは前方一致を付けない (最小 2 文字、全 term スキャン回避)."""

    def test_single_char_token_no_prefix(self) -> None:
        result = compile_free_text("a")
        assert result == {
            "bool": {"must": [{"multi_match": {"query": "a", "fields": _DEFAULT_FIELDS, "operator": "and"}}]},
        }

    def test_two_char_token_has_prefix(self) -> None:
        result = compile_free_text("ab")
        assert _has_clause(result, "match_phrase_prefix") or "phrase_prefix" in _multi_match_types(result)

    def test_multiword_last_word_single_char_no_prefix(self) -> None:
        # 末尾語が 1 文字 (Homo s の "s") → 前方一致なし。phrase_prefix は末尾語にかかるため。
        result = compile_free_text("Homo s")
        assert "phrase_prefix" not in _multi_match_types(result)
        assert result == {
            "bool": {"must": [{"multi_match": {"query": "Homo s", "fields": _DEFAULT_FIELDS, "operator": "and"}}]},
        }


class TestKeywordBoxSuppressedUnlock:
    """accession 完全一致で suppressed を解禁したクエリは前方一致を抑止する (漏洩防止)."""

    def test_public_only_keeps_prefix(self) -> None:
        result = build_search_query(keywords="PRJDB1234", status_mode="public_only")
        assert _has_clause(result, "multi_match")
        assert "phrase_prefix" in _multi_match_types(result)

    def test_suppressed_unlock_disables_prefix(self) -> None:
        # 解禁時は phrase_prefix を出さない (PRJDB1234* で別 accession の suppressed を漏らさない)。
        result = build_search_query(keywords="PRJDB1234", status_mode="include_suppressed")
        assert "phrase_prefix" not in _multi_match_types(result)
        assert not _has_clause(result, "match_phrase_prefix")
        # 完全語 multi_match は残る (PRJDB1234 自体は引ける)。
        assert _multi_match_types(result) == ["and"]

    def test_db_portal_freetext_unlock_disables_prefix(self) -> None:
        result = es_query_from_ast(parse("PRJDB1234"), "include_suppressed")
        assert "phrase_prefix" not in _multi_match_types(result)

    def test_compile_to_es_enable_prefix_false(self) -> None:
        result = compile_to_es(parse("SAMD00012345"), enable_prefix=False)
        assert "phrase_prefix" not in _multi_match_types(result)

    def test_field_contains_unaffected_by_enable_prefix(self) -> None:
        # field-scoped contains は AND 制約で守られるため enable_prefix の影響を受けない
        # (suppressed 解禁クエリでも title:Homo は前方一致を保つ)。
        result = compile_to_es(parse("title:Homo"), enable_prefix=False)
        assert _has_clause(result, "match_phrase_prefix")


# === ES: field-scoped contains ===


class TestFieldContainsPrefix:
    @pytest.mark.parametrize("field", ["title", "name", "description", "organism_name"])
    def test_text_word_expands_to_exact_and_prefix(self, field: str) -> None:
        es_field = "organism.name" if field == "organism_name" else field
        assert compile_to_es(parse(f"{field}:Huma")) == {
            "bool": {
                "should": [
                    {"match_phrase": {es_field: "Huma"}},
                    {"match_phrase_prefix": {es_field: "Huma"}},
                ],
                "minimum_should_match": 1,
            },
        }

    def test_quoted_phrase_value_is_exact(self) -> None:
        # クオート値 (value_kind=phrase) は match_phrase 単独 (前方一致しない)。
        result = compile_to_es(parse('title:"cancer treatment"'))
        assert result == {"match_phrase": {"title": "cancer treatment"}}
        assert not _has_clause(result, "match_phrase_prefix")

    def test_symbol_word_value_is_exact(self) -> None:
        # 記号含み word (host:HIF-1) は match_phrase 単独 (auto-phrase と挙動を揃える)。
        result = compile_to_es(parse("host:HIF-1"))
        assert result == {"match_phrase": {"host": "HIF-1"}}
        assert not _has_clause(result, "match_phrase_prefix")

    def test_single_char_value_is_exact(self) -> None:
        # 1 文字 word は前方一致なし (最小 2 文字)。
        result = compile_to_es(parse("title:a"))
        assert result == {"match_phrase": {"title": "a"}}
        assert not _has_clause(result, "match_phrase_prefix")

    def test_nested_field_prefix_inside_wrapper(self) -> None:
        # nested の中身が should[match_phrase, match_phrase_prefix] になり、
        # nested / ignore_unmapped 構造は不変。
        assert compile_to_es(parse("submitter:Tok")) == {
            "nested": {
                "path": "organization",
                "query": {
                    "bool": {
                        "should": [
                            {"match_phrase": {"organization.name": "Tok"}},
                            {"match_phrase_prefix": {"organization.name": "Tok"}},
                        ],
                        "minimum_should_match": 1,
                    },
                },
                "ignore_unmapped": True,
            },
        }

    def test_double_nested_field_prefix_inside_inner_wrapper(self) -> None:
        result = compile_to_es(parse("grant_agency:JSPS"))
        inner = result["nested"]["query"]["nested"]["query"]
        assert inner == {
            "bool": {
                "should": [
                    {"match_phrase": {"grant.agency.name": "JSPS"}},
                    {"match_phrase_prefix": {"grant.agency.name": "JSPS"}},
                ],
                "minimum_should_match": 1,
            },
        }

    @pytest.mark.parametrize(
        "dsl",
        [
            "platform:ILLUMINA",  # enum → term
            "identifier:PRJDB1",  # identifier → term
            "organism_id:9606",  # identifier → term
            "date_published:2024-01-01",  # date → term
        ],
    )
    def test_non_text_types_never_prefix(self, dsl: str) -> None:
        result = compile_to_es(parse(dsl))
        assert not _has_clause(result, "match_phrase_prefix")
        assert _has_clause(result, "term")

    def test_text_wildcard_unaffected(self) -> None:
        # wildcard (starts_with) 経路は従来どおり wildcard query のまま。
        result = compile_to_es(parse("title:Huma*"))
        assert result == {"wildcard": {"title": {"value": "Huma*", "case_insensitive": True}}}


# === Solr: free-text & field contains ===


def _solr_has_unquoted_star(q: str) -> bool:
    """quote の外に ``*`` があるか (edismax bare wildcard の検出)."""
    in_quote = False
    for ch in q:
        if ch == '"':
            in_quote = not in_quote
        elif ch == "*" and not in_quote:
            return True
    return False


def _solr_unquoted_stars_ok(q: str) -> bool:
    """edismax 注入安全性: quote の外の全 ``*`` が ``<ASCII英数字のみの語>*`` の prefix である.

    安全な前方一致は ``word*`` (語が ASCII 英数字のみ) だけ。``*`` 直前の 1 文字ではなく
    **直前トークン全体**を見るので、``HIF-1*`` / ``ab-cd*`` のように記号を含む語に ``*`` が
    付く真の bare wildcard 注入を検出する (``*`` 直前 1 文字 ``d`` だけ見る弱いオラクルでは
    見逃す)。トークン区切りは空白 / ``(`` / ``)`` / ``:`` (field 区切り)。``*:*``
    (all-docs fallback) は固定リテラルなので例外的に許す。
    """
    if q == "*:*":
        return True
    in_quote = False
    term: list[str] = []  # quote 外の現在トークン (区切りでリセット)
    for ch in q:
        if ch == '"':
            in_quote = not in_quote
            term = []
        elif in_quote:
            continue
        elif ch in " ():":
            term = []
        elif ch == "*":
            if not term or not all(c.isascii() and c.isalnum() for c in term):
                return False
            term = []
        else:
            term.append(ch)
    return True


class TestSolrInjectionOracle:
    """``_solr_unquoted_stars_ok`` 自体の検出力 (PBT のオラクルが本物の注入を捕まえるか).

    オラクルが弱い (``*`` 直前 1 文字しか見ない) と ``HIF-1*`` のような注入を見逃し、
    PBT が空振りする。直前トークン全体を見る強いオラクルであることを固定する。
    """

    @pytest.mark.parametrize(
        "q",
        ['("aa" OR aa*)', '("Homo sap" OR (Homo AND sap*))', '(species:"Homo" OR species:Homo*)', "*:*", '"HIF-1"'],
    )
    def test_safe_outputs_pass(self, q: str) -> None:
        assert _solr_unquoted_stars_ok(q)

    @pytest.mark.parametrize(
        "q",
        ["(HIF-1* OR x)", "(ab-cd* OR x)", "species:Ho-mo*", "(a)*", "a.b*"],
    )
    def test_injection_outputs_fail(self, q: str) -> None:
        # 記号を含む語に unquoted * が付く = bare wildcard 注入。必ず検出する。
        assert not _solr_unquoted_stars_ok(q)


class TestSolrPrefix:
    def test_single_alnum_word(self) -> None:
        assert compile_free_text_solr("Huma") == '("Huma" OR Huma*)'

    def test_multiword_last_term_prefix(self) -> None:
        assert compile_free_text_solr("Homo sap") == '("Homo sap" OR (Homo AND sap*))'

    def test_three_words_last_term_prefix(self) -> None:
        assert compile_free_text_solr("aa bb cc") == '("aa bb cc" OR (aa AND bb AND cc*))'

    def test_symbol_token_exact_no_star(self) -> None:
        out = compile_free_text_solr("HIF-1")
        assert out == '"HIF-1"'
        assert not _solr_has_unquoted_star(out)

    def test_single_char_token_exact_no_star(self) -> None:
        # 1 文字 alnum は前方一致なし (最小 2 文字)。
        out = compile_free_text_solr("a")
        assert out == '"a"'
        assert not _solr_has_unquoted_star(out)

    def test_multiword_last_char_single_no_star(self) -> None:
        out = compile_free_text_solr("Homo s")
        assert out == '"Homo s"'
        assert not _solr_has_unquoted_star(out)

    def test_comma_tokens_each_expanded(self) -> None:
        assert compile_free_text_solr("cancer, tumor", operator="OR") == (
            '(("cancer" OR cancer*) OR ("tumor" OR tumor*))'
        )

    def test_whole_value_quote_is_single_exact_phrase(self) -> None:
        # is_phrase=True: コンマ分割せず 1 phrase。
        assert compile_to_solr(parse('"Homo sapiens"'), dialect="txsearch") == '"Homo sapiens"'

    def test_field_contains_prefix(self) -> None:
        assert compile_to_solr(parse("species:Homo"), dialect="txsearch") == (
            '(species:"Homo" OR species:Homo*)'
        )

    def test_field_contains_single_char_exact(self) -> None:
        out = compile_to_solr(parse("species:a"), dialect="txsearch")
        assert out == 'species:"a"'
        assert not _solr_has_unquoted_star(out)

    def test_field_enum_no_prefix(self) -> None:
        out = compile_to_solr(parse("division:BCT"), dialect="arsa")
        assert out == 'Division:"BCT"'
        assert not _solr_has_unquoted_star(out)

    @given(
        token=st.text(
            alphabet=st.sampled_from(list("abcXYZ012-/.:+()[]{}^~!|&\\\"' ")),
            min_size=1,
            max_size=12,
        ),
    )
    @settings(max_examples=80, deadline=None)
    def test_pbt_freetext_unquoted_star_only_as_alnum_prefix(self, token: str) -> None:
        # 任意入力で、quote 外の ``*`` は必ず ASCII 英数字直後の prefix のみ
        # (edismax bare wildcard 注入をしない安全性。分類不要の強い不変条件)。
        out = compile_free_text_solr(token)
        assert _solr_unquoted_stars_ok(out), f"unsafe star for token {token!r} → {out!r}"

    @given(
        word=st.text(
            alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")),
            min_size=2,
            max_size=12,
        ),
    )
    @settings(max_examples=40, deadline=None)
    def test_pbt_alnum_word_2plus_has_exact_and_prefix(self, word: str) -> None:
        # 記号なし 2 文字以上の単一語は ("w" OR w*)。exact phrase も残す。
        assert compile_free_text_solr(word) == f'("{word}" OR {word}*)'

    @given(
        # grammar の WORD で有効な文字のみ (`:()[]"{}^~*?/` と空白は field 値に使えない)。
        # 記号含み / 非英数字 / 1 文字値は exact に倒れ unquoted * を出さないことを検証する。
        value=st.text(
            alphabet=st.sampled_from(list("abXY09-.+&\\")),
            min_size=1,
            max_size=10,
        ),
    )
    @settings(max_examples=80, deadline=None)
    def test_pbt_field_contains_unquoted_star_only_as_alnum_prefix(self, value: str) -> None:
        # field-scoped contains の Solr 出力も bare wildcard 注入をしない。
        out = compile_to_solr(parse(f"species:{value}"), dialect="txsearch")
        assert _solr_unquoted_stars_ok(out), f"unsafe star for value {value!r} → {out!r}"
