"""
CLI for AI agents to interact with the self-organized knowledge base.

Schema discovery (see config.py):
  1. OKGV_SCHEMA env var →  "module:ClassName"
  2. Built-in QAEntrySchema fallback

Exit codes:  0=ok  1=failure  2=usage  3=not_found  4=connection
"""

import json
import sys

import click

from okgv.core import (
    EntryError, build_entry,
    log_count, log_get_entries_after, log_query, log_remove_entries, log_session,
    review_add, review_clear, review_count, review_get_rejected, review_list,
    review_purge_rejected, review_remove_entries, review_update,
    upsert_entries_batch, upsert_entry,
)
from okgv.helpers import EXIT_NOT_FOUND, EXIT_USAGE, err, log, output, read_raw
from okgv.protocols import entry_id
from okgv.session import Session


@click.group(
    help="Knowledge base CLI for AI agents. All output is JSON to stdout, logs to stderr."
)
@click.pass_context
def cli(ctx):
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(Path.cwd() / ".env")
    if ctx.obj is None:
        ctx.obj = Session()
    ctx.call_on_close(ctx.obj.close)


@cli.command(name="master-prompt")
def master_prompt():
    """Print agent instructions for using the CLI."""
    from importlib.resources import files

    templates = files("okgv.templates")
    click.echo(templates.joinpath("prompt.md").read_text())


@cli.command()
def init():
    """Initialize current directory with okgv scaffold files."""
    from importlib.resources import files
    from pathlib import Path

    templates = files("okgv.templates")
    cwd = Path.cwd()
    created = []

    scaffold = [
        ("env.txt", ".env"),
        ("schema.py.txt", "schema.py"),
        ("topics.json", "topics.json"),
    ]

    for template_name, target_name in scaffold:
        target = cwd / target_name
        if not target.exists():
            content = templates.joinpath(template_name).read_text()
            target.write_text(content)
            created.append(target_name)

    if created:
        output({"initialized": True, "created": created})
    else:
        output({"initialized": False, "message": "All files already exist", "created": []})


@cli.command(name="get-structure")
@click.option("--root", default=None, help="Start from this topic path. Default: full tree.")
@click.option("--depth", default=None, type=int, help="Max nesting levels to return. Default: unlimited.")
@click.pass_obj
def get_structure(session: Session, root: str | None, depth: int | None):
    """Return the topic/subtopic tree as nested JSON (no entries)."""
    tree = session.graph_db.get_topic_tree(root=root, max_depth=depth)
    if not tree:
        if root:
            err("not_found", detail=f"Topic '{root}' not found or has no subtopics", exit_code=EXIT_NOT_FOUND)
        else:
            err("no_topics", detail="No topics found in graph", exit_code=EXIT_NOT_FOUND)
    output(tree)


@cli.command(name="get-depth")
@click.option("--root", default=None, help="Start from this topic path. Default: full tree.")
@click.pass_obj
def get_depth(session: Session, root: str | None):
    """Return the maximum depth of the topic tree."""
    tree = session.graph_db.get_topic_tree(root=root, max_depth=1)
    if not tree:
        if root:
            err("not_found", detail=f"Topic '{root}' not found", exit_code=EXIT_NOT_FOUND)
        else:
            err("no_topics", detail="No topics found in graph", exit_code=EXIT_NOT_FOUND)
    depth = session.graph_db.get_topic_depth(root=root)
    result = {"depth": depth}
    if root:
        result["root"] = root
    output(result)


