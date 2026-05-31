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

from okgv.config import load_schema
from okgv.connections import connect_graph_db, connect_vector_db, get_embedder
from okgv.core import build_entry, log_session, upsert_entry
from okgv.helpers import EXIT_NOT_FOUND, EXIT_USAGE, err, log, output, read_raw
from okgv.protocols import entry_id

SCHEMA = load_schema()


@click.group(
    help="Knowledge base CLI for AI agents. All output is JSON to stdout, logs to stderr."
)
def cli():
    pass


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
def create_topic(name: str, parents: bool):
    """Create a topic node in the graph DB. Accepts paths.

    Without --parents: errors if parent topics don't exist.
    With --parents: creates all missing intermediate levels (like mkdir -p).
    """
    graph_db = connect_graph_db()
    try:
        segments = name.split("/")

        if len(segments) == 1:
            graph_db.create_topic(name)
        else:
            # Check/create each level
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
    finally:
        graph_db.close()


@cli.command(name="least-topic")
@click.option(
    "--topic",
    default=None,
    help="Parent topic path. Compares its direct children. Default: root topics.",
)
def least_topic(topic: str | None):
    """Return the child topic with the fewest entries."""
    graph_db = connect_graph_db()
    try:
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
    finally:
        graph_db.close()


@cli.command(name="topic-stats")
@click.option("--topic", required=True, help="Topic path to analyze.")
@click.option(
    "--fields",
    default=None,
    help="Comma-separated metadata fields to group by. Default: all metadata fields.",
)
def topic_stats(topic: str, fields: str | None):
    """Show entry counts grouped by metadata field combinations for a topic.

    Groups entries by their metadata values and shows counts per combination,
    helping identify underrepresented combinations.
    """
    from collections import Counter

    graph_db = connect_graph_db()
    try:
        entries = graph_db.get_entries_for_topic(topic)
        if not entries:
            err(
                "no_entries_in_topic",
                detail=f"Topic '{topic}' has no entries",
                exit_code=EXIT_NOT_FOUND,
            )

        # Determine which fields to group by
        if fields:
            group_fields = [f.strip() for f in fields.split(",")]
            # Validate fields exist in at least one entry
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
            # Use all metadata fields (intersection across entries for consistency)
            all_keys = set()
            for e in entries:
                all_keys.update(e.properties.keys())
            group_fields = sorted(all_keys)

        # Group and count
        counter: Counter = Counter()
        for e in entries:
            key = tuple(
                (f, e.properties.get(f)) for f in group_fields
            )
            counter[key] += 1

        # Format output
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
    finally:
        graph_db.close()


@cli.command()
@click.option("--topic", required=True, help="Topic to restrict similarity search to.")
@click.option(
    "--entry", required=True, help='Entry JSON string, or "-" to read from stdin.'
)
@click.option(
    "--top-k", default=5, show_default=True, help="Number of similar entries to return."
)
def similar(topic: str, entry: str, top_k: int):
    """Get top-N most similar entries within a topic, with full content."""
    raw = read_raw(entry)
    entry_obj = build_entry(SCHEMA, raw)

    graph_db = connect_graph_db()
    vector_db = connect_vector_db(SCHEMA)
    try:
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
        embedder = get_embedder()
        vector = embedder([SCHEMA.embedding_text(entry_obj)])[0]
        log(f"Searching top-{top_k} similar entries in topic '{topic}'...")
        matches = vector_db.get_top_n(vector, n=top_k, filter_ids=topic_ids)

        results = []
        for uid, certainty in matches:
            matched = vector_db.get_by_id(uid)
            item: dict = {"id": uid, "certainty": certainty}
            if matched:
                item["properties"] = matched.properties
            results.append(item)

        output({"candidate_id": entry_id(raw), "similar": results})
    finally:
        vector_db.close()
        graph_db.close()


