"""
data_io.py

Source-agnostic readers and writers, so the enrichment pipeline doesn't care
whether the user's movie list lives in a CSV or a SQLite table.

Also contains suggest_output() — a small heuristic that recommends a
sensible default output destination based on what the user provided as
input, without forcing it on them.
"""

import csv
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def read_csv_source(path: str) -> List[Dict[str, Any]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_sqlite_source(db_path: str, table: str) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(f"SELECT * FROM {table}")
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def list_sqlite_tables(db_path: str) -> List[str]:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def get_csv_columns(path: str) -> List[str]:
    with open(path, newline="", encoding="utf-8") as f:
        return next(csv.reader(f))


def get_sqlite_columns(db_path: str, table: str) -> List[str]:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(f"PRAGMA table_info({table})")
        return [row[1] for row in cur.fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

ENRICHMENT_COLUMNS = [
    "source_id", "source_title", "match_status", "match_confidence", "match_reason",
    "tmdb_id", "tmdb_title", "release_date", "runtime_minutes", "genres",
    "budget", "revenue", "vote_average", "popularity", "original_language",
]


def write_csv_output(path: str, rows: List[Dict[str, Any]]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=ENRICHMENT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in ENRICHMENT_COLUMNS})


def write_sqlite_output(db_path: str, table: str, rows: List[Dict[str, Any]]) -> None:
    """
    Always creates a NEW table — never mutates an existing one. If the
    requested table name already exists, raises rather than silently
    overwriting or appending, so a user can't accidentally clobber data.
    """
    conn = sqlite3.connect(db_path)
    try:
        existing = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if existing:
            raise ValueError(
                f"Table '{table}' already exists in {db_path}. "
                "Choose a different output table name to avoid overwriting data."
            )

        col_defs = ", ".join(f'"{c}" TEXT' for c in ENRICHMENT_COLUMNS)
        conn.execute(f'CREATE TABLE "{table}" ({col_defs})')

        placeholders = ", ".join(["?"] * len(ENRICHMENT_COLUMNS))
        col_names = ", ".join(f'"{c}"' for c in ENRICHMENT_COLUMNS)
        conn.executemany(
            f'INSERT INTO "{table}" ({col_names}) VALUES ({placeholders})',
            [tuple(str(row.get(c, "")) for c in ENRICHMENT_COLUMNS) for row in rows],
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Output suggestion heuristic
# ---------------------------------------------------------------------------

def suggest_output(source_type: str, source_path: str) -> Tuple[str, str]:
    """
    Returns (suggested_type, suggested_path_or_table).

    Heuristic: mirror whatever the user gave us as input, since that's
    almost always what they're set up to keep working with. They can
    always override it.
    """
    if source_type == "csv":
        stem = Path(source_path).stem
        return "csv", str(Path(source_path).parent / f"{stem}_enriched.csv")

    if source_type == "sqlite":
        return "sqlite", "tmdb_enrichment"

    return "csv", "enriched_output.csv"
