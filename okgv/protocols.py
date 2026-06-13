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
from collections.abc import Iterator
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

    Optional hook (not part of the protocol, detected at runtime):

    validate_for_topic(entry, topic[, spec]) — called on upsert (and on the
        destination of a move, and by `revalidate`) with the built entry and the
        topic path, before any DB write. Raise ValueError to reject the entry
        (relational constraints the raw dict alone cannot express, e.g. "entries
        under a function topic must call that function"). An optional third
        parameter receives the topic's folded effective spec (okgv.specs.Spec)
        so the hook need not re-read the structure file; `(entry, topic)` hooks
        still work. The library already enforces the spec's `entry`-namespace
        constraints generically before this hook runs, so the hook only handles
        what is dataset-specific. Schemas without the hook are unaffected.
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

    def get_topic_stats(
        self,
        topic: str,
        fields: list[str] | None = None,
    ) -> tuple[int, list[str], list[dict]]:
        """Aggregate entry counts grouped by fields.

        Returns (total_entries, group_fields, groups).
        If fields is None, discovers all property keys first.
        """
        ...

    def get_topic_depth(self, root: str | None = None) -> int:
        """Return max depth of topic tree from root."""
        ...

    def get_topic_tree(self, root: str | None = None, max_depth: int | None = None) -> dict:
        """Return nested dict of topics/subtopics (no entries)."""
        ...

    def upload_entry(self, topic: str, entry_id: str, properties: dict, overwrite: bool = False) -> None: ...

    def get_by_id(self, entry_id: str) -> GraphRecord | None: ...

    def get_topics_for_ids(self, ids: list[str]) -> dict[str, str]:
        """Return {id: topic} for the given entry IDs."""
        ...

    def get_all_entry_ids(self) -> list[str]: ...

    def delete_entries(self, ids: list[str]) -> None: ...

    def count_topics(self) -> int:
        """Return total number of Topic nodes."""
        ...

    def delete_all(self) -> None:
        """Delete all nodes (entries, topics) and relationships."""
        ...

    def iter_entry_ids(self, batch_size: int = 1000) -> Iterator[list[str]]:
        """Yield entry IDs in batches. For memory-efficient reconciliation."""
        ...

    def exists_batch(self, ids: list[str]) -> set[str]:
        """Return subset of ids that exist in the DB."""
        ...

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
        filter_topic: str | None = None,
        subtree: bool = False,
    ) -> list[tuple[str, float]]:
        """Top-n by similarity. filter_topic restricts to that topic; with
        subtree=True it restricts to that topic and all descendants (prefix)."""
        ...

    def get_by_id(self, entry_id: str) -> VectorRecord | None: ...

    def get_by_ids(self, entry_ids: list[str]) -> list[VectorRecord]: ...

    def get_by_topic(self, topic: str, limit: int) -> list[VectorRecord]: ...

    def upload_entry(
        self,
        entry_id: str,
        properties: dict,
        vector: list[float],
        topic: str,
        overwrite: bool = False,
    ) -> None: ...

    def update_entry_topic(self, entry_id: str, new_topic: str) -> None: ...

    def update_topics(self, old_prefix: str, new_prefix: str) -> None:
        """Update topic for all entries where topic == old_prefix or starts with old_prefix/."""
        ...

    def get_all_entry_ids(self) -> list[str]: ...

    def upload_entries_batch(
        self,
        entries: list[dict],
        vectors: list[list[float]],
        entry_ids: list[str],
        topic: str,
    ) -> list[str]:
        """Batch insert entries. Returns list of entry IDs that failed."""
        ...

    def iter_entry_ids(self, batch_size: int = 1000) -> Iterator[list[str]]:
        """Yield entry IDs in batches. For memory-efficient reconciliation."""
        ...

    def exists_batch(self, ids: list[str]) -> set[str]:
        """Return subset of ids that exist in the DB."""
        ...

    def delete_by_id(self, entry_id: str) -> None: ...

    def delete_by_ids(self, entry_ids: list[str]) -> None: ...

    def close(self) -> None: ...
