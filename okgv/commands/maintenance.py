"""Maintenance commands: submission log, undo, reconcile, purge."""

from datetime import UTC

import click

from okgv.core import (
    log_count,
    log_get_entries_after,
    log_query,
    log_remove_entries,
    review_remove_entries,
)
from okgv.helpers import EXIT_NOT_FOUND, EXIT_USAGE, err, log, output
from okgv.session import Session


@click.command(name="log")
@click.option("--topic", default=None, help="Filter by topic path.")
@click.option("--after", default=None, help="Show entries after this ISO timestamp.")
@click.option("--before", default=None, help="Show entries before this ISO timestamp.")
@click.option("--limit", default=20, show_default=True, help="Max entries to return.")
@click.option("--offset", default=0, help="Skip first N entries.")
@click.option(
    "--count",
    is_flag=True,
    default=False,
    help="Show counts instead of entries. Groups by topic if no --topic.",
)
@click.pass_obj
def log_cmd(
    session: Session,
    topic: str | None,
    after: str | None,
    before: str | None,
    limit: int,
    offset: int,
    count: bool,
):
    """Query the submission log."""
    from datetime import datetime

    db_path = session.db_path
    if not db_path.exists():
        err(
            "no_db",
            detail="okgv.db not found — no submissions yet",
            exit_code=EXIT_NOT_FOUND,
        )

    def _parse_ts(val: str, name: str) -> datetime:
        """Parse user input as local time, convert to UTC for querying."""
        try:
            ts = datetime.fromisoformat(val)
        except ValueError:
            err(
                "invalid_timestamp",
                detail=f"Bad --{name} value: {val}",
                suggestion="Use ISO 8601 format",
                exit_code=EXIT_USAGE,
            )
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return ts.astimezone(UTC)

    def _to_local(utc_str: str) -> str:
        """Convert stored UTC timestamp to local time for display."""
        ts = datetime.fromisoformat(utc_str).astimezone()
        return ts.isoformat()

    after_dt = _parse_ts(after, "after") if after else None
    before_dt = _parse_ts(before, "before") if before else None

    if count:
        output(log_count(db_path, topic=topic, group_by_topic=topic is None))
    else:
        entries = log_query(
            db_path,
            topic=topic,
            after=after_dt,
            before=before_dt,
            limit=limit,
            offset=offset,
        )
        for e in entries:
            e["timestamp"] = _to_local(e["timestamp"])
        output(entries)


@click.command()
@click.argument("timestamp")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Preview entries that would be deleted.",
)
@click.pass_obj
def undo(session: Session, timestamp: str, dry_run: bool):
    """Delete all entries submitted after TIMESTAMP from both DBs and log."""
    from datetime import datetime

    db_path = session.db_path
    try:
        cutoff = datetime.fromisoformat(timestamp)
    except ValueError as e:
        err(
            "invalid_timestamp",
            detail=str(e),
            suggestion="Use ISO 8601 format",
            exit_code=EXIT_USAGE,
        )

    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=datetime.now().astimezone().tzinfo)
    cutoff = cutoff.astimezone(UTC)

    if not db_path.exists():
        err("no_db", detail="okgv.db not found", exit_code=EXIT_NOT_FOUND)

    ids_to_delete = log_get_entries_after(db_path, cutoff)

    if not ids_to_delete:
        output({"deleted": [], "count": 0})
        return

    if dry_run:
        output(
            {
                "dry_run": True,
                "would_delete": ids_to_delete,
                "count": len(ids_to_delete),
            }
        )
        return

    log(f"Deleting {len(ids_to_delete)} entries...")
    session.vector_db.delete_by_ids(ids_to_delete)
    session.graph_db.delete_entries(ids_to_delete)
    log_remove_entries(db_path, ids_to_delete)
    review_remove_entries(db_path, ids_to_delete)

    output({"deleted": ids_to_delete, "count": len(ids_to_delete)})


@click.command()
@click.option("--dry-run", is_flag=True, default=False, help="Preview without deleting orphans.")
@click.option(
    "--batch-size",
    default=1000,
    show_default=True,
    help="Chunk size for iterating entry IDs.",
)
@click.pass_obj
def reconcile(session: Session, dry_run: bool, batch_size: int):
    """Find and fix entries that exist in graph but not vector, or vice versa."""
    graph_db = session.graph_db
    vector_db = session.vector_db

    # Find graph-only orphans: iterate graph, check existence in vector
    graph_only = []
    log("Scanning graph DB for orphans...")
    for chunk in graph_db.iter_entry_ids(batch_size):
        existing_in_vector = vector_db.exists_batch(chunk)
        graph_only.extend(eid for eid in chunk if eid not in existing_in_vector)

    # Find vector-only orphans: iterate vector, check existence in graph
    vector_only = []
    log("Scanning vector DB for orphans...")
    for chunk in vector_db.iter_entry_ids(batch_size):
        existing_in_graph = graph_db.exists_batch(chunk)
        vector_only.extend(eid for eid in chunk if eid not in existing_in_graph)

    if not graph_only and not vector_only:
        output({"consistent": True, "orphans": 0})
        return

    if dry_run:
        output(
            {
                "dry_run": True,
                "graph_only": sorted(graph_only),
                "vector_only": sorted(vector_only),
                "orphans": len(graph_only) + len(vector_only),
            }
        )
        return

    if graph_only:
        log(f"Deleting {len(graph_only)} orphan(s) from graph DB...")
        graph_db.delete_entries(graph_only)
    if vector_only:
        log(f"Deleting {len(vector_only)} orphan(s) from vector DB...")
        vector_db.delete_by_ids(vector_only)

    output(
        {
            "consistent": True,
            "deleted_from_graph": sorted(graph_only),
            "deleted_from_vector": sorted(vector_only),
            "orphans": len(graph_only) + len(vector_only),
        }
    )


@click.command(hidden=True)
@click.option("--confirm", default=None, help="Type 'delete all' to confirm.")
@click.option("--dry-run", is_flag=True, default=False, help="Preview what would be deleted.")
@click.pass_obj
def purge(session: Session, confirm: str | None, dry_run: bool):
    """Delete ALL entries from graph DB, vector DB, and log. Hidden command."""
    db_path = session.db_path

    if not dry_run and confirm != "delete all":
        err(
            "bad_confirm",
            detail="Pass --confirm 'delete all' to proceed",
            exit_code=EXIT_USAGE,
        )

    if not db_path.exists():
        output({"purged": True, "message": "No database found"})
        return

    import os
    import sqlite3

    if dry_run:
        try:
            graph_db = session.graph_db
            vector_db = session.vector_db
            vector_count = sum(len(chunk) for chunk in vector_db.iter_entry_ids())
            graph_count = sum(len(chunk) for chunk in graph_db.iter_entry_ids())
            topic_count = graph_db.count_topics()
        except sqlite3.OperationalError:
            graph_count = vector_count = topic_count = -1
        output(
            {
                "dry_run": True,
                "db_path": str(db_path),
                "graph_entries": graph_count,
                "graph_topics": topic_count,
                "vector_entries": vector_count,
                "db_corrupt": graph_count == -1,
            }
        )
        return

    log("Closing connection and removing database files...")
    session.close()

    for suffix in ("", "-shm", "-wal"):
        f = db_path.parent / (db_path.name + suffix)
        if f.exists():
            os.remove(f)

    output({"purged": True})


commands = (log_cmd, undo, reconcile, purge)
