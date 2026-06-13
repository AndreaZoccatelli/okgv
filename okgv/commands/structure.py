"""Topic tree commands: explore, create, visualize, reorganize."""

import json
import sys

import click

from okgv.helpers import EXIT_NOT_FOUND, EXIT_USAGE, err, log, output
from okgv.protocols import GraphRecord
from okgv.session import Session


def _revalidate_entry(session: Session, record: GraphRecord, topic: str) -> str | None:
    """Rebuild a stored entry and validate it against `topic`'s effective spec
    (the move destination, or its current topic for `revalidate`).

    Routes through `validate_entry_topic`, so it applies both the library's
    default `entry`-namespace enforcement and the schema's `validate_for_topic`
    hook. Returns an error message when the entry violates the spec, or None
    when it is valid, there is nothing to check, or the entry cannot be
    reconstructed from its stored properties (unverifiable, so the move is
    allowed; monotone narrowing already keeps upward refiling safe).
    """
    from okgv.core import validate_entry_topic
    from okgv.errors import EntryError

    props = dict(record.properties)
    vrec = session.vector_db.get_by_id(record.id)
    if vrec is not None:
        props.update(vrec.properties)
    try:
        entry = session.schema.entry_class(props)
    except Exception:
        return None
    try:
        validate_entry_topic(session.schema, entry, topic, session.effective_spec(topic))
    except EntryError as e:
        return str(e)
    return None


@click.command()
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


@click.command(name="get-structure")
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


@click.command(name="get-depth")
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


@click.command(name="create-topic")
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


