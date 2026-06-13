"""SQLite + sqlite-vec implementation of the VectorDB protocol."""

from __future__ import annotations

import json
import sqlite3
import struct
from collections.abc import Iterator

from okgv.errors import DuplicateEntryError
from okgv.protocols import VectorRecord


def _vec_f32(v: list[float]) -> bytes:
    """Pack float list into raw bytes for sqlite-vec."""
    return struct.pack(f"{len(v)}f", *v)


def _unpack_f32(blob: bytes) -> list[float]:
    """Unpack raw bytes from sqlite-vec into float list."""
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


class SQLiteVectorDB:
    """VectorDB backed by sqlite-vec (vec0 virtual table) + a relational table for properties."""

    def __init__(self, conn: sqlite3.Connection, embed_dim: int) -> None:
        self._conn = conn
        self._embed_dim = embed_dim
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS vector_entries (
                id TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                properties TEXT NOT NULL
            )"""
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_vector_entries_topic ON vector_entries(topic)")
        # vec0 virtual table with cosine distance and topic metadata column
        self._conn.execute(
            f"""CREATE VIRTUAL TABLE IF NOT EXISTS vec_entries USING vec0(
                id TEXT PRIMARY KEY,
                embedding FLOAT[{self._embed_dim}] distance_metric=cosine,
                topic TEXT
            )"""
        )
        self._conn.commit()

    def get_top_n(
        self,
        vector: list[float],
        n: int,
        filter_topic: str | None = None,
        subtree: bool = False,
    ) -> list[tuple[str, float]]:
        blob = _vec_f32(vector)
        if filter_topic and subtree:
            # Subtree scope: the entry topic equals filter_topic or sits under
            # it. vec0's KNN only allows equality/range on metadata columns
            # (no LIKE/OR), so the prefix match is pushed down as an id IN
            # prefilter against the relational table, which mirrors
            # get_by_topic. k then applies within the subtree, not globally.
            rows = self._conn.execute(
                "SELECT id, distance FROM vec_entries WHERE embedding MATCH ? AND k = ? "
                "AND id IN (SELECT id FROM vector_entries WHERE topic = ? OR topic LIKE ?)",
                (blob, n, filter_topic, filter_topic + "/%"),
            ).fetchall()
        elif filter_topic:
            rows = self._conn.execute(
                "SELECT id, distance FROM vec_entries WHERE embedding MATCH ? AND k = ? AND topic = ?",
                (blob, n, filter_topic),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, distance FROM vec_entries WHERE embedding MATCH ? AND k = ?",
                (blob, n),
            ).fetchall()
        # Convert cosine distance to certainty: 0 distance = 1.0 certainty
        return [(row[0], max(0.0, min(1.0, 1.0 - row[1]))) for row in rows]

    def get_by_id(self, entry_id: str) -> VectorRecord | None:
        row = self._conn.execute(
            "SELECT id, properties FROM vector_entries WHERE id = ?",
            (entry_id,),
        ).fetchone()
        if row is None:
            return None
        return VectorRecord(id=row[0], properties=json.loads(row[1]))

    def get_by_ids(self, entry_ids: list[str]) -> list[VectorRecord]:
        if not entry_ids:
            return []
        placeholders = ",".join("?" for _ in entry_ids)
        rows = self._conn.execute(
            f"SELECT id, properties FROM vector_entries WHERE id IN ({placeholders})",
            tuple(entry_ids),
        ).fetchall()
        return [VectorRecord(id=r[0], properties=json.loads(r[1])) for r in rows]

    def get_by_topic(self, topic: str, limit: int) -> list[VectorRecord]:
        rows = self._conn.execute(
            "SELECT id, properties FROM vector_entries WHERE topic = ? OR topic LIKE ? LIMIT ?",
            (topic, topic + "/%", limit),
        ).fetchall()
        return [VectorRecord(id=r[0], properties=json.loads(r[1])) for r in rows]

    def upload_entry(
        self,
        entry_id: str,
        properties: dict,
        vector: list[float],
        topic: str,
        overwrite: bool = False,
    ) -> None:
        existing = self._conn.execute("SELECT 1 FROM vector_entries WHERE id = ?", (entry_id,)).fetchone()
        if existing and not overwrite:
            raise DuplicateEntryError(f"Entry '{entry_id}' already exists in vector DB")
        props_json = json.dumps(properties, sort_keys=True)
        blob = _vec_f32(vector)
        if existing and overwrite:
            self._conn.execute(
                "UPDATE vector_entries SET topic = ?, properties = ? WHERE id = ?",
                (topic, props_json, entry_id),
            )
            self._conn.execute(
                "UPDATE vec_entries SET embedding = ?, topic = ? WHERE id = ?",
                (blob, topic, entry_id),
            )
        else:
            self._conn.execute(
                "INSERT INTO vector_entries (id, topic, properties) VALUES (?, ?, ?)",
                (entry_id, topic, props_json),
            )
            self._conn.execute(
                "INSERT INTO vec_entries (id, embedding, topic) VALUES (?, ?, ?)",
                (entry_id, blob, topic),
            )
        self._conn.commit()

    def upload_entries_batch(
        self,
        entries: list[dict],
        vectors: list[list[float]],
        entry_ids: list[str],
        topic: str,
    ) -> list[str]:
        """Batch insert entries. Returns list of entry IDs that failed."""
        failed = []
        for eid, props, vec in zip(entry_ids, entries, vectors):
            try:
                props_json = json.dumps(props, sort_keys=True)
                blob = _vec_f32(vec)
                self._conn.execute(
                    "INSERT INTO vector_entries (id, topic, properties) VALUES (?, ?, ?)",
                    (eid, topic, props_json),
                )
                self._conn.execute(
                    "INSERT INTO vec_entries (id, embedding, topic) VALUES (?, ?, ?)",
                    (eid, blob, topic),
                )
            except (sqlite3.Error, ValueError):
                failed.append(eid)
        self._conn.commit()
        return failed

    def update_entry_topic(self, entry_id: str, new_topic: str) -> None:
        self._conn.execute(
            "UPDATE vector_entries SET topic = ? WHERE id = ?",
            (new_topic, entry_id),
        )
        self._conn.execute(
            "UPDATE vec_entries SET topic = ? WHERE id = ?",
            (new_topic, entry_id),
        )
        self._conn.commit()

    def update_topics(self, old_prefix: str, new_prefix: str) -> None:
        """Update topic for all entries where topic == old_prefix or starts with old_prefix/."""
        rows = self._conn.execute(
            "SELECT id, topic FROM vector_entries WHERE topic = ? OR topic LIKE ?",
            (old_prefix, old_prefix + "/%"),
        ).fetchall()
        for eid, old_topic in rows:
            new_topic = new_prefix + old_topic[len(old_prefix) :]
            self._conn.execute(
                "UPDATE vector_entries SET topic = ? WHERE id = ?",
                (new_topic, eid),
            )
            self._conn.execute(
                "UPDATE vec_entries SET topic = ? WHERE id = ?",
                (new_topic, eid),
            )
        self._conn.commit()

    def get_all_entry_ids(self) -> list[str]:
        rows = self._conn.execute("SELECT id FROM vector_entries").fetchall()
        return [r[0] for r in rows]

    def iter_entry_ids(self, batch_size: int = 1000) -> Iterator[list[str]]:
        offset = 0
        while True:
            rows = self._conn.execute(
                "SELECT id FROM vector_entries ORDER BY id LIMIT ? OFFSET ?",
                (batch_size, offset),
            ).fetchall()
            if not rows:
                break
            yield [r[0] for r in rows]
            if len(rows) < batch_size:
                break
            offset += batch_size

    def exists_batch(self, ids: list[str]) -> set[str]:
        if not ids:
            return set()
        placeholders = ",".join("?" for _ in ids)
        rows = self._conn.execute(
            f"SELECT id FROM vector_entries WHERE id IN ({placeholders})",
            tuple(ids),
        ).fetchall()
        return {r[0] for r in rows}

    def delete_by_id(self, entry_id: str) -> None:
        self._conn.execute("DELETE FROM vector_entries WHERE id = ?", (entry_id,))
        self._conn.execute("DELETE FROM vec_entries WHERE id = ?", (entry_id,))
        self._conn.commit()

    def delete_by_ids(self, entry_ids: list[str]) -> None:
        if not entry_ids:
            return
        placeholders = ",".join("?" for _ in entry_ids)
        self._conn.execute(
            f"DELETE FROM vector_entries WHERE id IN ({placeholders})",
            tuple(entry_ids),
        )
        self._conn.execute(
            f"DELETE FROM vec_entries WHERE id IN ({placeholders})",
            tuple(entry_ids),
        )
        self._conn.commit()

    def close(self) -> None:
        # Connection owned externally
        pass
