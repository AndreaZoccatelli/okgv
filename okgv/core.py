"""Core logic: upsert, logging, schema validation, review."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from okgv.helpers import err, EXIT_USAGE
from okgv.protocols import EntrySchema, GraphDB, VectorDB, entry_id

_SCHEMA = """
CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    topic TEXT NOT NULL,
    entry_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review (
    entry_id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    reviewed_at TEXT
);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def validate_schema(schema: EntrySchema, meta: dict, graph_props: dict, vector_props: dict) -> None:
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


class EntryError(Exception):
    """Raised when a single entry fails to build or upsert."""
    pass


def build_entry(schema: EntrySchema, raw: dict):
    """Build entry object from raw dict using schema's entry_class.

    Raises EntryError on missing fields (catchable in batch operations).
    """
    try:
        return schema.entry_class(raw)
    except KeyError as e:
        raise EntryError(f"Entry JSON missing required key: {e}") from e


def upsert_entry(
    schema: EntrySchema,
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
    vector_db.upload_entry(
        entry_id=eid,
        properties={**meta, **vector_props},
        vector=vector,
        topic=topic,
        overwrite=overwrite,
    )

    return eid


def upsert_entries_batch(
    schema: EntrySchema,
    graph_db: GraphDB,
    vector_db: VectorDB,
    topic: str,
    raws: list[dict],
    entries: list | None = None,
    vectors: list[list[float]] = None,
    overwrite: bool = False,
) -> tuple[list[str], list[dict]]:
    """Batch upsert entries into both DBs.

    Returns (inserted_ids, failures) where failures are dicts with id and error.
    Graph uploads are individual (transactional). Vector upload is batched.

    If entries (pre-built entry objects) are provided, skips build_entry.
    Schema is validated once using the first entry.
    """
    if entries is None:
        entries = [build_entry(schema, raw) for raw in raws]

    # Validate schema once using first entry
    first = entries[0]
    validate_schema(
        schema, schema.metadata(first),
        schema.graph_properties(first), schema.vector_properties(first),
    )

    # Upload to graph individually, collect successes
    graph_ok = []
    failures = []
    for raw, entry, vec in zip(raws, entries, vectors):
        eid = entry_id(raw)
        meta = schema.metadata(entry)
        graph_props = schema.graph_properties(entry)
        vector_props = schema.vector_properties(entry)
        try:
            graph_db.upload_entry(
                topic=topic, entry_id=eid, properties={**meta, **graph_props}, overwrite=overwrite,
            )
        except (EntryError, ValueError) as e:
            failures.append({"id": eid, "error": str(e)})
            continue
        graph_ok.append((eid, {**meta, **vector_props}, vec))

    if not graph_ok:
        return [], failures

    # Batch insert to vector DB
    eids = [eid for eid, _, _ in graph_ok]
    props_list = [props for _, props, _ in graph_ok]
    vecs = [vec for _, _, vec in graph_ok]
    failed_ids = vector_db.upload_entries_batch(props_list, vecs, eids, topic)

    if failed_ids:
        failed_set = set(failed_ids)
        for fid in failed_ids:
            failures.append({"id": fid, "error": "Vector batch insert failed"})
        inserted = [eid for eid in eids if eid not in failed_set]
    else:
        inserted = eids

    return inserted, failures


def log_session(db_path: Path, topic: str, inserted_ids: list[str]) -> None:
    """Log submitted entry IDs to SQLite."""
    timestamp = datetime.now(timezone.utc).isoformat()
    conn = _connect(db_path)
    try:
        conn.executemany(
            "INSERT INTO log (timestamp, topic, entry_id) VALUES (?, ?, ?)",
            [(timestamp, topic, eid) for eid in inserted_ids],
        )
        conn.commit()
    finally:
        conn.close()


def log_get_entries_after(db_path: Path, cutoff: datetime) -> list[str]:
    """Return entry IDs logged after cutoff timestamp."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT entry_id FROM log WHERE timestamp > ? ORDER BY id",
            (cutoff.isoformat(),),
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def log_query(
    db_path: Path,
    topic: str | None = None,
    after: datetime | None = None,
    before: datetime | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """Query log entries with optional filters."""
    conn = _connect(db_path)
    try:
        clauses = []
        params = []
        if topic:
            clauses.append("topic = ?")
            params.append(topic)
        if after:
            clauses.append("timestamp > ?")
            params.append(after.isoformat())
        if before:
            clauses.append("timestamp < ?")
            params.append(before.isoformat())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT id, timestamp, topic, entry_id FROM log{where} ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(query, params).fetchall()
        return [
            {"id": r[0], "timestamp": r[1], "topic": r[2], "entry_id": r[3]}
            for r in rows
        ]
    finally:
        conn.close()


def log_count(
    db_path: Path,
    topic: str | None = None,
    group_by_topic: bool = False,
) -> dict:
    """Count log entries, optionally grouped by topic."""
    conn = _connect(db_path)
    try:
        if group_by_topic:
            rows = conn.execute(
                "SELECT topic, count(*) AS count FROM log GROUP BY topic ORDER BY count DESC"
            ).fetchall()
            return {"total": sum(r[1] for r in rows), "by_topic": {r[0]: r[1] for r in rows}}
        clauses = []
        params = []
        if topic:
            clauses.append("topic = ?")
            params.append(topic)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        row = conn.execute(f"SELECT count(*) FROM log{where}", params).fetchone()
        result = {"total": row[0]}
        if topic:
            result["topic"] = topic
        return result
    finally:
        conn.close()


def log_remove_entries(db_path: Path, entry_ids: list[str]) -> None:
    """Remove entries from log by ID."""
    conn = _connect(db_path)
    try:
        conn.executemany(
            "DELETE FROM log WHERE entry_id = ?",
            [(eid,) for eid in entry_ids],
        )
        conn.commit()
    finally:
        conn.close()


# ── Review ────────────────────────────────────────────────────────────

def review_add(db_path: Path, topic: str, entry_ids: list[str]) -> None:
    """Add entries to review queue as pending."""
    timestamp = datetime.now(timezone.utc).isoformat()
    conn = _connect(db_path)
    try:
        conn.executemany(
            "INSERT OR IGNORE INTO review (entry_id, topic, status, created_at) VALUES (?, ?, 'pending', ?)",
            [(eid, topic, timestamp) for eid in entry_ids],
        )
        conn.commit()
    finally:
        conn.close()


def review_list(
    db_path: Path,
    status: str = "pending",
    topic: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """List review entries filtered by status and optionally topic."""
    conn = _connect(db_path)
    try:
        clauses = ["status = ?"]
        params: list = [status]
        if topic:
            clauses.append("topic = ?")
            params.append(topic)
        where = " WHERE " + " AND ".join(clauses)
        query = f"SELECT entry_id, topic, status, created_at, reviewed_at FROM review{where} ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(query, params).fetchall()
        return [
            {"entry_id": r[0], "topic": r[1], "status": r[2], "created_at": r[3], "reviewed_at": r[4]}
            for r in rows
        ]
    finally:
        conn.close()


def review_count(db_path: Path, topic: str | None = None) -> dict:
    """Count review entries by status."""
    conn = _connect(db_path)
    try:
        if topic:
            rows = conn.execute(
                "SELECT status, count(*) FROM review WHERE topic = ? GROUP BY status",
                (topic,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT status, count(*) FROM review GROUP BY status"
            ).fetchall()
        counts = {r[0]: r[1] for r in rows}
        result = {
            "pending": counts.get("pending", 0),
            "approved": counts.get("approved", 0),
            "rejected": counts.get("rejected", 0),
            "total": sum(counts.values()),
        }
        if topic:
            result["topic"] = topic
        return result
    finally:
        conn.close()


def review_update(db_path: Path, entry_ids: list[str], status: str) -> int:
    """Update review status for entries. Returns number of rows updated."""
    timestamp = datetime.now(timezone.utc).isoformat()
    conn = _connect(db_path)
    try:
        cursor = conn.executemany(
            "UPDATE review SET status = ?, reviewed_at = ? WHERE entry_id = ?",
            [(status, timestamp, eid) for eid in entry_ids],
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def review_get_rejected(db_path: Path) -> list[str]:
    """Return entry IDs with rejected status."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT entry_id FROM review WHERE status = 'rejected'"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def review_purge_rejected(db_path: Path) -> list[str]:
    """Remove rejected entries from review DB. Returns deleted IDs."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT entry_id FROM review WHERE status = 'rejected'"
        ).fetchall()
        ids = [r[0] for r in rows]
        if ids:
            conn.executemany(
                "DELETE FROM review WHERE entry_id = ?",
                [(eid,) for eid in ids],
            )
            conn.commit()
        return ids
    finally:
        conn.close()


def review_remove_entries(db_path: Path, entry_ids: list[str]) -> None:
    """Remove entries from review DB by ID (used by undo)."""
    conn = _connect(db_path)
    try:
        conn.executemany(
            "DELETE FROM review WHERE entry_id = ?",
            [(eid,) for eid in entry_ids],
        )
        conn.commit()
    finally:
        conn.close()


def review_clear(db_path: Path) -> None:
    """Delete all entries from review DB (used by purge)."""
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM review")
        conn.commit()
    finally:
        conn.close()