@cli.command(name="create-topic")
@click.option("--name", required=True, help="Topic path to create (e.g. 'algebra/linear_algebra').")
@click.option("--parents", is_flag=True, default=False, help="Create missing parent topics.")
@click.pass_obj
def create_topic(session: Session, name: str, parents: bool):
    """Create a topic node in the graph DB. Accepts paths.

    Without --parents: errors if parent topics don't exist.
    With --parents: creates all missing intermediate levels (like mkdir -p).
    """
    graph_db = session.graph_db
    segments = name.split("/")

    if len(segments) == 1:
        graph_db.create_topic(name)
    else:
        for i, segment in enumerate(segments):
            if i == 0:
                if not graph_db.topic_exists(segment):
                    if not parents:
                        err(
                            "parent_not_found",
                            detail=f"Root topic '{segment}' does not exist",
                            suggestion="Use --parents to create missing levels",
                            exit_code=EXIT_NOT_FOUND,
                        )
                    graph_db.create_topic(segment)
            else:
                parent_path = "/".join(segments[:i])
                if not graph_db.topic_exists(parent_path):
                    if not parents:
                        err(
                            "parent_not_found",
                            detail=f"Parent topic '{parent_path}' does not exist",
                            suggestion="Use --parents to create missing levels",
                            exit_code=EXIT_NOT_FOUND,
                        )
                graph_db.create_subtopic(parent_path, segment)

    output({"topic": name, "created": True})


@cli.command(name="least-topic")
@click.option(
    "--topic",
    default=None,
    help="Parent topic path. Compares its direct children. Default: root topics.",
)
@click.pass_obj
def least_topic(session: Session, topic: str | None):
    """Return the child topic with the fewest entries."""
    graph_db = session.graph_db
    counts = graph_db.get_topic_entry_counts(parent=topic)
    if not counts:
        if topic:
            err(
                "no_subtopics",
                detail=f"Topic '{topic}' has no subtopics",
                exit_code=EXIT_NOT_FOUND,
            )
        else:
            err(
                "no_topics",
                detail="No topics found in graph",
                exit_code=EXIT_NOT_FOUND,
            )
    least = min(counts, key=lambda t: counts[t])
    output({"topic": least, "count": counts[least], "all_counts": counts})


@cli.command(name="topic-stats")
@click.option("--topic", required=True, help="Topic path to analyze.")
@click.option(
    "--fields",
    default=None,
    help="Comma-separated metadata fields to group by. Default: all metadata fields.",
)
@click.pass_obj
def topic_stats(session: Session, topic: str, fields: str | None):
    """Show entry counts grouped by metadata field combinations for a topic.

    Groups entries by their metadata values and shows counts per combination,
    helping identify underrepresented combinations.
    Aggregation is performed in the database, not in Python.
    """
    graph_db = session.graph_db
    field_list = [f.strip() for f in fields.split(",")] if fields else None

    try:
        total, group_fields, groups = graph_db.get_topic_stats(topic, field_list)
    except ValueError as e:
        err("invalid_field", detail=str(e), exit_code=EXIT_USAGE)

    if total == 0:
        err(
            "no_entries_in_topic",
            detail=f"Topic '{topic}' has no entries",
            exit_code=EXIT_NOT_FOUND,
        )

    output({
        "topic": topic,
        "total_entries": total,
        "group_by": group_fields,
        "groups": groups,
    })


@cli.command()
@click.option("--topic", required=True, help="Topic to restrict similarity search to.")
@click.option(
    "--entry", required=True, help='Entry JSON string, or "-" to read from stdin.'
)
@click.option(
    "--top-k", default=5, show_default=True, help="Number of similar entries to return."
)
@click.pass_obj
def similar(session: Session, topic: str, entry: str, top_k: int):
    """Get top-N most similar entries within a topic, with full content."""
    raw = read_raw(entry)
    schema = session.schema
    try:
        entry_obj = build_entry(schema, raw)
    except EntryError as e:
        return err("missing_field", detail=str(e), exit_code=EXIT_USAGE)

    vector_db = session.vector_db
    log("Loading embedding model...")
    vector = session.embedder([schema.embedding_text(entry_obj)])[0]
    log(f"Searching top-{top_k} similar entries in topic '{topic}'...")
    matches = vector_db.get_top_n(vector, n=top_k, filter_topic=topic)

    match_ids = [uid for uid, _ in matches]
    certainties = {uid: cert for uid, cert in matches}
    fetched = {r.id: r for r in vector_db.get_by_ids(match_ids)} if match_ids else {}

    results = []
    for uid in match_ids:
        item: dict = {"id": uid, "certainty": certainties[uid]}
        if uid in fetched:
            item["properties"] = fetched[uid].properties
        results.append(item)

    output({"candidate_id": entry_id(raw), "similar": results})


