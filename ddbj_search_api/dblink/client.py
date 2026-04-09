"""Read-only DuckDB client for dblink relation lookups.

Uses ``:memory:`` + ``ATTACH`` to bypass DuckDB's process-global
``DBInstanceCache``.  Without this, ``duckdb.connect(path)`` reuses a
cached database instance keyed by path string, so atomic file
replacement (``Path.replace()``) by the converter goes unnoticed.
"""

from __future__ import annotations

import collections.abc
import contextlib
from pathlib import Path

import duckdb

_CATALOG = "dblink"


def _escape_path(path: Path) -> str:
    """Escape single quotes in a path for DuckDB SQL strings."""
    return str(path).replace("'", "''")


def _check_db(db_path: Path) -> None:
    """Raise FileNotFoundError if *db_path* does not exist."""
    if not db_path.exists():
        msg = f"DuckDB file not found: {db_path}"
        raise FileNotFoundError(msg)


@contextlib.contextmanager
def _open_dblink(
    db_path: Path,
) -> collections.abc.Generator[duckdb.DuckDBPyConnection, None, None]:
    """Open an in-memory connection with *db_path* attached read-only.

    Bypasses ``DBInstanceCache`` because ``:memory:`` is never cached.
    """
    _check_db(db_path)
    conn = duckdb.connect(":memory:")
    try:
        conn.execute(f"ATTACH '{_escape_path(db_path)}' AS {_CATALOG} (READ_ONLY)")
        yield conn
    finally:
        conn.close()


def iter_linked_ids(
    db_path: Path,
    type_: str,
    id_: str,
    target: list[str] | None = None,
    chunk_size: int = 10000,
) -> collections.abc.Generator[tuple[str, str], None, None]:
    """Yield related (type, accession) pairs in chunks.

    Streams results via ``fetchmany`` to avoid loading all rows into
    memory at once.  The connection is held open until the generator
    is closed.

    Args:
        db_path: Path to the DuckDB database file.
        type_: Source accession type.
        id_: Source accession identifier.
        target: Optional list of target accession types to filter by.
        chunk_size: Number of rows per ``fetchmany`` call.

    Yields:
        ``(type, accession)`` tuples, sorted by type then accession.

    Raises:
        FileNotFoundError: If *db_path* does not exist.
    """
    _check_db(db_path)

    conn = duckdb.connect(":memory:")
    try:
        conn.execute(f"ATTACH '{_escape_path(db_path)}' AS {_CATALOG} (READ_ONLY)")
        if target:
            cursor = conn.execute(
                f"""
                SELECT dst_type, dst_accession FROM {_CATALOG}.relation
                WHERE src_type = ? AND src_accession = ? AND dst_type IN (SELECT UNNEST(?))
                UNION ALL
                SELECT src_type, src_accession FROM {_CATALOG}.relation
                WHERE dst_type = ? AND dst_accession = ? AND src_type IN (SELECT UNNEST(?))
                ORDER BY 1, 2
                """,
                (type_, id_, list(target), type_, id_, list(target)),
            )
        else:
            cursor = conn.execute(
                f"""
                SELECT dst_type, dst_accession FROM {_CATALOG}.relation
                WHERE src_type = ? AND src_accession = ?
                UNION ALL
                SELECT src_type, src_accession FROM {_CATALOG}.relation
                WHERE dst_type = ? AND dst_accession = ?
                ORDER BY 1, 2
                """,
                (type_, id_, type_, id_),
            )
        while True:
            batch = cursor.fetchmany(chunk_size)
            if not batch:
                break
            yield from batch
    finally:
        conn.close()


def get_linked_ids_limited(
    db_path: Path,
    type_: str,
    id_: str,
    limit: int,
) -> list[tuple[str, str]]:
    """Return up to *limit* per linked type related (type, accession) pairs.

    Uses ``ROW_NUMBER() OVER (PARTITION BY linked_type)`` so that each
    linked type independently gets at most *limit* rows.

    Args:
        db_path: Path to the DuckDB database file.
        type_: Source accession type.
        id_: Source accession identifier.
        limit: Maximum number of rows to return per linked type.

    Returns:
        Sorted list of ``(type, accession)`` tuples (at most *limit* per linked type).

    Raises:
        FileNotFoundError: If *db_path* does not exist.
    """
    with _open_dblink(db_path) as conn:
        rows: list[tuple[str, str]] = conn.execute(
            _QUERY_LIMITED,
            (type_, id_, type_, id_, limit),
        ).fetchall()

    return rows


