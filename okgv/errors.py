"""Exceptions reported as structured JSON errors by the CLI.

Any OkgvError raised inside a command is converted by the CLI group into
the standard {error, detail, suggestion} payload on stderr, with the
exception's exit code (see helpers.err). Raise these from core or backend
code instead of calling err() directly.
"""

from okgv.helpers import EXIT_FAILURE, EXIT_USAGE


class OkgvError(Exception):
    """Base for errors with a structured CLI representation."""

    code = "error"
    exit_code = EXIT_FAILURE
    suggestion = ""


class EntryError(OkgvError):
    """Raised when a single entry fails to build or upsert."""

    code = "missing_field"
    exit_code = EXIT_USAGE


class SpecError(OkgvError):
    """Raised when a structure file's `_meta` block is malformed or its
    folded effective spec is contradictory (an ingest-time error)."""

    code = "invalid_meta"
    exit_code = EXIT_USAGE


class DuplicateEntryError(OkgvError, ValueError):
    """Raised when inserting an entry whose ID already exists.

    Subclasses ValueError so callers that handle generic backend
    ValueErrors keep working.
    """

    code = "duplicate_entry"
    exit_code = EXIT_USAGE
    suggestion = "Pass --overwrite to replace the existing entry"


class RelocationError(OkgvError, ValueError):
    """Raised when overwrite=True targets a topic different from the existing
    entry's topic. Overwrite re-derives an entry in place; changing its topic is
    a separate operation with different invariants (and would bypass the
    destination-spec revalidation that move performs).

    Subclasses ValueError so batch callers that catch backend ValueErrors
    collect it as a per-entry failure rather than aborting the batch.
    """

    code = "overwrite_relocation"
    exit_code = EXIT_USAGE
    suggestion = "Use the move-entry command to change an entry's topic"
