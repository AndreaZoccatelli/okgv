"""Session: explicit container for all runtime state."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from okgv.protocols import EntrySchema


class Session:
    """Holds DB connections, schema, embedder, and database path.

    All properties are lazily initialized on first access.
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

    def _ensure_db(self) -> None:
        """Create shared connection and both DB layers on first access."""
        if self._conn is not None:
            return
        from okgv.connections import get_embed_dim
        from okgv.db import create_db

        embed_dim = get_embed_dim() or self._detect_embed_dim()
        self._conn, self._graph_db, self._vector_db = create_db(
            self.db_path, embed_dim
        )

    def _detect_embed_dim(self) -> int:
        """Auto-detect embedding dimension from model."""
        test_vec = self.embedder(["test"])
        return len(test_vec[0])

    @property
    def graph_db(self):
        if self._graph_db is None:
            self._ensure_db()
        return self._graph_db

    @property
    def vector_db(self):
        if self._vector_db is None:
            self._ensure_db()
        return self._vector_db

    @property
    def embedder(self):
        if self._embedder is None:
            from okgv.connections import create_embedder

            self._embedder = create_embedder()
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

    @property
    def review_enabled(self) -> bool:
        """Check if review is enabled by default via OKGV_REVIEW env var."""
        return os.getenv("OKGV_REVIEW", "none").lower() == "all"

    def close(self) -> None:
        if not self._owns_connections:
            return
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self._graph_db = None
        self._vector_db = None
        self._embedder = None
