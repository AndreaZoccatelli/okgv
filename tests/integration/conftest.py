"""Integration test fixtures — real DB connections.

Uses DEDICATED test databases to avoid polluting real data:
  - SQLite: in-memory database for graph tests
  - Weaviate: random collection name per run (okgv_test_<hex>)

Env vars (defaults match local dev setup):
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


# ── SQLite Graph ──────────────────────────────────────────────────────


@pytest.fixture
def graph_db():
    """In-memory SQLite graph DB for testing."""
    from okgv.graph.sqlite_client import SQLiteGraphDB

    db = SQLiteGraphDB(":memory:")
    yield db
    db.close()


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
