"""SQLite implementation of the GraphDB protocol."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator

from okgv.protocols import GraphRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    path TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    parent TEXT,
    FOREIGN KEY (parent) REFERENCES topics(path)
);
CREATE INDEX IF NOT EXISTS idx_topics_parent ON topics(parent);

CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    properties TEXT NOT NULL,
    FOREIGN KEY (topic) REFERENCES topics(path)
);
CREATE INDEX IF NOT EXISTS idx_entries_topic ON entries(topic);
"""


class SQLiteGraphDB:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.executescript(_SCHEMA)

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, params)

    def _executemany(self, sql: str, params: list) -> sqlite3.Cursor:
        return self._conn.executemany(sql, params)

    def _commit(self) -> None:
        self._conn.commit()

    def topic_exists(self, path: str) -> bool:
        row = self._execute("SELECT 1 FROM topics WHERE path = ?", (path,)).fetchone()
        return row is not None

    def create_topic(self, name: str) -> None:
        self._execute(
            "INSERT OR IGNORE INTO topics (path, name, parent) VALUES (?, ?, NULL)",
            (name, name),
        )
        self._commit()

    def create_subtopic(self, parent: str, name: str) -> None:
        path = f"{parent}/{name}"
        self._execute(
            "INSERT OR IGNORE INTO topics (path, name, parent) VALUES (?, ?, ?)",
            (path, name, parent),
        )
        self._commit()

    def get_subtopics(self, topic: str) -> list[str]:
        rows = self._execute("SELECT path FROM topics WHERE parent = ?", (topic,)).fetchall()
        return [r[0] for r in rows]

    def get_topic_entry_counts(self, parent: str | None = None) -> dict[str, int]:
        if parent is None:
            children = self._execute("SELECT path FROM topics WHERE parent IS NULL").fetchall()
        else:
            children = self._execute("SELECT path FROM topics WHERE parent = ?", (parent,)).fetchall()

        counts = {}
        for (child_path,) in children:
            row = self._execute(
                "SELECT count(*) FROM entries WHERE topic = ? OR topic LIKE ?",
                (child_path, child_path + "/%"),
            ).fetchone()
            counts[child_path] = row[0]
        return counts

    def get_entry_ids_for_topic(self, topic: str) -> list[str]:
        rows = self._execute(
            "SELECT DISTINCT id FROM entries WHERE topic = ? OR topic LIKE ?",
            (topic, topic + "/%"),
        ).fetchall()
        return [r[0] for r in rows]

    def get_entries_for_topic(self, topic: str) -> list[GraphRecord]:
        rows = self._execute(
            "SELECT id, topic, properties FROM entries WHERE topic = ? OR topic LIKE ?",
            (topic, topic + "/%"),
        ).fetchall()
        records = []
        for eid, t, props_json in rows:
            props = json.loads(props_json)
            records.append(GraphRecord(id=eid, topic=t, properties=props))
        return records

    def get_topic_stats(
        self,
        topic: str,
        fields: list[str] | None = None,
    ) -> tuple[int, list[str], list[dict]]:
        _FIELD_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

        # Count total
        row = self._execute(
            "SELECT count(*) FROM entries WHERE topic = ? OR topic LIKE ?",
            (topic, topic + "/%"),
        ).fetchone()
        total = row[0]

        if total == 0:
            return total, fields or [], []

        # Discover fields if not provided
        if fields is None:
            rows = self._execute(
                "SELECT properties FROM entries WHERE topic = ? OR topic LIKE ?",
                (topic, topic + "/%"),
            ).fetchall()
            all_keys: set[str] = set()
            for (props_json,) in rows:
                props = json.loads(props_json)
                all_keys.update(props.keys())
            fields = sorted(all_keys)

        for f in fields:
            if not _FIELD_RE.match(f):
                raise ValueError(f"Invalid field name: {f!r}")

        if not fields:
            return total, fields, []

        # Build GROUP BY using json_extract
        extracts = ", ".join(f"json_extract(properties, '$.{f}')" for f in fields)
        query = (
            f"SELECT {extracts}, count(*) AS cnt "
            f"FROM entries WHERE topic = ? OR topic LIKE ? "
            f"GROUP BY {extracts} ORDER BY cnt DESC"
        )
        rows = self._execute(query, (topic, topic + "/%")).fetchall()

        groups = []
        for row in rows:
            group_fields = {f: row[i] for i, f in enumerate(fields)}
            groups.append({"fields": group_fields, "count": row[-1]})
        return total, fields, groups

    def get_topic_tree(self, root: str | None = None, max_depth: int | None = None) -> dict:
        if root is not None:
            rows = self._execute(
                "SELECT path, name, parent FROM topics WHERE path = ? OR path LIKE ? ORDER BY path",
                (root, root + "/%"),
            ).fetchall()
        else:
            rows = self._execute("SELECT path, name, parent FROM topics ORDER BY path").fetchall()

        if max_depth is not None:
            if root is not None:
                base_depth = root.count("/")
                rows = [(p, n, par) for p, n, par in rows if p.count("/") - base_depth <= max_depth]
            else:
                rows = [(p, n, par) for p, n, par in rows if p.count("/") < max_depth]

        # Build nested tree
        tree: dict = {}
        nodes: dict[str, dict] = {}
        for path, name, parent in rows:
            nodes[path] = {}
        for path, name, parent in rows:
            if parent is None or parent not in nodes:
                tree[name] = nodes[path]
            else:
                nodes[parent][name] = nodes[path]
        return tree

    def get_topic_depth(self, root: str | None = None) -> int:
        if root is not None:
            rows = self._execute(
                "SELECT path FROM topics WHERE path = ? OR path LIKE ?",
                (root, root + "/%"),
            ).fetchall()
            if not rows:
                return 0
            base_depth = root.count("/")
            return max(p.count("/") - base_depth for (p,) in rows)
        else:
            rows = self._execute("SELECT path FROM topics").fetchall()
            if not rows:
                return 0
            return max(p.count("/") for (p,) in rows)

    def upload_entry(self, topic: str, entry_id: str, properties: dict, overwrite: bool = False) -> None:
        if not overwrite:
            existing = self._execute("SELECT 1 FROM entries WHERE id = ?", (entry_id,)).fetchone()
            if existing is not None:
                raise ValueError(f"Entry '{entry_id}' already exists in graph DB. Pass overwrite=True to replace.")
        props_json = json.dumps(properties, sort_keys=True)
        self._execute(
            "INSERT OR REPLACE INTO entries (id, topic, properties) VALUES (?, ?, ?)",
            (entry_id, topic, props_json),
        )
        self._commit()

    def get_by_id(self, entry_id: str) -> GraphRecord | None:
        row = self._execute(
            "SELECT id, topic, properties FROM entries WHERE id = ?",
            (entry_id,),
        ).fetchone()
        if row is None:
            return None
        props = json.loads(row[2])
        return GraphRecord(id=row[0], topic=row[1], properties=props)

    def get_topics_for_ids(self, ids: list[str]) -> dict[str, str]:
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = self._execute(f"SELECT id, topic FROM entries WHERE id IN ({placeholders})", tuple(ids)).fetchall()
        return {r[0]: r[1] for r in rows}

    def get_all_entry_ids(self) -> list[str]:
        rows = self._execute("SELECT id FROM entries").fetchall()
        return [r[0] for r in rows]

    def iter_entry_ids(self, batch_size: int = 1000) -> Iterator[list[str]]:
        offset = 0
        while True:
            rows = self._execute(
                "SELECT id FROM entries ORDER BY id LIMIT ? OFFSET ?",
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
        rows = self._execute(f"SELECT id FROM entries WHERE id IN ({placeholders})", tuple(ids)).fetchall()
        return {r[0] for r in rows}

    def delete_entries(self, ids: list[str]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        self._execute(f"DELETE FROM entries WHERE id IN ({placeholders})", tuple(ids))
        self._commit()

    def count_topics(self) -> int:
        row = self._execute("SELECT count(*) FROM topics").fetchone()
        return row[0]

    def delete_all(self) -> None:
        self._execute("DELETE FROM entries")
        self._execute("DELETE FROM topics")
        self._commit()

    def move_topic(self, source: str, destination: str) -> None:
        name = source.rsplit("/", 1)[-1]
        new_path = f"{destination}/{name}"

        # Check conflict
        conflict = self._execute(
            "SELECT 1 FROM topics WHERE parent = ? AND name = ?",
            (destination, name),
        ).fetchone()
        if conflict:
            raise ValueError(f"Destination '{destination}' already has subtopic '{name}'")

        # Temporarily disable FK checks for bulk path update
        self._execute("PRAGMA foreign_keys=OFF")

        # Fetch all affected topics (source + descendants)
        affected = self._execute(
            "SELECT path FROM topics WHERE path = ? OR path LIKE ? ORDER BY path",
            (source, source + "/%"),
        ).fetchall()

        for (old_path,) in affected:
            # Replace source prefix with new_path, keeping the suffix.
            # e.g. source="a/b", new_path="c/b", old_path="a/b/x" -> "c/b/x"
            updated_path = new_path + old_path[len(source) :]
            if old_path == source:
                # Root of the moved subtree: parent is the destination topic
                new_parent = destination
            else:
                # Descendant: rewrite its parent path with the same prefix swap
                old_parent = old_path.rsplit("/", 1)[0]
                new_parent = new_path + old_parent[len(source) :]
            self._execute(
                "UPDATE topics SET path = ?, parent = ? WHERE path = ?",
                (updated_path, new_parent, old_path),
            )

        # Update entry topics
        entries_affected = self._execute(
            "SELECT id, topic FROM entries WHERE topic = ? OR topic LIKE ?",
            (source, source + "/%"),
        ).fetchall()
        for eid, old_topic in entries_affected:
            # Same prefix swap for entry topic paths
            updated_topic = new_path + old_topic[len(source) :]
            self._execute(
                "UPDATE entries SET topic = ? WHERE id = ?",
                (updated_topic, eid),
            )

        self._execute("PRAGMA foreign_keys=ON")
        self._commit()

    def move_entry(self, entry_id: str, new_topic: str) -> None:
        self._execute(
            "UPDATE entries SET topic = ? WHERE id = ?",
            (new_topic, entry_id),
        )
        self._commit()

    def close(self) -> None:
        self._conn.close()
