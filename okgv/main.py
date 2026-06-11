"""
CLI for AI agents to interact with the self-organized knowledge base.

Schema discovery (see config.py):
  OKGV_SCHEMA env var →  "module:ClassName"

Exit codes:  0=ok  1=failure  2=usage  3=not_found  4=connection
"""

import json
import sqlite3
import sys
from datetime import UTC

import click

from okgv.core import (
    build_entry,
    log_count,
    log_get_entries_after,
    log_query,
    log_remove_entries,
    log_session,
    review_add,
    review_count,
    review_get_pending_ids,
    review_get_rejected,
    review_list,
    review_purge_rejected,
    review_remove_entries,
    review_update,
    upsert_entries_batch,
    upsert_entry,
)
from okgv.errors import EntryError, OkgvError
from okgv.helpers import EXIT_FAILURE, EXIT_NOT_FOUND, EXIT_USAGE, err, log, output, read_raw
from okgv.protocols import entry_id
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
@click.pass_context
def cli(ctx):
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(Path.cwd() / ".env")
    if ctx.obj is None:
        ctx.obj = Session()
    ctx.call_on_close(ctx.obj.close)


@cli.command(name="cli-prompt")
@click.pass_context
def cli_prompt(ctx):
    """Print agent instructions for using the CLI."""
    from importlib.resources import files

    templates = files("okgv.templates")
    text = templates.joinpath("cli-prompt.md").read_text()

    click.echo(text)


@cli.command(name="entry-prompt")
@click.pass_context
def entry_prompt(ctx):
    """Print entry field descriptions and constraints for the agent."""
    validators = getattr(ctx.obj.schema, "validators", [])
    validator_map = {v.field: v for v in validators}
    descriptions = getattr(ctx.obj.schema, "field_descriptions", {})

    fields = dict.fromkeys([*descriptions.keys(), *validator_map.keys()])
    if not fields:
        click.echo("No field descriptions or validators defined in schema.")
        return

    text = "# Entry Fields\n\nEach entry in this knowledge base has the following fields:\n\n"
    for field in fields:
        desc = descriptions.get(field)
        validator = validator_map.get(field)

        if isinstance(desc, tuple):
            label, options = desc
            constraint = f". {validator.prompt().split(': ', 1)[1]}" if validator else ""
            text += f"- **{field}**: {label}{constraint}\n"
            for opt, explanation in options.items():
                text += f"  - {opt}: {explanation}\n"
        else:
            parts = []
            if desc:
                parts.append(desc)
            if validator:
                parts.append(validator.prompt().split(": ", 1)[1])
            text += f"- **{field}**: {'. '.join(parts)}\n"

    balance_fields = getattr(ctx.obj.schema, "balance_fields", [])
    if balance_fields:
        text += "\n## Balancing\n\n"
        text += f"Ensure balanced coverage across these fields: {', '.join(balance_fields)}.\n"
        text += "Use `okgv topic-stats` to check current distribution within a specific topic.\n"

    click.echo(text)


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
        ("generation-guide.md", "generation-guide.md"),
        ("schema.py.txt", "config/schema.py"),
        ("structure.json", "config/structure.json"),
        ("schema-guide.md", "prompts/schema-guide.md"),
        ("reviewer-prompt.md", "prompts/reviewer-prompt.md"),
        ("structure-prompt.md", "prompts/structure-prompt.md"),
    ]

    for template_name, target_name in scaffold:
        target = cwd / target_name
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            content = templates.joinpath(template_name).read_text()
            target.write_text(content)
            created.append(target_name)

    # Ensure config/ is importable as a Python package (needed for schema import)
    init_py = cwd / "config" / "__init__.py"
    if not init_py.exists() and (cwd / "config").exists():
        init_py.write_text("")
        created.append("config/__init__.py")

    if created:
        output({"initialized": True, "created": created})
    else:
        output({"initialized": False, "message": "All files already exist", "created": []})


