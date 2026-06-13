"""Entry schema: retrieval-evaluation pairs (query, relevant passage).

Used to evaluate a retriever. The document taxonomy is the topic tree; sibling
leaves can legitimately overlap, so structure.json sets `similarity_scope:
subtree` on the shared parent to dedup across the subtree. Dedup is on the
query (it is what gets embedded).

Set OKGV_SCHEMA=config.schema:RetrievalSchema in .env.
"""

from okgv.protocols import PropertyDefinition
from okgv.validators import NotEmpty

query = NotEmpty("query")
passage = NotEmpty("passage")


class RetrievalEntry:
    def __init__(self, raw: dict):
        self.query = query.validate(raw["query"])
        self.passage = passage.validate(raw["passage"])


class RetrievalSchema:
    entry_class = RetrievalEntry
    validators = [query, passage]
    # Coverage is by topic only; there is no orthogonal field to balance.
    field_descriptions = {
        "query": "a natural-language search query a user might issue",
        "passage": "the passage that correctly answers the query",
    }

    @staticmethod
    def metadata(entry: RetrievalEntry) -> dict:
        return {}

    @staticmethod
    def graph_properties(entry: RetrievalEntry) -> dict:
        return {"query": entry.query, "passage": entry.passage}

    @staticmethod
    def vector_properties(entry: RetrievalEntry) -> dict:
        # Keep both so `similar` returns the matched pair with full content.
        return {"query": entry.query, "passage": entry.passage}

    @staticmethod
    def embedding_text(entry: RetrievalEntry) -> str:
        return entry.query

    @staticmethod
    def vector_property_definitions() -> list[PropertyDefinition]:
        return [
            PropertyDefinition(name="query", data_type="text"),
            PropertyDefinition(name="passage", data_type="text"),
        ]