@cli.command()
@click.option("--topic", required=True, help="Target topic name.")
@click.option(
    "--entry", required=True, help='Entry JSON string, or "-" to read from stdin.'
)
@click.option("--overwrite", is_flag=True, default=False, help="Overwrite if entry already exists in vector DB.")
@click.option("--review/--no-review", default=None, help="Flag entry for review. Default: uses OKGV_REVIEW env var.")
@click.pass_obj
def submit(session: Session, topic: str, entry: str, overwrite: bool, review: bool | None):
    """Upsert entry into both graph and vector DBs."""
    schema = session.schema
    raw = read_raw(entry)

    log("Loading embedding model...")
    log(f"Upserting entry into topic '{topic}'...")
    try:
        eid = upsert_entry(
            schema, session.graph_db, session.vector_db, topic, raw,
            session.embedder, overwrite=overwrite,
        )
    except EntryError as e:
        return err("missing_field", detail=str(e), exit_code=EXIT_USAGE)
    log_session(session.log_db, topic, [eid])
    needs_review = review if review is not None else session.review_enabled
    if needs_review:
        review_add(session.review_db, topic, [eid])
    output({"id": eid, "submitted": True, "review": needs_review})


@cli.command(name="similar-batch")
@click.option("--topic", required=True, help="Topic to restrict similarity search to.")
@click.option(
    "--entries",
    required=True,
    help='JSON array of entry objects, or "-" to read from stdin.',
)
@click.option(
    "--top-k",
    default=5,
    show_default=True,
    help="Number of similar entries per candidate.",
)
@click.pass_obj
def similar_batch(session: Session, topic: str, entries: str, top_k: int):
    """Get top-N similar entries for each candidate in a batch. Single model load."""
    schema = session.schema
    if entries == "-":
        raw_str = sys.stdin.read()
    else:
        raw_str = entries
    try:
        rows = json.loads(raw_str)
    except json.JSONDecodeError as e:
        err("invalid_json", detail=str(e), exit_code=EXIT_USAGE)
    if not isinstance(rows, list):
        err(
            "invalid_input",
            detail="Expected a JSON array of entries",
            exit_code=EXIT_USAGE,
        )

    vector_db = session.vector_db
    log(f"Loading embedding model and embedding {len(rows)} candidates...")
    # Build entries, skipping bad ones
    valid = []
    results_all = []
    for i, raw in enumerate(rows):
        try:
            entry_obj = build_entry(schema, raw)
        except EntryError as e:
            log(f"[{i + 1}/{len(rows)}] Skipping bad entry: {e}")
            results_all.append({"candidate_id": entry_id(raw), "error": str(e)})
            continue
        valid.append((i, raw, entry_obj))

    if valid:
        texts = [schema.embedding_text(e) for _, _, e in valid]
        vectors = session.embedder(texts)

        for (i, raw, _), vector in zip(valid, vectors):
            log(f"[{i + 1}/{len(rows)}] Searching top-{top_k} similar for candidate...")
            matches = vector_db.get_top_n(vector, n=top_k, filter_topic=topic)
            match_ids = [uid for uid, _ in matches]
            certainties = {uid: cert for uid, cert in matches}
            fetched = {r.id: r for r in vector_db.get_by_ids(match_ids)} if match_ids else {}
            results = []
            for uid in match_ids:
                item: dict = {"id": uid, "certainty": certainties[uid]}
                if uid in fetched:
                    item["properties"] = fetched[uid].properties
                results.append(item)
            results_all.append({"candidate_id": entry_id(raw), "similar": results})

    output(results_all)