@cli.command()
@click.option("--topic", required=True, help="Target topic name.")
@click.option(
    "--entry", required=True, help='Entry JSON string, or "-" to read from stdin.'
)
@click.option("--overwrite", is_flag=True, default=False, help="Overwrite if entry already exists in vector DB.")
def submit(topic: str, entry: str, overwrite: bool):
    """Upsert entry into both graph and vector DBs."""
    raw = read_raw(entry)

    graph_db = connect_graph_db()
    vector_db = connect_vector_db(SCHEMA)
    try:
        log("Loading embedding model...")
        embedder = get_embedder()
        log(f"Upserting entry into topic '{topic}'...")
        eid = upsert_entry(SCHEMA, graph_db, vector_db, topic, raw, embedder, overwrite=overwrite)
        log_session(topic, [eid])
        output({"id": eid, "submitted": True})
    finally:
        vector_db.close()
        graph_db.close()


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
def similar_batch(topic: str, entries: str, top_k: int):
    """Get top-N similar entries for each candidate in a batch. Single model load."""
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

    graph_db = connect_graph_db()
    vector_db = connect_vector_db(SCHEMA)
    try:
        log(f"Fetching entry IDs for topic '{topic}'...")
        topic_ids = graph_db.get_entry_ids_for_topic(topic)
        if not topic_ids:
            err(
                "no_entries_in_topic",
                detail=f"Topic '{topic}' has no entries",
                suggestion="Check topic name or run least-topic to list topics",
                exit_code=EXIT_NOT_FOUND,
            )
        log(f"Loading embedding model (once for {len(rows)} candidates)...")
        embedder = get_embedder()

        results_all = []
        for i, raw in enumerate(rows):
            entry_obj = build_entry(SCHEMA, raw)
            vector = embedder([SCHEMA.embedding_text(entry_obj)])[0]
            log(f"[{i + 1}/{len(rows)}] Searching top-{top_k} similar for candidate...")
            matches = vector_db.get_top_n(vector, n=top_k, filter_ids=topic_ids)
            results = []
            for uid, certainty in matches:
                matched = vector_db.get_by_id(uid)
                item: dict = {"id": uid, "certainty": certainty}
                if matched:
                    item["properties"] = matched.properties
                results.append(item)
            results_all.append({"candidate_id": entry_id(raw), "similar": results})

        output(results_all)
    finally:
        vector_db.close()
        graph_db.close()


@cli.command(name="submit-batch")
@click.option("--topic", required=True, help="Target topic name.")
@click.option(
    "--entries",
    required=True,
    help='JSON array of entry objects, or "-" to read from stdin.',
)
@click.option("--overwrite", is_flag=True, default=False, help="Overwrite if entries already exist in vector DB.")
def submit_batch(topic: str, entries: str, overwrite: bool):
    """Upsert multiple entries into graph and vector DBs. Single model load."""
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

    graph_db = connect_graph_db()
    vector_db = connect_vector_db(SCHEMA)
    try:
        log(f"Loading embedding model (once for {len(rows)} entries)...")
        embedder = get_embedder()
        inserted_ids = []
        results = []
        for i, raw in enumerate(rows):
            log(f"[{i + 1}/{len(rows)}] Upserting entry into topic '{topic}'...")
            eid = upsert_entry(SCHEMA, graph_db, vector_db, topic, raw, embedder, overwrite=overwrite)
            inserted_ids.append(eid)
            results.append({"id": eid, "submitted": True})
        log_session(topic, inserted_ids)
        output(results)
    finally:
        vector_db.close()
        graph_db.close()


@cli.command(name="create-structure")
@click.option(
    "--file",
    "file_path",
    required=True,
    help='Path to JSON file defining topic hierarchy, or "-" for stdin.',
)
def create_structure(file_path: str):
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

    graph_db = connect_graph_db()
    try:
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
    finally:
        graph_db.close()


@cli.command(name="move-topic")
@click.option("--source", required=True, help="Path of topic/subtopic to move.")
@click.option("--destination", required=True, help="Path of new parent topic.")
def move_topic(source: str, destination: str):
    """Move a topic/subtopic under a different parent. Blocked if name conflict."""
    graph_db = connect_graph_db()
    try:
        graph_db.move_topic(source, destination)
        name = source.rsplit("/", 1)[-1]
        new_path = f"{destination}/{name}"
        output({"moved": source, "new_path": new_path})
    except ValueError as e:
        err("name_conflict", detail=str(e), exit_code=EXIT_USAGE)
    finally:
        graph_db.close()


