"""Review queue commands: query, approve/reject, export/import, purge/recover."""

import json

import click

from okgv.core import (
    log_remove_entries,
    review_count,
    review_get_rejected,
    review_list,
    review_purge_rejected,
    review_update,
)
from okgv.helpers import EXIT_NOT_FOUND, EXIT_USAGE, err, log, output
from okgv.session import Session


@click.command(name="review")
@click.option("--topic", default=None, help="Filter by topic path.")
@click.option(
    "--status",
    default="pending",
    show_default=True,
    type=click.Choice(["pending", "approved", "rejected"]),
    help="Filter by status.",
)
@click.option("--limit", default=20, show_default=True, help="Max entries to return.")
@click.option("--offset", default=0, help="Skip first N entries.")
@click.option("--count", is_flag=True, default=False, help="Show counts by status.")
@click.option(
    "--export",
    "export_path",
    default=None,
    help="Export review entries with content to JSON file.",
)
@click.option(
    "--import",
    "import_path",
    default=None,
    help="Import review decisions from JSON file.",
)
@click.option(
    "--interactive",
    "-i",
    is_flag=True,
    default=False,
    help="Launch interactive terminal UI for review.",
)
@click.option(
    "--purge-rejected",
    is_flag=True,
    default=False,
    help="Delete rejected entries from all DBs.",
)
@click.option(
    "--recover-rejected",
    is_flag=True,
    default=False,
    help="Set rejected entries back to pending.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Preview purge/recover without applying.",
)
@click.pass_obj
def review_cmd(
    session: Session,
    topic: str | None,
    status: str,
    limit: int,
    offset: int,
    count: bool,
    export_path: str | None,
    import_path: str | None,
    interactive: bool,
    purge_rejected: bool,
    recover_rejected: bool,
    dry_run: bool,
):
    """Query the review queue, export/import decisions, purge or recover rejected entries."""
    db_path = session.db_path

    if interactive:
        try:
            from okgv.tui import run_tui
        except ImportError:
            err("missing_dependency", "textual is required for interactive mode: pip install okgv[tui]", exit_code=1)

        run_tui(
            db_path=db_path,
            graph_db=session.graph_db,
            vector_db=session.vector_db,
            topic=topic,
            limit=limit,
        )
        return

    if import_path:
        from pathlib import Path

        p = Path(import_path)
        if not p.exists():
            err(
                "file_not_found",
                detail=f"File '{import_path}' not found",
                exit_code=EXIT_USAGE,
            )
        try:
            rows = json.loads(p.read_text())
        except json.JSONDecodeError as e:
            err("invalid_json", detail=str(e), exit_code=EXIT_USAGE)
        if not isinstance(rows, list):
            err("invalid_input", detail="Expected a JSON array", exit_code=EXIT_USAGE)
        valid_statuses = {"approved", "rejected"}
        approved = [r["id"] for r in rows if r.get("status") == "approved"]
        rejected = [r["id"] for r in rows if r.get("status") == "rejected"]
        invalid = [
            {"id": r.get("id"), "status": r["status"]}
            for r in rows
            if "status" in r and r["status"] not in valid_statuses
        ]
        results: dict = {}
        if approved:
            review_update(db_path, approved, "approved")
            results["approved"] = len(approved)
        if rejected:
            review_update(db_path, rejected, "rejected")
            results["rejected"] = len(rejected)
        skipped = len(rows) - len(approved) - len(rejected) - len(invalid)
        if skipped:
            results["skipped"] = skipped
        if invalid:
            results["invalid"] = invalid
        output(results)
        return

    if purge_rejected:
        rejected_ids = review_get_rejected(db_path)
        if not rejected_ids:
            output({"purged": 0})
            return
        if dry_run:
            output(
                {
                    "dry_run": True,
                    "would_delete": rejected_ids,
                    "count": len(rejected_ids),
                }
            )
            return
        log(f"Deleting {len(rejected_ids)} rejected entries from vector DB...")
        session.vector_db.delete_by_ids(rejected_ids)
        log(f"Deleting {len(rejected_ids)} rejected entries from graph DB...")
        session.graph_db.delete_entries(rejected_ids)
        log_remove_entries(db_path, rejected_ids)
        review_purge_rejected(db_path)
        output({"purged": len(rejected_ids), "ids": rejected_ids})
        return

    if recover_rejected:
        rejected_ids = review_get_rejected(db_path)
        if not rejected_ids:
            output({"recovered": 0})
            return
        if dry_run:
            output(
                {
                    "dry_run": True,
                    "would_recover": rejected_ids,
                    "count": len(rejected_ids),
                }
            )
            return
        review_update(db_path, rejected_ids, "pending")
        output({"recovered": len(rejected_ids), "ids": rejected_ids})
        return

    if export_path:
        entries = review_list(db_path, status=status, topic=topic, limit=limit, offset=offset)
        if not entries:
            err(
                "no_entries",
                detail="No entries match the filter",
                exit_code=EXIT_NOT_FOUND,
            )
        entry_ids = [e["entry_id"] for e in entries]
        fetched = {r.id: r.properties for r in session.vector_db.get_by_ids(entry_ids)}
        export_data = []
        for row in entries:
            item = {"id": row["entry_id"], "status": row["status"], "topic": row["topic"]}
            if row["entry_id"] in fetched:
                item.update(fetched[row["entry_id"]])
            export_data.append(item)
        from pathlib import Path

        Path(export_path).write_text(json.dumps(export_data, indent=2, ensure_ascii=False))
        output({"exported": len(export_data), "file": export_path})
        return

    if count:
        output(review_count(db_path, topic=topic))
    else:
        entries = review_list(db_path, status=status, topic=topic, limit=limit, offset=offset)
        output(entries)


@click.command()
@click.option("--id", "entry_id", required=True, help="Entry UUID to approve.")
@click.pass_obj
def approve(session: Session, entry_id: str):
    """Mark entry as approved in the review queue."""
    updated = review_update(session.db_path, [entry_id], "approved")
    if updated == 0:
        err(
            "not_found",
            detail=f"Entry '{entry_id}' not in review queue",
            exit_code=EXIT_NOT_FOUND,
        )
    output({"id": entry_id, "status": "approved"})


@click.command()
@click.option("--id", "entry_id", required=True, help="Entry UUID to reject.")
@click.pass_obj
def reject(session: Session, entry_id: str):
    """Mark entry as rejected in the review queue."""
    updated = review_update(session.db_path, [entry_id], "rejected")
    if updated == 0:
        err(
            "not_found",
            detail=f"Entry '{entry_id}' not in review queue",
            exit_code=EXIT_NOT_FOUND,
        )
    output({"id": entry_id, "status": "rejected"})


commands = (review_cmd, approve, reject)
