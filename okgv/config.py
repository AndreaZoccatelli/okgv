"""
Schema discovery.

Reads OKGV_SCHEMA env var in "module:ClassName" format.
The module is resolved relative to cwd. Example: "schema:MyEntrySchema"
imports schema.py from cwd and uses MyEntrySchema class.

Run `okgv init` to scaffold a schema.py template.
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
    """Load the active EntrySchema from OKGV_SCHEMA env var."""
    env_specifier = os.getenv("OKGV_SCHEMA")
    if not env_specifier:
        from okgv.helpers import EXIT_USAGE, err

        err(
            "no_schema",
            detail="OKGV_SCHEMA environment variable is not set",
            suggestion="Set OKGV_SCHEMA in .env (e.g. OKGV_SCHEMA=schema:MyEntrySchema). Run 'okgv init' to scaffold.",
            exit_code=EXIT_USAGE,
        )
    return _import_schema(env_specifier)