@cli.command()
@click.option("--root", default=None, help="Start from this topic path. Default: full tree.")
@click.option("--counts", is_flag=True, default=False, help="Show entry counts per node.")
@click.option(
    "--interactive",
    "-i",
    is_flag=True,
    default=False,
    help="Interactive browser: navigate topics, view entries.",
)
@click.option(
    "--limit",
    default=20,
    show_default=True,
    help="Max entries per topic in interactive mode.",
)
@click.option(
    "--export",
    "export_fmt",
    type=click.Choice(["dot", "json"]),
    default=None,
    help="Export format: dot (Graphviz) or json.",
)
@click.pass_obj
def tree(
    session: Session,
    root: str | None,
    counts: bool,
    interactive: bool,
    limit: int,
    export_fmt: str | None,
):
    """Display the topic tree visually in the terminal."""
    tree_data = session.graph_db.get_topic_tree(root=root)
    if not tree_data:
        if root:
            err(
                "not_found",
                detail=f"Topic '{root}' not found or has no subtopics",
                exit_code=EXIT_NOT_FOUND,
            )
        else:
            err("no_topics", detail="No topics found", exit_code=EXIT_NOT_FOUND)

    if interactive:
        try:
            from okgv.tui import run_browse
        except ImportError:
            err("missing_dependency", "textual is required for interactive mode: pip install okgv[tui]", exit_code=1)

        # Pass vector_db lazily: browsing the tree must not trigger an embedding
        # model load when the vector DB has no stored dimension yet.
        run_browse(
            graph_db=session.graph_db,
            get_vector_db=lambda: session.vector_db,
            root=root,
            entry_limit=limit,
        )
        return

    if export_fmt == "json":
        output(tree_data)
        return

    if export_fmt == "dot":
        lines = ["digraph topics {", "  rankdir=TB;", "  node [shape=box];"]

        def _dot(subtree: dict, parent: str | None = None):
            for name, children in subtree.items():
                node_id = name.replace("/", "_").replace("-", "_")
                lines.append(f'  "{node_id}" [label="{name}"];')
                if parent:
                    lines.append(f'  "{parent}" -> "{node_id}";')
                _dot(children, node_id)

        _dot(tree_data)
        lines.append("}")
        click.echo("\n".join(lines))
        return

    # Terminal display with rich.tree
    try:
        from rich.console import Console
        from rich.tree import Tree as RichTree
    except ImportError:
        err(
            "missing_dependency",
            detail="rich is required for tree display: pip install rich (or use --export json/dot)",
            exit_code=1,
        )

    # Counts are keyed by full topic path: sibling topics may share a name
    # (e.g. algebra/basics and geometry/basics) without colliding.
    count_map: dict[str, int] = {}

    def _collect_counts(subtree: dict, prefix: str | None = None):
        for name, children in subtree.items():
            path = f"{prefix}/{name}" if prefix else name
            count_map[path] = len(session.graph_db.get_entry_ids_for_topic(path))
            _collect_counts(children, path)

    def _build(subtree: dict, parent_tree: RichTree, prefix: str | None = None):
        for name, children in subtree.items():
            path = f"{prefix}/{name}" if prefix else name
            label = name
            if counts and path in count_map:
                label = f"{name} [dim]({count_map[path]})[/dim]"
            branch = parent_tree.add(label)
            _build(children, branch, path)

    label = root or "topics"
    rich_tree = RichTree(f"[bold]{label}[/bold]")
    # get_topic_tree includes the root itself as the top key; strip it so the
    # root is not rendered twice (once as RichTree label, once as a child).
    render_data = tree_data
    if root:
        render_data = tree_data.get(root.rsplit("/", 1)[-1], {})
    if counts:
        _collect_counts(render_data, root)
    _build(render_data, rich_tree, root)
    Console(stderr=True).print(rich_tree)