@cli.command(name="move-entry")
@click.option("--id", "entry_id", required=True, help="Entry UUID to move.")
@click.option("--destination", required=True, help="Path of target topic.")
def move_entry(entry_id: str, destination: str):
    """Move an entry to a different topic."""
    graph_db = connect_graph_db()
    try:
        graph_db.move_entry(entry_id, destination)
        output({"id": entry_id, "moved_to": destination})
    finally:
        graph_db.close()


@cli.command(name="get-by-topic")
@click.option("--topic", required=True, help="Topic name to fetch entries from.")
@click.option("--limit", default=3, show_default=True, help="Max entries to return.")
def get_by_topic(topic: str, limit: int):
    """Fetch sample entries for a topic from vector DB."""
    graph_db = connect_graph_db()
    vector_db = connect_vector_db(SCHEMA)
    try:
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
    finally:
        vector_db.close()
        graph_db.close()


@cli.command(name="get-vector")
@click.option("--id", "entry_id", required=True, help="Entry UUID to fetch.")
def get_vector(entry_id: str):
    """Fetch entry from vector DB by ID."""
    vector_db = connect_vector_db(SCHEMA)
    try:
        matched = vector_db.get_by_id(entry_id)
        if matched is None:
            err(
                "not_found",
                detail=f"No entry with id '{entry_id}' in vector DB",
                exit_code=EXIT_NOT_FOUND,
            )
        output({"id": matched.id, **matched.properties})
    finally:
        vector_db.close()


@cli.command(name="get-graph")
@click.option("--id", "entry_id", required=True, help="Entry UUID to fetch.")
def get_graph(entry_id: str):
    """Fetch entry from graph DB by ID."""
    graph_db = connect_graph_db()
    try:
        matched = graph_db.get_by_id(entry_id)
        if matched is None:
            err(
                "not_found",
                detail=f"No entry with id '{entry_id}' in graph DB",
                exit_code=EXIT_NOT_FOUND,
            )
        output({"id": matched.id, "topic": matched.topic, **matched.properties})
    finally:
        graph_db.close()


@cli.command()
@click.argument("timestamp")
def undo(timestamp: str):
    """Delete all entries submitted after TIMESTAMP from both DBs and log.json."""
    from datetime import datetime, timezone

    from okgv.core import LOG_FILE

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

    if not LOG_FILE.exists():
        err("no_log", detail="log.json not found", exit_code=EXIT_NOT_FOUND)

    log_data = json.loads(LOG_FILE.read_text())

    ids_to_delete: list[str] = []
    keys_to_remove: list[str] = []
    for key, topic_dict in log_data.items():
        try:
            ts = datetime.fromisoformat(key)
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts > cutoff:
            for ids in topic_dict.values():
                ids_to_delete.extend(ids)
            keys_to_remove.append(key)

    if not ids_to_delete:
        output({"deleted": [], "count": 0})
        return

    graph_db = connect_graph_db()
    vector_db = connect_vector_db(SCHEMA)
    try:
        deleted = []
        failed_at = None
        for i, uid in enumerate(ids_to_delete):
            log(f"[{i + 1}/{len(ids_to_delete)}] Deleting {uid}...")
            graph_db.delete_entries([uid])
            try:
                vector_db.delete_by_id(uid)
            except Exception as e:
                # Graph already deleted this entry but vector failed.
                # Don't update log — ID stays so user can retry.
                failed_at = {"id": uid, "error": str(e), "deleted_so_far": deleted}
                log(f"Vector DB delete failed for {uid}: {e}")
                break
            deleted.append(uid)

            # Progressive log update: remove this ID from log_data
            for key in list(log_data.keys()):
                if key in keys_to_remove:
                    for _topic, ids in log_data[key].items():
                        if uid in ids:
                            ids.remove(uid)
                    if all(len(ids) == 0 for ids in log_data[key].values()):
                        del log_data[key]
            LOG_FILE.write_text(json.dumps(log_data, indent=2))

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
    finally:
        vector_db.close()
        graph_db.close()


if __name__ == "__main__":
    cli()
