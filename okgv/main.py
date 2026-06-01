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

from okgv.core import EntryError, build_entry, log_get_entries_after, log_remove_entries, log_session, upsert_entry
from okgv.helpers import EXIT_NOT_FOUND, EXIT_USAGE, err, log, output, read_raw
from okgv.protocols import entry_id
from okgv.session import Session


@click.group(
    help="Knowledge base CLI for AI agents. All output is JSON to stdout, logs to stderr."
)
@click.pass_context
def cli(ctx):
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
    """
    from collections import Counter

    graph_db = session.graph_db
    entries = graph_db.get_entries_for_topic(topic)
    if not entries:
        err(
            "no_entries_in_topic",
            detail=f"Topic '{topic}' has no entries",
            exit_code=EXIT_NOT_FOUND,
        )

    if fields:
        group_fields = [f.strip() for f in fields.split(",")]
        all_keys = set()
        for e in entries:
            all_keys.update(e.properties.keys())
        missing = set(group_fields) - all_keys
        if missing:
            err(
                "unknown_fields",
                detail=f"Fields not found in entries: {missing}",
                suggestion=f"Available fields: {sorted(all_keys)}",
                exit_code=EXIT_USAGE,
            )
    else:
        all_keys = set()
        for e in entries:
            all_keys.update(e.properties.keys())
        group_fields = sorted(all_keys)

    counter: Counter = Counter()
    for e in entries:
        key = tuple(
            (f, e.properties.get(f)) for f in group_fields
        )
        counter[key] += 1

    groups = []
    for combo, count in counter.most_common():
        groups.append({
            "fields": dict(combo),
            "count": count,
        })

    output({
        "topic": topic,
        "total_entries": len(entries),
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
        err("missing_field", detail=str(e), exit_code=EXIT_USAGE)

    graph_db = session.graph_db
    vector_db = session.vector_db
    log(f"Fetching entry IDs for topic '{topic}'...")
    topic_ids = graph_db.get_entry_ids_for_topic(topic)
    if not topic_ids:
        err(
            "no_entries_in_topic",
            detail=f"Topic '{topic}' has no entries",
            suggestion="Check topic name or run least-topic to list topics",
            exit_code=EXIT_NOT_FOUND,
        )
    log("Loading embedding model...")
    embedder = session.embedder
    vector = embedder([schema.embedding_text(entry_obj)])[0]
    log(f"Searching top-{top_k} similar entries in topic '{topic}'...")
    matches = vector_db.get_top_n(vector, n=top_k, filter_ids=topic_ids)

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
@click.pass_obj
def submit(session: Session, topic: str, entry: str, overwrite: bool):
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
        err("missing_field", detail=str(e), exit_code=EXIT_USAGE)
    log_session(session.log_db, topic, [eid])
    output({"id": eid, "submitted": True})


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

    graph_db = session.graph_db
    vector_db = session.vector_db
    log(f"Fetching entry IDs for topic '{topic}'...")
    topic_ids = graph_db.get_entry_ids_for_topic(topic)
    if not topic_ids:
        err(
            "no_entries_in_topic",
            detail=f"Topic '{topic}' has no entries",
            suggestion="Check topic name or run least-topic to list topics",
            exit_code=EXIT_NOT_FOUND,
        )
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
            matches = vector_db.get_top_n(vector, n=top_k, filter_ids=topic_ids)
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
@click.pass_obj
def submit_batch(session: Session, topic: str, entries: str, overwrite: bool):
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
    # Build entries, skipping bad ones
    valid = []
    results = []
    for i, raw in enumerate(rows):
        try:
            entry_obj = build_entry(schema, raw)
        except EntryError as e:
            log(f"[{i + 1}/{len(rows)}] Skipping bad entry: {e}")
            results.append({"id": entry_id(raw), "submitted": False, "error": str(e)})
            continue
        valid.append((i, raw, entry_obj))

    if valid:
        texts = [schema.embedding_text(e) for _, _, e in valid]
        vectors = session.embedder(texts)

        inserted_ids = []
        for (i, raw, _), vec in zip(valid, vectors):
            log(f"[{i + 1}/{len(rows)}] Upserting entry into topic '{topic}'...")
            try:
                eid = upsert_entry(
                    schema, session.graph_db, session.vector_db, topic, raw,
                    session.embedder, overwrite=overwrite, vector=vec,
                )
            except (EntryError, ValueError) as e:
                log(f"[{i + 1}/{len(rows)}] Failed: {e}")
                results.append({"id": entry_id(raw), "submitted": False, "error": str(e)})
                continue
            inserted_ids.append(eid)
            results.append({"id": eid, "submitted": True})
        if inserted_ids:
            log_session(session.log_db, topic, inserted_ids)

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
    graph_db = session.graph_db
    graph_db.move_entry(entry_id, destination)
    output({"id": entry_id, "moved_to": destination})


@cli.command(name="get-by-topic")
@click.option("--topic", required=True, help="Topic name to fetch entries from.")
@click.option("--limit", default=3, show_default=True, help="Max entries to return.")
@click.pass_obj
def get_by_topic(session: Session, topic: str, limit: int):
    """Fetch sample entries for a topic from vector DB."""
    graph_db = session.graph_db
    vector_db = session.vector_db
    topic_ids = graph_db.get_entry_ids_for_topic(topic)
    if not topic_ids:
        err(
            "no_entries_in_topic",
            detail=f"Topic '{topic}' has no entries",
            suggestion="Check topic name or run least-topic to list topics",
            exit_code=EXIT_NOT_FOUND,
        )
    entries = vector_db.get_by_ids(topic_ids[:limit])
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
        cutoff = cutoff.replace(tzinfo=timezone.utc)

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
    deleted = []
    failed_at = None
    for i, uid in enumerate(ids_to_delete):
        log(f"[{i + 1}/{len(ids_to_delete)}] Deleting {uid}...")
        graph_db.delete_entries([uid])
        try:
            vector_db.delete_by_id(uid)
        except Exception as e:
            failed_at = {"id": uid, "error": str(e), "deleted_so_far": deleted}
            log(f"Vector DB delete failed for {uid}: {e}")
            break
        deleted.append(uid)

    # Remove successfully deleted entries from log
    if deleted:
        log_remove_entries(log_db, deleted)

    if failed_at:
        err(
            "undo_partial_failure",
            detail=f"Vector DB failed on entry '{failed_at['id']}': {failed_at['error']}. "
            f"Entry was deleted from graph but remains in vector DB. "
            f"{len(deleted)} entries fully deleted before failure.",
            suggestion="Re-run undo to retry. The failed entry's graph node is already gone.",
            exit_code=1,
        )

    output({"deleted": deleted, "count": len(deleted)})


@cli.command()
@click.option("--dry-run", is_flag=True, default=False, help="Preview without deleting orphans.")
@click.pass_obj
def reconcile(session: Session, dry_run: bool):
    """Find and fix entries that exist in one DB but not the other."""
    graph_db = session.graph_db
    vector_db = session.vector_db

    log("Fetching all entry IDs from graph DB...")
    graph_ids = set(graph_db.get_all_entry_ids())
    log("Fetching all entry IDs from vector DB...")
    vector_ids = set(vector_db.get_all_entry_ids())

    graph_only = graph_ids - vector_ids
    vector_only = vector_ids - graph_ids

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
        graph_db.delete_entries(list(graph_only))
    if vector_only:
        log(f"Deleting {len(vector_only)} orphan(s) from vector DB...")
        for uid in vector_only:
            vector_db.delete_by_id(uid)

    output({
        "consistent": True,
        "deleted_from_graph": sorted(graph_only),
        "deleted_from_vector": sorted(vector_only),
        "orphans": len(graph_only) + len(vector_only),
    })


if __name__ == "__main__":
    cli()
