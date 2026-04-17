# Plugin Protocol (v0 → 1.0)

yaya's kernel is deliberately small: an event bus, a plugin registry,
and a fixed agent loop. **Everything else is a plugin** — every user
surface, every LLM provider, every tool, every skill, every memory
backend, every next-action strategy. This document is the authoritative
contract.

- **v0** is the 0.1 shape we ship with. Shapes may evolve in 0.x.
- **1.0** freezes: event kinds, payload schemas, the registration ABI,
  the strategy interface, the adapter contract, and the web↔kernel WS
  schema. Plugins written against 1.0 keep working across 1.x.

## Plugin categories (closed set)

| Category | Role | Subscribes to | Emits |
|---|---|---|---|
| `adapter` | User surface (web, TUI, Telegram, …). Translates external I/O to kernel events and renders outgoing events. | `assistant.message.*`, `tool.call.start`, `plugin.*`, `kernel.*` | `user.message.received`, `user.interrupt` |
| `tool` | Executes a discrete action (run shell, read file, HTTP). | `tool.call.request` (filtered by tool name) | `tool.call.result` |
| `llm-provider` | Speaks to one LLM vendor (OpenAI, Anthropic, Ollama, …). | `llm.call.request` (filtered by provider id) | `llm.call.response`, `llm.call.error` |
| `strategy` | Decides the agent loop's next step. | `strategy.decide.request` | `strategy.decide.response` |
| `memory` | Stores and retrieves conversational state. | `memory.query`, `memory.write` | `memory.result` |
| `skill` | Domain-specific behavior built on top of the other categories. Subscribes to user messages (filtered) and orchestrates via kernel events. | `user.message.received` (filtered) | any public event via the kernel |

A plugin declares exactly one category. Multi-category plugins ship as
multiple packages.

## Event taxonomy (v0 — frozen at 1.0)

All events carry a common envelope:

```python
class Event(TypedDict):
    id: str              # uuid, kernel-assigned on publish
    kind: str            # dotted identifier — see catalog below
    session_id: str      # conversation scope; plugin-private events pick any stable id
    ts: float            # kernel-assigned unix epoch seconds
    source: str          # plugin name that emitted it (or "kernel")
    payload: dict        # kind-specific; shapes below
```

### Public event kinds (closed)

#### User input (adapter → kernel)

| kind | payload |
|---|---|
| `user.message.received` | `{ text: str, attachments?: list[Attachment] }` |
| `user.interrupt` | `{}` (ends the current turn) |

#### Assistant output (kernel → adapters)

| kind | payload |
|---|---|
| `assistant.message.delta` | `{ content: str }` (streaming chunk) |
| `assistant.message.done` | `{ content: str, tool_calls: list[ToolCall] }` |

#### LLM invocation (kernel ↔ llm-provider)

| kind | direction | payload |
|---|---|---|
| `llm.call.request` | kernel → provider | `{ provider: str, model: str, messages: list[Message], tools?: list[ToolSchema], params: dict }` |
| `llm.call.response` | provider → kernel | `{ text?: str, tool_calls?: list[ToolCall], usage: Usage, request_id?: str }` |
| `llm.call.error` | provider → kernel | `{ error: str, retry_after_s?: float, request_id?: str }` |

#### Tool execution (kernel ↔ tool)

| kind | direction | payload |
|---|---|---|
| `tool.call.request` | kernel → tool | `{ id: str, name: str, args: dict }` |
| `tool.call.start` | kernel → adapters (for UI) | `{ id: str, name: str, args: dict }` |
| `tool.call.result` | tool → kernel | `{ id: str, ok: bool, value?: Any, error?: str, request_id?: str }` |

#### Memory (kernel ↔ memory)

| kind | direction | payload |
|---|---|---|
| `memory.query` | kernel → memory | `{ query: str, k: int }` |
| `memory.write` | kernel → memory | `{ entry: MemoryEntry }` |
| `memory.result` | memory → kernel | `{ hits: list[MemoryEntry], request_id?: str }` |

