"""Shared SQLite connection factory for graph + vector DBs."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import sqlite_vec

from okgv.graph.sqlite_client import SQLiteGraphDB
from okgv.vector.sqlite_client import SQLiteVectorDB


def create_db(
    db_path: str | Path, embed_dim: int,
) -> tuple[sqlite3.Connection, SQLiteGraphDB, SQLiteVectorDB]:
    """Create a shared connection and both DB layers.

    Returns (connection, graph_db, vector_db). Caller owns the connection.
    """
    conn = sqlite3.connect(str(db_path))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    graph_db = SQLiteGraphDB(conn)
    vector_db = SQLiteVectorDB(conn, embed_dim=embed_dim)

    return conn, graph_db, vector_db
