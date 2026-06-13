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


def _reject_interior_topic(session: Session, topic: str) -> None:
    """Block submission to a topic that has child topics.

    Under a refinement hierarchy an entry on an interior node is unclassified
    along the child dimension (no quota cell, no dedup cell), so submission must
    target a leaf.
    """
    children = session.graph_db.get_subtopics(topic)
    if children:
        err(
            "interior_topic",
            detail=f"Topic '{topic}' has child topics {sorted(children)}; entries can only be submitted to a leaf",
            suggestion="Submit to one of its leaf descendants instead",
            exit_code=EXIT_USAGE,
        )


def _similar_results(session: Session, topic: str, matches: list[tuple[str, float]]) -> list[dict]:
    """Shape similarity matches for output, tagging each with its topic.

    Under subtree scope a match may live in a sibling topic: that is reported
    (``topic`` plus ``sibling`` flag) as a variant signal, not a hard
    duplicate, leaving the accept/reject call to the agent and the review queue.
    """
    match_ids = [uid for uid, _ in matches]
    certainties = {uid: cert for uid, cert in matches}
    fetched = {r.id: r for r in session.vector_db.get_by_ids(match_ids)} if match_ids else {}
    topics = session.graph_db.get_topics_for_ids(match_ids) if match_ids else {}

    results = []
    for uid in match_ids:
        match_topic = topics.get(uid)
        item: dict = {"id": uid, "certainty": certainties[uid], "topic": match_topic}
        if match_topic is not None and match_topic != topic:
            item["sibling"] = True
        if uid in fetched:
            item["properties"] = fetched[uid].properties
        results.append(item)
    return results


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
    scope, search_root = session.similarity_scope(topic)
    log("Loading embedding model...")
    vector = session.embedder([schema.embedding_text(entry_obj)])[0]
    if scope == "subtree":
        log(f"Searching top-{top_k} similar entries under subtree '{search_root}'...")
    else:
        log(f"Searching top-{top_k} similar entries in topic '{topic}'...")
    matches = vector_db.get_top_n(vector, n=top_k, filter_topic=search_root, subtree=scope == "subtree")

    results = _similar_results(session, topic, matches)
    output({"candidate_id": entry_id(raw), "scope": scope, "similar": results})


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
    scope, search_root = session.similarity_scope(topic)
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
            matches = vector_db.get_top_n(vector, n=top_k, filter_topic=search_root, subtree=scope == "subtree")
            results = _similar_results(session, topic, matches)
            results_all.append({"candidate_id": entry_id(raw), "scope": scope, "similar": results})

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
    _reject_interior_topic(session, topic)

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
    _reject_interior_topic(session, topic)
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


def _parse_split_spec(spec: str) -> list[tuple[str, float]]:
    """Parse 'train=0.8,val=0.1,test=0.1' into [(name, fraction), ...]."""
    suggestion = 'Use e.g. "train=0.8,val=0.1,test=0.1" (fractions must sum to 1)'
    splits: list[tuple[str, float]] = []
    names: set[str] = set()
    for part in spec.split(","):
        name, _, frac_str = part.strip().partition("=")
        name = name.strip()
        try:
            frac = float(frac_str)
        except ValueError:
            err(
                "invalid_split",
                detail=f"Bad fraction in '{part.strip()}'",
                suggestion=suggestion,
                exit_code=EXIT_USAGE,
            )
        if not name or name in names:
            err("invalid_split", detail=f"Missing or duplicate split name in '{part.strip()}'", exit_code=EXIT_USAGE)
        if not 0 < frac <= 1:
            err("invalid_split", detail=f"Fraction for '{name}' must be in (0, 1]", exit_code=EXIT_USAGE)
        names.add(name)
        splits.append((name, frac))
    total = sum(frac for _, frac in splits)
    if abs(total - 1.0) > 1e-6:
        err(
            "invalid_split",
            detail=f"Fractions sum to {total}, expected 1.0",
            suggestion=suggestion,
            exit_code=EXIT_USAGE,
        )
    return splits


