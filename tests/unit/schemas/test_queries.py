"""Tests for ddbj_search_api.schemas.queries.

このファイルが扱うのは「直接インスタンス化したときにロジックが走る class」
のみ。FastAPI Depends() の `Query(..., ge=1, le=100, pattern=...)` 等の境界値
バリデーションは HTTP 経路でしか走らないため、`tests/unit/routers/` の
TestClient テストでカバーされる。ここで storage tautology を確認しても
バグ検出力がないので削除済み。

残しているのは:
- enum 値の包含 / 拒否 (KeywordOperator, BulkFormat)
- TypesFilterQuery の HTTPException raise 経路
- FacetsParamQuery の正規化 / 422 raise (whitespace strip, trailing comma, allowlist)
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from ddbj_search_api.schemas.queries import (
    BulkFormat,
    FacetsParamQuery,
    KeywordOperator,
    TypesFilterQuery,
)

# === Enums ===


class TestKeywordOperator:
    """KeywordOperator enum: AND / OR."""

    def test_and(self) -> None:
        assert KeywordOperator("AND") == KeywordOperator.AND

    def test_or(self) -> None:
        assert KeywordOperator("OR") == KeywordOperator.OR

    def test_has_exactly_2_members(self) -> None:
        assert len(KeywordOperator) == 2

    def test_invalid_value_raises_error(self) -> None:
        with pytest.raises(ValueError):
            KeywordOperator("NOT")


class TestBulkFormat:
    """BulkFormat enum: json / ndjson."""

    def test_json(self) -> None:
        assert BulkFormat("json") == BulkFormat.json

    def test_ndjson(self) -> None:
        assert BulkFormat("ndjson") == BulkFormat.ndjson

    def test_has_exactly_2_members(self) -> None:
        assert len(BulkFormat) == 2

    def test_invalid_value_raises_error(self) -> None:
        with pytest.raises(ValueError):
            BulkFormat("csv")


# === TypesFilterQuery (HTTPException raise paths) ===


class TestTypesFilterQuery:
    """TypesFilterQuery: HTTPException raise for invalid type values."""

    def test_none_is_accepted(self) -> None:
        q = TypesFilterQuery(types=None)
        assert q.types is None

    def test_valid_single_type_stored(self) -> None:
        q = TypesFilterQuery(types="bioproject")
        assert q.types == "bioproject"

    def test_valid_multi_type_stored(self) -> None:
        q = TypesFilterQuery(types="bioproject,sra-study")
        assert q.types == "bioproject,sra-study"

    def test_invalid_type_raises_422(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            TypesFilterQuery(types="bogus")
        assert exc_info.value.status_code == 422
        # 詳細メッセージに invalid 値が含まれる
        assert "bogus" in exc_info.value.detail

    def test_mixed_valid_and_invalid_raises_422(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            TypesFilterQuery(types="bioproject,bogus")
        assert exc_info.value.status_code == 422
        # 「invalid だけを列挙する」のが下流のメッセージ品質
        assert "bogus" in exc_info.value.detail
        # valid 側を invalid と誤報しない (regression guard)
        assert "Invalid types: bioproject" not in exc_info.value.detail

    def test_empty_string_via_whitespace_stripped(self) -> None:
        """全 token が空文字になるケース (`,,,`) は invalid raise しない。
        実 HTTP では Query pattern で先に弾かれるが、ここでは直接呼んで
        TypesFilterQuery 内のロジック単体で動作確認する。"""
        q = TypesFilterQuery(types=",,,")
        # フィールドに保管された raw 値はそのまま (downstream で split される想定)
        assert q.types == ",,,"


# === FacetsParamQuery (normalisation + raise) ===


class TestFacetsParamQuery:
    """FacetsParamQuery: allowlist enforcement at the wire boundary."""

    def test_default_none_passthrough(self) -> None:
        q = FacetsParamQuery(facets=None)
        assert q.facets is None

    def test_empty_string_preserved(self) -> None:
        # docs/api-spec.md § ファセット集計対象の選択
        # facets="" → 集計 0 個
        q = FacetsParamQuery(facets="")
        assert q.facets == ""

    @pytest.mark.parametrize(
        "value",
        [
            "organism",
            "organism,accessibility",
            "objectType",
            "libraryStrategy,librarySource",
            "experimentType",
            "type",
            "type,objectType",
        ],
    )
    def test_valid_values_pass(self, value: str) -> None:
        q = FacetsParamQuery(facets=value)
        assert q.facets == value

    def test_whitespace_around_tokens_normalized(self) -> None:
        """Whitespace は strip され、再結合された正規形が attribute に
        格納される (downstream は再 split/strip する必要がない)。"""
        q = FacetsParamQuery(facets=" organism , accessibility ")
        assert q.facets == "organism,accessibility"

    def test_trailing_comma_normalized(self) -> None:
        """``organism,`` のように trailing comma で空トークンが含まれる
        場合は空要素を除外した正規形にする。"""
        q = FacetsParamQuery(facets="organism,")
        assert q.facets == "organism"

    @pytest.mark.parametrize(
        "value",
        [
            "totallyUnknown",
            "organism,fakeFacet",
        ],
    )
    def test_invalid_values_raise_422(self, value: str) -> None:
        with pytest.raises(HTTPException) as exc_info:
            FacetsParamQuery(facets=value)
        assert exc_info.value.status_code == 422

    def test_only_commas_raises_422(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            FacetsParamQuery(facets=",,,")
        assert exc_info.value.status_code == 422