@click.command(name="create-structure")
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

    A key starting with ``_`` is node metadata, not a child topic. ``_meta``
    blocks declare per-node constraints folded along each root-to-leaf path
    (see okgv.specs); a malformed validator or a contradictory fold fails here,
    before any topic is written. Files without ``_meta`` parse exactly as before.
    """
    from okgv.specs import build_specs, collect_warnings

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

    # Parse and fold every `_meta` block before touching the DB: a malformed
    # validator, a contradiction, or a function redeclaration must abort the
    # whole ingest, not leave a half-built tree behind. Raises SpecError
    # (converted to a structured CLI error by the top-level group).
    specs = build_specs(structure)

    graph_db = session.graph_db
    # Re-running over a populated DB may tighten specs under existing entries;
    # nothing revalidates them automatically, so flag it.
    preexisting_entries = bool(graph_db.get_all_entry_ids())

    created = []
    stack: list[tuple[dict, str | None]] = [(structure, None)]

    while stack:
        tree, parent = stack.pop()
        for name, children in tree.items():
            if name.startswith("_"):
                continue  # node metadata (e.g. _meta), not a child topic
            if parent is None:
                graph_db.create_topic(name)
                path = name
            else:
                graph_db.create_subtopic(parent, name)
                path = f"{parent}/{name}"
            created.append(path)
            if isinstance(children, dict) and children:
                stack.append((children, path))

    warnings = collect_warnings(specs)
    if preexisting_entries:
        warnings.append(
            {
                "level": "warning",
                "message": "structure (re)created over a DB that already has entries; "
                "run `revalidate` to find entries that violate the updated specs",
            }
        )
    for w in warnings:
        log(f"{w['level']}: {w['message']}")

    output(
        {
            "created_topics": created,
            "count": len(created),
            "warnings": [w["message"] for w in warnings if w["level"] == "warning"],
        }
    )


@click.command(name="least-topic")
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


@click.command(name="topic-stats")
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


@click.command(name="report")
@click.option("--topic", default=None, help="Scope report to this subtree. Default: full tree.")
@click.option(
    "--fields",
    default=None,
    help="Comma-separated balance fields. Default: schema balance_fields.",
)
@click.pass_obj
def report(session: Session, topic: str | None, fields: str | None):
    """Dataset-level balance report across leaf topics.

    Shows entry counts for every leaf topic and, when balance fields are
    available, counts for every leaf x field-value combination including
    empty cells, so coverage gaps are visible in one command. Field values
    come from OneOf validators when declared, otherwise from observed data.
    """
    from itertools import product

    graph_db = session.graph_db
    tree = graph_db.get_topic_tree(root=topic)
    if not tree:
        if topic:
            err("not_found", detail=f"Topic '{topic}' not found", exit_code=EXIT_NOT_FOUND)
        err("no_topics", detail="No topics found in graph", exit_code=EXIT_NOT_FOUND)

    # Collect leaf topic paths. get_topic_tree includes the root itself as
    # the top key when --topic is given; walk its children with the full path.
    leaves: list[str] = []

    def _walk(subtree: dict, prefix: str | None):
        for name, children in subtree.items():
            path = f"{prefix}/{name}" if prefix else name
            if children:
                _walk(children, path)
            else:
                leaves.append(path)

    if topic:
        sub = tree.get(topic.rsplit("/", 1)[-1], {})
        if sub:
            _walk(sub, topic)
        else:
            leaves.append(topic)
    else:
        _walk(tree, None)

    if fields:
        field_list = [f.strip() for f in fields.split(",")]
    else:
        field_list = list(getattr(session.schema, "balance_fields", None) or [])

    # Declared value sets from OneOf-style validators (field + valid attrs),
    # so values that were never generated still show up as empty cells.
    declared: dict[str, list] = {}
    for v in getattr(session.schema, "validators", []) or []:
        valid = getattr(v, "valid", None)
        if valid is not None and v.field in field_list:
            declared[v.field] = sorted(valid)

    leaf_stats = []
    observed: dict[str, set] = {f: set() for f in field_list}
    total_leaf_entries = 0
    for leaf in leaves:
        if field_list:
            try:
                count, _, groups = graph_db.get_topic_stats(leaf, field_list)
            except ValueError as e:
                err("invalid_field", detail=str(e), exit_code=EXIT_USAGE)
        else:
            count = len(graph_db.get_entry_ids_for_topic(leaf))
            groups = []
        total_leaf_entries += count
        leaf_stats.append((leaf, count, groups))
        for g in groups:
            for f, val in g["fields"].items():
                observed[f].add(val)

    value_sets = []
    for f in field_list:
        vals = declared.get(f) or sorted(observed[f], key=lambda x: (x is None, str(x)))
        value_sets.append(vals)

    result_leaves = []
    empty_cells = []
    for leaf, count, groups in leaf_stats:
        item: dict = {"topic": leaf, "count": count}
        if field_list and all(value_sets):
            observed_counts = {tuple(g["fields"][f] for f in field_list): g["count"] for g in groups}
            cells = []
            for combo in product(*value_sets):
                cell_count = observed_counts.get(combo, 0)
                cell_fields = dict(zip(field_list, combo))
                cells.append({"fields": cell_fields, "count": cell_count})
                if cell_count == 0:
                    empty_cells.append({"topic": leaf, "fields": cell_fields})
            item["cells"] = cells
        result_leaves.append(item)

    # Entries can live on non-leaf topics too; count them so nothing is hidden.
    if topic:
        total = len(graph_db.get_entry_ids_for_topic(topic))
    else:
        total = sum(len(graph_db.get_entry_ids_for_topic(t)) for t in tree)

    result: dict = {
        "total_entries": total,
        "leaf_topics": len(leaves),
        "balance_fields": field_list,
        "leaves": result_leaves,
    }
    if total - total_leaf_entries:
        result["non_leaf_entries"] = total - total_leaf_entries
    if field_list:
        result["empty_cells"] = empty_cells
    if result_leaves:
        least = min(result_leaves, key=lambda item: item["count"])
        most = max(result_leaves, key=lambda item: item["count"])
        result["least_filled_leaf"] = {"topic": least["topic"], "count": least["count"]}
        result["most_filled_leaf"] = {"topic": most["topic"], "count": most["count"]}
    output(result)


@click.command(name="move-topic")
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

    # Sideways/downward moves can violate the destination spec, so revalidate
    # every moved entry against its new path before applying anything.
    violations = []
    for rec in graph_db.get_entries_for_topic(source):
        new_topic = new_path + rec.topic[len(source) :]
        msg = _revalidate_entry(session, rec, new_topic)
        if msg is not None:
            violations.append({"id": rec.id, "new_topic": new_topic, "error": msg})
    if violations:
        ids = [v["id"] for v in violations]
        err(
            "invalid_for_topic",
            detail=f"{len(violations)} entr{'y' if len(violations) == 1 else 'ies'} "
            f"would violate the destination spec: {ids[:10]}",
            suggestion="Fix or refile the offending entries, or move to a compatible topic",
            exit_code=EXIT_USAGE,
        )

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


@click.command(name="move-entry")
@click.option("--id", "entry_id", required=True, help="Entry UUID to move.")
@click.option("--destination", required=True, help="Path of target topic.")
@click.option("--dry-run", is_flag=True, default=False, help="Preview without applying changes.")
@click.pass_obj
def move_entry(session: Session, entry_id: str, destination: str, dry_run: bool):
    """Move an entry to a different topic."""
    graph_db = session.graph_db
    record = graph_db.get_by_id(entry_id)
    if record is None:
        err("not_found", detail=f"No entry with id '{entry_id}'", exit_code=EXIT_NOT_FOUND)
    if not graph_db.topic_exists(destination):
        err(
            "not_found",
            detail=f"Topic '{destination}' does not exist",
            suggestion="Create it first with create-topic",
            exit_code=EXIT_NOT_FOUND,
        )

    msg = _revalidate_entry(session, record, destination)
    if msg is not None:
        err(
            "invalid_for_topic",
            detail=f"Entry '{entry_id}' is invalid for destination '{destination}': {msg}",
            suggestion="Move to a topic whose spec the entry satisfies",
            exit_code=EXIT_USAGE,
        )

    if dry_run:
        output({"dry_run": True, "would_move": entry_id, "destination": destination})
        return
    with session.transaction():
        session.graph_db.move_entry(entry_id, destination)
        session.vector_db.update_entry_topic(entry_id, destination)
    output({"id": entry_id, "moved_to": destination})


commands = (
    tree,
    get_structure,
    get_depth,
    create_topic,
    create_structure,
    least_topic,
    topic_stats,
    report,
    move_topic,
    move_entry,
)
