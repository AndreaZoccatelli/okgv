"""DB connection factories with retry on transient failures."""

import os
import time

from okgv.embedding import make_embedder
from pathlib import Path

from okgv.graph.sqlite_client import SQLiteGraphDB
from okgv.helpers import EXIT_CONNECTION, env_int, err
from okgv.protocols import EntrySchema, GraphDB, VectorDB
from okgv.vector.client import WeaviateVectorDB

_MAX_CONNECT_RETRIES = 3
_CONNECT_RETRY_DELAY = 2


def _retry_connect(fn, label: str):
    """Retry connection factory with exponential backoff."""
    last_err = None
    for attempt in range(_MAX_CONNECT_RETRIES):
        try:
            return fn()
        except Exception as e:
            last_err = e
            if attempt < _MAX_CONNECT_RETRIES - 1:
                delay = _CONNECT_RETRY_DELAY * (attempt + 1)
                import sys
                print(f"[okgv] {label} connection failed (attempt {attempt + 1}/{_MAX_CONNECT_RETRIES}), retrying in {delay}s...", file=sys.stderr)
                time.sleep(delay)
    err(
        f"{label}_connection_failed",
        detail=str(last_err),
        suggestion=f"Check connection env vars. Failed after {_MAX_CONNECT_RETRIES} attempts.",
        exit_code=EXIT_CONNECTION,
    )


def create_graph_db(db_path: str | Path) -> GraphDB:
    return SQLiteGraphDB(db_path)


def create_vector_db(schema: EntrySchema) -> VectorDB:
    def _connect():
        return WeaviateVectorDB(
            host=os.getenv("WEAVIATE_HOST", "localhost"),
            http_port=env_int("WEAVIATE_PORT", 8080),
            grpc_port=env_int("WEAVIATE_GRPC_PORT", 50051),
            collection_name=os.getenv("WEAVIATE_COLLECTION", "knowledge_base"),
            property_definitions=schema.vector_property_definitions(),
            api_key=os.getenv("WEAVIATE_API_KEY"),
        )
    return _retry_connect(_connect, "vector_db")


def create_embedder():
    return make_embedder(
        os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    )
