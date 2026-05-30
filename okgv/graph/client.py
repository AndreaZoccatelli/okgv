"""Neo4j implementation of the GraphDB protocol."""

from __future__ import annotations

from neo4j import GraphDatabase

from protocols import GraphEntry


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
        self,
        topic: str,
        entry_id: str,
        question: str,
        answer: str,
        options: list[str],
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
                  ON CREATE SET
                    e.question    = $question,
                    e.answer      = $answer,
                    e.options     = $options,
                    e.num_options = $num_options
                  ON MATCH SET
                    e.question    = $question,
                    e.answer      = $answer,
                    e.options     = $options,
                    e.num_options = $num_options
                WITH t, e
                MERGE (t)-[r:HAS_ENTRY]->(e)
                  ON CREATE SET t.entry_count = coalesce(t.entry_count, 0) + 1
                """,
                topic=topic,
                id=entry_id,
                question=question,
                answer=answer,
                options=options,
                num_options=len(options),
            )

    def get_by_id(self, entry_id: str) -> GraphEntry | None:
        with self._session() as session:
            result = session.run(
                """
                MATCH (t:Topic)-[:HAS_ENTRY]->(e:Entry {id: $id})
                RETURN e.id AS id, t.name AS topic, e.question AS question,
                       e.answer AS answer, e.options AS options
                """,
                id=entry_id,
            )
            row = result.single()
            if row is None:
                return None
            return GraphEntry(
                id=row["id"],
                topic=row["topic"],
                question=row["question"],
                answer=row["answer"],
                options=row["options"],
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
