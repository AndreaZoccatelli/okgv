"""Session: explicit container for all runtime state."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from okgv.protocols import EntrySchema


class Session:
    """Holds DB connections, schema, embedder, and log file path.

    All properties are lazily initialized on first access.
    For testing, pass pre-built objects to __init__ to skip real connections.
    """

    def __init__(
        self,
        graph_db=None,
        vector_db=None,
        embedder: Callable | None = None,
        schema: EntrySchema | None = None,
        log_db: Path | None = None,
    ):
        self._graph_db = graph_db
        self._vector_db = vector_db
        self._embedder = embedder
        self._schema = schema
        self._log_db = log_db
        self._owns_connections = graph_db is None and vector_db is None

    @property
    def schema(self) -> EntrySchema:
        if self._schema is None:
            from okgv.config import load_schema

            self._schema = load_schema()
        return self._schema

    @property
    def graph_db(self):
        if self._graph_db is None:
            from okgv.connections import create_graph_db

            self._graph_db = create_graph_db()
        return self._graph_db

    @property
    def vector_db(self):
        if self._vector_db is None:
            from okgv.connections import create_vector_db

            self._vector_db = create_vector_db(self.schema)
        return self._vector_db

    @property
    def embedder(self):
        if self._embedder is None:
            from okgv.connections import create_embedder

            self._embedder = create_embedder()
        return self._embedder

    @property
    def log_db(self) -> Path:
        if self._log_db is None:
            custom = os.getenv("OKGV_LOG")
            if custom:
                p = Path(custom)
                if p.is_dir() or custom.endswith("/"):
                    self._log_db = p / "log.db"
                else:
                    self._log_db = p
            else:
                self._log_db = Path.cwd() / "log.db"
        return self._log_db

    @property
    def review_enabled(self) -> bool:
        """Check if review is enabled by default via OKGV_REVIEW env var."""
        return os.getenv("OKGV_REVIEW", "none").lower() == "all"

    def close(self) -> None:
        if not self._owns_connections:
            return
        if self._graph_db is not None:
            self._graph_db.close()
            self._graph_db = None
        if self._vector_db is not None:
            self._vector_db.close()
            self._vector_db = None
        self._embedder = None
