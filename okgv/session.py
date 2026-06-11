"""Session: explicit container for all runtime state."""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from okgv.protocols import EntrySchema


class Session:
    """Holds DB connections, schema, embedder, and database path.

    All properties are lazily initialized on first access.
    graph_db only needs a connection (no model loading).
    vector_db additionally needs embed_dim (may trigger model loading on first run).
    For testing, pass pre-built objects to __init__ to skip real connections.
    """

    def __init__(
        self,
        graph_db=None,
        vector_db=None,
        embedder: Callable | None = None,
        schema: EntrySchema | None = None,
        db_path: Path | None = None,
    ):
        self._graph_db = graph_db
        self._vector_db = vector_db
        self._embedder = embedder
        self._schema = schema
        self._db_path = db_path
        self._conn = None
        self._owns_connections = graph_db is None and vector_db is None

    @property
    def schema(self) -> EntrySchema:
        if self._schema is None:
            from okgv.config import load_schema

            self._schema = load_schema()
        return self._schema

    def _ensure_conn(self) -> None:
        """Create shared connection + graph DB. No model loading."""
        if self._conn is not None:
            return
        from okgv.db import create_conn

        self._conn = create_conn(self.db_path)

        from okgv.graph.sqlite_client import SQLiteGraphDB

        self._graph_db = SQLiteGraphDB(self._conn)

    def _ensure_vector_db(self) -> None:
        """Create vector DB layer. Loads model only if dim not yet stored."""
        self._ensure_conn()
        if self._vector_db is not None:
            return
        from okgv.connections import get_embed_dim
        from okgv.db import _store_dim, get_stored_dim
        from okgv.vector.sqlite_client import SQLiteVectorDB

        env_dim = get_embed_dim()
        stored_dim = get_stored_dim(self._conn)

        if env_dim and stored_dim and env_dim != stored_dim:
            from okgv.helpers import EXIT_USAGE, err

            err(
                "embed_dim_mismatch",
                detail=f"EMBED_DIM={env_dim} but DB was created with dim={stored_dim}",
                suggestion="Remove EMBED_DIM to use stored value, or purge and recreate the DB",
                exit_code=EXIT_USAGE,
            )

        embed_dim = env_dim or stored_dim
        if embed_dim is None:
            embed_dim = self._detect_embed_dim()

        _store_dim(self._conn, embed_dim)
        self._vector_db = SQLiteVectorDB(self._conn, embed_dim=embed_dim)

    def _detect_embed_dim(self) -> int:
        """Auto-detect embedding dimension from model. Only called on first run."""
        test_vec = self.embedder(["test"])
        return len(test_vec[0])

    @property
    def graph_db(self):
        if self._graph_db is None:
            self._ensure_conn()
        return self._graph_db

    @property
    def vector_db(self):
        if self._vector_db is None:
            self._ensure_vector_db()
        return self._vector_db

    @property
    def embedder(self):
        if self._embedder is None:
            from okgv.connections import create_embedder

            try:
                self._embedder = create_embedder()
            except ImportError as e:
                from okgv.helpers import err

                err("missing_dependency", detail=str(e), exit_code=1)
        return self._embedder

    @property
    def db_path(self) -> Path:
        if self._db_path is None:
            custom = os.getenv("OKGV_DB")
            if custom:
                p = Path(custom)
                if p.is_dir() or custom.endswith("/"):
                    self._db_path = p / "okgv.db"
                else:
                    self._db_path = p
            else:
                self._db_path = Path.cwd() / "okgv.db"
        return self._db_path

    @contextmanager
    def transaction(self):
        """Group graph and vector writes into one atomic SQLite transaction.

        Commits on exit, rolls back on error. No-op when backends were
        injected (they may not share a connection); atomicity is then the
        backends' responsibility.
        """
        if not self._owns_connections:
            yield
            return
        self._ensure_conn()
        with self._conn.transaction():
            yield

    @property
    def review_enabled(self) -> bool:
        """Check if review is enabled by default via OKGV_REVIEW env var."""
        value = os.getenv("OKGV_REVIEW", "none").lower()
        if value not in ("none", "all"):
            from okgv.helpers import EXIT_USAGE, err

            err(
                "invalid_config",
                detail=f"Unknown OKGV_REVIEW value '{value}'",
                suggestion="Use 'none' or 'all'",
                exit_code=EXIT_USAGE,
            )
        return value == "all"

    def close(self) -> None:
        if not self._owns_connections:
            return
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self._graph_db = None
        self._vector_db = None
        self._embedder = None
