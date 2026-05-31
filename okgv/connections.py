"""DB connection factories with cached reuse."""

import os

from okgv.embedding import make_embedder
from okgv.graph.client import Neo4jGraphDB
from okgv.helpers import env, env_int, err, EXIT_CONNECTION
from okgv.protocols import GraphDB, VectorDB
from okgv.vector.client import WeaviateVectorDB

_graph_db: GraphDB | None = None
_vector_db: VectorDB | None = None
_embedder = None


def connect_graph_db() -> GraphDB:
    global _graph_db
    if _graph_db is not None:
        return _graph_db
    try:
        _graph_db = Neo4jGraphDB(
            uri=env("NEO4J_URI", "bolt://localhost:7687"),
            user=env("NEO4J_USER", "neo4j"),
            password=env("NEO4J_PASSWORD", "password"),
            database=env("NEO4J_DATABASE", "neo4j"),
        )
        return _graph_db
    except Exception as e:
        err(
            "graph_db_connection_failed",
            detail=str(e),
            suggestion="Check NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE env vars",
            exit_code=EXIT_CONNECTION,
        )


def connect_vector_db(schema) -> VectorDB:
    global _vector_db
    if _vector_db is not None:
        return _vector_db
    try:
        _vector_db = WeaviateVectorDB(
            host=env("WEAVIATE_HOST", "localhost"),
            http_port=env_int("WEAVIATE_PORT", 8080),
            grpc_port=env_int("WEAVIATE_GRPC_PORT", 50051),
            collection_name=env("WEAVIATE_COLLECTION", "knowledge_base"),
            property_definitions=schema.vector_property_definitions(),
            api_key=os.getenv("WEAVIATE_API_KEY"),
        )
        return _vector_db
    except Exception as e:
        err(
            "vector_db_connection_failed",
            detail=str(e),
            suggestion="Check WEAVIATE_HOST, WEAVIATE_PORT, WEAVIATE_COLLECTION env vars",
            exit_code=EXIT_CONNECTION,
        )


def get_embedder():
    global _embedder
    if _embedder is not None:
        return _embedder
    _embedder = make_embedder(env("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2"))
    return _embedder


def close_all() -> None:
    """Close all cached connections."""
    global _graph_db, _vector_db, _embedder
    if _graph_db is not None:
        _graph_db.close()
        _graph_db = None
    if _vector_db is not None:
        _vector_db.close()
        _vector_db = None
    _embedder = None
