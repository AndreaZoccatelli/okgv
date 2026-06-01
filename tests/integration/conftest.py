"""Integration test fixtures — real DB connections.

Skip all tests if DBs are unavailable.
Uses DEDICATED test databases to avoid polluting real data:
  - Neo4j: database "okgv_test" (override with NEO4J_TEST_DATABASE)
  - Weaviate: random collection name per run (okgv_test_<hex>)

Env vars (defaults match local dev setup):
  NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
  NEO4J_TEST_DATABASE (default: "okgv_test")
  WEAVIATE_HOST, WEAVIATE_PORT, WEAVIATE_GRPC_PORT
"""

import os
import uuid

import pytest

pytestmark = pytest.mark.integration

from okgv.protocols import PropertyDefinition

# Test schema: simple text entries
TEST_PROPERTY_DEFINITIONS = [
    PropertyDefinition(name="text", data_type="text"),
    PropertyDefinition(name="text_length", data_type="int"),
]


def _test_collection_name() -> str:
    """Unique collection name per test run to avoid collisions."""
    return f"okgv_test_{uuid.uuid4().hex[:8]}"


# ── Neo4j ──────────────────────────────────────────────────────────────


NEO4J_TEST_DB = os.getenv("NEO4J_TEST_DATABASE", "okgv-test")


@pytest.fixture(scope="session")
def graph_db():
    """Real Neo4j connection using dedicated test database. Skips if unavailable.

    IMPORTANT: Uses database "okgv_test" by default, NOT "neo4j".
    Create it in Neo4j Desktop: CREATE DATABASE okgv_test IF NOT EXISTS
    """
    try:
        from okgv.graph.client import Neo4jGraphDB

        db = Neo4jGraphDB(
            uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            user=os.getenv("NEO4J_USER", "neo4j"),
            password=os.getenv("NEO4J_PASSWORD", "password"),
            database=NEO4J_TEST_DB,
        )
    except Exception as e:
        pytest.skip(f"Neo4j unavailable (database={NEO4J_TEST_DB}): {e}")
    yield db
    # Cleanup: remove all test nodes
    with db._session() as session:
        session.run("MATCH (n) DETACH DELETE n")
    db.close()


@pytest.fixture(autouse=True)
def _clean_graph(graph_db):
    """Wipe graph before each test."""
    with graph_db._session() as session:
        session.run("MATCH (n) DETACH DELETE n")


# ── Weaviate ───────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def vector_db():
    """Real Weaviate connection with isolated test collection. Skips if unavailable."""
    try:
        from okgv.vector.client import WeaviateVectorDB

        collection_name = _test_collection_name()
        db = WeaviateVectorDB(
            host=os.getenv("WEAVIATE_HOST", "localhost"),
            http_port=int(os.getenv("WEAVIATE_PORT", "8080")),
            grpc_port=int(os.getenv("WEAVIATE_GRPC_PORT", "50051")),
            collection_name=collection_name,
            property_definitions=TEST_PROPERTY_DEFINITIONS,
        )
        db.ensure_collection()
    except Exception as e:
        pytest.skip(f"Weaviate unavailable: {e}")
    yield db
    # Cleanup: drop test collection
    try:
        db._client.collections.delete(collection_name)
    except Exception:
        pass
    db.close()


@pytest.fixture(autouse=True)
def _clean_vector(vector_db):
    """Wipe all entries from test collection before each test."""
    for eid in vector_db.get_all_entry_ids():
        vector_db.delete_by_id(eid)


# ── Shared helpers ─────────────────────────────────────────────────────


def make_vector(dim: int = 384) -> list[float]:
    """Generate a random-ish vector of given dimension."""
    import random

    random.seed(42)
    return [random.random() for _ in range(dim)]


def make_vector_unique(seed: int, dim: int = 384) -> list[float]:
    """Generate a deterministic vector from seed."""
    import random

    random.seed(seed)
    return [random.random() for _ in range(dim)]


def make_uuid(label: str) -> str:
    """Deterministic UUID5 from a label string. Weaviate requires valid UUIDs."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, label))
