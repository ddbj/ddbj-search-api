"""Read-only DuckDB client for dblink relation lookups."""

from __future__ import annotations

import collections.abc
from pathlib import Path

import duckdb


def _check_db(db_path: Path) -> None:
    """Raise FileNotFoundError if *db_path* does not exist."""
    if not db_path.exists():
        msg = f"DuckDB file not found: {db_path}"
        raise FileNotFoundError(msg)


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

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        if target:
            cursor = conn.execute(
                """
                SELECT dst_type, dst_accession FROM relation
                WHERE src_type = ? AND src_accession = ? AND dst_type IN (SELECT UNNEST(?))
                UNION ALL
                SELECT src_type, src_accession FROM relation
                WHERE dst_type = ? AND dst_accession = ? AND src_type IN (SELECT UNNEST(?))
                ORDER BY 1, 2
                """,
                (type_, id_, list(target), type_, id_, list(target)),
            )
        else:
            cursor = conn.execute(
                """
                SELECT dst_type, dst_accession FROM relation
                WHERE src_type = ? AND src_accession = ?
                UNION ALL
                SELECT src_type, src_accession FROM relation
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
    """Return up to *limit* related (type, accession) pairs.

    Uses ``LIMIT`` in SQL for efficient truncated retrieval.
    Suitable for search/detail endpoints that need a subset of dbXrefs.

    Args:
        db_path: Path to the DuckDB database file.
        type_: Source accession type.
        id_: Source accession identifier.
        limit: Maximum number of rows to return.

    Returns:
        Sorted list of ``(type, accession)`` tuples (at most *limit*).

    Raises:
        FileNotFoundError: If *db_path* does not exist.
    """
    _check_db(db_path)

    with duckdb.connect(str(db_path), read_only=True) as conn:
        rows: list[tuple[str, str]] = conn.execute(
            """
            SELECT * FROM (
                SELECT dst_type, dst_accession FROM relation
                WHERE src_type = ? AND src_accession = ?
                UNION ALL
                SELECT src_type, src_accession FROM relation
                WHERE dst_type = ? AND dst_accession = ?
                ORDER BY 1, 2
            ) LIMIT ?
            """,
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
    _check_db(db_path)

    with duckdb.connect(str(db_path), read_only=True) as conn:
        rows: list[tuple[str, int]] = conn.execute(
            """
            SELECT linked_type, COUNT(*) AS cnt FROM (
                SELECT dst_type AS linked_type FROM relation
                WHERE src_type = ? AND src_accession = ?
                UNION ALL
                SELECT src_type AS linked_type FROM relation
                WHERE dst_type = ? AND dst_accession = ?
            )
            GROUP BY linked_type
            ORDER BY linked_type
            """,
            (type_, id_, type_, id_),
        ).fetchall()

    return dict(rows)


def get_linked_ids_limited_bulk(
    db_path: Path,
    entries: list[tuple[str, str]],
    limit: int,
) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """Return up to *limit* related (type, accession) pairs per entry.

    Args:
        db_path: Path to the DuckDB database file.
        entries: List of ``(type, id)`` pairs to look up.
        limit: Maximum number of rows to return per entry.

    Returns:
        Dict mapping ``(type, id)`` to sorted list of ``(linked_type, accession)`` tuples.

    Raises:
        FileNotFoundError: If *db_path* does not exist.
    """
    _check_db(db_path)

    if not entries:
        return {}

    with duckdb.connect(str(db_path), read_only=True) as conn:
        conn.execute(
            """
            CREATE TEMPORARY TABLE _input_entries (type TEXT, id TEXT)
            """
        )
        conn.executemany(
            "INSERT INTO _input_entries VALUES (?, ?)",
            entries,
        )

        rows: list[tuple[str, str, str, str]] = conn.execute(
            """
            SELECT e.type, e.id, sub.linked_type, sub.linked_accession
            FROM _input_entries e
            CROSS JOIN LATERAL (
                SELECT linked_type, linked_accession FROM (
                    SELECT r.dst_type AS linked_type, r.dst_accession AS linked_accession
                    FROM relation r
                    WHERE r.src_type = e.type AND r.src_accession = e.id
                    UNION ALL
                    SELECT r.src_type AS linked_type, r.src_accession AS linked_accession
                    FROM relation r
                    WHERE r.dst_type = e.type AND r.dst_accession = e.id
                    ORDER BY 1, 2
                ) LIMIT ?
            ) sub
            ORDER BY e.type, e.id, sub.linked_type, sub.linked_accession
            """,
            (limit,),
        ).fetchall()

    result: dict[tuple[str, str], list[tuple[str, str]]] = {(t, i): [] for t, i in entries}
    for entry_type, entry_id, linked_type, linked_accession in rows:
        result[(entry_type, entry_id)].append((linked_type, linked_accession))

    return result


def count_linked_ids_bulk(
    db_path: Path,
    entries: list[tuple[str, str]],
) -> dict[tuple[str, str], dict[str, int]]:
    """Return per-type counts for multiple accessions in one query.

    Args:
        db_path: Path to the DuckDB database file.
        entries: List of ``(type, id)`` pairs to look up.

    Returns:
        Dict mapping ``(type, id)`` to ``{linked_type: count}``.

    Raises:
        FileNotFoundError: If *db_path* does not exist.
    """
    _check_db(db_path)

    if not entries:
        return {}

    with duckdb.connect(str(db_path), read_only=True) as conn:
        # Build a CTE with the input entries
        conn.execute(
            """
            CREATE TEMPORARY TABLE _input_entries (type TEXT, id TEXT)
            """
        )
        conn.executemany(
            "INSERT INTO _input_entries VALUES (?, ?)",
            entries,
        )

        rows: list[tuple[str, str, str, int]] = conn.execute(
            """
            SELECT e.type, e.id, linked_type, COUNT(*) AS cnt FROM (
                SELECT r.src_type AS entry_type, r.src_accession AS entry_id,
                       r.dst_type AS linked_type
                FROM relation r
                INNER JOIN _input_entries e ON r.src_type = e.type AND r.src_accession = e.id
                UNION ALL
                SELECT r.dst_type AS entry_type, r.dst_accession AS entry_id,
                       r.src_type AS linked_type
                FROM relation r
                INNER JOIN _input_entries e ON r.dst_type = e.type AND r.dst_accession = e.id
            ) sub
            INNER JOIN _input_entries e ON sub.entry_type = e.type AND sub.entry_id = e.id
            GROUP BY e.type, e.id, linked_type
            ORDER BY e.type, e.id, linked_type
            """,
        ).fetchall()

    result: dict[tuple[str, str], dict[str, int]] = {(t, i): {} for t, i in entries}
    for entry_type, entry_id, linked_type, cnt in rows:
        result[(entry_type, entry_id)][linked_type] = cnt

    return result