@cli.command(name="submit-batch")
@click.option("--topic", required=True, help="Target topic name.")
@click.option(
    "--entries",
    required=True,
    help='JSON array of entry objects, or "-" to read from stdin.',
)
@click.option("--overwrite", is_flag=True, default=False, help="Overwrite if entries already exist in vector DB.")
@click.option("--review/--no-review", default=None, help="Flag entries for review. Default: uses OKGV_REVIEW env var.")
@click.pass_obj
def submit_batch(session: Session, topic: str, entries: str, overwrite: bool, review: bool | None):
    """Upsert multiple entries into graph and vector DBs. Single model load."""
    schema = session.schema
    if entries == "-":
        raw_str = sys.stdin.read()
    else:
        raw_str = entries
    try:
        rows = json.loads(raw_str)
    except json.JSONDecodeError as e:
        err("invalid_json", detail=str(e), exit_code=EXIT_USAGE)
    if not isinstance(rows, list):
        err(
            "invalid_input",
            detail="Expected a JSON array of entries",
            exit_code=EXIT_USAGE,
        )

    log(f"Loading embedding model and embedding {len(rows)} entries...")
    # Build entries once, skipping bad ones
    valid_raws = []
    valid_entries = []
    results = []
    for i, raw in enumerate(rows):
        try:
            entry_obj = build_entry(schema, raw)
        except EntryError as e:
            log(f"[{i + 1}/{len(rows)}] Skipping bad entry: {e}")
            results.append({"id": entry_id(raw), "submitted": False, "error": str(e)})
            continue
        valid_raws.append(raw)
        valid_entries.append(entry_obj)

    if valid_raws:
        texts = [schema.embedding_text(e) for e in valid_entries]
        vectors = session.embedder(texts)

        log(f"Batch upserting {len(valid_raws)} entries into topic '{topic}'...")
        inserted_ids, failures = upsert_entries_batch(
            schema, session.graph_db, session.vector_db, topic,
            valid_raws, valid_entries, vectors, overwrite=overwrite,
        )
        for eid in inserted_ids:
            results.append({"id": eid, "submitted": True})
        for f in failures:
            results.append({"id": f["id"], "submitted": False, "error": f["error"]})
        if inserted_ids:
            log_session(session.log_db, topic, inserted_ids)
            needs_review = review if review is not None else session.review_enabled
            if needs_review:
                review_add(session.review_db, topic, inserted_ids)

    output(results)


@cli.command(name="create-structure")
@click.option(
    "--file",
    "file_path",
    required=True,
    help='Path to JSON file defining topic hierarchy, or "-" for stdin.',
)
@click.pass_obj
def create_structure(session: Session, file_path: str):
    """Create topic/subtopic tree from a JSON file.

    Expected format: nested dict where keys are topic names, values are dicts of subtopics.
    Example: {"algebra": {"linear_algebra": {"basics": {}, "advanced": {}}, "abstract_algebra": {}}}
    """
    if file_path == "-":
        raw_str = sys.stdin.read()
    else:
        from pathlib import Path

        p = Path(file_path)
        if not p.exists():
            err(
                "file_not_found",
                detail=f"File '{file_path}' not found",
                exit_code=EXIT_USAGE,
            )
        raw_str = p.read_text()

    try:
        structure = json.loads(raw_str)
    except json.JSONDecodeError as e:
        err("invalid_json", detail=str(e), exit_code=EXIT_USAGE)
    if not isinstance(structure, dict):
        err(
            "invalid_input",
            detail="Expected a JSON object (nested dict)",
            exit_code=EXIT_USAGE,
        )

    graph_db = session.graph_db
    created = []
    stack: list[tuple[dict, str | None]] = [(structure, None)]

    while stack:
        tree, parent = stack.pop()
        for name, children in tree.items():
            if parent is None:
                graph_db.create_topic(name)
                path = name
            else:
                graph_db.create_subtopic(parent, name)
                path = f"{parent}/{name}"
            created.append(path)
            if isinstance(children, dict) and children:
                stack.append((children, path))

    output({"created_topics": created, "count": len(created)})


