"""
Schema discovery.

Resolution:
  1. OKGV_SCHEMA env var  →  "module:ClassName"
  2. Built-in QAEntrySchema (fallback)

The "module:ClassName" format imports `module` (relative to cwd) and
gets `ClassName` from it. Example: "schema:MyEntrySchema" imports
schema.py from cwd and uses MyEntrySchema class.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

from okgv.protocols import EntrySchema


def _import_schema(specifier: str) -> EntrySchema:
    """Import schema class from 'module:ClassName' specifier.

    The module is resolved relative to cwd (added to sys.path if needed).
    """
    if ":" not in specifier:
        raise ValueError(
            f"Invalid schema specifier '{specifier}'. Expected format: 'module:ClassName' (e.g. 'schema:MyEntrySchema')"
        )
    module_path, class_name = specifier.rsplit(":", 1)

    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as e:
        raise ImportError(
            f"Cannot import schema module '{module_path}': {e}. Make sure the file exists in {cwd}"
        ) from e

    try:
        cls = getattr(module, class_name)
    except AttributeError:
        raise ImportError(
            f"Module '{module_path}' has no class '{class_name}'. "
            f"Available: {[n for n in dir(module) if not n.startswith('_')]}"
        )

    return cls()


def load_schema() -> EntrySchema:
    """Discover and load the active EntrySchema."""
    env_specifier = os.getenv("OKGV_SCHEMA")
    if env_specifier:
        return _import_schema(env_specifier)

    from okgv.schemas.qa import QAEntrySchema

    return QAEntrySchema()
