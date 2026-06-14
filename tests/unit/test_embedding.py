"""Graceful degradation when the optional `embeddings` backend is absent.

The default `pip install okgv` ships without sentence-transformers; the user is
expected to add it via `okgv[embeddings]` or bring their own backend. A command
that needs to embed must then fail with a friendly `missing_dependency` error,
never a traceback.
"""

import builtins

import pytest

from okgv.embedding import make_embedder
from okgv.session import Session


def _block_sentence_transformers(monkeypatch):
    """Make `import sentence_transformers` fail, as on a core-only install."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("sentence_transformers"):
            raise ImportError("No module named 'sentence_transformers'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def test_make_embedder_without_sentence_transformers(monkeypatch):
    _block_sentence_transformers(monkeypatch)
    with pytest.raises(ImportError, match=r"okgv\[embeddings\]"):
        make_embedder("sentence-transformers/all-MiniLM-L6-v2")


def test_session_embedder_missing_dep_errors_cleanly(monkeypatch, capsys, tmp_path):
    """Accessing the embedder without the backend exits 1 with missing_dependency."""
    _block_sentence_transformers(monkeypatch)
    session = Session(db_path=tmp_path / "okgv.db")  # no embedder injected → real path
    with pytest.raises(SystemExit) as exc:
        _ = session.embedder
    assert exc.value.code == 1
    assert "missing_dependency" in capsys.readouterr().err