#### Strategy (kernel ↔ strategy)

| kind | direction | payload |
|---|---|---|
| `strategy.decide.request` | kernel → strategy | `{ state: AgentLoopState }` |
| `strategy.decide.response` | strategy → kernel | `{ next: "llm" \| "tool" \| "memory" \| "done", request_id?: str, ... }` |

#### Plugin lifecycle (kernel → all)

| kind | payload |
|---|---|
| `plugin.loaded` | `{ name: str, version: str, category: str }` |
| `plugin.reloaded` | `{ name: str, version: str }` |
| `plugin.removed` | `{ name: str }` |
| `plugin.error` | `{ name: str, error: str }` |

#### Kernel (kernel → all)

| kind | payload |
|---|---|
| `kernel.ready` | `{ version: str }` |
| `kernel.shutdown` | `{ reason: str }` |
| `kernel.error` | `{ source: str, message: str }` |

### Extension namespace

Plugins may emit and subscribe to events named `x.<plugin>.<kind>`.
The kernel routes these through the same bus but **does not interpret
them and does not promise compatibility across versions**. Use this
namespace for plugin-private payloads (e.g., a `stats` skill plugin
emitting `x.stats.token.counted`). Do not use `x.*` as a workaround
for missing public events — propose a public event kind instead.

### What makes the set "closed"

- A PR that introduces a new **public** event kind (anything not under
  `x.<plugin>.<kind>`) is a **governance** change. It amends this
  document, `GOAL.md`'s plugin category table, and the Python
  `kernel/events.py` catalog in the same PR, and must carry the
  `governance` label.
- A PR that changes the **payload shape** of an existing public kind
  is a breaking change. Before 1.0, bump the minor version and note
  the migration. After 1.0, it requires a new kind (`foo.v2`) with
  both carried during a deprecation window.

## Plugin discovery and loading

Plugins are ordinary Python packages that expose a setuptools entry
point in the `yaya.plugins.v1` group:

```toml
# your-plugin's pyproject.toml
[project]
name = "yaya-tool-bash"
version = "0.1.0"

[project.entry-points."yaya.plugins.v1"]
bash = "yaya_tool_bash:plugin"
```

`yaya_tool_bash:plugin` resolves to a `Plugin` object (see ABI below).

The kernel discovers plugins in this order at boot:

1. **Bundled** — subpackages of `src/yaya/plugins/` declared in yaya's
   own `pyproject.toml` under the same entry-point group. Bundled
   plugins load through the **same protocol** as third-party plugins.
   No special cases.
2. **Installed** — any package in the active environment exposing a
   `yaya.plugins.v1` entry point (e.g., `pip install yaya-tool-bash`).
3. **Local overrides** — packages registered via `yaya plugin install
   <path>` (dev mode) in the user state directory.

`yaya plugin install <src>` accepts:
- A PyPI name: `yaya plugin install yaya-tool-bash` → shells to `pip install`.
- A local path: `yaya plugin install ./my-plugin` → editable install.
- A registry URL (2.0+): resolved through the future marketplace.

## Plugin ABI

Every plugin module exposes a `plugin` attribute conforming to this
interface:

```python
# pseudocode — authoritative Python lives in src/yaya/kernel/plugin.py

class Plugin(Protocol):
    name: str              # globally unique, kebab-case
    version: str           # semver
    category: Category     # one of the six categories above
    requires: list[str]    # other plugin names this depends on

    def subscriptions(self) -> list[str]:
        """Event kinds this plugin subscribes to.

        For `tool`, `llm-provider`, `strategy`, `memory`: the kernel
        filters by the category's default routing rules (see below).
        For `adapter` and `skill`: the plugin picks from the public
        event set; filtering by session_id is the plugin's job.
        """

    async def on_load(self, ctx: KernelContext) -> None:
        """Called once after registration, before any event delivery."""

    async def on_event(self, ev: Event, ctx: KernelContext) -> None:
        """Handle an event. Raise to surface a plugin.error."""

    async def on_unload(self, ctx: KernelContext) -> None:
        """Called on hot-reload or kernel shutdown. Must be idempotent."""
```

