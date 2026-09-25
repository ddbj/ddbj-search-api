"""Read-only DuckDB client for dblink dbxref lookups.

Uses an in-memory connection with ``ATTACH ... (READ_ONLY)`` to bypass
DuckDB's process-global ``DBInstanceCache`` (``duckdb.connect(path)``
caches by path string and would hide the converter's atomic
``Path.replace()`` updates).

The in-memory connection is shared across requests via a TTL-based
module-level cache, and each caller gets its own cursor via
``conn.cursor()`` to avoid contention on a single default cursor.
``PRAGMA threads`` is lowered per-connection to keep one query from
saturating every CPU core when requests arrive concurrently.  ``memory_limit`` is
capped per-connection as well, because DuckDB's default budget is derived
from the memory it can see rather than from what the lookups need.

When the converter atomically replaces the DuckDB file, the new inode
becomes visible either (a) after :data:`_CACHE_TTL_SECONDS` elapses,
or (b) after :func:`_reset_cache` is called explicitly.

The converter also writes ``dbxref_heavy``: per-linked-type row counts for the
few accessions with more than ten thousand rows (some have tens of millions).
It is read into memory together with the connection so that the per-type
limited lookups and the counts can treat those accessions differently without
touching their rows (see :func:`_fetch_limited`).
"""

from __future__ import annotations

import collections.abc
import tempfile
import threading
import time
from pathlib import Path

import duckdb

_CATALOG = "dblink"
_CACHE_TTL_SECONDS = 900
_PRAGMA_THREADS = 2
# DuckDB caches every block it has read until it reaches memory_limit, and the
# default limit is 80% of the memory it can see: the whole host when the
# container is unlimited. With a database of tens of GB that lets each worker
# process grow to the size of the database. Lookups here are index point reads,
# so a small budget costs no latency; the file itself stays in the page cache.
_MEMORY_LIMIT = "2GB"
# (accession_type, accession) -> [(linked_type, row count), ...] sorted by linked_type
HeavyMap = dict[tuple[str, str], list[tuple[str, int]]]
_CONN_CACHE: dict[Path, tuple[duckdb.DuckDBPyConnection, float, HeavyMap]] = {}
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
    return _get_conn_and_heavy(db_path)[0]


def _get_conn_and_heavy(db_path: Path) -> tuple[duckdb.DuckDBPyConnection, HeavyMap]:
    """Return the cached connection for *db_path* and its ``dbxref_heavy`` contents."""
    _check_db(db_path)
    now = time.monotonic()
    with _LOCK:
        cached = _CONN_CACHE.get(db_path)
        if cached is not None and now - cached[1] < _CACHE_TTL_SECONDS:
            return cached[0], cached[2]
        conn = duckdb.connect(":memory:")
        conn.execute(f"ATTACH '{_escape_path(db_path)}' AS {_CATALOG} (READ_ONLY)")
        conn.execute(f"PRAGMA threads={_PRAGMA_THREADS}")
        conn.execute(f"SET memory_limit='{_MEMORY_LIMIT}'")
        # A query that outgrows memory_limit spills to temp_directory, which
        # defaults to ``.tmp`` under the working directory: the bind-mounted
        # source tree. Spill files there outlive the container and end up next
        # to the code, so point them at the container's own temp dir instead.
        conn.execute(f"SET temp_directory='{_escape_path(Path(tempfile.gettempdir()) / 'duckdb')}'")
        # iter_linked_ids returns rows in stored order instead of sorting them.
        conn.execute("SET preserve_insertion_order=true")
        heavy = _load_heavy(conn)
        _CONN_CACHE[db_path] = (conn, now, heavy)
        return conn, heavy


