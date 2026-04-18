## Philosophy
Stdlib `sqlite3`-backed memory plugin. Persists entries to `<state_dir>/memory.db` and answers `memory.query` with a top-k `LIKE` match ordered by recency.

## External Reality
- [`docs/dev/plugin-protocol.md`](../../../../docs/dev/plugin-protocol.md) (Memory row).
- Contract: [`specs/plugin-memory-sqlite.spec`](../../../../specs/plugin-memory-sqlite.spec).
- Tests: `tests/plugins/memory_sqlite/`.

## Constraints
- `Category.MEMORY`. Subscribes to `memory.query` + `memory.write`.
- Stdlib `sqlite3` only — no `aiosqlite`, no ORM. Run every blocking DB call through the plugin's dedicated single-worker `ThreadPoolExecutor` (see `_run_db`) — `asyncio.to_thread` hops worker threads and races the `sqlite3.Connection` (lesson #20).
- Connection is **per-plugin-instance**, opened with `check_same_thread=False` and owned by the single-worker executor so concurrent sessions serialize at the thread level. `on_load` opens the connection AND the executor; `on_unload` closes both.
- Schema: `memory(id TEXT PRIMARY KEY, text TEXT NOT NULL, meta TEXT, ts REAL)`. `meta` is JSON-serialized.
- Every `memory.result` echoes `request_id` (lesson #15).
- Duplicate `entry.id` on `memory.write` → log WARNING and return (do NOT raise — duplicates are a data event, not a plugin bug).

## Interaction (patterns)
- `memory.write` with missing `entry.id` → generate a `uuid4().hex`.
- Default `k=5` if the query omits or mis-types it; `k<=0` coerced to default.
- Empty `query` matches everything (tails the most recent N entries by `ts`).
- Do NOT leak the connection across `on_unload`/`on_load` pairs — hot-reload would double-open.

## Budget & Loading
- Sibling: [`../AGENT.md`](../AGENT.md). Authoritative: [`docs/dev/plugin-protocol.md`](../../../../docs/dev/plugin-protocol.md#memory-kernel--memory).
