"""Tests for ddbj_search_api.dblink.client."""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

import duckdb
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ddbj_search_api.dblink import client as dblink_client
from ddbj_search_api.dblink.client import (
    _get_conn,
    _reset_cache,
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
        _create_test_db(db, [("humandbs", "hum0014", "jga-study", "JGAS000101")])

        result = list(iter_linked_ids(db, "humandbs", "NONEXISTENT"))

        assert result == []

    def test_bidirectional(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        _create_test_db(db, [("humandbs", "hum0014", "jga-study", "JGAS000101")])

        result_fwd = list(iter_linked_ids(db, "humandbs", "hum0014"))
        result_rev = list(iter_linked_ids(db, "jga-study", "JGAS000101"))

        assert result_fwd == [("jga-study", "JGAS000101")]
        assert result_rev == [("humandbs", "hum0014")]

    def test_returns_dst_when_queried_as_src(self, tmp_path: Path) -> None:
        db = tmp_path / "test.duckdb"
        _create_test_db(db, [("humandbs", "hum0014", "jga-study", "JGAS000101")])

        result = list(iter_linked_ids(db, "humandbs", "hum0014"))

        assert result == [("jga-study", "JGAS000101")]

    def test_returns_src_when_queried_as_dst(self, tmp_path: Path) -> None:
        db = tmp_path / "test.duckdb"
        _create_test_db(db, [("humandbs", "hum0014", "jga-study", "JGAS000101")])

        result = list(iter_linked_ids(db, "jga-study", "JGAS000101"))

        assert result == [("humandbs", "hum0014")]

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
                ("humandbs", "hum0014", "jga-study", "JGAS000101"),
                ("humandbs", "hum0014", "bioproject", "PRJDB100"),
            ],
        )

        result = list(iter_linked_ids(db, "humandbs", "hum0014", target=["jga-study"]))

        assert result == [("jga-study", "JGAS000101")]

    def test_multiple_targets(self, tmp_path: Path) -> None:
        db = tmp_path / "test.duckdb"
        _create_test_db(
            db,
            [
                ("humandbs", "hum0014", "jga-study", "JGAS000101"),
                ("humandbs", "hum0014", "bioproject", "PRJDB100"),
                ("humandbs", "hum0014", "biosample", "SAMD001"),
            ],
        )

        result = list(iter_linked_ids(db, "humandbs", "hum0014", target=["jga-study", "bioproject"]))

        assert len(result) == 2
        types = {r[0] for r in result}
        assert types == {"jga-study", "bioproject"}

    def test_target_not_matching_returns_empty(self, tmp_path: Path) -> None:
        db = tmp_path / "test.duckdb"
        _create_test_db(db, [("humandbs", "hum0014", "jga-study", "JGAS000101")])

        result = list(iter_linked_ids(db, "humandbs", "hum0014", target=["bioproject"]))

        assert result == []

    def test_target_filters_reverse_direction(self, tmp_path: Path) -> None:
        db = tmp_path / "test.duckdb"
        _create_test_db(
            db,
            [
                ("humandbs", "hum0014", "jga-study", "JGAS000101"),
                ("bioproject", "PRJDB100", "jga-study", "JGAS000101"),
            ],
        )

        result = list(iter_linked_ids(db, "jga-study", "JGAS000101", target=["humandbs"]))

        assert result == [("humandbs", "hum0014")]

    def test_target_none_returns_all(self, tmp_path: Path) -> None:
        db = tmp_path / "test.duckdb"
        _create_test_db(
            db,
            [
                ("humandbs", "hum0014", "jga-study", "JGAS000101"),
                ("humandbs", "hum0014", "bioproject", "PRJDB100"),
            ],
        )

        result = list(iter_linked_ids(db, "humandbs", "hum0014", target=None))

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
            list(iter_linked_ids(missing, "humandbs", "hum0014"))


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
        _create_test_db(db, [("humandbs", "hum0014", "jga-study", "JGAS000101")])

        result = get_linked_ids_limited(db, "humandbs", "NONEXISTENT", limit=10)

        assert result == []


class TestGetLinkedIdsLimitedEquivalence:
    """Limited results are a per-type prefix of full results."""

    def test_prefix_of_full_results_single_type(self, tmp_path: Path) -> None:
        """With a single type, limited is still a prefix of full."""
        db = tmp_path.joinpath("test.duckdb")
        rows = [("bioproject", "PRJDB100", "biosample", f"SAMD{i:03d}") for i in range(10)]
        _create_test_db(db, rows)

        full = list(iter_linked_ids(db, "bioproject", "PRJDB100"))
        limited = get_linked_ids_limited(db, "bioproject", "PRJDB100", limit=5)

        assert limited == full[:5]


