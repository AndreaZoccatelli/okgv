"""Interactive TUI for reviewing entries using Textual."""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Static

from okgv.core import review_list, review_update
from okgv.protocols import VectorDB


class DetailPanel(Static):
    """Shows full content of selected entry."""

    def update_entry(self, properties: dict) -> None:
        lines = []
        for key, value in properties.items():
            text = str(value)
            if len(text) > 200:
                text = text[:200] + "..."
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
    #status-bar {
        dock: bottom;
        height: 1;
        background: $accent;
        color: $text;
        padding: 0 1;
    }
    DataTable {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("a", "approve", "Approve"),
        Binding("r", "reject", "Reject"),
        Binding("s", "skip", "Skip/Next"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        log_db: Path,
        vector_db: VectorDB,
        topic: str | None = None,
        limit: int = 100,
    ):
        super().__init__()
        self._log_db = log_db
        self._vector_db = vector_db
        self._topic = topic
        self._limit = limit
        self._entries: list[dict] = []
        self._content: dict[str, dict] = {}
        self._approved = 0
        self._rejected = 0

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            with Vertical(id="table-container"):
                yield DataTable(id="table")
            with Vertical(id="detail-container"):
                yield DetailPanel(id="detail")
        yield Static(id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        self._load_entries()
        table = self.query_one("#table", DataTable)
        table.cursor_type = "row"
        table.add_columns("ID", "Topic", "Status")
        self._refresh_table()
        self._update_status()

    def _load_entries(self) -> None:
        self._entries = review_list(
            self._log_db, status="pending", topic=self._topic, limit=self._limit,
        )
        if self._entries:
            entry_ids = [e["entry_id"] for e in self._entries]
            records = self._vector_db.get_by_ids(entry_ids)
            self._content = {r.id: r.properties for r in records}

    def _refresh_table(self) -> None:
        table = self.query_one("#table", DataTable)
        table.clear()
        for e in self._entries:
            short_id = e["entry_id"][:12] + "..."
            table.add_row(short_id, e["topic"], e["status"], key=e["entry_id"])
        if self._entries:
            table.move_cursor(row=0)
            self._show_detail(self._entries[0]["entry_id"])

    def _update_status(self) -> None:
        pending = sum(1 for e in self._entries if e["status"] == "pending")
        topic_str = f" | topic: {self._topic}" if self._topic else ""
        self.query_one("#status-bar", Static).update(
            f" Pending: {pending} | Approved: {self._approved} | Rejected: {self._rejected}{topic_str}"
        )

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
        entry_id = self._current_entry_id()
        if entry_id is None:
            return
        review_update(self._log_db, [entry_id], status)
        for e in self._entries:
            if e["entry_id"] == entry_id:
                e["status"] = status
                break
        if status == "approved":
            self._approved += 1
        elif status == "rejected":
            self._rejected += 1
        # Remove from table and advance
        table = self.query_one("#table", DataTable)
        cursor_row = table.cursor_coordinate.row
        table.remove_row(entry_id)
        self._entries = [e for e in self._entries if e["entry_id"] != entry_id]
        if self._entries:
            new_row = min(cursor_row, len(self._entries) - 1)
            table.move_cursor(row=new_row)
            self._show_detail(self._entries[new_row]["entry_id"])
        else:
            self.query_one("#detail", DetailPanel).update("[dim]All entries reviewed[/dim]")
        self._update_status()

    def action_approve(self) -> None:
        self._mark_entry("approved")

    def action_reject(self) -> None:
        self._mark_entry("rejected")

    def action_skip(self) -> None:
        table = self.query_one("#table", DataTable)
        if table.row_count == 0:
            return
        cursor_row = table.cursor_coordinate.row
        new_row = (cursor_row + 1) % table.row_count
        table.move_cursor(row=new_row)
        self._show_detail(self._entries[new_row]["entry_id"])

    @on(DataTable.RowHighlighted)
    def on_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key and event.row_key.value:
            self._show_detail(str(event.row_key.value))


def run_tui(log_db: Path, vector_db: VectorDB, topic: str | None = None, limit: int = 100) -> None:
    app = ReviewApp(log_db=log_db, vector_db=vector_db, topic=topic, limit=limit)
    app.run()
