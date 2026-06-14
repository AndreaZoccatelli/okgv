"""Session: explicit container for all runtime state."""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from okgv.protocols import EntrySchema, GraphDB, VectorDB

if TYPE_CHECKING:
    from okgv.db import ManagedConnection
    from okgv.specs import Spec


class Session:
    """Holds DB connections, schema, embedder, and database path.

    All properties are lazily initialized on first access.
    graph_db only needs a connection (no model loading).
    vector_db additionally needs embed_dim (may trigger model loading on first run).
    For testing, pass pre-built objects to __init__ to skip real connections.
    """

    def __init__(
        self,
        graph_db: GraphDB | None = None,
        vector_db: VectorDB | None = None,
        embedder: Callable | None = None,
        schema: EntrySchema | None = None,
        db_path: Path | None = None,
    ):
        self._graph_db = graph_db
        self._vector_db = vector_db
        self._embedder = embedder
        self._schema = schema
        self._db_path = db_path
        self._conn: ManagedConnection | None = None
        self._owns_connections = graph_db is None and vector_db is None
        self._specs: dict[str, Spec] | None = None  # lazily built {topic_path: Spec} from the structure file
        self._validators_loaded = False

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
        assert self._conn is not None  # _ensure_conn guarantees this
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
    def graph_db(self) -> GraphDB:
        if self._graph_db is None:
            self._ensure_conn()
        assert self._graph_db is not None
        return self._graph_db

    @property
    def vector_db(self) -> VectorDB:
        if self._vector_db is None:
            self._ensure_vector_db()
        assert self._vector_db is not None
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

    @property
    def structure_path(self) -> Path:
        """Location of the structure file the in-memory specs are folded from.

        ``OKGV_STRUCTURE`` overrides the default ``config/structure.json``
        (relative to cwd), mirroring how prompts.py refers to it.
        """
        custom = os.getenv("OKGV_STRUCTURE")
        return Path(custom) if custom else Path.cwd() / "config" / "structure.json"

    @property
    def specs(self) -> dict:
        """Effective (folded) constraint spec per topic path, keyed by path.

        Parsed once from the structure file (see okgv.specs). Empty when the
        file is absent: the library stays permissive, and schemas that require
        constraints enforce that themselves in validate_for_topic.
        """
        if self._specs is None:
            self._specs = self._load_specs()
        return self._specs

    def ensure_validators(self) -> None:
        """Import OKGV_VALIDATORS modules so custom tags register before a fold.

        Idempotent per session. Must run before any `build_specs` call that may
        reference a custom validator tag (`create-structure`, spec loading).
        """
        if self._validators_loaded:
            return
        from okgv.config import load_validators

        load_validators()
        self._validators_loaded = True

    def _load_specs(self) -> dict:
        import json

        from okgv.specs import build_specs

        path = self.structure_path
        if not path.exists():
            return {}
        self.ensure_validators()
        return build_specs(json.loads(path.read_text()))

    def effective_spec(self, topic: str):
        """Folded spec for a topic path, or None when it carries no constraints."""
        return self.specs.get(topic)

    def similarity_scope(self, topic: str) -> tuple[str, str]:
        """Resolve the dedup scope for a topic into ``(scope, search_root)``.

        ``scope`` is ``leaf`` (default, current exact-match behavior) or
        ``subtree``, read from the topic's folded ``similarity_scope`` (nearest
        ancestor wins). For ``subtree`` the search must cover the siblings that
        the scope was declared to dedup against, so ``search_root`` climbs to
        the topmost contiguous ancestor still carrying ``subtree`` (the split
        node); ``get_top_n`` then prefix-matches that root. For ``leaf`` the
        root is the topic itself.
        """
        spec = self.effective_spec(topic)
        if spec is None or spec.scope() != "subtree":
            return "leaf", topic
        root = topic
        while "/" in root:
            parent = root.rsplit("/", 1)[0]
            pspec = self.effective_spec(parent)
            if pspec is None or pspec.scope() != "subtree":
                break
            root = parent
        return "subtree", root

    def check_structure_consistency(self) -> list[str]:
        """Warn when the DB's topic set has drifted from the structure file.

        Specs live in memory keyed by topic path, so a DB whose topics no longer
        match the file would validate against stale constraints. Skipped (no
        warnings) when either the structure file or the DB is absent, so it
        never forces a DB to be created or a model to load.
        """
        from okgv.helpers import log
        from okgv.specs import topic_paths

        path = self.structure_path
        if not path.exists() or not self.db_path.exists():
            return []

        import json

        file_topics = topic_paths(json.loads(path.read_text()))
        db_topics = topic_paths(self.graph_db.get_topic_tree())

        warnings = []
        missing = file_topics - db_topics
        extra = db_topics - file_topics
        if missing:
            warnings.append(f"topics in {path.name} but not in the DB: {sorted(missing)}; run create-structure")
        if extra:
            warnings.append(f"topics in the DB but not in {path.name}: {sorted(extra)}; structure file is stale")
        for w in warnings:
            log(f"warning: {w}")
        return warnings

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
        assert self._conn is not None
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
        self._specs = None
        self._validators_loaded = False
