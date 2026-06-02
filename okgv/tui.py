"""Interactive TUIs: review queue and topic/entry browser."""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Static, Tree

from rich.text import Text

from okgv.core import review_count, review_list, review_update
from okgv.protocols import GraphDB, VectorDB


def _status_text(status: str) -> Text:
    if status == "approved":
        return Text("✓", style="bold green")
    if status == "rejected":
        return Text("✗", style="bold red")
    return Text("—", style="dim")


class DetailPanel(Static):
    """Shows full content of selected entry."""

    def update_entry(self, properties: dict) -> None:
        lines = []
        for key, value in properties.items():
            text = str(value)
            if len(text) > 500:
                text = text[:500] + "..."
            lines.append(f"[bold]{key}[/bold]: {text}")
        self.update("\n".join(lines) if lines else "[dim]No entry selected[/dim]")


class ReviewApp(App):
    CSS = """
    #main {
        height: 1fr;
    }
    #table-container {
        width: 1fr;
        min-width: 40;
    }
    #detail-container {
        width: 2fr;
        border-left: solid $accent;
        padding: 1 2;
        overflow-y: auto;
    }
    #detail {
        width: 100%;
    }
    DataTable {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("a", "approve", "Approve", priority=True),
        Binding("r", "reject", "Reject", priority=True),
        Binding("u", "undo_mark", "Undo", priority=True),
        Binding("s", "skip", "Skip/Next", priority=True),
        Binding("n", "load_more", "More", priority=True),
        Binding("c", "commit", "Commit", priority=True),
        Binding("q", "quit_discard", "Quit", priority=True),
    ]

    def __init__(
        self,
        db_path: Path,
        vector_db: VectorDB,
        topic: str | None = None,
        limit: int = 100,
    ):
        super().__init__()
        self._db_path = db_path
        self._vector_db = vector_db
        self._topic = topic
        self._limit = limit
        self._offset = 0
        self._total = 0
        self._entries: list[dict] = []
        self._content: dict[str, dict] = {}
        # Staged changes: entry_id -> new status
        self._staged: dict[str, str] = {}
        self._quit_pending = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            with Vertical(id="table-container"):
                yield DataTable(id="table")
            with Vertical(id="detail-container"):
                yield DetailPanel(id="detail")
        yield Footer()

    def on_mount(self) -> None:
        counts = review_count(self._db_path, topic=self._topic)
        self._total = counts.get("pending", 0)
        self._load_entries()
        table = self.query_one("#table", DataTable)
        table.cursor_type = "row"
        table.cursor_foreground_priority = "renderable"
        table.add_columns("ID", "Topic", "Status")
        self._refresh_table()
        self._update_status()

    def _load_entries(self) -> None:
        batch = review_list(
            self._db_path, status="pending", topic=self._topic,
            limit=self._limit, offset=self._offset,
        )
        self._entries.extend(batch)
        self._offset += len(batch)
        if batch:
            entry_ids = [e["entry_id"] for e in batch]
            records = self._vector_db.get_by_ids(entry_ids)
            self._content.update({r.id: r.properties for r in records})

    def _get_status(self, entry_id: str) -> str:
        return self._staged.get(entry_id, "pending")

    def _refresh_table(self) -> None:
        table = self.query_one("#table", DataTable)
        table.clear()
        for e in self._entries:
            short_id = e["entry_id"][:12] + "..."
            status = self._get_status(e["entry_id"])
            table.add_row(short_id, e["topic"], _status_text(status), key=e["entry_id"])
        if self._entries:
            table.move_cursor(row=0)
            self._show_detail(self._entries[0]["entry_id"])

    def _update_row_status(self, entry_id: str) -> None:
        table = self.query_one("#table", DataTable)
        status = self._get_status(entry_id)
        for i, e in enumerate(self._entries):
            if e["entry_id"] == entry_id:
                table.update_cell_at((i, 2), _status_text(status))
                break

    def _update_status(self) -> None:
        pending = sum(1 for e in self._entries if self._get_status(e["entry_id"]) == "pending")
        approved = sum(1 for s in self._staged.values() if s == "approved")
        rejected = sum(1 for s in self._staged.values() if s == "rejected")
        topic_str = f"{self._topic} | " if self._topic else ""
        unsaved = f" | +{len(self._staged)} unsaved" if self._staged else ""
        loaded = f" | showing {len(self._entries)} of {self._total}" if self._total > len(self._entries) else ""
        self.title = f"{topic_str}pending:{pending} approved:{approved} rejected:{rejected}{unsaved}{loaded}"

    def _show_detail(self, entry_id: str) -> None:
        detail = self.query_one("#detail", DetailPanel)
        props = self._content.get(entry_id, {})
        detail.update_entry(props)

    def _current_entry_id(self) -> str | None:
        table = self.query_one("#table", DataTable)
        if table.row_count == 0:
            return None
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        return str(row_key.value)

    def _mark_entry(self, status: str) -> None:
        self._quit_pending = False
        entry_id = self._current_entry_id()
        if entry_id is None:
            return
        current = self._get_status(entry_id)
        if current == status:
            # Toggle back to pending
            self._staged.pop(entry_id, None)
        else:
            self._staged[entry_id] = status
        self._update_status()
        self._update_row_status(entry_id)

    def action_approve(self) -> None:
        self._mark_entry("approved")

    def action_reject(self) -> None:
        self._mark_entry("rejected")

    def action_undo_mark(self) -> None:
        self._quit_pending = False
        entry_id = self._current_entry_id()
        if entry_id is None:
            return
        if entry_id in self._staged:
            del self._staged[entry_id]
            self._update_row_status(entry_id)
            self._update_status()

    def action_skip(self) -> None:
        self._quit_pending = False
        table = self.query_one("#table", DataTable)
        if table.row_count == 0:
            return
        cursor_row = table.cursor_coordinate.row
        new_row = (cursor_row + 1) % table.row_count
        table.move_cursor(row=new_row)
        self._show_detail(self._entries[new_row]["entry_id"])

    def action_load_more(self) -> None:
        self._quit_pending = False
        if self._offset >= self._total:
            self.notify("All entries loaded", severity="information")
            return
        prev_count = len(self._entries)
        self._load_entries()
        new_count = len(self._entries) - prev_count
        if new_count == 0:
            self.notify("No more entries", severity="information")
            return
        table = self.query_one("#table", DataTable)
        for e in self._entries[prev_count:]:
            short_id = e["entry_id"][:12] + "..."
            status = self._get_status(e["entry_id"])
            table.add_row(short_id, e["topic"], _status_text(status), key=e["entry_id"])
        self._update_status()
        self.notify(f"Loaded {new_count} more ({len(self._entries)} of {self._total})")

    def action_commit(self) -> None:
        self._quit_pending = False
        if not self._staged:
            self.notify("Nothing to commit", severity="warning")
            return
        approved = [eid for eid, s in self._staged.items() if s == "approved"]
        rejected = [eid for eid, s in self._staged.items() if s == "rejected"]
        if approved:
            review_update(self._db_path, approved, "approved")
        if rejected:
            review_update(self._db_path, rejected, "rejected")
        total = len(approved) + len(rejected)
        self.notify(f"Committed {total} decisions ({len(approved)} approved, {len(rejected)} rejected)")
        # Remove committed entries from list
        committed_ids = set(self._staged.keys())
        self._entries = [e for e in self._entries if e["entry_id"] not in committed_ids]
        self._staged.clear()
        self._refresh_table()
        self._update_status()
        if not self._entries:
            self.query_one("#detail", DetailPanel).update("[dim]All entries reviewed[/dim]")

    def action_quit_discard(self) -> None:
        if self._staged and not self._quit_pending:
            self._quit_pending = True
            self.notify(
                f"{len(self._staged)} unsaved changes — press q again to discard and quit",
                severity="warning",
            )
            return
        self.exit()

    @on(DataTable.RowHighlighted)
    def on_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key and event.row_key.value:
            self._show_detail(str(event.row_key.value))


def run_tui(db_path: Path, vector_db: VectorDB, topic: str | None = None, limit: int = 100) -> None:
    app = ReviewApp(db_path=db_path, vector_db=vector_db, topic=topic, limit=limit)
    app.run()


# ── Browse TUI ─────────────────────────────────────────────────────────


class EntryTable(DataTable):
    pass


class EntryDetail(Static):
    """Shows full content of selected entry."""

    def show_entry(self, properties: dict) -> None:
        lines = []
        for key, value in properties.items():
            text = str(value)
            if len(text) > 500:
                text = text[:500] + "..."
            lines.append(f"[bold]{key}[/bold]: {text}")
        self.update("\n".join(lines) if lines else "[dim]Select an entry[/dim]")

    def clear_entry(self) -> None:
        self.update("[dim]Select an entry[/dim]")


class BrowseApp(App):
    CSS = """
    #browse-main {
        height: 1fr;
    }
    #tree-panel {
        width: 1fr;
        min-width: 30;
        max-width: 50;
    }
    #right-panel {
        width: 3fr;
        border-left: solid $accent;
    }
    #entry-table {
        height: 1fr;
        max-height: 50%;
    }
    #entry-detail {
        height: 1fr;
        border-top: solid $accent;
        padding: 1 2;
        overflow-y: auto;
    }
    """

    BINDINGS = [
        Binding("enter", "select_node", "Enter topic", show=True, priority=True),
        Binding("escape", "back_to_tree", "Back to tree", show=True),
        Binding("n", "load_more", "More entries", show=True),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        graph_db: GraphDB,
        vector_db: VectorDB,
        root: str | None = None,
        entry_limit: int = 20,
    ):
        super().__init__()
        self._graph_db = graph_db
        self._vector_db = vector_db
        self._root = root
        self._entry_limit = entry_limit
        self._current_topic: str | None = None
        # All entry IDs per topic (from graph), paginated via slicing
        self._topic_all_ids: dict[str, list[str]] = {}
        # Loaded records per topic
        self._entry_cache: dict[str, list] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="browse-main"):
            with Vertical(id="tree-panel"):
                yield Tree("topics", id="topic-tree")
            with Vertical(id="right-panel"):
                yield EntryTable(id="entry-table")
                yield EntryDetail(id="entry-detail")
        yield Footer()

    def on_mount(self) -> None:
        tree_widget = self.query_one("#topic-tree", Tree)
        tree_widget.show_root = self._root is not None
        if self._root:
            tree_widget.root.label = self._root

        tree_data = self._graph_db.get_topic_tree(root=self._root)
        counts = self._graph_db.get_topic_entry_counts(parent=self._root)

        self._build_tree(tree_widget.root, tree_data, counts, prefix=self._root)
        tree_widget.root.expand()

        table = self.query_one("#entry-table", EntryTable)
        table.cursor_type = "row"
        table.add_columns("ID", "Topic")
        self.title = "okgv browse"

    def _build_tree(
        self,
        parent_node,
        subtree: dict,
        counts: dict[str, int],
        prefix: str | None = None,
    ) -> None:
        for name, children in subtree.items():
            path = f"{prefix}/{name}" if prefix else name
            count = counts.get(path, 0)
            label = f"{name} [dim]({count})[/dim]" if count else name
            node = parent_node.add(label, data=path)
            child_counts = self._graph_db.get_topic_entry_counts(parent=path)
            self._build_tree(node, children, child_counts, prefix=path)

    @on(Tree.NodeHighlighted)
    def on_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        topic_path = event.node.data
        if topic_path is None:
            return
        self._load_entries_for_topic(topic_path)

    def _load_entries_for_topic(self, topic_path: str) -> None:
        self._current_topic = topic_path
        table = self.query_one("#entry-table", EntryTable)
        detail = self.query_one("#entry-detail", EntryDetail)
        table.clear()
        detail.clear_entry()

        # Get all IDs once, then paginate
        if topic_path not in self._topic_all_ids:
            self._topic_all_ids[topic_path] = self._graph_db.get_entry_ids_for_topic(topic_path)
            self._entry_cache[topic_path] = []

        all_ids = self._topic_all_ids[topic_path]
        if not all_ids:
            self.sub_title = f"{topic_path} — no entries"
            return

        # Load first page if not cached yet
        if not self._entry_cache[topic_path]:
            self._fetch_next_page(topic_path)

        self._render_entries(topic_path)

    def _fetch_next_page(self, topic_path: str) -> int:
        """Fetch next batch of entries for topic. Returns number of new records."""
        all_ids = self._topic_all_ids[topic_path]
        loaded = self._entry_cache[topic_path]
        offset = len(loaded)
        if offset >= len(all_ids):
            return 0
        next_ids = all_ids[offset:offset + self._entry_limit]
        records = self._vector_db.get_by_ids(next_ids)
        loaded.extend(records)
        return len(records)

    def _render_entries(self, topic_path: str) -> None:
        table = self.query_one("#entry-table", EntryTable)
        table.clear()
        records = self._entry_cache.get(topic_path, [])
        total = len(self._topic_all_ids.get(topic_path, []))
        showing = len(records)
        suffix = f" (showing {showing} of {total})" if showing < total else ""
        self.sub_title = f"{topic_path} — {total} entries{suffix}"
        for r in records:
            short_id = r.id[:12] + "..."
            table.add_row(short_id, topic_path, key=r.id)

    @on(EntryTable.RowHighlighted)
    def on_entry_highlighted(self, event: EntryTable.RowHighlighted) -> None:
        if not event.row_key or not event.row_key.value:
            return
        entry_id = str(event.row_key.value)
        detail = self.query_one("#entry-detail", EntryDetail)

        # Find entry in cache
        for records in self._entry_cache.values():
            for r in records:
                if r.id == entry_id:
                    detail.show_entry(r.properties)
                    return
        detail.clear_entry()

    def action_select_node(self) -> None:
        """Enter pressed: if leaf topic (no children) with entries, move to entry table."""
        tree_widget = self.query_one("#topic-tree", Tree)
        if not tree_widget.has_focus:
            return
        node = tree_widget.cursor_node
        if node is None or node.data is None:
            return
        if node.children:
            node.toggle()
            return
        table = self.query_one("#entry-table", EntryTable)
        if table.row_count > 0:
            table.focus()
            table.move_cursor(row=0)

    def action_load_more(self) -> None:
        """Load next page of entries for the current topic."""
        if not self._current_topic:
            self.notify("No topic selected", severity="warning")
            return
        loaded = self._fetch_next_page(self._current_topic)
        if loaded == 0:
            self.notify("All entries loaded")
            return
        self._render_entries(self._current_topic)
        self.notify(f"Loaded {loaded} more entries")

    def action_back_to_tree(self) -> None:
        """Escape: move focus back to topic tree."""
        self.query_one("#topic-tree", Tree).focus()


def run_browse(
    graph_db: GraphDB,
    vector_db: VectorDB,
    root: str | None = None,
    entry_limit: int = 20,
) -> None:
    app = BrowseApp(
        graph_db=graph_db, vector_db=vector_db,
        root=root, entry_limit=entry_limit,
    )
    app.run()
