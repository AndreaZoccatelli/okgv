"""Interactive TUI for reviewing entries using Textual."""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Static

from rich.text import Text

from okgv.core import review_list, review_update
from okgv.protocols import VectorDB


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
        self._load_entries()
        table = self.query_one("#table", DataTable)
        table.cursor_type = "row"
        table.cursor_foreground_priority = "renderable"
        table.add_columns("ID", "Topic", "Status")
        self._refresh_table()
        self._update_status()

    def _load_entries(self) -> None:
        self._entries = review_list(
            self._db_path, status="pending", topic=self._topic, limit=self._limit,
        )
        if self._entries:
            entry_ids = [e["entry_id"] for e in self._entries]
            records = self._vector_db.get_by_ids(entry_ids)
            self._content = {r.id: r.properties for r in records}

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
        self.title = f"{topic_str}pending:{pending} approved:{approved} rejected:{rejected}{unsaved}"

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