@cli.command(name="move-topic")
@click.option("--source", required=True, help="Path of topic/subtopic to move.")
@click.option("--destination", required=True, help="Path of new parent topic.")
@click.option("--dry-run", is_flag=True, default=False, help="Preview without applying changes.")
@click.pass_obj
def move_topic(session: Session, source: str, destination: str, dry_run: bool):
    """Move a topic/subtopic under a different parent. Blocked if name conflict."""
    name = source.rsplit("/", 1)[-1]
    new_path = f"{destination}/{name}"
    if dry_run:
        output({"dry_run": True, "would_move": source, "new_path": new_path})
        return
    graph_db = session.graph_db
    try:
        graph_db.move_topic(source, destination)
    except ValueError as e:
        err("name_conflict", detail=str(e), exit_code=EXIT_USAGE)
    session.vector_db.update_topics(source, new_path)
    output({"moved": source, "new_path": new_path})


@cli.command(name="move-entry")
@click.option("--id", "entry_id", required=True, help="Entry UUID to move.")
@click.option("--destination", required=True, help="Path of target topic.")
@click.option("--dry-run", is_flag=True, default=False, help="Preview without applying changes.")
@click.pass_obj
def move_entry(session: Session, entry_id: str, destination: str, dry_run: bool):
    """Move an entry to a different topic."""
    if dry_run:
        output({"dry_run": True, "would_move": entry_id, "destination": destination})
        return
    session.graph_db.move_entry(entry_id, destination)
    session.vector_db.update_entry_topic(entry_id, destination)
    output({"id": entry_id, "moved_to": destination})


@cli.command(name="get-by-topic")
@click.option("--topic", required=True, help="Topic name to fetch entries from.")
@click.option("--limit", default=3, show_default=True, help="Max entries to return.")
@click.pass_obj
def get_by_topic(session: Session, topic: str, limit: int):
    """Fetch sample entries for a topic from vector DB."""
    entries = session.vector_db.get_by_topic(topic, limit)
    if not entries:
        err(
            "no_entries_in_topic",
            detail=f"Topic '{topic}' has no entries",
            suggestion="Check topic name or run least-topic to list topics",
            exit_code=EXIT_NOT_FOUND,
        )
    output([{"id": e.id, **e.properties} for e in entries])


@cli.command(name="get-vector")
@click.option("--id", "entry_id", required=True, help="Entry UUID to fetch.")
@click.pass_obj
def get_vector(session: Session, entry_id: str):
    """Fetch entry from vector DB by ID."""
    vector_db = session.vector_db
    matched = vector_db.get_by_id(entry_id)
    if matched is None:
        err(
            "not_found",
            detail=f"No entry with id '{entry_id}' in vector DB",
            exit_code=EXIT_NOT_FOUND,
        )
    output({"id": matched.id, **matched.properties})


@cli.command(name="get-graph")
@click.option("--id", "entry_id", required=True, help="Entry UUID to fetch.")
@click.pass_obj
def get_graph(session: Session, entry_id: str):
    """Fetch entry from graph DB by ID."""
    graph_db = session.graph_db
    matched = graph_db.get_by_id(entry_id)
    if matched is None:
        err(
            "not_found",
            detail=f"No entry with id '{entry_id}' in graph DB",
            exit_code=EXIT_NOT_FOUND,
        )
    output({"id": matched.id, "topic": matched.topic, **matched.properties})


@cli.command(name="review")
@click.option("--topic", default=None, help="Filter by topic path.")
@click.option("--status", default="pending", show_default=True, help="Filter by status: pending, approved, rejected.")
@click.option("--limit", default=20, show_default=True, help="Max entries to return.")
@click.option("--offset", default=0, help="Skip first N entries.")
@click.option("--count", is_flag=True, default=False, help="Show counts by status.")
@click.option("--purge-rejected", is_flag=True, default=False, help="Delete rejected entries from all DBs.")
@click.option("--dry-run", is_flag=True, default=False, help="Preview purge without deleting.")
@click.pass_obj
def review_cmd(session: Session, topic: str | None, status: str, limit: int, offset: int, count: bool, purge_rejected: bool, dry_run: bool):
    """Query the review queue or purge rejected entries."""
    review_db = session.review_db

    if purge_rejected:
        rejected_ids = review_get_rejected(review_db)
        if not rejected_ids:
            output({"purged": 0})
            return
        if dry_run:
            output({"dry_run": True, "would_delete": rejected_ids, "count": len(rejected_ids)})
            return
        log(f"Deleting {len(rejected_ids)} rejected entries from vector DB...")
        session.vector_db.delete_by_ids(rejected_ids)
        log(f"Deleting {len(rejected_ids)} rejected entries from graph DB...")
        session.graph_db.delete_entries(rejected_ids)
        log_remove_entries(session.log_db, rejected_ids)
        review_purge_rejected(review_db)
        output({"purged": len(rejected_ids), "ids": rejected_ids})
        return

    if count:
        output(review_count(review_db, topic=topic))
    else:
        entries = review_list(review_db, status=status, topic=topic, limit=limit, offset=offset)
        output(entries)


