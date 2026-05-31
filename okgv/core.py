"""Core logic: upsert, logging, schema validation."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from okgv.helpers import err, EXIT_USAGE
from okgv.protocols import GraphDB, VectorDB, entry_id

_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    topic TEXT NOT NULL,
    entry_id TEXT NOT NULL
);
"""


def _log_connect(log_db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(log_db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_LOG_SCHEMA)
    return conn


def validate_schema(schema, meta: dict, graph_props: dict, vector_props: dict) -> None:
    """Check for key collisions and missing vector property definitions."""
    graph_overlap = set(meta) & set(graph_props)
    if graph_overlap:
        err(
            "schema_key_collision",
            detail=f"metadata() and graph_properties() share keys: {graph_overlap}",
            suggestion="Remove duplicates from one of the methods",
            exit_code=EXIT_USAGE,
        )
    vector_overlap = set(meta) & set(vector_props)
    if vector_overlap:
        err(
            "schema_key_collision",
            detail=f"metadata() and vector_properties() share keys: {vector_overlap}",
            suggestion="Remove duplicates from one of the methods",
            exit_code=EXIT_USAGE,
        )

    defined_names = {pd.name for pd in schema.vector_property_definitions()}
    actual_keys = set(meta) | set(vector_props)
    missing = actual_keys - defined_names
    if missing:
        err(
            "schema_missing_definitions",
            detail=f"vector_property_definitions() missing keys: {missing}",
            suggestion="Add PropertyDefinition entries for these keys",
            exit_code=EXIT_USAGE,
        )
    extra = defined_names - actual_keys
    if extra:
        err(
            "schema_extra_definitions",
            detail=f"vector_property_definitions() defines unused keys: {extra}",
            suggestion="Remove these PropertyDefinition entries or add them to metadata()/vector_properties()",
            exit_code=EXIT_USAGE,
        )


def build_entry(schema, raw: dict):
    """Build entry object from raw dict using schema's entry_class."""
    try:
        return schema.entry_class(raw)
    except KeyError as e:
        err(
            "missing_field",
            detail=f"Entry JSON missing required key: {e}",
            exit_code=EXIT_USAGE,
        )


def upsert_entry(
    schema,
    graph_db: GraphDB,
    vector_db: VectorDB,
    topic: str,
    raw: dict,
    embedder: Callable[[list[str]], list[list[float]]],
    overwrite: bool = False,
    vector: list[float] | None = None,
) -> str:
    """Upsert entry into both DBs.

    If vector is provided, uses it directly instead of calling embedder.
    This allows batch callers to pre-compute all embeddings in one call.
    """
    eid = entry_id(raw)
    entry = build_entry(schema, raw)
    meta = schema.metadata(entry)
    graph_props = schema.graph_properties(entry)
    vector_props = schema.vector_properties(entry)

    validate_schema(schema, meta, graph_props, vector_props)

    graph_db.upload_entry(
        topic=topic,
        entry_id=eid,
        properties={**meta, **graph_props},
        overwrite=overwrite,
    )

    if vector is None:
        vector = embedder([schema.embedding_text(entry)])[0]
    try:
        vector_db.upload_entry(
            entry_id=eid,
            properties={**meta, **vector_props},
            vector=vector,
            overwrite=overwrite,
        )
    except Exception:
        graph_db.delete_entries([eid])
        raise

    return eid


def log_session(log_db: Path, topic: str, inserted_ids: list[str]) -> None:
    """Log submitted entry IDs to SQLite."""
    timestamp = datetime.now(timezone.utc).isoformat()
    conn = _log_connect(log_db)
    try:
        conn.executemany(
            "INSERT INTO log (timestamp, topic, entry_id) VALUES (?, ?, ?)",
            [(timestamp, topic, eid) for eid in inserted_ids],
        )
        conn.commit()
    finally:
        conn.close()


def log_get_entries_after(log_db: Path, cutoff: datetime) -> list[str]:
    """Return entry IDs logged after cutoff timestamp."""
    conn = _log_connect(log_db)
    try:
        rows = conn.execute(
            "SELECT entry_id FROM log WHERE timestamp > ? ORDER BY id",
            (cutoff.isoformat(),),
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def log_remove_entries(log_db: Path, entry_ids: list[str]) -> None:
    """Remove entries from log by ID."""
    conn = _log_connect(log_db)
    try:
        conn.executemany(
            "DELETE FROM log WHERE entry_id = ?",
            [(eid,) for eid in entry_ids],
        )
        conn.commit()
    finally:
        conn.close()
