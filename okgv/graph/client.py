"""Neo4j implementation of the GraphDB protocol."""

from __future__ import annotations

from neo4j import GraphDatabase

from okgv.protocols import GraphRecord


class Neo4jGraphDB:
    def __init__(
        self, uri: str, user: str, password: str, database: str = "neo4j"
    ) -> None:
        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._database = database
        self._driver.verify_connectivity()

    def _session(self):
        return self._driver.session(database=self._database)

    def get_topic_entry_counts(self) -> dict[str, int]:
        with self._session() as session:
            result = session.run(
                "MATCH (t:Topic) "
                "RETURN t.name AS topic, coalesce(t.entry_count, 0) AS count"
            )
            return {r["topic"]: r["count"] for r in result}

    def get_entry_ids_for_topic(self, topic: str) -> list[str]:
        with self._session() as session:
            result = session.run(
                "MATCH (t:Topic {name: $topic})-[:HAS_ENTRY]->(e:Entry) "
                "RETURN e.id AS id",
                topic=topic,
            )
            return [r["id"] for r in result]

    def upload_entry(
        self, topic: str, entry_id: str, properties: dict
    ) -> None:
        with self._session() as session:
            session.run(
                "MERGE (t:Topic {name: $topic}) ON CREATE SET t.entry_count = 0",
                topic=topic,
            )
            session.run(
                """
                MATCH (t:Topic {name: $topic})
                MERGE (e:Entry {id: $id})
                  ON CREATE SET e += $props
                  ON MATCH SET e += $props
                WITH t, e
                MERGE (t)-[r:HAS_ENTRY]->(e)
                  ON CREATE SET t.entry_count = coalesce(t.entry_count, 0) + 1
                """,
                topic=topic,
                id=entry_id,
                props=properties,
            )

    def get_by_id(self, entry_id: str) -> GraphRecord | None:
        with self._session() as session:
            result = session.run(
                """
                MATCH (t:Topic)-[:HAS_ENTRY]->(e:Entry {id: $id})
                RETURN e AS node, t.name AS topic
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

    def delete_entries(self, ids: list[str]) -> None:
        with self._session() as session:
            session.run(
                """
                UNWIND $ids AS id
                MATCH (t:Topic)-[:HAS_ENTRY]->(e:Entry {id: id})
                SET t.entry_count = coalesce(t.entry_count, 0) - 1
                DETACH DELETE e
                """,
                ids=ids,
            )

    def ensure_indexes(self) -> None:
        with self._session() as session:
            session.run(
                "CREATE INDEX topic_name IF NOT EXISTS FOR (t:Topic) ON (t.name)"
            )
            session.run(
                "CREATE INDEX entry_id IF NOT EXISTS FOR (e:Entry) ON (e.id)"
            )

    def close(self) -> None:
        self._driver.close()
