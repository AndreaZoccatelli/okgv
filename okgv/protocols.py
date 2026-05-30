"""
Abstract protocols for graph and vector database backends.

Implementations must satisfy these interfaces. Current backends:
  - Neo4j   → graph.neo4j.Neo4jGraphDB
  - Weaviate → vector.weaviate.WeaviateVectorDB
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class GraphEntry:
    id: str
    topic: str
    question: str
    answer: str
    options: list[str]


@dataclass
class VectorEntry:
    id: str
    question: str
    options: dict[str, str]
    answer: str


@runtime_checkable
class GraphDB(Protocol):
    def get_topic_entry_counts(self) -> dict[str, int]: ...

    def get_entry_ids_for_topic(self, topic: str) -> list[str]: ...

    def upload_entry(
        self,
        topic: str,
        entry_id: str,
        question: str,
        answer: str,
        options: list[str],
    ) -> None: ...

    def get_by_id(self, entry_id: str) -> GraphEntry | None: ...

    def delete_entries(self, ids: list[str]) -> None: ...

    def close(self) -> None: ...


@runtime_checkable
class VectorDB(Protocol):
    def get_top_n(
        self,
        vector: list[float],
        n: int,
        filter_ids: list[str] | None = None,
    ) -> list[tuple[str, float]]: ...

    def get_by_id(self, entry_id: str) -> VectorEntry | None: ...

    def get_by_ids(self, entry_ids: list[str]) -> list[VectorEntry]: ...

    def upload_entry(
        self,
        entry_id: str,
        properties: dict,
        vector: list[float],
        overwrite: bool = False,
    ) -> None: ...

    def delete_by_id(self, entry_id: str) -> None: ...

    def ensure_collection(self) -> None: ...

    def close(self) -> None: ...
