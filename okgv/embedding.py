"""Embedding utilities, independent of vector DB backend."""

from typing import Callable

from sentence_transformers import SentenceTransformer


def make_embedder(model_name: str) -> Callable[[list[str]], list[list[float]]]:
    """Load SentenceTransformers model and return a batch-embedding callable."""
    model = SentenceTransformer(model_name)

    def embedder(texts: list[str]) -> list[list[float]]:
        return model.encode(texts, show_progress_bar=False).tolist()

    return embedder
