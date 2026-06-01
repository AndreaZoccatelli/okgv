"""Neo4j implementation of the GraphDB protocol.

Topic nodes have two properties:
  - path: unique identifier, e.g. "algebra/linear_algebra" (used for lookups)
  - name: display name, e.g. "linear_algebra" (last segment of path)

Root topics have path == name.
"""

from __future__ import annotations

import time
from typing import Callable, TypeVar

from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, SessionExpired

from okgv.protocols import GraphRecord

_T = TypeVar("_T")
_TRANSIENT = (ServiceUnavailable, SessionExpired, ConnectionError, OSError)
_MAX_RETRIES = 2
_RETRY_DELAY = 1


class Neo4jGraphDB:
    def __init__(
        self, uri: str, user: str, password: str, database: str = "neo4j"
    ) -> None:
        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._database = database
        self._driver.verify_connectivity()
        self.ensure_indexes()

    def _session(self):
        return self._driver.session(database=self._database)

    def _with_retry(self, fn: Callable[[], _T]) -> _T:
        for attempt in range(_MAX_RETRIES + 1):
            try:
                return fn()
            except _TRANSIENT:
                if attempt == _MAX_RETRIES:
                    raise
                time.sleep(_RETRY_DELAY * (attempt + 1))
                self._driver.verify_connectivity()
        raise RuntimeError("unreachable")

    def topic_exists(self, path: str) -> bool:
        def _op():
            with self._session() as session:
                result = session.run(
                    "MATCH (t:Topic {path: $path}) RETURN t LIMIT 1",
                    path=path,
                )
                return result.single() is not None
        return self._with_retry(_op)

    def create_topic(self, name: str) -> None:
        """Idempotent — safe to call if topic already exists (uses MERGE)."""
        def _op():
            with self._session() as session:
                session.run(
                    "MERGE (t:Topic {path: $path}) ON CREATE SET t.name = $name",
                    path=name,
                    name=name,
                )
        self._with_retry(_op)

    def create_subtopic(self, parent: str, name: str) -> None:
        path = f"{parent}/{name}"
        def _op():
            with self._session() as session:
                session.run(
                    """
                    MATCH (p:Topic {path: $parent})
                    MERGE (c:Topic {path: $path})
                      ON CREATE SET c.name = $name
                    MERGE (p)-[:HAS_SUBTOPIC]->(c)
                    """,
                    parent=parent,
                    path=path,
                    name=name,
                )
        self._with_retry(_op)

    def get_subtopics(self, topic: str) -> list[str]:
        def _op():
            with self._session() as session:
                result = session.run(
                    "MATCH (t:Topic {path: $path})-[:HAS_SUBTOPIC]->(c:Topic) "
                    "RETURN c.path AS path",
                    path=topic,
                )
                return [r["path"] for r in result]
        return self._with_retry(_op)

    def get_topic_entry_counts(self, parent: str | None = None) -> dict[str, int]:
        """Return entry counts for direct children of parent (recursive per child).

        If parent is None, returns counts for root topics.
        Each child's count includes entries in all its descendants.
        """
        def _op():
            with self._session() as session:
                if parent is None:
                    result = session.run(
                        """
                        MATCH (t:Topic)
                        WHERE NOT ()-[:HAS_SUBTOPIC]->(t)
                        OPTIONAL MATCH (t)-[:HAS_SUBTOPIC*0..]->(desc:Topic)-[:HAS_ENTRY]->(e:Entry)
                        RETURN t.path AS topic, count(DISTINCT e) AS count
                        """
                    )
                else:
                    result = session.run(
                        """
                        MATCH (p:Topic {path: $parent})-[:HAS_SUBTOPIC]->(t:Topic)
                        OPTIONAL MATCH (t)-[:HAS_SUBTOPIC*0..]->(desc:Topic)-[:HAS_ENTRY]->(e:Entry)
                        RETURN t.path AS topic, count(DISTINCT e) AS count
                        """,
                        parent=parent,
                    )
                return {r["topic"]: r["count"] for r in result}
        return self._with_retry(_op)

    def get_entry_ids_for_topic(self, topic: str) -> list[str]:
        """Return entry IDs recursively (includes entries in all sub-topics)."""
        def _op():
            with self._session() as session:
                result = session.run(
                    """
                    MATCH (t:Topic {path: $path})-[:HAS_SUBTOPIC*0..]->(desc:Topic)-[:HAS_ENTRY]->(e:Entry)
                    RETURN DISTINCT e.id AS id
                    """,
                    path=topic,
                )
                return [r["id"] for r in result]
        return self._with_retry(_op)

    def get_entries_for_topic(self, topic: str) -> list:
        """Return all entries (with properties) recursively for a topic."""
        from okgv.protocols import GraphRecord

        def _op():
            with self._session() as session:
                result = session.run(
                    """
                    MATCH (t:Topic {path: $path})-[:HAS_SUBTOPIC*0..]->(desc:Topic)-[:HAS_ENTRY]->(e:Entry)
                    RETURN DISTINCT e AS node, desc.path AS topic
                    """,
                    path=topic,
                )
                records = []
                for row in result:
                    node = row["node"]
                    props = dict(node)
                    eid = props.pop("id", None)
                    records.append(GraphRecord(id=eid, topic=row["topic"], properties=props))
                return records
        return self._with_retry(_op)

    def upload_entry(
        self, topic: str, entry_id: str, properties: dict, overwrite: bool = False
    ) -> None:
        def _work(tx):
            if not overwrite:
                exists = tx.run(
                    "MATCH (e:Entry {id: $id}) RETURN e LIMIT 1",
                    id=entry_id,
                ).single()
                if exists is not None:
                    raise ValueError(
                        f"Entry '{entry_id}' already exists in graph DB. "
                        f"Pass overwrite=True to replace."
                    )
            tx.run(
                """
                MERGE (t:Topic {path: $path})
                MERGE (e:Entry {id: $id})
                  ON CREATE SET e += $props
                  ON MATCH SET e += $props
                MERGE (t)-[:HAS_ENTRY]->(e)
                """,
                path=topic,
                id=entry_id,
                props=properties,
            )

        with self._session() as session:
            session.execute_write(_work)

    def get_by_id(self, entry_id: str) -> GraphRecord | None:
        def _op():
            with self._session() as session:
                result = session.run(
                    """
                    MATCH (t:Topic)-[:HAS_ENTRY]->(e:Entry {id: $id})
                    RETURN e AS node, t.path AS topic
                    """,
                    id=entry_id,
                )
                row = result.single()
                if row is None:
                    return None
                node = row["node"]
                props = dict(node)
                props.pop("id", None)
                return GraphRecord(
                    id=entry_id,
                    topic=row["topic"],
                    properties=props,
                )
        return self._with_retry(_op)

    def get_all_entry_ids(self) -> list[str]:
        def _op():
            with self._session() as session:
                result = session.run("MATCH (e:Entry) RETURN e.id AS id")
                return [r["id"] for r in result]
        return self._with_retry(_op)

    def iter_entry_ids(self, batch_size: int = 1000):
        """Yield entry IDs in batches using SKIP/LIMIT pagination."""
        offset = 0
        while True:
            def _op(skip=offset):
                with self._session() as session:
                    result = session.run(
                        "MATCH (e:Entry) RETURN e.id AS id ORDER BY e.id SKIP $skip LIMIT $limit",
                        skip=skip,
                        limit=batch_size,
                    )
                    return [r["id"] for r in result]
            batch = self._with_retry(_op)
            if not batch:
                break
            yield batch
            if len(batch) < batch_size:
                break
            offset += batch_size

    def exists_batch(self, ids: list[str]) -> set[str]:
        """Return subset of ids that exist in the graph DB."""
        def _op():
            with self._session() as session:
                result = session.run(
                    "UNWIND $ids AS id MATCH (e:Entry {id: id}) RETURN e.id AS id",
                    ids=ids,
                )
                return {r["id"] for r in result}
        return self._with_retry(_op)

    def delete_entries(self, ids: list[str]) -> None:
        def _op():
            with self._session() as session:
                session.run(
                    """
                    UNWIND $ids AS id
                    MATCH (e:Entry {id: id})
                    DETACH DELETE e
                    """,
                    ids=ids,
                )
        self._with_retry(_op)

    def move_topic(self, source: str, destination: str) -> None:
        """Move topic at `source` path under `destination` topic."""
        name = source.rsplit("/", 1)[-1]
        new_path = f"{destination}/{name}"

        def _work(tx):
            # Check destination doesn't already have child with same name
            conflict = tx.run(
                "MATCH (d:Topic {path: $dest})-[:HAS_SUBTOPIC]->(c:Topic) "
                "WHERE c.name = $name RETURN c.path AS path",
                dest=destination,
                name=name,
            ).single()
            if conflict:
                raise ValueError(
                    f"Destination '{destination}' already has subtopic '{name}'"
                )

            # Detach source from old parent
            tx.run(
                "MATCH (p:Topic)-[r:HAS_SUBTOPIC]->(s:Topic {path: $source}) DELETE r",
                source=source,
            )

            # Attach to new parent
            tx.run(
                """
                MATCH (d:Topic {path: $dest})
                MATCH (s:Topic {path: $source})
                MERGE (d)-[:HAS_SUBTOPIC]->(s)
                """,
                dest=destination,
                source=source,
            )

            # Update paths: source and all descendants
            tx.run(
                """
                MATCH (s:Topic {path: $source})-[:HAS_SUBTOPIC*0..]->(desc:Topic)
                WITH desc, desc.path AS old_path
                SET desc.path = $new_path + substring(old_path, size($source))
                """,
                source=source,
                new_path=new_path,
            )

        with self._session() as session:
            session.execute_write(_work)

    def move_entry(self, entry_id: str, new_topic: str) -> None:
        """Move entry to a different topic."""
        def _work(tx):
            tx.run(
                """
                MATCH (t:Topic)-[r:HAS_ENTRY]->(e:Entry {id: $id})
                DELETE r
                """,
                id=entry_id,
            )
            tx.run(
                """
                MATCH (d:Topic {path: $dest})
                MATCH (e:Entry {id: $id})
                MERGE (d)-[:HAS_ENTRY]->(e)
                """,
                dest=new_topic,
                id=entry_id,
            )

        with self._session() as session:
            session.execute_write(_work)

    def ensure_indexes(self) -> None:
        with self._session() as session:
            session.run(
                "CREATE INDEX topic_path IF NOT EXISTS FOR (t:Topic) ON (t.path)"
            )
            session.run(
                "CREATE INDEX entry_id IF NOT EXISTS FOR (e:Entry) ON (e.id)"
            )

    def close(self) -> None:
        self._driver.close()