def count_linked_ids(
    db_path: Path,
    type_: str,
    id_: str,
) -> dict[str, int]:
    """Return per-type counts of related accessions.

    Args:
        db_path: Path to the DuckDB database file.
        type_: Source accession type.
        id_: Source accession identifier.

    Returns:
        Dict mapping related accession types to their counts.

    Raises:
        FileNotFoundError: If *db_path* does not exist.
    """
    with _open_dblink(db_path) as conn:
        rows: list[tuple[str, int]] = conn.execute(
            _QUERY_COUNT,
            (type_, id_, type_, id_),
        ).fetchall()

    return dict(rows)


_QUERY_LIMITED = f"""
    SELECT linked_type, linked_accession FROM (
        SELECT linked_type, linked_accession,
               ROW_NUMBER() OVER (PARTITION BY linked_type ORDER BY linked_accession) AS rn
        FROM (
            SELECT dst_type AS linked_type, dst_accession AS linked_accession
            FROM {_CATALOG}.relation WHERE src_type = ? AND src_accession = ?
            UNION ALL
            SELECT src_type AS linked_type, src_accession AS linked_accession
            FROM {_CATALOG}.relation WHERE dst_type = ? AND dst_accession = ?
        )
    )
    WHERE rn <= ?
    ORDER BY linked_type, linked_accession
"""

_QUERY_COUNT = f"""
    SELECT linked_type, COUNT(*) AS cnt FROM (
        SELECT dst_type AS linked_type FROM {_CATALOG}.relation
        WHERE src_type = ? AND src_accession = ?
        UNION ALL
        SELECT src_type AS linked_type FROM {_CATALOG}.relation
        WHERE dst_type = ? AND dst_accession = ?
    )
    GROUP BY linked_type
    ORDER BY linked_type
"""


def get_linked_ids_limited_bulk(
    db_path: Path,
    entries: list[tuple[str, str]],
    limit: int,
) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """Return up to *limit* per linked type related (type, accession) pairs per entry.

    Uses a single connection and loops over entries to avoid expensive
    LATERAL joins on large tables.

    Args:
        db_path: Path to the DuckDB database file.
        entries: List of ``(type, id)`` pairs to look up.
        limit: Maximum number of rows to return per entry per linked type.

    Returns:
        Dict mapping ``(type, id)`` to sorted list of ``(linked_type, accession)`` tuples.

    Raises:
        FileNotFoundError: If *db_path* does not exist.
    """
    if not entries:
        return {}

    result: dict[tuple[str, str], list[tuple[str, str]]] = {(t, i): [] for t, i in entries}
    with _open_dblink(db_path) as conn:
        for type_, id_ in entries:
            rows: list[tuple[str, str]] = conn.execute(
                _QUERY_LIMITED,
                (type_, id_, type_, id_, limit),
            ).fetchall()
            result[(type_, id_)] = rows

    return result


def count_linked_ids_bulk(
    db_path: Path,
    entries: list[tuple[str, str]],
) -> dict[tuple[str, str], dict[str, int]]:
    """Return per-type counts for multiple accessions in one query.

    Uses a single connection and loops over entries to avoid expensive
    joins on large tables.

    Args:
        db_path: Path to the DuckDB database file.
        entries: List of ``(type, id)`` pairs to look up.

    Returns:
        Dict mapping ``(type, id)`` to ``{linked_type: count}``.

    Raises:
        FileNotFoundError: If *db_path* does not exist.
    """
    if not entries:
        return {}

    result: dict[tuple[str, str], dict[str, int]] = {(t, i): {} for t, i in entries}
    with _open_dblink(db_path) as conn:
        for type_, id_ in entries:
            rows: list[tuple[str, int]] = conn.execute(
                _QUERY_COUNT,
                (type_, id_, type_, id_),
            ).fetchall()
            result[(type_, id_)] = dict(rows)

    return result
