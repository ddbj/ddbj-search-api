"""Tests for ddbj_search_api.dblink.client."""

from __future__ import annotations

import tempfile
from pathlib import Path

import duckdb
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ddbj_search_api.dblink.client import (
    count_linked_ids,
    count_linked_ids_bulk,
    get_linked_ids_limited,
    get_linked_ids_limited_bulk,
    iter_linked_ids,
)
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


# --- iter_linked_ids ---


class TestIterLinkedIdsNormal:
    """Normal-case tests for iter_linked_ids."""

    def test_yields_all_rows(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        _create_test_db(
            db,
            [
                ("bioproject", "PRJDB100", "biosample", "SAMD001"),
                ("bioproject", "PRJDB100", "sra-study", "DRP001"),
            ],
        )

        result = list(iter_linked_ids(db, "bioproject", "PRJDB100"))

        assert len(result) == 2
        assert ("biosample", "SAMD001") in result
        assert ("sra-study", "DRP001") in result

    def test_results_are_sorted(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        _create_test_db(
            db,
            [
                ("bioproject", "PRJDB100", "sra-study", "DRP002"),
                ("bioproject", "PRJDB100", "biosample", "SAMD001"),
                ("bioproject", "PRJDB100", "sra-study", "DRP001"),
            ],
        )

        result = list(iter_linked_ids(db, "bioproject", "PRJDB100"))

        assert result == sorted(result)

    def test_empty_result_for_nonexistent_id(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        _create_test_db(db, [("hum-id", "hum0014", "jga-study", "JGAS000101")])

        result = list(iter_linked_ids(db, "hum-id", "NONEXISTENT"))

        assert result == []

    def test_bidirectional(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        _create_test_db(db, [("hum-id", "hum0014", "jga-study", "JGAS000101")])

        result_fwd = list(iter_linked_ids(db, "hum-id", "hum0014"))
        result_rev = list(iter_linked_ids(db, "jga-study", "JGAS000101"))

        assert result_fwd == [("jga-study", "JGAS000101")]
        assert result_rev == [("hum-id", "hum0014")]

    def test_returns_dst_when_queried_as_src(self, tmp_path: Path) -> None:
        db = tmp_path / "test.duckdb"
        _create_test_db(db, [("hum-id", "hum0014", "jga-study", "JGAS000101")])

        result = list(iter_linked_ids(db, "hum-id", "hum0014"))

        assert result == [("jga-study", "JGAS000101")]

    def test_returns_src_when_queried_as_dst(self, tmp_path: Path) -> None:
        db = tmp_path / "test.duckdb"
        _create_test_db(db, [("hum-id", "hum0014", "jga-study", "JGAS000101")])

        result = list(iter_linked_ids(db, "jga-study", "JGAS000101"))

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

        result = list(iter_linked_ids(db, "bioproject", "PRJDB100"))

        assert ("biosample", "SAMD001") in result
        assert ("sra-study", "DRP001") in result
        assert len(result) == 2


class TestIterLinkedIdsSort:
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

        result = list(iter_linked_ids(db, "bioproject", "PRJDB100"))

        assert result == [
            ("biosample", "SAMD001"),
            ("biosample", "SAMD002"),
            ("sra-study", "DRP001"),
            ("sra-study", "DRP002"),
        ]


class TestIterLinkedIdsTargetFilter:
    """Target filter tests for iter_linked_ids."""

    def test_single_target_filters(self, tmp_path: Path) -> None:
        db = tmp_path / "test.duckdb"
        _create_test_db(
            db,
            [
                ("hum-id", "hum0014", "jga-study", "JGAS000101"),
                ("hum-id", "hum0014", "bioproject", "PRJDB100"),
            ],
        )

        result = list(iter_linked_ids(db, "hum-id", "hum0014", target=["jga-study"]))

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

        result = list(iter_linked_ids(db, "hum-id", "hum0014", target=["jga-study", "bioproject"]))

        assert len(result) == 2
        types = {r[0] for r in result}
        assert types == {"jga-study", "bioproject"}

    def test_target_not_matching_returns_empty(self, tmp_path: Path) -> None:
        db = tmp_path / "test.duckdb"
        _create_test_db(db, [("hum-id", "hum0014", "jga-study", "JGAS000101")])

        result = list(iter_linked_ids(db, "hum-id", "hum0014", target=["bioproject"]))

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

        result = list(iter_linked_ids(db, "jga-study", "JGAS000101", target=["hum-id"]))

        assert result == [("hum-id", "hum0014")]

    def test_target_none_returns_all(self, tmp_path: Path) -> None:
        db = tmp_path / "test.duckdb"
        _create_test_db(
            db,
            [
                ("hum-id", "hum0014", "jga-study", "JGAS000101"),
                ("hum-id", "hum0014", "bioproject", "PRJDB100"),
            ],
        )

        result = list(iter_linked_ids(db, "hum-id", "hum0014", target=None))

        assert len(result) == 2


class TestIterLinkedIdsChunking:
    """Verify chunked fetching works correctly."""

    def test_small_chunk_size(self, tmp_path: Path) -> None:
        """chunk_size=1 still returns all rows."""
        db = tmp_path.joinpath("test.duckdb")
        rows = [("bioproject", "PRJDB100", "biosample", f"SAMD{i:03d}") for i in range(5)]
        _create_test_db(db, rows)

        result = list(iter_linked_ids(db, "bioproject", "PRJDB100", chunk_size=1))

        assert len(result) == 5

    def test_chunk_larger_than_result(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        _create_test_db(
            db,
            [("bioproject", "PRJDB100", "biosample", "SAMD001")],
        )

        result = list(iter_linked_ids(db, "bioproject", "PRJDB100", chunk_size=10000))

        assert result == [("biosample", "SAMD001")]


class TestIterLinkedIdsDbMissing:
    def test_raises_file_not_found(self, tmp_path: Path) -> None:
        missing = tmp_path.joinpath("does_not_exist.duckdb")

        with pytest.raises(FileNotFoundError, match="DuckDB file not found"):
            list(iter_linked_ids(missing, "hum-id", "hum0014"))


class TestIterLinkedIdsPBT:
    """Property-based tests for iter_linked_ids."""

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

            result = list(iter_linked_ids(db, acc_type, acc_id))

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

        result = list(iter_linked_ids(db, "bioproject", "ACC001"))

        assert result == sorted(result)


class TestIterLinkedIdsEquivalence:
    """iter_linked_ids with target produces consistent results."""

    def test_target_subset_of_full(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        rows = [
            ("bioproject", "PRJDB100", "sra-study", "DRP002"),
            ("bioproject", "PRJDB100", "biosample", "SAMD001"),
            ("sra-study", "DRP001", "bioproject", "PRJDB100"),
        ]
        _create_test_db(db, rows)

        full = list(iter_linked_ids(db, "bioproject", "PRJDB100"))
        filtered = list(iter_linked_ids(db, "bioproject", "PRJDB100", target=["biosample"]))

        assert all(row in full for row in filtered)
        assert all(row[0] == "biosample" for row in filtered)


# --- get_linked_ids_limited ---


class TestGetLinkedIdsLimitedNormal:
    """Normal-case tests for get_linked_ids_limited."""

    def test_limit_fewer_than_total(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        rows = [("bioproject", "PRJDB100", "biosample", f"SAMD{i:03d}") for i in range(10)]
        _create_test_db(db, rows)

        result = get_linked_ids_limited(db, "bioproject", "PRJDB100", limit=3)

        assert len(result) == 3

    def test_limit_more_than_total(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        _create_test_db(
            db,
            [
                ("bioproject", "PRJDB100", "biosample", "SAMD001"),
                ("bioproject", "PRJDB100", "biosample", "SAMD002"),
            ],
        )

        result = get_linked_ids_limited(db, "bioproject", "PRJDB100", limit=100)

        assert len(result) == 2

    def test_limit_zero(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        _create_test_db(
            db,
            [("bioproject", "PRJDB100", "biosample", "SAMD001")],
        )

        result = get_linked_ids_limited(db, "bioproject", "PRJDB100", limit=0)

        assert result == []

    def test_results_are_sorted(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        rows = [
            ("bioproject", "PRJDB100", "sra-study", "DRP002"),
            ("bioproject", "PRJDB100", "biosample", "SAMD001"),
            ("bioproject", "PRJDB100", "sra-study", "DRP001"),
        ]
        _create_test_db(db, rows)

        result = get_linked_ids_limited(db, "bioproject", "PRJDB100", limit=2)

        assert result == sorted(result)

    def test_empty_result_for_nonexistent_id(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        _create_test_db(db, [("hum-id", "hum0014", "jga-study", "JGAS000101")])

        result = get_linked_ids_limited(db, "hum-id", "NONEXISTENT", limit=10)

        assert result == []


class TestGetLinkedIdsLimitedEquivalence:
    """Limited results are a prefix of full results."""

    def test_prefix_of_full_results(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        rows = [("bioproject", "PRJDB100", "biosample", f"SAMD{i:03d}") for i in range(10)]
        _create_test_db(db, rows)

        full = list(iter_linked_ids(db, "bioproject", "PRJDB100"))
        limited = get_linked_ids_limited(db, "bioproject", "PRJDB100", limit=5)

        assert limited == full[:5]


class TestGetLinkedIdsLimitedDbMissing:
    def test_raises_file_not_found(self, tmp_path: Path) -> None:
        missing = tmp_path.joinpath("does_not_exist.duckdb")

        with pytest.raises(FileNotFoundError, match="DuckDB file not found"):
            get_linked_ids_limited(missing, "hum-id", "hum0014", limit=10)


# --- get_linked_ids_limited_bulk ---


class TestGetLinkedIdsLimitedBulkNormal:
    """Normal-case tests for get_linked_ids_limited_bulk."""

    def test_single_entry(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        _create_test_db(
            db,
            [
                ("bioproject", "PRJDB100", "biosample", "SAMD001"),
                ("bioproject", "PRJDB100", "biosample", "SAMD002"),
            ],
        )

        result = get_linked_ids_limited_bulk(db, [("bioproject", "PRJDB100")], limit=10)

        assert len(result[("bioproject", "PRJDB100")]) == 2

    def test_multiple_entries(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        _create_test_db(
            db,
            [
                ("bioproject", "PRJDB100", "biosample", "SAMD001"),
                ("bioproject", "PRJDB200", "sra-study", "DRP001"),
                ("bioproject", "PRJDB200", "sra-study", "DRP002"),
            ],
        )

        result = get_linked_ids_limited_bulk(
            db,
            [("bioproject", "PRJDB100"), ("bioproject", "PRJDB200")],
            limit=10,
        )

        assert len(result[("bioproject", "PRJDB100")]) == 1
        assert len(result[("bioproject", "PRJDB200")]) == 2

    def test_limit_applied_per_entry(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        rows = [("bioproject", "PRJDB100", "biosample", f"SAMD{i:03d}") for i in range(10)]
        _create_test_db(db, rows)

        result = get_linked_ids_limited_bulk(db, [("bioproject", "PRJDB100")], limit=3)

        assert len(result[("bioproject", "PRJDB100")]) == 3

    def test_entry_not_in_db(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        _create_test_db(db, [])

        result = get_linked_ids_limited_bulk(db, [("bioproject", "PRJDB100")], limit=10)

        assert result == {("bioproject", "PRJDB100"): []}

    def test_empty_entries(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        _create_test_db(db, [])

        result = get_linked_ids_limited_bulk(db, [], limit=10)

        assert result == {}


class TestGetLinkedIdsLimitedBulkConsistency:
    """Bulk limited results match individual limited results."""

    def test_matches_individual_limited(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        _create_test_db(
            db,
            [
                ("bioproject", "PRJDB100", "biosample", "SAMD001"),
                ("bioproject", "PRJDB100", "sra-study", "DRP001"),
                ("bioproject", "PRJDB200", "sra-study", "DRP002"),
            ],
        )

        entries = [("bioproject", "PRJDB100"), ("bioproject", "PRJDB200")]
        bulk_result = get_linked_ids_limited_bulk(db, entries, limit=10)

        for entry_type, entry_id in entries:
            individual = get_linked_ids_limited(db, entry_type, entry_id, limit=10)
            assert bulk_result[(entry_type, entry_id)] == individual


class TestGetLinkedIdsLimitedBulkDbMissing:
    def test_raises_file_not_found(self, tmp_path: Path) -> None:
        missing = tmp_path.joinpath("does_not_exist.duckdb")

        with pytest.raises(FileNotFoundError, match="DuckDB file not found"):
            get_linked_ids_limited_bulk(missing, [("bioproject", "PRJDB100")], limit=10)


# --- count_linked_ids ---


class TestCountLinkedIdsNormal:
    """Normal-case tests for count_linked_ids."""

    def test_single_type(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        _create_test_db(
            db,
            [
                ("bioproject", "PRJDB100", "biosample", "SAMD001"),
                ("bioproject", "PRJDB100", "biosample", "SAMD002"),
            ],
        )

        result = count_linked_ids(db, "bioproject", "PRJDB100")

        assert result == {"biosample": 2}

    def test_multiple_types(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        _create_test_db(
            db,
            [
                ("bioproject", "PRJDB100", "biosample", "SAMD001"),
                ("bioproject", "PRJDB100", "biosample", "SAMD002"),
                ("bioproject", "PRJDB100", "sra-study", "DRP001"),
                ("sra-run", "DRR001", "bioproject", "PRJDB100"),
            ],
        )

        result = count_linked_ids(db, "bioproject", "PRJDB100")

        assert result == {"biosample": 2, "sra-run": 1, "sra-study": 1}

    def test_nonexistent_id(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        _create_test_db(db, [("hum-id", "hum0014", "jga-study", "JGAS000101")])

        result = count_linked_ids(db, "hum-id", "NONEXISTENT")

        assert result == {}

    def test_bidirectional(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        _create_test_db(db, [("hum-id", "hum0014", "jga-study", "JGAS000101")])

        fwd = count_linked_ids(db, "hum-id", "hum0014")
        rev = count_linked_ids(db, "jga-study", "JGAS000101")

        assert fwd == {"jga-study": 1}
        assert rev == {"hum-id": 1}


class TestCountLinkedIdsConsistency:
    """count_linked_ids is consistent with iter_linked_ids."""

    def test_counts_match_full_results(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        rows = [("bioproject", "PRJDB100", "biosample", f"SAMD{i:03d}") for i in range(5)] + [
            ("bioproject", "PRJDB100", "sra-study", f"DRP{i:03d}") for i in range(3)
        ]
        _create_test_db(db, rows)

        full = list(iter_linked_ids(db, "bioproject", "PRJDB100"))
        counts = count_linked_ids(db, "bioproject", "PRJDB100")

        assert sum(counts.values()) == len(full)
        assert counts["biosample"] == 5
        assert counts["sra-study"] == 3


class TestCountLinkedIdsDbMissing:
    def test_raises_file_not_found(self, tmp_path: Path) -> None:
        missing = tmp_path.joinpath("does_not_exist.duckdb")

        with pytest.raises(FileNotFoundError, match="DuckDB file not found"):
            count_linked_ids(missing, "hum-id", "hum0014")


# --- count_linked_ids_bulk ---


class TestCountLinkedIdsBulkNormal:
    """Normal-case tests for count_linked_ids_bulk."""

    def test_single_entry(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        _create_test_db(
            db,
            [
                ("bioproject", "PRJDB100", "biosample", "SAMD001"),
                ("bioproject", "PRJDB100", "biosample", "SAMD002"),
            ],
        )

        result = count_linked_ids_bulk(db, [("bioproject", "PRJDB100")])

        assert result == {("bioproject", "PRJDB100"): {"biosample": 2}}

    def test_multiple_entries(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        _create_test_db(
            db,
            [
                ("bioproject", "PRJDB100", "biosample", "SAMD001"),
                ("bioproject", "PRJDB200", "sra-study", "DRP001"),
                ("bioproject", "PRJDB200", "sra-study", "DRP002"),
            ],
        )

        result = count_linked_ids_bulk(
            db,
            [("bioproject", "PRJDB100"), ("bioproject", "PRJDB200")],
        )

        assert result[("bioproject", "PRJDB100")] == {"biosample": 1}
        assert result[("bioproject", "PRJDB200")] == {"sra-study": 2}

    def test_entry_not_in_db(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        _create_test_db(db, [])

        result = count_linked_ids_bulk(db, [("bioproject", "PRJDB100")])

        assert result == {("bioproject", "PRJDB100"): {}}

    def test_empty_entries(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        _create_test_db(db, [])

        result = count_linked_ids_bulk(db, [])

        assert result == {}


class TestCountLinkedIdsBulkConsistency:
    """Bulk counts match individual counts."""

    def test_matches_individual_counts(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        _create_test_db(
            db,
            [
                ("bioproject", "PRJDB100", "biosample", "SAMD001"),
                ("bioproject", "PRJDB100", "sra-study", "DRP001"),
                ("bioproject", "PRJDB200", "sra-study", "DRP002"),
            ],
        )

        entries = [("bioproject", "PRJDB100"), ("bioproject", "PRJDB200")]
        bulk_result = count_linked_ids_bulk(db, entries)

        for entry_type, entry_id in entries:
            individual = count_linked_ids(db, entry_type, entry_id)
            assert bulk_result[(entry_type, entry_id)] == individual


class TestCountLinkedIdsBulkDbMissing:
    def test_raises_file_not_found(self, tmp_path: Path) -> None:
        missing = tmp_path.joinpath("does_not_exist.duckdb")

        with pytest.raises(FileNotFoundError, match="DuckDB file not found"):
            count_linked_ids_bulk(missing, [("bioproject", "PRJDB100")])


# --- PBT for new functions ---


class TestNewFunctionsPBT:
    """Property-based tests for iter_linked_ids, get_linked_ids_limited, count_linked_ids."""

    @settings(max_examples=20)
    @given(
        limit=st.integers(min_value=0, max_value=100),
    )
    def test_limited_len_le_limit(self, limit: int) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Path(tmpdir).joinpath("pbt_limited.duckdb")
            rows = [("bioproject", "PRJDB100", "biosample", f"SAMD{i:03d}") for i in range(20)]
            _create_test_db(db, rows)

            result = get_linked_ids_limited(db, "bioproject", "PRJDB100", limit=limit)

            assert len(result) <= limit

    @given(
        acc_type=st.sampled_from([e.value for e in AccessionType]),
        acc_id=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
            min_size=1,
            max_size=20,
        ),
    )
    def test_count_empty_db_always_returns_empty(self, acc_type: str, acc_id: str) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Path(tmpdir).joinpath("pbt_empty.duckdb")
            _create_test_db(db, [])

            result = count_linked_ids(db, acc_type, acc_id)

            assert result == {}