def _load_heavy(conn: duckdb.DuckDBPyConnection) -> HeavyMap:
    """Read ``dbxref_heavy`` into memory; empty when the table is absent.

    Databases built before the converter wrote this table lack it.  Treating
    every accession as light then gives the same results, only slower for the
    large ones.
    """
    exists = conn.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE database_name = ? AND table_name = 'dbxref_heavy'",
        (_CATALOG,),
    ).fetchone()
    if exists is None or exists[0] == 0:
        return {}
    heavy: HeavyMap = {}
    rows = conn.execute(
        f"SELECT accession_type, accession, linked_type, n FROM {_CATALOG}.dbxref_heavy "
        "ORDER BY accession_type, accession, linked_type"
    ).fetchall()
    for accession_type, accession, linked_type, n in rows:
        heavy.setdefault((accession_type, accession), []).append((linked_type, int(n)))
    return heavy


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

    The rows come back in stored order, which is already
    ``(linked_type, linked_accession)`` within one accession because the
    converter writes ``dbxref`` sorted by all four columns.  Sorting here
    would make DuckDB materialize the whole result, millions of rows for
    some accessions, and keep it pinned against ``memory_limit`` until the
    client has downloaded the last byte.

    The *target* filter is a plain row predicate for the same reason:
    ``IN (SELECT ...)`` is planned as a join, which does not keep the stored
    order.

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
                SELECT linked_type, linked_accession FROM {_CATALOG}.dbxref
                WHERE accession_type = ? AND accession = ?
                  AND list_contains(?::VARCHAR[], linked_type)
                """,
                (type_, id_, list(target)),
            )
        else:
            cursor.execute(
                f"""
                SELECT linked_type, linked_accession FROM {_CATALOG}.dbxref
                WHERE accession_type = ? AND accession = ?
                """,
                (type_, id_),
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

    Each linked type independently gets at most *limit* rows, the first ones
    in ``(linked_type, linked_accession)`` order (see :func:`_fetch_limited`).

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
    conn, heavy = _get_conn_and_heavy(db_path)
    cursor = conn.cursor()
    try:
        rows = _fetch_limited(cursor, heavy, [(type_, id_)], limit)
    finally:
        cursor.close()

    return [(linked_type, linked_accession) for _, _, linked_type, linked_accession in rows]


def _fetch_limited(
    cursor: duckdb.DuckDBPyConnection,
    heavy: HeavyMap,
    entries: list[tuple[str, str]],
    limit: int,
) -> list[tuple[str, str, str, str]]:
    """Return the first *limit* linked accessions per (entry, linked type).

    Light entries go through one window query that reads all their rows and
    ranks them per linked type; they have at most ten thousand rows each, so
    that is cheap.  The same query over an accession with tens of millions of
    rows makes DuckDB read and sort every one of them, so entries listed in
    ``dbxref_heavy`` are read per linked type with ``LIMIT`` instead.
    ``LIMIT`` without ``ORDER BY`` returns the first rows in stored order,
    which is already ``(linked_type, linked_accession)`` within one accession
    because the converter writes ``dbxref`` sorted by all four columns, so
    the scan stops after *limit* rows.

    Returns:
        ``(input_type, input_accession, linked_type, linked_accession)``
        tuples, sorted.
    """
    if limit <= 0 or not entries:
        return []

    light = [e for e in entries if e not in heavy]
    groups = [(t, a, linked_type) for t, a in entries if (t, a) in heavy for linked_type, _ in heavy[(t, a)]]

    rows: list[tuple[str, str, str, str]] = []
    if light:
        rows.extend(
            cursor.execute(
                _QUERY_LIMITED_BULK,
                ([t for t, _ in light], [a for _, a in light], limit),
            ).fetchall(),
        )
    for t, a, linked_type in groups:
        rows.extend(
            (t, a, lt, la) for lt, la in cursor.execute(_QUERY_GROUP_HEAD, (t, a, linked_type, limit)).fetchall()
        )

    rows.sort()
    return rows


def count_linked_ids(
    db_path: Path,
    type_: str,
    id_: str,
) -> dict[str, int]:
    """Return per-type counts of related accessions.

    Accessions listed in ``dbxref_heavy`` are answered from it without
    counting their rows.

    Args:
        db_path: Path to the DuckDB database file.
        type_: Source accession type.
        id_: Source accession identifier.

    Returns:
        Dict mapping related accession types to their counts.

    Raises:
        FileNotFoundError: If *db_path* does not exist.
    """
    conn, heavy = _get_conn_and_heavy(db_path)
    if (type_, id_) in heavy:
        return dict(heavy[(type_, id_)])
    cursor = conn.cursor()
    try:
        rows: list[tuple[str, int]] = cursor.execute(
            _QUERY_COUNT,
            (type_, id_),
        ).fetchall()
    finally:
        cursor.close()

    return dict(rows)


_QUERY_GROUP_HEAD = f"""
    SELECT linked_type, linked_accession
    FROM {_CATALOG}.dbxref
    WHERE accession_type = ? AND accession = ? AND linked_type = ?
    LIMIT ?
