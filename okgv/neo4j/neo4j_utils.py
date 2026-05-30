"""
Neo4j utilities: query helpers.

Functions:
  get_by_id — fetch stored properties for a given entry UUID
"""

from dataclasses import dataclass


@dataclass
class Neo4jEntry:
    id: str
    topic: str
    question: str
    answer: str
    options: list[str]


def get_by_id(session, entry_id: str) -> Neo4jEntry | None:
    """
    Fetch stored entry by UUID. Returns None if not found.

    Args:
        session:   active Neo4j session
        entry_id:  UUID string (same as Weaviate entry id)

    Returns:
        Neo4jEntry with topic, question, answer, options populated, or None.
    """
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
    return Neo4jEntry(
        id=row["id"],
        topic=row["topic"],
        question=row["question"],
        answer=row["answer"],
        options=row["options"],
    )