@cli.command()
@click.option("--id", "entry_id", required=True, help="Entry UUID to approve.")
@click.pass_obj
def approve(session: Session, entry_id: str):
    """Mark entry as approved in the review queue."""
    updated = review_update(session.review_db, [entry_id], "approved")
    if updated == 0:
        err("not_found", detail=f"Entry '{entry_id}' not in review queue", exit_code=EXIT_NOT_FOUND)
    output({"id": entry_id, "status": "approved"})


@cli.command()
@click.option("--id", "entry_id", required=True, help="Entry UUID to reject.")
@click.pass_obj
def reject(session: Session, entry_id: str):
    """Mark entry as rejected in the review queue."""
    updated = review_update(session.review_db, [entry_id], "rejected")
    if updated == 0:
        err("not_found", detail=f"Entry '{entry_id}' not in review queue", exit_code=EXIT_NOT_FOUND)
    output({"id": entry_id, "status": "rejected"})


@cli.command(name="log")
@click.option("--topic", default=None, help="Filter by topic path.")
@click.option("--after", default=None, help="Show entries after this ISO timestamp.")
@click.option("--before", default=None, help="Show entries before this ISO timestamp.")
@click.option("--limit", default=20, show_default=True, help="Max entries to return.")
@click.option("--offset", default=0, help="Skip first N entries.")
@click.option("--count", is_flag=True, default=False, help="Show counts instead of entries. Groups by topic if no --topic.")
@click.pass_obj
def log_cmd(session: Session, topic: str | None, after: str | None, before: str | None, limit: int, offset: int, count: bool):
    """Query the submission log."""
    from datetime import datetime, timezone

    log_db = session.log_db
    if not log_db.exists():
        err("no_log", detail="log.db not found — no submissions yet", exit_code=EXIT_NOT_FOUND)

    def _parse_ts(val: str, name: str) -> datetime:
        """Parse user input as local time, convert to UTC for querying."""
        try:
            ts = datetime.fromisoformat(val)
        except ValueError:
            err("invalid_timestamp", detail=f"Bad --{name} value: {val}", suggestion="Use ISO 8601 format", exit_code=EXIT_USAGE)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return ts.astimezone(timezone.utc)

    def _to_local(utc_str: str) -> str:
        """Convert stored UTC timestamp to local time for display."""
        ts = datetime.fromisoformat(utc_str).astimezone()
        return ts.isoformat()

    after_dt = _parse_ts(after, "after") if after else None
    before_dt = _parse_ts(before, "before") if before else None

    if count:
        output(log_count(log_db, topic=topic, group_by_topic=topic is None))
    else:
        entries = log_query(log_db, topic=topic, after=after_dt, before=before_dt, limit=limit, offset=offset)
        for e in entries:
            e["timestamp"] = _to_local(e["timestamp"])
        output(entries)


