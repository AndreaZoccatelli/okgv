"""Integration test fixtures — real DB connections.

Uses a shared in-memory SQLite connection with sqlite-vec for both graph and vector.
"""

import sqlite3
import uuid

import pytest
import sqlite_vec

from okgv.graph.sqlite_client import SQLiteGraphDB
from okgv.protocols import PropertyDefinition
from okgv.vector.sqlite_client import SQLiteVectorDB

pytestmark = pytest.mark.integration

# Test schema: simple text entries
TEST_PROPERTY_DEFINITIONS = [
    PropertyDefinition(name="text", data_type="text"),
    PropertyDefinition(name="text_length", data_type="int"),
]

# Embedding dimension for tests
TEST_EMBED_DIM = 384


# ── Shared connection ─────────────────────────────────────────────────


@pytest.fixture
def shared_conn():
    """In-memory SQLite connection with sqlite-vec loaded."""
    conn = sqlite3.connect(":memory:")
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    yield conn
    conn.close()


@pytest.fixture
def graph_db(shared_conn):
    """SQLite graph DB sharing the test connection."""
    return SQLiteGraphDB(shared_conn)


@pytest.fixture
def vector_db(shared_conn):
    """SQLite vector DB sharing the test connection."""
    return SQLiteVectorDB(shared_conn, embed_dim=TEST_EMBED_DIM)


# ── Shared helpers ─────────────────────────────────────────────────────


def make_vector(dim: int = TEST_EMBED_DIM) -> list[float]:
    """Generate a random-ish vector of given dimension."""
    import random

    random.seed(42)
    return [random.random() for _ in range(dim)]


def make_vector_unique(seed: int, dim: int = TEST_EMBED_DIM) -> list[float]:
    """Generate a deterministic vector from seed."""
    import random

    random.seed(seed)
    return [random.random() for _ in range(dim)]


def make_uuid(label: str) -> str:
    """Deterministic UUID5 from a label string."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, label))
