"""Read-only DuckDB client for dblink relation lookups.

Uses an in-memory connection with ``ATTACH ... (READ_ONLY)`` to bypass
DuckDB's process-global ``DBInstanceCache`` (``duckdb.connect(path)``
caches by path string and would hide the converter's atomic
``Path.replace()`` updates).

The in-memory connection is shared across requests via a TTL-based
module-level cache, and each caller gets its own cursor via
``conn.cursor()`` to avoid contention on a single default cursor.
``PRAGMA threads`` is lowered per-connection to keep one query from
saturating every CPU core when requests arrive concurrently.

When the converter atomically replaces the DuckDB file, the new inode
becomes visible either (a) after :data:`_CACHE_TTL_SECONDS` elapses,
or (b) after :func:`_reset_cache` is called explicitly.
"""

from __future__ import annotations

import collections.abc
import threading
import time
from pathlib import Path

import duckdb

_CATALOG = "dblink"
_CACHE_TTL_SECONDS = 900
_PRAGMA_THREADS = 2
_CONN_CACHE: dict[Path, tuple[duckdb.DuckDBPyConnection, float]] = {}
_LOCK = threading.Lock()


def _escape_path(path: Path) -> str:
    """Escape single quotes in a path for DuckDB SQL strings."""
    return str(path).replace("'", "''")


def _check_db(db_path: Path) -> None:
    """Raise FileNotFoundError if *db_path* does not exist."""
    if not db_path.exists():
        msg = f"DuckDB file not found: {db_path}"
        raise FileNotFoundError(msg)


def _get_conn(db_path: Path) -> duckdb.DuckDBPyConnection:
    """Return a cached in-memory DuckDB connection with *db_path* attached read-only.

    On cache miss creates a new ``:memory:`` connection, attaches the
    database at *db_path*, and lowers ``PRAGMA threads`` to
    :data:`_PRAGMA_THREADS` to prevent per-query thread explosion.
    Subsequent calls within :data:`_CACHE_TTL_SECONDS` return the same
    connection object.
    """
    _check_db(db_path)
    now = time.monotonic()
    with _LOCK:
        cached = _CONN_CACHE.get(db_path)
        if cached is not None and now - cached[1] < _CACHE_TTL_SECONDS:
            return cached[0]
        conn = duckdb.connect(":memory:")
        conn.execute(f"ATTACH '{_escape_path(db_path)}' AS {_CATALOG} (READ_ONLY)")
        conn.execute(f"PRAGMA threads={_PRAGMA_THREADS}")
        _CONN_CACHE[db_path] = (conn, now)
        return conn


def _reset_cache() -> None:
    """Drop all cached connections.

    The next :func:`_get_conn` call reopens the database so that an
    atomic file replacement by the converter becomes immediately
    visible.  Previously-cached connections are not explicitly closed;
    in-flight cursors keep them alive until the consumer finishes, at
    which point the OS releases the underlying file handle.
    """
    with _LOCK:
        _CONN_CACHE.clear()


def iter_linked_ids(
    db_path: Path,
    type_: str,
    id_: str,
    target: list[str] | None = None,
    chunk_size: int = 10000,
) -> collections.abc.Generator[tuple[str, str], None, None]:
    """Yield related (type, accession) pairs in chunks.

    Streams results via ``fetchmany`` on an independent cursor so that
    concurrent generators on the same cached connection do not share
    state.

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
    conn = _get_conn(db_path)
    cursor = conn.cursor()
    try:
        if target:
            cursor.execute(
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
            cursor.execute(
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
        cursor.close()


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
    conn = _get_conn(db_path)
    cursor = conn.cursor()
    try:
        rows: list[tuple[str, str]] = cursor.execute(
            _QUERY_LIMITED,
            (type_, id_, type_, id_, limit),
        ).fetchall()
    finally:
        cursor.close()

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
    conn = _get_conn(db_path)
    cursor = conn.cursor()
    try:
        rows: list[tuple[str, int]] = cursor.execute(
            _QUERY_COUNT,
            (type_, id_, type_, id_),
        ).fetchall()
    finally:
        cursor.close()

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

    Uses a single cached connection and loops over entries via fresh
    cursors to avoid expensive LATERAL joins on large tables.

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
    conn = _get_conn(db_path)
    for type_, id_ in entries:
        cursor = conn.cursor()
        try:
            rows: list[tuple[str, str]] = cursor.execute(
                _QUERY_LIMITED,
                (type_, id_, type_, id_, limit),
            ).fetchall()
        finally:
            cursor.close()
        result[(type_, id_)] = rows

    return result


def count_linked_ids_bulk(
    db_path: Path,
    entries: list[tuple[str, str]],
) -> dict[tuple[str, str], dict[str, int]]:
    """Return per-type counts for multiple accessions in one pass.

    Uses a single cached connection and loops over entries via fresh
    cursors to avoid expensive joins on large tables.

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
    conn = _get_conn(db_path)
    for type_, id_ in entries:
        cursor = conn.cursor()
        try:
            rows: list[tuple[str, int]] = cursor.execute(
                _QUERY_COUNT,
                (type_, id_, type_, id_),
            ).fetchall()
        finally:
            cursor.close()
        result[(type_, id_)] = dict(rows)

    return result
