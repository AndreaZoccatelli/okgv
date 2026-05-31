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
from okgv.helpers import err, log, output, parse_raw, read_raw, EXIT_NOT_FOUND, EXIT_USAGE
from okgv.protocols import entry_id

SCHEMA = load_schema()


@click.group(
    help="Knowledge base CLI for AI agents. All output is JSON to stdout, logs to stderr."
)
def cli():
    pass


@cli.command(name="create-topic")
@click.option("--name", required=True, help="Topic name to create.")
def create_topic(name: str):
    """Create a topic node in the graph DB. Idempotent."""
    graph_db = connect_graph_db()
    try:
        graph_db.create_topic(name)
        output({"topic": name, "created": True})
    finally:
        graph_db.close()


@cli.command(name="create-subtopic")
@click.option("--parent", required=True, help="Parent topic name.")
@click.option("--name", required=True, help="Sub-topic name to create.")
def create_subtopic(parent: str, name: str):
    """Create a sub-topic under an existing topic. Idempotent."""
    graph_db = connect_graph_db()
    try:
        graph_db.create_subtopic(parent, name)
        output({"parent": parent, "subtopic": name, "created": True})
    finally:
        graph_db.close()


@cli.command(name="least-topic")
def least_topic():
    """Return the topic with the fewest entries."""
    graph_db = connect_graph_db()
    try:
        counts = graph_db.get_topic_entry_counts()
        if not counts:
            err("no_topics", detail="No topics found in graph", exit_code=EXIT_NOT_FOUND)
        topic = min(counts, key=lambda t: counts[t])
        output({"topic": topic, "count": counts[topic]})
    finally:
        graph_db.close()


@cli.command()
@click.option("--topic", required=True, help="Topic to restrict similarity search to.")
@click.option("--entry", required=True, help='Entry JSON string, or "-" to read from stdin.')
@click.option("--top-k", default=5, show_default=True, help="Number of similar entries to return.")
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
@click.option("--entry", required=True, help='Entry JSON string, or "-" to read from stdin.')
def submit(topic: str, entry: str):
    """Upsert entry into both graph and vector DBs."""
    raw = read_raw(entry)

    graph_db = connect_graph_db()
    vector_db = connect_vector_db(SCHEMA)
    try:
        log("Loading embedding model...")
        embedder = get_embedder()
        log(f"Upserting entry into topic '{topic}'...")
        eid = upsert_entry(SCHEMA, graph_db, vector_db, topic, raw, embedder)
        log_session(topic, [eid])
        output({"id": eid, "submitted": True})
    finally:
        vector_db.close()
        graph_db.close()


@cli.command(name="similar-batch")
@click.option("--topic", required=True, help="Topic to restrict similarity search to.")
@click.option("--entries", required=True, help='JSON array of entry objects, or "-" to read from stdin.')
@click.option("--top-k", default=5, show_default=True, help="Number of similar entries per candidate.")
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
        err("invalid_input", detail="Expected a JSON array of entries", exit_code=EXIT_USAGE)

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
            log(f"[{i+1}/{len(rows)}] Searching top-{top_k} similar for candidate...")
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
@click.option("--entries", required=True, help='JSON array of entry objects, or "-" to read from stdin.')
def submit_batch(topic: str, entries: str):
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
        err("invalid_input", detail="Expected a JSON array of entries", exit_code=EXIT_USAGE)

    graph_db = connect_graph_db()
    vector_db = connect_vector_db(SCHEMA)
    try:
        log(f"Loading embedding model (once for {len(rows)} entries)...")
        embedder = get_embedder()
        inserted_ids = []
        results = []
        for i, raw in enumerate(rows):
            log(f"[{i+1}/{len(rows)}] Upserting entry into topic '{topic}'...")
            eid = upsert_entry(SCHEMA, graph_db, vector_db, topic, raw, embedder)
            inserted_ids.append(eid)
            results.append({"id": eid, "submitted": True})
        log_session(topic, inserted_ids)
        output(results)
    finally:
        vector_db.close()
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
            err("not_found", detail=f"No entry with id '{entry_id}' in vector DB", exit_code=EXIT_NOT_FOUND)
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
            err("not_found", detail=f"No entry with id '{entry_id}' in graph DB", exit_code=EXIT_NOT_FOUND)
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
        err("invalid_timestamp", detail=str(e), suggestion="Use ISO 8601 format", exit_code=EXIT_USAGE)

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
        log(f"Deleting {len(ids_to_delete)} entries from graph DB...")
        graph_db.delete_entries(ids_to_delete)

        log(f"Deleting {len(ids_to_delete)} entries from vector DB...")
        for uid in ids_to_delete:
            vector_db.delete_by_id(uid)

        for key in keys_to_remove:
            del log_data[key]
        LOG_FILE.write_text(json.dumps(log_data, indent=2))

        output({"deleted": ids_to_delete, "count": len(ids_to_delete)})
    finally:
        vector_db.close()
        graph_db.close()


if __name__ == "__main__":
    cli()
