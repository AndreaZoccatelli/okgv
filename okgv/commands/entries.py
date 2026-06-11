"""Entry commands: similarity search, submission, retrieval, export."""

import json
import sys

import click

from okgv.core import (
    build_entry,
    log_session,
    review_add,
    review_get_pending_ids,
    upsert_entries_batch,
    upsert_entry,
)
from okgv.errors import EntryError
from okgv.helpers import EXIT_NOT_FOUND, EXIT_USAGE, err, log, output, read_raw
from okgv.protocols import entry_id
from okgv.session import Session


@click.command()
@click.option("--topic", required=True, help="Topic to restrict similarity search to.")
@click.option(
    "--entry",
    required=True,
    help='Complete candidate entry JSON (same shape as submit), or "-" to read from stdin.',
)
@click.option("--top-k", default=5, show_default=True, help="Number of similar entries to return.")
@click.pass_obj
def similar(session: Session, topic: str, entry: str, top_k: int):
    """Get top-N most similar entries within a topic, with full content.

    The entry must be the complete candidate (the same JSON you would
    submit): similarity is computed on the schema's embedding text, so a
    partial entry would not match submit-time behavior.
    """
    raw = read_raw(entry)
    schema = session.schema
    entry_obj = build_entry(schema, raw)

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


@click.command(name="similar-batch")
@click.option("--topic", required=True, help="Topic to restrict similarity search to.")
@click.option(
    "--entries",
    required=True,
    help='JSON array of complete entry objects (same shape as submit), or "-" to read from stdin.',
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


@click.command()
@click.option("--topic", required=True, help="Target topic name.")
@click.option("--entry", required=True, help='Entry JSON string, or "-" to read from stdin.')
@click.option(
    "--overwrite",
    is_flag=True,
    default=False,
    help="Overwrite if entry already exists in vector DB.",
)
@click.option(
    "--review/--no-review",
    default=None,
    help="Flag entry for review. Default: uses OKGV_REVIEW env var.",
)
@click.pass_obj
def submit(session: Session, topic: str, entry: str, overwrite: bool, review: bool | None):
    """Upsert entry into both graph and vector DBs."""
    schema = session.schema
    raw = read_raw(entry)

    log("Loading embedding model...")
    log(f"Upserting entry into topic '{topic}'...")
    with session.transaction():
        eid = upsert_entry(
            schema,
            session.graph_db,
            session.vector_db,
            topic,
            raw,
            session.embedder,
            overwrite=overwrite,
        )
    log_session(session.db_path, topic, [eid])
    needs_review = review if review is not None else session.review_enabled
    if needs_review:
        review_add(session.db_path, topic, [eid])
    output({"id": eid, "submitted": True, "review": needs_review})


@click.command(name="submit-batch")
@click.option("--topic", required=True, help="Target topic name.")
@click.option(
    "--entries",
    required=True,
    help='JSON array of entry objects, or "-" to read from stdin.',
)
@click.option(
    "--overwrite",
    is_flag=True,
    default=False,
    help="Overwrite if entries already exist in vector DB.",
)
@click.option(
    "--review/--no-review",
    default=None,
    help="Flag entries for review. Default: uses OKGV_REVIEW env var.",
)
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
        with session.transaction():
            inserted_ids, failures = upsert_entries_batch(
                schema,
                session.graph_db,
                session.vector_db,
                topic,
                valid_raws,
                valid_entries,
                vectors,
                overwrite=overwrite,
            )
        for eid in inserted_ids:
            results.append({"id": eid, "submitted": True})
        for f in failures:
            results.append({"id": f["id"], "submitted": False, "error": f["error"]})
        if inserted_ids:
            log_session(session.db_path, topic, inserted_ids)
            needs_review = review if review is not None else session.review_enabled
            if needs_review:
                review_add(session.db_path, topic, inserted_ids)

    output(results)


@click.command(name="get-by-topic")
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


@click.command(name="get-vector")
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


@click.command(name="get-graph")
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


@click.command(name="export")
@click.option("--output", "output_path", default=None, help="Path to output .jsonl file. Required unless --dry-run.")
@click.option(
    "--fields",
    default=None,
    help="Comma-separated fields to include. Default: all fields + id + topic.",
)
@click.option(
    "--exclude-in-review",
    is_flag=True,
    default=False,
    help="Exclude entries currently pending in the review queue.",
)
@click.option("--batch-size", default=500, show_default=True, help="Batch size for DB reads.")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print count of entries to export, no file written.",
)
@click.pass_obj
def export_cmd(
    session: Session,
    output_path: str | None,
    fields: str | None,
    exclude_in_review: bool,
    batch_size: int,
    dry_run: bool,
):
    """Export all entries to a JSONL file for model training."""
    import os

    if not dry_run and not output_path:
        err("usage", detail="--output is required unless --dry-run is set", exit_code=EXIT_USAGE)

    field_set = {f.strip() for f in fields.split(",")} if fields else None
    pending_ids = review_get_pending_ids(session.db_path) if exclude_in_review else set()

    vector_db = session.vector_db
    graph_db = session.graph_db

    if dry_run:
        total = 0
        for chunk in vector_db.iter_entry_ids(batch_size):
            filtered = [eid for eid in chunk if eid not in pending_ids]
            total += len(filtered)
        output(
            {
                "dry_run": True,
                "would_export": total,
                "exclude_in_review": exclude_in_review,
            }
        )
        return

    out_path = output_path if os.path.isabs(output_path) else os.path.join(os.getcwd(), output_path)
    written = 0
    with open(out_path, "w", encoding="utf-8") as fh:
        for chunk in vector_db.iter_entry_ids(batch_size):
            chunk = [eid for eid in chunk if eid not in pending_ids]
            if not chunk:
                continue
            records = vector_db.get_by_ids(chunk)
            topic_map = graph_db.get_topics_for_ids(chunk)
            for rec in records:
                row: dict = {"id": rec.id, "topic": topic_map.get(rec.id)}
                row.update(rec.properties)
                if field_set is not None:
                    row = {k: v for k, v in row.items() if k in field_set}
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                written += 1

    log(f"Exported {written} entries to {out_path}")
    output({"exported": written, "file": out_path})


commands = (
    similar,
    similar_batch,
    submit,
    submit_batch,
    get_by_topic,
    get_vector,
    get_graph,
    export_cmd,
)
