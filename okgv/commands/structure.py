"""Topic tree commands: explore, create, visualize, reorganize."""

import json
import sys

import click

from okgv.helpers import EXIT_NOT_FOUND, EXIT_USAGE, err, output
from okgv.session import Session


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


commands = (
    tree,
    get_structure,
    get_depth,
    create_topic,
    create_structure,
    least_topic,
    topic_stats,
    move_topic,
    move_entry,
)
