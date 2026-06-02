# Changelog

## 0.1.0

Initial release.

- Single SQLite database for graph, vectors (sqlite-vec), submission log, and review queue
- CLI with JSON output for agent integration
- Topic tree with path-based identity and recursive queries
- Vector similarity search scoped per topic
- Deterministic UUID5 entry IDs from content
- Batch submit and similarity operations
- Review system with TUI, export/import, purge/recover
- Configurable entry schema via user-defined Python classes
- Session logging with undo support
- Reconcile command for cross-table consistency checks
