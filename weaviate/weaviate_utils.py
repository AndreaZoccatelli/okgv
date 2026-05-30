"""
Weaviate utilities: embedding factory and query helpers.

Functions:
  make_embedder         — build a SentenceTransformers embedding callable
  search_by_similarity    — embed query text, return (id, score) pairs above threshold
  get_top_n_by_similarity — embed query text, return top-n (id, score) pairs, no threshold
  get_by_id               — fetch stored properties for a given UUID
"""

import json
from dataclasses import dataclass
from typing import Callable

import weaviate.classes as wvc
from sentence_transformers import SentenceTransformer

import weaviate


@dataclass
class WeaviateEntry:
    id: str  # UUID5 — must match Neo4j Entry id
    question: str
    options: dict[str, str]
    answer: str


def make_embedder(model_name: str) -> Callable[[list[str]], list[list[float]]]:
    """Load SentenceTransformers model and return a batch-embedding callable."""
    model = SentenceTransformer(model_name)

    def embedder(texts: list[str]) -> list[list[float]]:
        return model.encode(texts, show_progress_bar=False).tolist()

    return embedder


def search_by_similarity(
    client: weaviate.WeaviateClient,
    query: str,
    embedder: Callable[[list[str]], list[list[float]]],
    threshold: float,
    collection_name: str = "Entry",
    limit: int = 1000,
) -> list[tuple[str, float]]:
    """
    Embed query and return (id, certainty) for all entries above threshold.

    Args:
        query:      raw text to embed and search
        embedder:   same embedding fn used at upload time
        threshold:  minimum cosine certainty in [0, 1]
        limit:      max candidates to retrieve

    Returns:
        List of (uuid_str, certainty) sorted by certainty descending.
    """
    vector = embedder([query])[0]
    collection = client.collections.get(collection_name)
    response = collection.query.near_vector(
        near_vector=vector,
        certainty=threshold,
        limit=limit,
        return_metadata=wvc.query.MetadataQuery(certainty=True),
    )
    return [(str(obj.uuid), obj.metadata.certainty) for obj in response.objects]


def get_top_n_by_similarity(
    client: weaviate.WeaviateClient,
    query: str,
    embedder: Callable[[list[str]], list[list[float]]],
    n: int,
    collection_name: str = "Entry",
    filter_ids: list[str] | None = None,
) -> list[tuple[str, float]]:
    """
    Embed query and return top-n (id, certainty) pairs, no threshold filter.

    Args:
        query:      raw text to embed and search
        embedder:   same embedding fn used at upload time
        n:          number of top results to return
        collection_name: Weaviate collection to query
        filter_ids: if given, restrict search to these UUIDs

    Returns:
        List of (uuid_str, certainty) sorted by certainty descending, length <= n.
    """
    vector = embedder([query])[0]
    collection = client.collections.get(collection_name)
    filters = (
        wvc.query.Filter.by_id().contains_any(filter_ids)
        if filter_ids
        else None
    )
    response = collection.query.near_vector(
        near_vector=vector,
        limit=n,
        filters=filters,
        return_metadata=wvc.query.MetadataQuery(certainty=True),
    )
    return [(str(obj.uuid), obj.metadata.certainty) for obj in response.objects]


def get_by_ids(
    client: weaviate.WeaviateClient,
    entry_ids: list[str],
    collection_name: str = "Entry",
) -> list[WeaviateEntry]:
    """
    Fetch multiple stored entries by UUID list.

    Args:
        entry_ids: list of UUID strings

    Returns:
        List of WeaviateEntry for IDs that exist (missing IDs silently skipped).
    """
    collection = client.collections.get(collection_name)
    response = collection.query.fetch_objects(
        filters=wvc.query.Filter.by_id().contains_any(entry_ids),
        limit=len(entry_ids),
    )
    return [
        WeaviateEntry(
            id=str(obj.uuid),
            question=obj.properties["question"],
            options=json.loads(obj.properties["options"]),
            answer=obj.properties["answer"],
        )
        for obj in response.objects
    ]


def get_by_id(
    client: weaviate.WeaviateClient,
    entry_id: str,
    collection_name: str = "Entry",
) -> WeaviateEntry | None:
    """
    Fetch stored entry by UUID. Returns None if not found.

    Args:
        entry_id: UUID string (same as Neo4j Entry id)

    Returns:
        WeaviateEntry with question, options, answer populated, or None.
    """
    collection = client.collections.get(collection_name)
    obj = collection.query.fetch_object_by_id(entry_id)
    if obj is None:
        return None
    return WeaviateEntry(
        id=str(obj.uuid),
        question=obj.properties["question"],
        options=json.loads(obj.properties["options"]),
        answer=obj.properties["answer"],
    )
