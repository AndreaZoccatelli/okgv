"""Embedding backend dispatcher.

Resolves EMBED_MODEL prefix to the appropriate backend:
  - "sentence-transformers/..." → sentence-transformers (optional dep)
  - No prefix → treated as sentence-transformers model name

New backends can be added by extending _BACKENDS.
"""

from collections.abc import Callable

Embedder = Callable[[list[str]], list[list[float]]]

_BACKENDS: dict[str, Callable[[str], Embedder]] = {}


def register_backend(prefix: str, factory: Callable[[str], Embedder]) -> None:
    """Register an embedding backend for a given prefix."""
    _BACKENDS[prefix] = factory


def _load_sentence_transformers(model_name: str) -> Embedder:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        raise ImportError(
            f"sentence-transformers is required for model '{model_name}': pip install okgv[embeddings]"
        ) from None

    model = SentenceTransformer(model_name)

    def embedder(texts: list[str]) -> list[list[float]]:
        return model.encode(texts, show_progress_bar=False).tolist()

    return embedder


register_backend("sentence-transformers", _load_sentence_transformers)


def make_embedder(model_spec: str) -> Embedder:
    """Create an embedder from a model specifier.

    Format: "backend/model-name" or just "model-name" (defaults to sentence-transformers).
    """
    if "/" in model_spec:
        prefix, model_name = model_spec.split("/", 1)
        if prefix in _BACKENDS:
            return _BACKENDS[prefix](model_name)

    # No recognized prefix — default to sentence-transformers
    return _load_sentence_transformers(model_spec)