`KernelContext` gives the plugin an `emit(kind, payload, *,
session_id)` method, a scoped logger, access to its configuration,
and a state directory under `<XDG_DATA_HOME>/yaya/plugins/<name>/`.

### Category-specific extras

- **`tool`** declares `tool_name: str` and `json_schema: dict` for
  arguments. The kernel routes `tool.call.request` to the plugin whose
  `tool_name` matches.
- **`llm-provider`** declares `provider_id: str` (e.g., `"openai"`).
  The kernel routes `llm.call.request` by `payload.provider`.
- **`strategy`** declares `strategy_id: str`. Only one strategy is
  active per session; `yaya serve --strategy <id>` (or a per-session
  setting) selects it. Default: `react`.
- **`memory`** declares whether it is a "short-term" or "long-term"
  store. Kernel may route queries differently based on session age.
- **`adapter`** declares `adapter_id: str`. An adapter may be
  short-lived (one WebSocket session per user) or long-lived (Telegram
  polling loop).

## Agent loop (kernel-owned)

The loop shape is fixed. Each turn runs this sequence:

```
user.message.received
  → strategy.decide.request  →  strategy.decide.response
    → memory.query           →  memory.result (if requested)
    → llm.call.request       →  llm.call.response
      → tool.call.request    →  tool.call.result   (repeat per tool)
    → assistant.message.done
  → memory.write (if requested by strategy)
```

Strategies control: which tools to offer, when to call memory, when to
stop. Strategies **do not** change the ordering of the sequence —
that is the kernel's contract with adapters.

### Correlation via event id

Request/response pairs (`strategy.decide.*`, `llm.call.*`,
`memory.query` / `memory.result`, `tool.call.request` /
`tool.call.result`) are correlated by the **originating event's `id`**:
when a plugin responds, it MUST mirror the request event's `id` back on
its response payload as `request_id`. The kernel's agent loop stamps a
fresh event id on each outbound request and awaits the response whose
`request_id` equals that id. This is how concurrent in-flight calls on
the same session are matched to the right awaiter without introducing a
separate correlation channel. `request_id` is an additive optional
field on the five response payloads above (`strategy.decide.response`,
`llm.call.response`, `llm.call.error`, `memory.result`,
`tool.call.result`) — compatible with hand-crafted test fixtures, but
required in practice for the kernel loop to observe a response.

## Plugin failure model

- A plugin raising from `on_event` produces `plugin.error` and the
  kernel continues. Repeated failures (threshold configurable) unload
  the plugin and emit `plugin.removed`.
- A plugin hanging in `on_event` past a deadline (default 30s, per
  category) is cancelled; the same counter increments.
- `on_load` failure prevents registration; the plugin is marked
  `status: failed` in `yaya plugin list` with the stack trace in its
  state directory.
- **The kernel never crashes because a plugin did**. If the kernel
  itself raises, `kernel.error` fires, and `yaya serve` exits non-zero.

## Security posture (1.0)

- Plugins run in-process as trusted code. There is no sandbox in 1.0.
- `yaya plugin install` surfaces a confirmation prompt showing the
  source (PyPI / path / URL) and declared category.
- The future sandbox (2.0) will restrict plugins by category-default
  capability sets (e.g., `tool` plugins get no network unless they
  declare it).

## What NOT To Do

- Do NOT add a special code path for bundled plugins. They must load,
  subscribe, and fail through the same protocol as third-party plugins.
- Do NOT emit public event kinds from plugin-private code paths. Use
  the `x.<plugin>.<kind>` namespace.
- Do NOT introduce a parallel event channel (e.g., a "fast path" for
  adapter events). The bus is the bus.
- Do NOT let plugins import from `src/yaya/cli/` or from each other
  directly. Cross-plugin communication happens through events.
- Do NOT break the agent loop's event ordering contract in a strategy
  plugin. Strategies decide *content*, not *order*.