@cli.command(name="get-structure")
@click.option("--root", default=None, help="Start from this topic path. Default: full tree.")
@click.option(
    "--depth",
    default=None,
    type=int,
    help="Max nesting levels to return. Default: unlimited.",
)
@click.pass_obj
def get_structure(session: Session, root: str | None, depth: int | None):
    """Return the topic/subtopic tree as nested JSON (no entries)."""
    tree = session.graph_db.get_topic_tree(root=root, max_depth=depth)
    if not tree:
        if root:
            err(
                "not_found",
                detail=f"Topic '{root}' not found or has no subtopics",
                exit_code=EXIT_NOT_FOUND,
            )
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
            err(
                "not_found",
                detail=f"Topic '{root}' not found",
                exit_code=EXIT_NOT_FOUND,
            )
        else:
            err("no_topics", detail="No topics found in graph", exit_code=EXIT_NOT_FOUND)
    depth = session.graph_db.get_topic_depth(root=root)
    result = {"depth": depth}
    if root:
        result["root"] = root
    output(result)


@cli.command(name="create-topic")
@click.option(
    "--name",
    required=True,
    help="Topic path to create (e.g. 'algebra/linear_algebra').",
)
@click.option("--parents", is_flag=True, default=False, help="Create missing parent topics.")
@click.pass_obj
def create_topic(session: Session, name: str, parents: bool):
    """Create a topic node in the graph DB. Accepts paths.

    Without --parents: errors if parent topics don't exist.
    With --parents: creates all missing intermediate levels (like mkdir -p).
    """
    graph_db = session.graph_db
    if graph_db.topic_exists(name):
        output({"topic": name, "created": False, "existed": True})
        return
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
    if fields:
        field_list = [f.strip() for f in fields.split(",")]
    else:
        field_list = getattr(session.schema, "balance_fields", None)

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

    output(
        {
            "topic": topic,
            "total_entries": total,
            "group_by": group_fields,
            "groups": groups,
        }
    )


@cli.command()
@click.option("--topic", required=True, help="Topic to restrict similarity search to.")
@click.option("--entry", required=True, help='Entry JSON string, or "-" to read from stdin.')
@click.option("--top-k", default=5, show_default=True, help="Number of similar entries to return.")
@click.pass_obj
def similar(session: Session, topic: str, entry: str, top_k: int):
    """Get top-N most similar entries within a topic, with full content."""
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


@cli.command()
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
    graph_db = session.graph_db
    if not graph_db.topic_exists(source):
        err("not_found", detail=f"Topic '{source}' does not exist", exit_code=EXIT_NOT_FOUND)
    if not graph_db.topic_exists(destination):
        err(
            "not_found",
            detail=f"Topic '{destination}' does not exist",
            suggestion="Create it first with create-topic",
            exit_code=EXIT_NOT_FOUND,
        )
    name = source.rsplit("/", 1)[-1]
    new_path = f"{destination}/{name}"
    if dry_run:
        output({"dry_run": True, "would_move": source, "new_path": new_path})
        return
    try:
        with session.transaction():
            graph_db.move_topic(source, destination)
            session.vector_db.update_topics(source, new_path)
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
    graph_db = session.graph_db
    if graph_db.get_by_id(entry_id) is None:
        err("not_found", detail=f"No entry with id '{entry_id}'", exit_code=EXIT_NOT_FOUND)
    if not graph_db.topic_exists(destination):
        err(
            "not_found",
            detail=f"Topic '{destination}' does not exist",
            suggestion="Create it first with create-topic",
            exit_code=EXIT_NOT_FOUND,
        )
    if dry_run:
        output({"dry_run": True, "would_move": entry_id, "destination": destination})
        return
    with session.transaction():
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
        results = {}
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
        for e in entries:
            item = {"id": e["entry_id"], "status": e["status"], "topic": e["topic"]}
            if e["entry_id"] in fetched:
                item.update(fetched[e["entry_id"]])
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


@cli.command()
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


@cli.command()
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


@cli.command(name="log")
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


@cli.command()
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


@cli.command()
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


@cli.command(hidden=True)
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


@cli.command(name="export")
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


if __name__ == "__main__":
    cli()