"""

_QUERY_COUNT = f"""
    SELECT linked_type, COUNT(*) AS cnt
    FROM {_CATALOG}.dbxref
    WHERE accession_type = ? AND accession = ?
    GROUP BY linked_type
    ORDER BY linked_type
"""

_QUERY_LIMITED_BULK = f"""
    WITH input AS (
        SELECT UNNEST(?::VARCHAR[]) AS accession_type,
               UNNEST(?::VARCHAR[]) AS accession
    )
    SELECT input_type, input_accession, linked_type, linked_accession FROM (
        SELECT
            i.accession_type AS input_type,
            i.accession      AS input_accession,
            d.linked_type,
            d.linked_accession,
            ROW_NUMBER() OVER (
                PARTITION BY i.accession_type, i.accession, d.linked_type
                ORDER BY d.linked_accession
            ) AS rn
        FROM input i
        JOIN {_CATALOG}.dbxref d USING (accession_type, accession)
    )
    WHERE rn <= ?
    ORDER BY input_type, input_accession, linked_type, linked_accession
"""

_QUERY_BULK_UNLIMITED = f"""
    WITH input AS (
        SELECT UNNEST(?::VARCHAR[]) AS accession_type,
               UNNEST(?::VARCHAR[]) AS accession
    )
    SELECT
        i.accession_type AS input_type,
        i.accession      AS input_accession,
        d.linked_type,
        d.linked_accession
    FROM input i
    JOIN {_CATALOG}.dbxref d USING (accession_type, accession)
    ORDER BY i.accession_type, i.accession, d.linked_type, d.linked_accession
"""

_QUERY_COUNT_BULK = f"""
    WITH input AS (
        SELECT UNNEST(?::VARCHAR[]) AS accession_type,
               UNNEST(?::VARCHAR[]) AS accession
    )
    SELECT
        i.accession_type AS input_type,
        i.accession      AS input_accession,
        d.linked_type,
        COUNT(*)         AS cnt
    FROM input i
    JOIN {_CATALOG}.dbxref d USING (accession_type, accession)
    GROUP BY i.accession_type, i.accession, d.linked_type
    ORDER BY i.accession_type, i.accession, d.linked_type