@cli.command()
@click.argument("timestamp")
@click.option("--dry-run", is_flag=True, default=False, help="Preview entries that would be deleted.")
@click.pass_obj
def undo(session: Session, timestamp: str, dry_run: bool):
    """Delete all entries submitted after TIMESTAMP from both DBs and log."""
    from datetime import datetime, timezone

    log_db = session.log_db
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
    cutoff = cutoff.astimezone(timezone.utc)

    if not log_db.exists():
        err("no_log", detail="log.db not found", exit_code=EXIT_NOT_FOUND)

    ids_to_delete = log_get_entries_after(log_db, cutoff)

    if not ids_to_delete:
        output({"deleted": [], "count": 0})
        return

    if dry_run:
        output({"dry_run": True, "would_delete": ids_to_delete, "count": len(ids_to_delete)})
        return

    graph_db = session.graph_db
    vector_db = session.vector_db

    # Delete vector first — if it fails, graph+log are intact and undo can be retried.
    # If graph fails after vector succeeds, reconcile will clean up the orphans.
    log(f"Batch deleting {len(ids_to_delete)} entries from vector DB...")
    try:
        vector_db.delete_by_ids(ids_to_delete)
    except Exception as e:
        err(
            "undo_vector_failed",
            detail=f"Vector DB batch delete failed: {e}. No changes were made.",
            suggestion="Fix connection and re-run undo.",
            exit_code=1,
        )

    log(f"Batch deleting {len(ids_to_delete)} entries from graph DB...")
    graph_db.delete_entries(ids_to_delete)
    log_remove_entries(log_db, ids_to_delete)
    review_remove_entries(session.review_db, ids_to_delete)

    output({"deleted": ids_to_delete, "count": len(ids_to_delete)})


@cli.command()
@click.option("--dry-run", is_flag=True, default=False, help="Preview without deleting orphans.")
@click.option("--batch-size", default=1000, show_default=True, help="Chunk size for iterating entry IDs.")
@click.pass_obj
def reconcile(session: Session, dry_run: bool, batch_size: int):
    """Find and fix entries that exist in one DB but not the other.

    Uses chunked iteration to avoid loading all IDs into memory at once.
    """
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
        output({
            "dry_run": True,
            "graph_only": sorted(graph_only),
            "vector_only": sorted(vector_only),
            "orphans": len(graph_only) + len(vector_only),
        })
        return

    if graph_only:
        log(f"Deleting {len(graph_only)} orphan(s) from graph DB...")
        graph_db.delete_entries(graph_only)
    if vector_only:
        log(f"Deleting {len(vector_only)} orphan(s) from vector DB...")
        vector_db.delete_by_ids(vector_only)

    output({
        "consistent": True,
        "deleted_from_graph": sorted(graph_only),
        "deleted_from_vector": sorted(vector_only),
        "orphans": len(graph_only) + len(vector_only),
    })


@cli.command(hidden=True)
@click.option("--confirm", default=None, help="Type 'delete all' to confirm.")
@click.option("--dry-run", is_flag=True, default=False, help="Preview what would be deleted.")
@click.pass_obj
def purge(session: Session, confirm: str | None, dry_run: bool):
    """Delete ALL entries from graph DB, vector DB, and log. Hidden command."""
    if not dry_run and confirm != "delete all":
        err("bad_confirm", detail="Pass --confirm 'delete all' to proceed", exit_code=EXIT_USAGE)

    graph_db = session.graph_db
    vector_db = session.vector_db
    log_db = session.log_db

    if dry_run:
        vector_count = sum(len(chunk) for chunk in vector_db.iter_entry_ids())
        graph_count = sum(len(chunk) for chunk in graph_db.iter_entry_ids())
        topic_count = graph_db.count_topics()
        output({
            "dry_run": True,
            "graph_db": graph_db.database_name,
            "graph_entries": graph_count,
            "graph_topics": topic_count,
            "vector_db_collection": vector_db.collection_name,
            "vector_entries": vector_count,
            "log_exists": log_db.exists(),
            "review_exists": session.review_db.exists(),
        })
        return

    log("Deleting all entries from vector DB...")
    for chunk in vector_db.iter_entry_ids():
        vector_db.delete_by_ids(chunk)

    log("Deleting all nodes from graph DB (entries + topics)...")
    graph_db.delete_all()

    import os
    if log_db.exists():
        log("Clearing log DB...")
        os.remove(log_db)

    if session.review_db.exists():
        log("Clearing review DB...")
        os.remove(session.review_db)

    output({"purged": True})


if __name__ == "__main__":
    cli()
