"""Tests for ddbj_search_api.search.accession."""

from __future__ import annotations

import pytest
from ddbj_search_converter.dblink.db import AccessionType
from ddbj_search_converter.id_patterns import ID_PATTERN_MAP
from hypothesis import given
from hypothesis import strategies as st

from ddbj_search_api.schemas.common import DbType
from ddbj_search_api.search.accession import (
    detect_accession_exact_match,
    is_accession_like,
)

# DbType の値は AccessionType (21) のサブセットなので、enum value を
# そのまま AccessionType 型として扱える。
_DB_TYPE_VALUES: list[AccessionType] = [e.value for e in DbType]

# 各 DbType の代表 accession 例 (pattern 先頭マッチ確認用)
_DB_TYPE_SAMPLE_ACCESSIONS: list[str] = [
    "PRJDB1234",
    "PRJNA99999",
    "PRJEB5",
    "SAMD00000001",
    "SAMN12345678",
    "SAME123",
    "DRA000001",
    "SRA999999",
    "ERA123456",
    "DRP000001",
    "SRP123",
    "ERP7",
    "DRX000001",
    "DRR000001",
    "DRS000001",
    "DRZ000001",
    "JGAS000001",
    "JGAD000001",
    "JGAC000001",
    "JGAP000001",
    "E-GEAD-1",  # gea
    "MTBKS1",  # metabobank
]

# DbType に含まれない AccessionType の例 (NG になるべき)
_NON_DB_TYPE_ACCESSIONS: list[str] = [
    "GSE12345",  # geo
    "hum0014",  # humandbs
    "GCA_000001405",  # insdc-assembly
    "1234567",  # pubmed/taxonomy (数字のみ、意図的に除外)
]


class TestIsAccessionLike:
    @pytest.mark.parametrize("token", _DB_TYPE_SAMPLE_ACCESSIONS)
    def test_valid_db_type_accessions(self, token: str) -> None:
        assert is_accession_like(token) is True

    @pytest.mark.parametrize("token", _NON_DB_TYPE_ACCESSIONS)
    def test_non_db_type_accessions_excluded(self, token: str) -> None:
        assert is_accession_like(token) is False

    @pytest.mark.parametrize(
        "token",
        [
            "",
            "cancer",
            "PRJDB",
            "PRJDB*",
            "PRJDB?",
            "PRJDB1234 ",  # 呼び出し側で strip 前提
            " PRJDB1234",
        ],
    )
    def test_invalid_tokens(self, token: str) -> None:
        assert is_accession_like(token) is False

    @pytest.mark.parametrize("token", ["*", "?", "*PRJDB*"])
    def test_wildcard_excluded(self, token: str) -> None:
        assert is_accession_like(token) is False


class TestDetectAccessionExactMatch:
    @pytest.mark.parametrize(
        ("keywords", "expected"),
        [
            ("PRJDB1234", "PRJDB1234"),
            ("  PRJDB1234  ", "PRJDB1234"),
            ("\tPRJDB1234\t", "PRJDB1234"),
            ('"PRJDB1234"', "PRJDB1234"),
            ('  "PRJDB1234"  ', "PRJDB1234"),
            ('"  PRJDB1234  "', "PRJDB1234"),
            ("'DRA000001'", "DRA000001"),
            ("JGAS000001", "JGAS000001"),
            ("SAMD00000001", "SAMD00000001"),
        ],
    )
    def test_exact_match(self, keywords: str, expected: str) -> None:
        assert detect_accession_exact_match(keywords) == expected

    @pytest.mark.parametrize(
        "keywords",
        [
            None,
            "",
            "   ",
            "\t\n",
            "cancer",
            "PRJDB1234,DRA000001",
            "PRJDB1234,cancer",
            ",",
            "PRJDB*",
            "DRA000?",
            "PRJDB*1234",
            "GSE12345",  # geo: DbType に無い
            "1234567",  # pubmed/taxonomy 数字のみパターンは DbType に無い
            '""',
            "''",
            '"   "',
        ],
    )
    def test_no_match(self, keywords: str | None) -> None:
        assert detect_accession_exact_match(keywords) is None

    def test_mixed_quote_types_not_stripped(self) -> None:
        assert detect_accession_exact_match("\"PRJDB1234'") is None

    def test_nested_quotes_not_stripped(self) -> None:
        assert detect_accession_exact_match("\"'PRJDB1234'\"") is None


# === PBT ===


@st.composite
def _valid_accession_for_db_type(draw: st.DrawFn) -> str:
    """DbType のパターンから fullmatch する accession ID を生成。"""
    db_type_value = draw(st.sampled_from(_DB_TYPE_VALUES))
    pattern = ID_PATTERN_MAP[db_type_value]
    return draw(st.from_regex(pattern, fullmatch=True))


class TestAccessionPBT:
    @given(_valid_accession_for_db_type())
    def test_generated_accession_matches(self, accession: str) -> None:
        assert is_accession_like(accession) is True
        assert detect_accession_exact_match(accession) == accession

    @given(_valid_accession_for_db_type())
    def test_with_leading_trailing_whitespace(self, accession: str) -> None:
        assert detect_accession_exact_match(f"  {accession}  ") == accession
        assert detect_accession_exact_match(f"\t{accession}\n") == accession

    @given(_valid_accession_for_db_type())
    def test_with_double_quotes(self, accession: str) -> None:
        assert detect_accession_exact_match(f'"{accession}"') == accession
        assert detect_accession_exact_match(f'  "{accession}"  ') == accession

    @given(_valid_accession_for_db_type())
    def test_with_single_quotes(self, accession: str) -> None:
        assert detect_accession_exact_match(f"'{accession}'") == accession

    @given(
        _valid_accession_for_db_type(),
        _valid_accession_for_db_type(),
    )
    def test_comma_separated_rejected(self, a: str, b: str) -> None:
        assert detect_accession_exact_match(f"{a},{b}") is None

    @given(st.from_regex(r"[a-z]+", fullmatch=True))
    def test_lowercase_never_matches(self, text: str) -> None:
        # DbType のパターンはすべて大文字で始まるので、
        # 全小文字のトークンは絶対にマッチしない
        assert detect_accession_exact_match(text) is None