"""


def get_linked_ids_limited_bulk(
    db_path: Path,
    entries: list[tuple[str, str]],
    limit: int,
) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """Return up to *limit* per linked type related (type, accession) pairs per entry.

    All light entries are looked up in one query rather than one cursor per
    entry; see :func:`_fetch_limited`.  Duplicate entries are deduplicated
    before the SQL call (the result dict has one entry per unique
    ``(type, id)`` pair).

    Args:
        db_path: Path to the DuckDB database file.
        entries: List of ``(type, id)`` pairs to look up.
        limit: Maximum number of rows to return per entry per linked type.

    Returns:
        Dict mapping ``(type, id)`` to sorted list of ``(linked_type, accession)`` tuples.
        Entries with no matches map to an empty list.

    Raises:
        FileNotFoundError: If *db_path* does not exist.
    """
    if not entries:
        return {}

    unique_entries = list(dict.fromkeys(entries))

    conn, heavy = _get_conn_and_heavy(db_path)
    cursor = conn.cursor()
    try:
        rows = _fetch_limited(cursor, heavy, unique_entries, limit)
    finally:
        cursor.close()

    result: dict[tuple[str, str], list[tuple[str, str]]] = {e: [] for e in unique_entries}
    for input_type, input_accession, linked_type, linked_accession in rows:
        result[(input_type, input_accession)].append((linked_type, linked_accession))
    return result


def get_linked_ids_bulk(
    db_path: Path,
    entries: list[tuple[str, str]],
) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """Return *all* related ``(linked_type, accession)`` pairs per entry.

    Unlike :func:`get_linked_ids_limited_bulk`, no per-linked-type row
    cap is applied: every match in the join is returned.  Used by the
    bulk endpoint, which has no ``dbXrefsLimit`` parameter and must
    serialize the full ``dbXrefs`` list per visible entry.  Implemented
    as a single SQL query that ``UNNEST``s the inputs and joins against
    the dbxref table, eliminating the per-entry cursor loop and
    bringing N=1000 from N round-trips to a single SQL execution.

    Args:
        db_path: Path to the DuckDB database file.
        entries: List of ``(type, id)`` pairs to look up.

    Returns:
        Dict mapping ``(type, id)`` to sorted list of
        ``(linked_type, accession)`` tuples.  Entries with no matches
        map to an empty list.  Duplicate inputs are deduplicated before
        the SQL call (one result entry per unique key).

    Raises:
        FileNotFoundError: If *db_path* does not exist.
    """
    if not entries:
        return {}

    unique_entries = list(dict.fromkeys(entries))
    types = [t for t, _ in unique_entries]
    accessions = [a for _, a in unique_entries]

    conn = _get_conn(db_path)
    cursor = conn.cursor()
    try:
        rows = cursor.execute(
            _QUERY_BULK_UNLIMITED,
            (types, accessions),
        ).fetchall()
    finally:
        cursor.close()

    result: dict[tuple[str, str], list[tuple[str, str]]] = {e: [] for e in unique_entries}
    for input_type, input_accession, linked_type, linked_accession in rows:
        result[(input_type, input_accession)].append((linked_type, linked_accession))
    return result


def count_linked_ids_bulk(
    db_path: Path,
    entries: list[tuple[str, str]],
) -> dict[tuple[str, str], dict[str, int]]:
    """Return per-type counts for multiple accessions in one SQL query.

    Implements the count as a single SQL query that ``UNNEST``s the
    input tuples and joins them against ``dbxref``, eliminating the
    per-entry cursor loop.  Entries listed in ``dbxref_heavy`` are answered
    from it without counting their rows.  Duplicate entries are
    deduplicated before the SQL call.

    Args:
        db_path: Path to the DuckDB database file.
        entries: List of ``(type, id)`` pairs to look up.

    Returns:
        Dict mapping ``(type, id)`` to ``{linked_type: count}``.
        Entries with no matches map to an empty dict.

    Raises:
        FileNotFoundError: If *db_path* does not exist.
    """
    if not entries:
        return {}

    unique_entries = list(dict.fromkeys(entries))
    conn, heavy = _get_conn_and_heavy(db_path)
    result: dict[tuple[str, str], dict[str, int]] = {e: dict(heavy.get(e, [])) for e in unique_entries}
    light = [e for e in unique_entries if e not in heavy]
    if not light:
        return result

    cursor = conn.cursor()
    try:
        rows = cursor.execute(
            _QUERY_COUNT_BULK,
            ([t for t, _ in light], [a for _, a in light]),
        ).fetchall()
    finally:
        cursor.close()

    for input_type, input_accession, linked_type, cnt in rows:
        result[(input_type, input_accession)][linked_type] = cnt
    return result
