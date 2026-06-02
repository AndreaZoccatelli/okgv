"""Shared SQLite connection factory with sqlite-vec."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import sqlite_vec

_META_SCHEMA = """
CREATE TABLE IF NOT EXISTS vec_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def create_conn(db_path: str | Path) -> sqlite3.Connection:
    """Create a shared connection with sqlite-vec loaded."""
    conn = sqlite3.connect(str(db_path))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(_META_SCHEMA)
    return conn


def get_stored_dim(conn: sqlite3.Connection) -> int | None:
    """Read embed_dim from vec_meta, or None if not stored yet."""
    try:
        row = conn.execute(
            "SELECT value FROM vec_meta WHERE key = 'embed_dim'"
        ).fetchone()
        return int(row[0]) if row else None
    except sqlite3.OperationalError:
        return None


def _store_dim(conn: sqlite3.Connection, dim: int) -> None:
    """Store embed_dim in vec_meta."""
    conn.execute(
        "INSERT OR REPLACE INTO vec_meta (key, value) VALUES ('embed_dim', ?)",
        (str(dim),),
    )
    conn.commit()
