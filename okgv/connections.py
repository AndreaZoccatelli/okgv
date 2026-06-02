"""DB connection factories."""

import os

from okgv.embedding import make_embedder


def create_embedder():
    return make_embedder(os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2"))


def get_embed_dim() -> int | None:
    """Return EMBED_DIM from env, or None for auto-detect."""
    val = os.getenv("EMBED_DIM")
    if val:
        return int(val)
    return None
