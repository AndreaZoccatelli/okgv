"""
CLI for AI agents to interact with the self-organized knowledge base.

Schema discovery (see config.py):
  OKGV_SCHEMA env var →  "module:ClassName"

Exit codes:  0=ok  1=failure  2=usage  3=not_found  4=connection

Commands live in okgv.commands, grouped by domain:
  prompts      — cli-prompt, entry-prompt, init
  structure    — tree, get-structure, get-depth, create-topic,
                 create-structure, least-topic, topic-stats,
                 move-topic, move-entry
  entries      — similar, similar-batch, submit, submit-batch,
                 get-by-topic, get-vector, get-graph, export
  review       — review, approve, reject
  maintenance  — log, undo, reconcile, purge
"""

import sqlite3

import click

from okgv.commands import all_commands
from okgv.errors import OkgvError
from okgv.helpers import EXIT_FAILURE, EXIT_USAGE, err
from okgv.session import Session


class OkgvGroup(click.Group):
    """Click group that converts uncaught exceptions into structured JSON errors.

    Keeps the CLI's contract: errors are always {error, detail, suggestion}
    on stderr with a meaningful exit code, never a Python traceback.
    """

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except (click.exceptions.Exit, click.ClickException, click.Abort):
            raise
        except OkgvError as e:
            err(e.code, detail=str(e), suggestion=e.suggestion, exit_code=e.exit_code)
        except sqlite3.IntegrityError as e:
            err("constraint_violation", detail=str(e), exit_code=EXIT_USAGE)
        except Exception as e:
            err("unexpected_error", detail=f"{type(e).__name__}: {e}", exit_code=EXIT_FAILURE)


@click.group(
    cls=OkgvGroup,
    help="Knowledge base CLI for AI agents. All output is JSON to stdout, logs to stderr.",
)
@click.version_option(package_name="okgv")
@click.pass_context
def cli(ctx):
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(Path.cwd() / ".env")
    if ctx.obj is None:
        ctx.obj = Session()
    ctx.call_on_close(ctx.obj.close)

    # Session-start drift check: specs live in memory keyed by topic path, so a
    # DB whose topics no longer match the structure file would validate against
    # stale constraints. Advisory only (stderr), never fatal, and a no-op unless
    # both the structure file and the DB already exist.
    try:
        ctx.obj.check_structure_consistency()
    except Exception:
        pass


for _command in all_commands:
    cli.add_command(_command)


if __name__ == "__main__":
    cli()
