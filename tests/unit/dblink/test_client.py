"""Tests for ddbj_search_api.dblink.client."""

from __future__ import annotations

import tempfile
from pathlib import Path

import duckdb
import pytest
from hypothesis import given
from hypothesis import strategies as st

from ddbj_search_api.dblink.client import get_linked_ids
from ddbj_search_api.schemas.dblink import AccessionType

# --- Helpers ---


def _create_test_db(db_path: Path, rows: list[tuple[str, str, str, str]]) -> None:
    """Create a DuckDB file with a ``relation`` table populated with rows."""
    with duckdb.connect(str(db_path)) as conn:
        conn.execute("""
            CREATE TABLE relation (
                src_type TEXT,
                src_accession TEXT,
                dst_type TEXT,
                dst_accession TEXT
            )
        """)
        if rows:
            conn.executemany(
                "INSERT INTO relation VALUES (?, ?, ?, ?)",
                rows,
            )


# --- Tests ---


class TestGetLinkedIdsNormal:
    """Normal-case tests for get_linked_ids."""

    def test_returns_dst_when_queried_as_src(self, tmp_path: Path) -> None:
        db = tmp_path / "test.duckdb"
        _create_test_db(db, [("hum-id", "hum0014", "jga-study", "JGAS000101")])

        result = get_linked_ids(db, "hum-id", "hum0014")

        assert result == [("jga-study", "JGAS000101")]

    def test_returns_src_when_queried_as_dst(self, tmp_path: Path) -> None:
        db = tmp_path / "test.duckdb"
        _create_test_db(db, [("hum-id", "hum0014", "jga-study", "JGAS000101")])

        result = get_linked_ids(db, "jga-study", "JGAS000101")

        assert result == [("hum-id", "hum0014")]

    def test_bidirectional_multiple_results(self, tmp_path: Path) -> None:
        db = tmp_path / "test.duckdb"
        _create_test_db(
            db,
            [
                ("bioproject", "PRJDB100", "biosample", "SAMD001"),
                ("sra-study", "DRP001", "bioproject", "PRJDB100"),
            ],
        )

        result = get_linked_ids(db, "bioproject", "PRJDB100")

        assert ("biosample", "SAMD001") in result
        assert ("sra-study", "DRP001") in result
        assert len(result) == 2

    def test_empty_result_for_nonexistent_id(self, tmp_path: Path) -> None:
        db = tmp_path / "test.duckdb"
        _create_test_db(db, [("hum-id", "hum0014", "jga-study", "JGAS000101")])

        result = get_linked_ids(db, "hum-id", "NONEXISTENT")

        assert result == []


class TestGetLinkedIdsSort:
    """Verify results are sorted by (type, accession)."""

    def test_sorted_by_type_then_accession(self, tmp_path: Path) -> None:
        db = tmp_path / "test.duckdb"
        _create_test_db(
            db,
            [
                ("bioproject", "PRJDB100", "sra-study", "DRP002"),
                ("bioproject", "PRJDB100", "biosample", "SAMD001"),
                ("bioproject", "PRJDB100", "sra-study", "DRP001"),
                ("bioproject", "PRJDB100", "biosample", "SAMD002"),
            ],
        )

        result = get_linked_ids(db, "bioproject", "PRJDB100")

        assert result == [
            ("biosample", "SAMD001"),
            ("biosample", "SAMD002"),
            ("sra-study", "DRP001"),
            ("sra-study", "DRP002"),
        ]


class TestGetLinkedIdsTargetFilter:
    """Target filter tests."""

    def test_single_target_filters(self, tmp_path: Path) -> None:
        db = tmp_path / "test.duckdb"
        _create_test_db(
            db,
            [
                ("hum-id", "hum0014", "jga-study", "JGAS000101"),
                ("hum-id", "hum0014", "bioproject", "PRJDB100"),
            ],
        )

        result = get_linked_ids(db, "hum-id", "hum0014", target=["jga-study"])

        assert result == [("jga-study", "JGAS000101")]

    def test_multiple_targets(self, tmp_path: Path) -> None:
        db = tmp_path / "test.duckdb"
        _create_test_db(
            db,
            [
                ("hum-id", "hum0014", "jga-study", "JGAS000101"),
                ("hum-id", "hum0014", "bioproject", "PRJDB100"),
                ("hum-id", "hum0014", "biosample", "SAMD001"),
            ],
        )

        result = get_linked_ids(db, "hum-id", "hum0014", target=["jga-study", "bioproject"])

        assert len(result) == 2
        types = {r[0] for r in result}
        assert types == {"jga-study", "bioproject"}

    def test_target_not_matching_returns_empty(self, tmp_path: Path) -> None:
        db = tmp_path / "test.duckdb"
        _create_test_db(db, [("hum-id", "hum0014", "jga-study", "JGAS000101")])

        result = get_linked_ids(db, "hum-id", "hum0014", target=["bioproject"])

        assert result == []

    def test_target_filters_reverse_direction(self, tmp_path: Path) -> None:
        db = tmp_path / "test.duckdb"
        _create_test_db(
            db,
            [
                ("hum-id", "hum0014", "jga-study", "JGAS000101"),
                ("bioproject", "PRJDB100", "jga-study", "JGAS000101"),
            ],
        )

        result = get_linked_ids(db, "jga-study", "JGAS000101", target=["hum-id"])

        assert result == [("hum-id", "hum0014")]


class TestGetLinkedIdsDbMissing:
    """FileNotFoundError when DB file does not exist."""

    def test_raises_file_not_found(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.duckdb"

        with pytest.raises(FileNotFoundError, match="DuckDB file not found"):
            get_linked_ids(missing, "hum-id", "hum0014")


class TestGetLinkedIdsPBT:
    """Property-based tests for get_linked_ids."""

    @given(
        acc_type=st.sampled_from([e.value for e in AccessionType]),
        acc_id=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
            min_size=1,
            max_size=20,
        ),
    )
    def test_empty_db_always_returns_empty(self, acc_type: str, acc_id: str) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Path(tmpdir) / "pbt_empty.duckdb"
            _create_test_db(db, [])

            result = get_linked_ids(db, acc_type, acc_id)

            assert result == []

    def test_result_is_always_sorted(self, tmp_path: Path) -> None:
        """Insert multiple relation types and verify sort order."""
        db = tmp_path / "pbt_sorted.duckdb"
        rows = [
            ("bioproject", "ACC001", "sra-study", "DRP002"),
            ("bioproject", "ACC001", "biosample", "SAMD001"),
            ("bioproject", "ACC001", "sra-study", "DRP001"),
            ("bioproject", "ACC001", "jga-study", "JGAS001"),
        ]
        _create_test_db(db, rows)

        result = get_linked_ids(db, "bioproject", "ACC001")

        assert result == sorted(result)