class TestGetLinkedIdsLimitedPerType:
    """Verify limit is applied per linked type, not globally."""

    def test_limit_per_type_returns_each_type(self, tmp_path: Path) -> None:
        """With limit=2, biosample gets first 2 and sra-study gets first 2."""
        db = tmp_path.joinpath("test.duckdb")
        rows = [
            *[("bioproject", "PRJDB100", "biosample", f"SAMD{i:03d}") for i in range(5)],
            *[("bioproject", "PRJDB100", "sra-study", f"DRP{i:03d}") for i in range(3)],
        ]
        _create_test_db(db, rows)

        result = get_linked_ids_limited(db, "bioproject", "PRJDB100", limit=2)

        by_type: dict[str, list[str]] = {}
        for t, acc in result:
            by_type.setdefault(t, []).append(acc)

        # Count per type
        assert len(by_type["biosample"]) == 2
        assert len(by_type["sra-study"]) == 2
        # First 2 by accession order within each type
        assert by_type["biosample"] == ["SAMD000", "SAMD001"]
        assert by_type["sra-study"] == ["DRP000", "DRP001"]

    def test_limit_larger_than_type_count(self, tmp_path: Path) -> None:
        """When limit > count for a type, all entries of that type are returned."""
        db = tmp_path.joinpath("test.duckdb")
        rows = [
            *[("bioproject", "PRJDB100", "biosample", f"SAMD{i:03d}") for i in range(3)],
            *[("bioproject", "PRJDB100", "sra-study", f"DRP{i:03d}") for i in range(2)],
        ]
        _create_test_db(db, rows)

        result = get_linked_ids_limited(db, "bioproject", "PRJDB100", limit=10)

        by_type: dict[str, list[str]] = {}
        for t, acc in result:
            by_type.setdefault(t, []).append(acc)

        assert len(by_type["biosample"]) == 3
        assert len(by_type["sra-study"]) == 2

    def test_limit_zero_returns_empty(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        rows = [
            ("bioproject", "PRJDB100", "biosample", "SAMD001"),
            ("bioproject", "PRJDB100", "sra-study", "DRP001"),
        ]
        _create_test_db(db, rows)

        result = get_linked_ids_limited(db, "bioproject", "PRJDB100", limit=0)

        assert result == []

    def test_results_sorted(self, tmp_path: Path) -> None:
        """Results are sorted by (type, accession) across all types."""
        db = tmp_path.joinpath("test.duckdb")
        rows = [
            *[("bioproject", "PRJDB100", "sra-study", f"DRP{i:03d}") for i in range(5)],
            *[("bioproject", "PRJDB100", "biosample", f"SAMD{i:03d}") for i in range(5)],
        ]
        _create_test_db(db, rows)

        result = get_linked_ids_limited(db, "bioproject", "PRJDB100", limit=3)

        assert result == sorted(result)


class TestGetLinkedIdsLimitedDbMissing:
    def test_raises_file_not_found(self, tmp_path: Path) -> None:
        missing = tmp_path.joinpath("does_not_exist.duckdb")

        with pytest.raises(FileNotFoundError, match="DuckDB file not found"):
            get_linked_ids_limited(missing, "humandbs", "hum0014", limit=10)


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


class TestGetLinkedIdsLimitedBulkPerType:
    """Verify bulk limit is applied per linked type."""

    def test_limit_per_type_in_bulk(self, tmp_path: Path) -> None:
        """Each entry gets limit per linked type, not globally."""
        db = tmp_path.joinpath("test.duckdb")
        rows = [
            *[("bioproject", "PRJDB100", "biosample", f"SAMD{i:03d}") for i in range(5)],
            *[("bioproject", "PRJDB100", "sra-study", f"DRP{i:03d}") for i in range(3)],
        ]
        _create_test_db(db, rows)

        result = get_linked_ids_limited_bulk(db, [("bioproject", "PRJDB100")], limit=2)
        linked = result[("bioproject", "PRJDB100")]

        by_type: dict[str, list[str]] = {}
        for t, acc in linked:
            by_type.setdefault(t, []).append(acc)

        assert len(by_type["biosample"]) == 2
        assert len(by_type["sra-study"]) == 2


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
        _create_test_db(db, [("humandbs", "hum0014", "jga-study", "JGAS000101")])

        result = count_linked_ids(db, "humandbs", "NONEXISTENT")

        assert result == {}

    def test_bidirectional(self, tmp_path: Path) -> None:
        db = tmp_path.joinpath("test.duckdb")
        _create_test_db(db, [("humandbs", "hum0014", "jga-study", "JGAS000101")])

        fwd = count_linked_ids(db, "humandbs", "hum0014")
        rev = count_linked_ids(db, "jga-study", "JGAS000101")

        assert fwd == {"jga-study": 1}
        assert rev == {"humandbs": 1}


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
            count_linked_ids(missing, "humandbs", "hum0014")


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

    @settings(
        max_examples=20,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        limit=st.integers(min_value=0, max_value=100),
    )
    def test_limited_per_type_len_le_limit(self, limit: int, tmp_path: Path) -> None:
        """Each linked type has at most *limit* entries."""
        db = tmp_path / "pbt_limited.duckdb"
        if not db.exists():
            rows = [
                *[("bioproject", "PRJDB100", "biosample", f"SAMD{i:03d}") for i in range(20)],
                *[("bioproject", "PRJDB100", "sra-study", f"DRP{i:03d}") for i in range(15)],
            ]
            _create_test_db(db, rows)

        result = get_linked_ids_limited(db, "bioproject", "PRJDB100", limit=limit)

        by_type: dict[str, int] = {}
        for t, _ in result:
            by_type[t] = by_type.get(t, 0) + 1
        for count in by_type.values():
            assert count <= limit

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


# --- Connection cache & file replacement ---


class TestCacheBypassAfterInvalidation:
    """Verify that atomic file replacement becomes visible after cache invalidation.

    DuckDB's process-global ``DBInstanceCache`` caches database instances
    by file path.  The client bypasses this via ``:memory:`` + ATTACH and
    maintains its own TTL-based connection cache.  Atomic file
    replacement becomes visible after :func:`_reset_cache` clears the
    cached connection.
    """

    def test_iter_sees_new_data_after_file_replacement(self, tmp_path: Path) -> None:
        db = tmp_path / "dblink.duckdb"
        _create_test_db(db, [("bioproject", "PRJDB100", "biosample", "SAMD001")])

        result1 = list(iter_linked_ids(db, "bioproject", "PRJDB100"))
        assert len(result1) == 1

        new_db = tmp_path / "dblink.tmp.duckdb"
        _create_test_db(
            new_db,
            [
                ("bioproject", "PRJDB100", "biosample", "SAMD001"),
                ("bioproject", "PRJDB100", "biosample", "SAMD002"),
            ],
        )
        new_db.replace(db)
        _reset_cache()

        result2 = list(iter_linked_ids(db, "bioproject", "PRJDB100"))
        assert len(result2) == 2

    def test_count_sees_new_data_after_file_replacement(self, tmp_path: Path) -> None:
        db = tmp_path / "dblink.duckdb"
        _create_test_db(db, [("bioproject", "PRJDB100", "biosample", "SAMD001")])

        counts1 = count_linked_ids(db, "bioproject", "PRJDB100")
        assert counts1 == {"biosample": 1}

        new_db = tmp_path / "dblink.tmp.duckdb"
        _create_test_db(
            new_db,
            [
                ("bioproject", "PRJDB100", "biosample", "SAMD001"),
                ("bioproject", "PRJDB100", "biosample", "SAMD002"),
            ],
        )
        new_db.replace(db)
        _reset_cache()

        counts2 = count_linked_ids(db, "bioproject", "PRJDB100")
        assert counts2 == {"biosample": 2}

    def test_limited_sees_new_data_after_file_replacement(self, tmp_path: Path) -> None:
        db = tmp_path / "dblink.duckdb"
        _create_test_db(db, [("bioproject", "PRJDB100", "biosample", "SAMD001")])

        result1 = get_linked_ids_limited(db, "bioproject", "PRJDB100", limit=10)
        assert len(result1) == 1

        new_db = tmp_path / "dblink.tmp.duckdb"
        _create_test_db(
            new_db,
            [
                ("bioproject", "PRJDB100", "biosample", "SAMD001"),
                ("bioproject", "PRJDB100", "biosample", "SAMD002"),
            ],
        )
        new_db.replace(db)
        _reset_cache()

        result2 = get_linked_ids_limited(db, "bioproject", "PRJDB100", limit=10)
        assert len(result2) == 2


class TestCacheBypassBeforeInvalidation:
    """Without cache invalidation, cached connection keeps returning old data."""

    def test_stale_data_returned_when_cache_not_cleared(self, tmp_path: Path) -> None:
        db = tmp_path / "dblink.duckdb"
        _create_test_db(db, [("bioproject", "PRJDB100", "biosample", "SAMD001")])

        result1 = get_linked_ids_limited(db, "bioproject", "PRJDB100", limit=10)
        assert len(result1) == 1

        new_db = tmp_path / "dblink.tmp.duckdb"
        _create_test_db(
            new_db,
            [
                ("bioproject", "PRJDB100", "biosample", "SAMD001"),
                ("bioproject", "PRJDB100", "biosample", "SAMD002"),
            ],
        )
        new_db.replace(db)

        # Within TTL, the cached connection still reports the old state.
        result2 = get_linked_ids_limited(db, "bioproject", "PRJDB100", limit=10)
        assert len(result2) == 1


# --- Connection cache TTL ---


class TestConnCacheTtl:
    """Verify TTL-based connection cache behaviour of :func:`_get_conn`."""

    def test_reuses_connection_within_ttl(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        db = tmp_path / "dblink.duckdb"
        _create_test_db(db, [("bioproject", "PRJDB100", "biosample", "SAMD001")])

        t = [0.0]
        monkeypatch.setattr("ddbj_search_api.dblink.client.time.monotonic", lambda: t[0])

        conn1 = _get_conn(db)
        t[0] = dblink_client._CACHE_TTL_SECONDS - 1
        conn2 = _get_conn(db)

        assert conn1 is conn2

    def test_creates_new_connection_after_ttl(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        db = tmp_path / "dblink.duckdb"
        _create_test_db(db, [("bioproject", "PRJDB100", "biosample", "SAMD001")])

        t = [0.0]
        monkeypatch.setattr("ddbj_search_api.dblink.client.time.monotonic", lambda: t[0])

        conn1 = _get_conn(db)
        t[0] = dblink_client._CACHE_TTL_SECONDS + 1
        conn2 = _get_conn(db)

        assert conn1 is not conn2

    def test_reset_cache_invalidates_immediately(self, tmp_path: Path) -> None:
        db = tmp_path / "dblink.duckdb"
        _create_test_db(db, [("bioproject", "PRJDB100", "biosample", "SAMD001")])

        conn1 = _get_conn(db)
        _reset_cache()
        conn2 = _get_conn(db)

        assert conn1 is not conn2

    def test_separate_paths_get_separate_connections(self, tmp_path: Path) -> None:
        db_a = tmp_path / "a.duckdb"
        db_b = tmp_path / "b.duckdb"
        _create_test_db(db_a, [("bioproject", "PRJDB100", "biosample", "SAMD001")])
        _create_test_db(db_b, [("bioproject", "PRJDB200", "biosample", "SAMD002")])

        conn_a = _get_conn(db_a)
        conn_b = _get_conn(db_b)

        assert conn_a is not conn_b

    def test_threads_pragma_is_applied(self, tmp_path: Path) -> None:
        db = tmp_path / "dblink.duckdb"
        _create_test_db(db, [])

        conn = _get_conn(db)
        value = conn.execute("SELECT current_setting('threads')").fetchone()
        assert value is not None
        assert int(value[0]) == dblink_client._PRAGMA_THREADS


# --- Cursor independence ---


class TestCursorIndependence:
    """Multiple cursors on the same cached connection must not interfere."""

    def test_parallel_cursors_in_separate_threads(self, tmp_path: Path) -> None:
        """Two threads querying distinct IDs concurrently see only their own results."""
        db = tmp_path / "dblink.duckdb"
        _create_test_db(
            db,
            [
                ("bioproject", "PRJDB100", "biosample", "SAMD001"),
                ("bioproject", "PRJDB100", "biosample", "SAMD002"),
                ("bioproject", "PRJDB200", "sra-study", "DRP001"),
            ],
        )

        results: dict[str, list[tuple[str, str]]] = {}
        barrier = threading.Barrier(2)

        def worker(acc_id: str) -> None:
            barrier.wait()
            results[acc_id] = get_linked_ids_limited(db, "bioproject", acc_id, limit=10)

        threads = [
            threading.Thread(target=worker, args=("PRJDB100",)),
            threading.Thread(target=worker, args=("PRJDB200",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sorted(results["PRJDB100"]) == [
            ("biosample", "SAMD001"),
            ("biosample", "SAMD002"),
        ]
        assert results["PRJDB200"] == [("sra-study", "DRP001")]

    def test_iter_and_limited_can_run_sequentially_on_shared_connection(
        self,
        tmp_path: Path,
    ) -> None:
        """A partially-consumed iter_linked_ids and a later get_linked_ids_limited
        on the same cached connection both produce correct results.
        """
        db = tmp_path / "dblink.duckdb"
        rows = [("bioproject", "PRJDB100", "biosample", f"SAMD{i:03d}") for i in range(6)]
        _create_test_db(db, rows)

        iterator = iter_linked_ids(db, "bioproject", "PRJDB100", chunk_size=2)
        first_two = [next(iterator), next(iterator)]

        limited = get_linked_ids_limited(db, "bioproject", "PRJDB100", limit=10)
        rest = list(iterator)

        assert len(first_two) == 2
        assert len(limited) == 6
        assert len(first_two) + len(rest) == 6
