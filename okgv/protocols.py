"""
Abstract protocols for the knowledge base.

EntrySchema  — user implements to define project-specific entry structure.
GraphDB      — graph database backend (relationships, topics, entries).
VectorDB     — vector database backend (embeddings, similarity search).

Records returned by DB backends carry raw dicts, not typed fields,
so they stay generic regardless of user-defined entry shape.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


def entry_id(raw: dict) -> str:
    """Deterministic UUID5 from canonical JSON serialization of raw input."""
    canonical = json.dumps(raw, sort_keys=True)
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, canonical))


# ── Generic records returned by DB backends ───────────────────────────────


@dataclass
class GraphRecord:
    id: str
    topic: str
    properties: dict


@dataclass
class VectorRecord:
    id: str
    properties: dict


# ── Property definition for vector DB collection schema ───────────────────


@dataclass
class PropertyDefinition:
    """Describes a single property in the vector DB collection.

    data_type uses generic names mapped by each backend:
      "text", "int", "float", "bool", "text[]"
    """

    name: str
    data_type: str


# ── Entry schema protocol ────────────────────────────────────────────────


@runtime_checkable
class EntrySchema(Protocol):
    """User implements this to define their entry structure.

    entry_class: a class whose __init__ accepts a raw dict.
        Construction is wrapped in try/except KeyError for validation.

    Methods receive an instance of entry_class (not raw dict),
    so computed properties are available as methods/attributes.
    """

    entry_class: type

    @staticmethod
    def metadata(entry: Any) -> dict:
        """Computed metadata — stored in both DBs."""
        ...

    @staticmethod
    def graph_properties(entry: Any) -> dict:
        """Additional properties for graph DB only."""
        ...

    @staticmethod
    def vector_properties(entry: Any) -> dict:
        """Additional properties for vector DB only."""
        ...

    @staticmethod
    def embedding_text(entry: Any) -> str:
        """Produce text to embed for vector similarity search."""
        ...

    @staticmethod
    def vector_property_definitions() -> list[PropertyDefinition]:
        """Schema for vector DB collection properties.

        Must cover properties from both metadata() and vector_properties().
        """
        ...


# ── Database protocols ────────────────────────────────────────────────────


@runtime_checkable
class GraphDB(Protocol):
    def topic_exists(self, path: str) -> bool: ...

    def create_topic(self, name: str) -> None: ...

    def create_subtopic(self, parent: str, name: str) -> None: ...

    def get_subtopics(self, topic: str) -> list[str]: ...

    def get_topic_entry_counts(self, parent: str | None = None) -> dict[str, int]: ...

    def get_entry_ids_for_topic(self, topic: str) -> list[str]: ...

    def get_entries_for_topic(self, topic: str) -> list[GraphRecord]: ...

    def upload_entry(
        self, topic: str, entry_id: str, properties: dict, overwrite: bool = False
    ) -> None: ...

    def get_by_id(self, entry_id: str) -> GraphRecord | None: ...

    def get_all_entry_ids(self) -> list[str]: ...

    def delete_entries(self, ids: list[str]) -> None: ...

    def move_topic(self, source: str, destination: str) -> None:
        """Move a topic/subtopic under a new parent topic.

        Raises ValueError if destination already has a child with same name.
        Updates paths of source and all its descendants.
        """
        ...

    def move_entry(self, entry_id: str, new_topic: str) -> None:
        """Move an entry to a different topic."""
        ...

    def close(self) -> None: ...


@runtime_checkable
class VectorDB(Protocol):
    def get_top_n(
        self,
        vector: list[float],
        n: int,
        filter_ids: list[str] | None = None,
    ) -> list[tuple[str, float]]: ...

    def get_by_id(self, entry_id: str) -> VectorRecord | None: ...

    def get_by_ids(self, entry_ids: list[str]) -> list[VectorRecord]: ...

    def upload_entry(
        self,
        entry_id: str,
        properties: dict,
        vector: list[float],
        overwrite: bool = False,
    ) -> None: ...

    def get_all_entry_ids(self) -> list[str]: ...

    def delete_by_id(self, entry_id: str) -> None: ...

    def ensure_collection(self) -> None: ...

    def close(self) -> None: ...
