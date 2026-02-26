"""Read-only DuckDB client for dblink relation lookups."""

from __future__ import annotations

from pathlib import Path

import duckdb


def get_linked_ids(
    db_path: Path,
    type_: str,
    id_: str,
    target: list[str] | None = None,
) -> list[tuple[str, str]]:
    """Return related (type, accession) pairs for the given accession.

    Performs a bidirectional search on the ``relation`` table, returning
    all entries linked to ``(type_, id_)`` regardless of edge direction.

    Args:
        db_path: Path to the DuckDB database file.
        type_: Source accession type.
        id_: Source accession identifier.
        target: Optional list of target accession types to filter by.

    Returns:
        Sorted list of ``(type, accession)`` tuples.

    Raises:
        FileNotFoundError: If *db_path* does not exist.
    """
    if not db_path.exists():
        msg = f"DuckDB file not found: {db_path}"
        raise FileNotFoundError(msg)

    with duckdb.connect(str(db_path), read_only=True) as conn:
        if target:
            rows: list[tuple[str, str]] = conn.execute(
                """
                SELECT dst_type, dst_accession FROM relation
                WHERE src_type = ? AND src_accession = ? AND dst_type IN (SELECT UNNEST(?))
                UNION ALL
                SELECT src_type, src_accession FROM relation
                WHERE dst_type = ? AND dst_accession = ? AND src_type IN (SELECT UNNEST(?))
                ORDER BY 1, 2
                """,
                (type_, id_, list(target), type_, id_, list(target)),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT dst_type, dst_accession FROM relation
                WHERE src_type = ? AND src_accession = ?
                UNION ALL
                SELECT src_type, src_accession FROM relation
                WHERE dst_type = ? AND dst_accession = ?
                ORDER BY 1, 2
                """,
                (type_, id_, type_, id_),
            ).fetchall()

    return rows