def _build_split_assignment(
    session: Session,
    splits: list[tuple[str, float]],
    seed: int,
    pending_ids: set[str],
    batch_size: int,
) -> tuple[dict[str, str], dict[str, int], dict[str, dict]]:
    """Assign each entry ID to a split, stratified by topic and balance fields.

    Each stratum (one topic x balance-field-value combination) is shuffled
    deterministically and divided by the split fractions, so every split
    keeps the dataset's topic and balance distribution. Returns
    ({entry_id: split_name}, {split_name: count},
    {split_name: {field: {value: count}}}).
    """
    import random

    balance_fields = list(getattr(session.schema, "balance_fields", None) or [])
    vector_db = session.vector_db
    graph_db = session.graph_db

    strata: dict[tuple, list[str]] = {}
    for chunk in vector_db.iter_entry_ids(batch_size):
        chunk = [eid for eid in chunk if eid not in pending_ids]
        if not chunk:
            continue
        topic_map = graph_db.get_topics_for_ids(chunk)
        props = {r.id: r.properties for r in vector_db.get_by_ids(chunk)} if balance_fields else {}
        for eid in chunk:
            key = (topic_map.get(eid),) + tuple(str(props.get(eid, {}).get(f)) for f in balance_fields)
            strata.setdefault(key, []).append(eid)

    rng = random.Random(seed)
    assignment: dict[str, str] = {}
    counts = {name: 0 for name, _ in splits}
    balance_counts: dict[str, dict] = {name: {f: {} for f in balance_fields} for name, _ in splits}
    seen = 0
    for key in sorted(strata, key=str):
        ids = sorted(strata[key])
        rng.shuffle(ids)
        n = len(ids)
        seen += n
        base = [int(frac * n) for _, frac in splits]
        # Rounding leftovers go to the splits with the largest *global*
        # deficit (running target minus assigned). Per-stratum rounding
        # would hand every leftover to the largest split, starving small
        # splits when strata are tiny (e.g. a few entries per cell).
        leftover = n - sum(base)
        deficits = [frac * seen - (counts[name] + b) for (name, frac), b in zip(splits, base)]
        for i in sorted(range(len(splits)), key=lambda i: deficits[i], reverse=True)[:leftover]:
            base[i] += 1
        start = 0
        for (name, _), c in zip(splits, base):
            for eid in ids[start : start + c]:
                assignment[eid] = name
            counts[name] += c
            if c:
                for i, f in enumerate(balance_fields):
                    value_counts = balance_counts[name][f]
                    value = key[1 + i]
                    value_counts[value] = value_counts.get(value, 0) + c
            start += c
    return assignment, counts, balance_counts


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
@click.option(
    "--split",
    "split_spec",
    default=None,
    help='Stratified split spec, e.g. "train=0.8,val=0.1,test=0.1". Writes one JSONL file per split.',
)
@click.option("--seed", default=42, show_default=True, help="Shuffle seed for --split assignment.")
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
    split_spec: str | None,
    seed: int,
    batch_size: int,
    dry_run: bool,
):
    """Export all entries to JSONL for model training.

    With --split, writes one file per split (e.g. dataset-train.jsonl),
    stratified by topic and balance fields so each split keeps the
    dataset's distribution. Assignment is deterministic for a given seed.
    """
    import os

    if not dry_run and not output_path:
        err("usage", detail="--output is required unless --dry-run is set", exit_code=EXIT_USAGE)

    field_set = {f.strip() for f in fields.split(",")} if fields else None
    pending_ids = review_get_pending_ids(session.db_path) if exclude_in_review else set()

    vector_db = session.vector_db
    graph_db = session.graph_db

    splits = _parse_split_spec(split_spec) if split_spec else None
    assignment: dict[str, str] = {}
    split_counts: dict[str, int] = {}
    balance_counts: dict[str, dict] = {}
    if splits:
        assignment, split_counts, balance_counts = _build_split_assignment(
            session, splits, seed, pending_ids, batch_size
        )

    def _split_summary(name: str) -> dict:
        item: dict = {"count": split_counts[name]}
        if balance_counts.get(name):
            item["balance"] = balance_counts[name]
        return item

    if dry_run:
        if splits:
            output(
                {
                    "dry_run": True,
                    "would_export": sum(split_counts.values()),
                    "splits": {name: _split_summary(name) for name, _ in splits},
                    "seed": seed,
                    "exclude_in_review": exclude_in_review,
                }
            )
            return
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
    if splits:
        stem, ext = os.path.splitext(out_path)
        split_files = {name: f"{stem}-{name}{ext or '.jsonl'}" for name, _ in splits}
        handles = {name: open(path, "w", encoding="utf-8") for name, path in split_files.items()}
    else:
        handles = {None: open(out_path, "w", encoding="utf-8")}

    written = 0
    try:
        for chunk in vector_db.iter_entry_ids(batch_size):
            chunk = [eid for eid in chunk if eid not in pending_ids]
            if not chunk:
                continue
            records = vector_db.get_by_ids(chunk)
            topic_map = graph_db.get_topics_for_ids(chunk)
            for rec in records:
                target = assignment.get(rec.id) if splits else None
                if splits and target is None:
                    continue
                row: dict = {"id": rec.id, "topic": topic_map.get(rec.id)}
                row.update(rec.properties)
                if field_set is not None:
                    row = {k: v for k, v in row.items() if k in field_set}
                handles[target].write(json.dumps(row, ensure_ascii=False) + "\n")
                written += 1
    finally:
        for fh in handles.values():
            fh.close()

    if splits:
        log(f"Exported {written} entries across {len(splits)} splits")
        output(
            {
                "exported": written,
                "seed": seed,
                "splits": {name: {"file": split_files[name], **_split_summary(name)} for name, _ in splits},
            }
        )
    else:
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
