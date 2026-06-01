"""DB connection factories. No cached state — Session handles lifecycle."""

import os

from okgv.embedding import make_embedder
from okgv.graph.client import Neo4jGraphDB
from okgv.helpers import EXIT_CONNECTION, env_int, err
from okgv.protocols import GraphDB, VectorDB
from okgv.vector.client import WeaviateVectorDB


def create_graph_db() -> GraphDB:
    try:
        return Neo4jGraphDB(
            uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            user=os.getenv("NEO4J_USER", "neo4j"),
            password=os.getenv("NEO4J_PASSWORD", "password"),
            database=os.getenv("NEO4J_DATABASE", "neo4j"),
        )
    except Exception as e:
        err(
            "graph_db_connection_failed",
            detail=str(e),
            suggestion="Check NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE env vars",
            exit_code=EXIT_CONNECTION,
        )


def create_vector_db(schema) -> VectorDB:
    try:
        return WeaviateVectorDB(
            host=os.getenv("WEAVIATE_HOST", "localhost"),
            http_port=env_int("WEAVIATE_PORT", 8080),
            grpc_port=env_int("WEAVIATE_GRPC_PORT", 50051),
            collection_name=os.getenv("WEAVIATE_COLLECTION", "knowledge_base"),
            property_definitions=schema.vector_property_definitions(),
            api_key=os.getenv("WEAVIATE_API_KEY"),
        )
    except Exception as e:
        err(
            "vector_db_connection_failed",
            detail=str(e),
            suggestion="Check WEAVIATE_HOST, WEAVIATE_PORT, WEAVIATE_COLLECTION env vars",
            exit_code=EXIT_CONNECTION,
        )


def create_embedder():
    return make_embedder(
        os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    )
