"""CLI command modules, grouped by domain.

Each module exposes a `commands` tuple; `all_commands` aggregates them for
registration on the CLI group in okgv.main.
"""

from okgv.commands import entries, lifecycle, maintenance, prompts, review, structure

all_commands = (
    *prompts.commands,
    *structure.commands,
    *entries.commands,
    *review.commands,
    *maintenance.commands,
    *lifecycle.commands,
)
