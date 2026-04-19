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
| `llm.call.delta` | provider → kernel | `{ content?: str, tool_call_partial?: dict, request_id?: str }` |
| `llm.call.response` | provider → kernel | `{ text?: str, tool_calls?: list[ToolCall], usage: Usage, request_id?: str }` |
| `llm.call.error` | provider → kernel | `{ error: str, kind?: "connection"\|"timeout"\|"status"\|"empty"\|"other", status_code?: int, retry_after_s?: float, request_id?: str }` |

#### Tool execution (kernel ↔ tool)

| kind | direction | payload |
|---|---|---|
| `tool.call.request` | kernel → tool | `{ id: str, name: str, args: dict, schema_version?: "v1", request_id?: str }` |
| `tool.call.start` | kernel → adapters (for UI) | `{ id: str, name: str, args: dict }` |
| `tool.call.result` | tool → kernel | `{ id: str, ok: bool, value?: Any, error?: str, envelope?: dict, request_id?: str }` |
| `tool.error` | kernel → originator | `{ id: str, kind: "validation" \| "not_found" \| "rejected", brief: str, detail?: dict, request_id?: str }` |

#### Approval (kernel ↔ adapter)

| kind | direction | payload |
|---|---|---|
| `approval.request` | kernel → adapter | `{ id: str, tool_name: str, params: dict, brief: str }` |
| `approval.response` | adapter → kernel | `{ id: str, response: "approve" \| "approve_for_session" \| "reject", feedback?: str }` |
| `approval.cancelled` | kernel → adapter | `{ id: str, reason: "timeout" \| "shutdown" }` |

All three envelopes route on the reserved `"kernel"` session id — NOT
the originating tool call's session. See **Approval flow** below for
the deadlock rationale (lesson #2).

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
| `kernel.error` | `{ source: str, message: str, detail?: dict }` |

#### Session lifecycle (kernel → all)

| kind | payload |
|---|---|
| `session.started` | `{ session_id: str, tape_name: str, workspace: str }` |
| `session.handoff` | `{ session_id: str, name: str, state: dict }` |
| `session.reset` | `{ session_id: str, archive_path: str \| null }` |
| `session.archived` | `{ session_id: str, archive_path: str }` |
| `session.forked` | `{ parent_id: str, child_id: str }` |

#### Conversation compaction (kernel → all)

All three events route on the reserved `session_id="kernel"` session.
The originating session is carried inside `payload.target_session_id`
so adapters can correlate progress against the right conversation.
Publishing on the originating session would deadlock its FIFO because
the handler that triggered the compaction check is still draining it
(same rule as `approval.*`).

| kind | payload |
|---|---|
| `session.compaction.started` | `{ target_session_id: str, tokens_before: int }` |
| `session.compaction.completed` | `{ target_session_id: str, tokens_before: int, tokens_after: int }` |
| `session.compaction.failed` | `{ target_session_id: str, error: str }` |

#### Live config store (kernel → all)

The live KV config store (`yaya.kernel.config_store`) emits one
`config.updated` per `set` / `unset` / migration write. Every event
routes on the reserved `session_id="kernel"` session (same rule as
`approval.*` and compaction): the originating session worker may be
mid-drain when a plugin calls into the config CLI, so the write
cannot fan out on that session without deadlocking its FIFO.

| kind | payload |
|---|---|
| `config.updated` | `{ key: str, prefix_match_hint: str }` |

`prefix_match_hint` is the key up to and including the final dot
(e.g. `"plugin.llm_openai."`) or the empty string when the key has
no dots — plugins can early-exit a subscription filter without
splitting the key themselves. Plugins that need to hot-reload
typically match `key.startswith("plugin.<name>.")`:
`llm_openai` rebuilds its `AsyncOpenAI` client on `api_key` /
`base_url` change, `strategy_react` reads `ctx.config["provider"]`
per decision so the next `strategy.decide.response` reflects a new
provider without restart.

#### Provider instances (kernel → llm-provider plugins)

The kernel reserves a flat `providers.<id>.*` namespace inside the
config store so one `llm-provider` plugin can back many configured
*instances* — e.g. one `llm-openai` plugin powering separate
"OpenAI prod", "Azure OpenAI", and "local-LM-Studio" records with
distinct `base_url` / `api_key` / `model` fields. The schema:

```
providers.<instance_id>.plugin    = "<plugin-name>"
providers.<instance_id>.label     = "<human label>"
providers.<instance_id>.<field>   = <value>          # free-form plugin schema
provider                          = "<instance_id>"  # active instance
```

`<instance_id>` is any dotted-safe string. `plugin` and `label` are
reserved meta fields — everything else lands in the plugin's schema.
Writes ride the normal `config.updated` event so hot-reload subscribers
just need to match `key.startswith("providers.")`.

**Read surface.** `KernelContext.providers` exposes a read-only
`ProvidersView` over the live store:

```python
rows = ctx.providers.list_instances()
mine = ctx.providers.instances_for_plugin("llm-openai")
active = ctx.providers.active_id            # the current `provider` key value
one = ctx.providers.get_instance("prod")    # returns None when absent
```

The view re-parses the store cache on every call, so subsequent writes
via `ConfigStore.set` are visible without cache invalidation. Writes
go through `ConfigStore.set` — this namespace layer is read-only.

**Bootstrap.** `PluginRegistry.start` runs a one-time pass guarded by
the `_meta.providers_seeded_at` marker. For every currently-loaded
`llm-provider` plugin it:

1. writes `providers.<plugin.name>.plugin = <plugin.name>`
2. writes `providers.<plugin.name>.label = "<plugin.name> (default)"`
3. lifts every `plugin.<ns>.<field>` legacy row (where `ns =
   plugin.name.replace("-", "_")`) into
   `providers.<plugin.name>.<field>`
4. when `provider` is unset, defaults it to the first seeded id

Legacy `plugin.<ns>.*` rows stay in place so pre-migration plugins
keep working; a follow-up garbage-collects them once every bundled
`llm-provider` reads instance-scoped config. The marker is stamped
last so a crash mid-bootstrap re-runs cleanly on next boot.

**Plugin iteration pattern** (for the coming D4b migration):

```python
async def on_load(self, ctx: KernelContext) -> None:
    view = ctx.providers
    if view is None:
        return  # test/transient context — fall back to ctx.config
    for instance in view.instances_for_plugin(self.name):
        self._instances[instance.id] = _Client.from_config(instance.config)
```

### Extension namespace

Plugins may emit and subscribe to events named `x.<plugin>.<kind>`.
The kernel routes these through the same bus but **does not interpret
them and does not promise compatibility across versions**. Use this
namespace for plugin-private payloads (e.g., a `stats` skill plugin
emitting `x.stats.token.counted`). Do not use `x.*` as a workaround
for missing public events — propose a public event kind instead.

**Private session ids.** Plugins may use session ids prefixed with
`_bridge:<plugin-name>` for internal routing (publishing to themselves
without touching a user-facing session). This reserves `_bridge:` as a
plugin-private namespace; user / adapter sessions MUST NOT start with
`_bridge:`. Example: `mcp_bridge` routes lifecycle events on
`_bridge:mcp-bridge`.

Currently in use by bundled plugins:

| Extension kind | Owner | Payload | Purpose |
|---|---|---|---|
| `x.mcp.server.ready` | `mcp_bridge` | `{ server: str, tools: list[{name, mcp_name, description}] }` | One emit per MCP server the bridge brought up at boot. |
| `x.mcp.server.error` | `mcp_bridge` | `{ server: str, kind: "config_invalid" \| "boot_failed", message: str }` | One emit per MCP server that failed config validation or exhausted boot retries. The bridge keeps running. |
| `x.agent.subagent.started` | `agent_tool` | `{ parent_id: str, child_id: str, goal: str, strategy: str, tools: list[str] \| null }` | One emit when `AgentTool.run` has forked the parent session and published `user.message.received` on the child. |
| `x.agent.subagent.completed` | `agent_tool` | `{ child_id: str, final_text: str, steps_used: int, forbidden_tool_hits: list[str] }` | One emit when the sub-agent's `assistant.message.done` resolves the run. |
| `x.agent.subagent.failed` | `agent_tool` | `{ child_id: str, reason: "timeout" \| "cancelled" }` | One emit when the sub-agent exhausts `max_wall_seconds` or the parent turn is cancelled. |
| `x.agent.allowlist.narrowed` | `agent_tool` | `{ child_id: str, attempted: list[str], allowed: list[str] }` | Emitted once at run end if the child issued any `tool.call.request` outside the observational allowlist. |

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

### Tools (v1 contract)

Since 0.2, tools declare their contract through a pydantic-backed
`Tool` base class in `yaya.kernel.tool`. Plugins on this path do
**not** implement `on_event` to route `tool.call.request` themselves
— the kernel's dispatcher does it for them.

```python
from typing import ClassVar
from yaya.kernel import KernelContext
from yaya.kernel.tool import Tool, ToolOk, ToolReturnValue, TextBlock, register_tool

class EchoTool(Tool):
    name: ClassVar[str] = "echo"
    description: ClassVar[str] = "Echo the input text."
    text: str  # parameters are ordinary pydantic fields

    async def run(self, ctx: KernelContext) -> ToolReturnValue:
        return ToolOk(brief=f"echo: {self.text[:40]}", display=TextBlock(text=self.text))

# In the plugin's on_load:
async def on_load(self, ctx: KernelContext) -> None:
    register_tool(EchoTool)
```

**JSON schema** is derived by `Tool.openai_function_spec()` →
`{"name", "description", "parameters": model_json_schema()}`,
directly compatible with the OpenAI chat-completions `tools` array
shape. Anthropic's Messages API accepts the same dict under a
different key, so adapters repack without rewriting.

**Return envelope** — `ToolOk` / `ToolError` each carry:

- `brief: str` — one-liner (≤80 chars) for logs and status panes.
- `display: DisplayBlock` — adapter-rendering hint. Built-ins:
  `TextBlock(kind="text", text=...)`,
  `MarkdownBlock(kind="markdown", markdown=...)`,
  `JsonBlock(kind="json", data=...)`.

`ToolError` additionally carries `kind: str` — one of
`"validation" | "timeout" | "rejected" | "crashed" | "internal"`.
Additional kinds may be introduced additively.

**Dispatcher behaviour** — `yaya.kernel.tool.dispatch` handles a
`tool.call.request` event whose payload's `schema_version` equals
`"v1"`:

1. Looks the tool up by `payload.name` in the registry. Unknown name →
   `tool.error` with `kind="not_found"`.
2. Validates `payload.args` against the tool's pydantic schema.
   Failure → `tool.error` with `kind="validation"` and
   `detail.errors` carrying pydantic's structured error list.
   `run()` is **not** called.
3. If `requires_approval=True`, calls `pre_approve(ctx)`. A `False`
   return → `tool.error` with `kind="rejected"`.
4. Calls `run(ctx)`. A raised exception is coerced into
   `tool.call.result` with a `ToolError(kind="crashed")` envelope —
   the kernel never lets a tool exception escape onto the bus.
5. Emits `tool.call.result` with `{"id", "ok", "envelope":
   <model_dump>, "request_id"}`.

**Approval runtime** — see the **Approval flow** section below. A
`Tool` subclass with `requires_approval: ClassVar[bool] = True`
routes through the runtime automatically; the default `pre_approve`
awaits the user's answer via bus events. Subclasses MAY override
`approval_brief(self) -> str` (≤80 chars) to give the prompt a
clearer headline.

**Backward compatibility** — A `tool.call.request` payload without
`schema_version` falls through to whatever plugin subscribed via
`on_event`. Bundled plugins that pre-date v1 (e.g. `tool_bash`) keep
working unchanged. If a legacy plugin and a v1 registration claim the
same tool name, the registry logs a WARNING; duplicate
`tool.call.result` emissions are possible until one path is retired.

**The new `tool.error` event kind** is a kernel → originator event
emitted only by the v1 dispatcher (never by plugins). Adapters
typically render it inline with the originating assistant turn rather
than as a tool-pane update, because the target tool never ran.

### Approval flow

Tools that mutate state (shell, filesystem writes, network writes)
declare `requires_approval: ClassVar[bool] = True`. The kernel's
approval runtime (see `yaya.kernel.approval.ApprovalRuntime`) runs
between validation and `run()`:

```
tool.call.request (session=S)
  → dispatcher validates args, finds requires_approval=True
  → runtime.request(Approval(id=A, session_id=S, ...))
    → approval.request (session="kernel")          # bus-routing session
      → adapter renders prompt to user
      → user clicks approve / approve_for_session / reject
    ← approval.response (session="kernel", id=A)
  ← ApprovalResult(id=A, response=..., feedback=...)
  → dispatcher calls tool.run()   OR   emits tool.error(kind="rejected")
```

**Routing on `"kernel"` (lesson #2).** All three approval events
(`approval.request`, `approval.response`, `approval.cancelled`) MUST
carry `session_id="kernel"` on the envelope. The dispatcher runs
inside the originating tool-call session's drain worker; that worker
is blocked on `await pending_future` while the prompt is outstanding.
A response delivered on the **same** session would queue behind the
blocked handler and only drain after the 60s approval timeout —
effectively a deadlock. Routing the response on `"kernel"` resolves
the future from a different worker and lets the original session
worker wake up.

**`approve_for_session` cache.** When the user picks
`approve_for_session`, the runtime caches the tuple
`(tool_name, params_fingerprint)` under the originating session id
(carried inside the `Approval` model, NOT the envelope's routing
session). Subsequent identical calls on the same session skip the
prompt entirely — exactly one `approval.request` is emitted per
unique tuple. Cache is in-memory, never persisted, never
auto-evicted in 0.2 (process-bounded).

**Timeout.** If the adapter does not publish an `approval.response`
within 60s (configurable per `ApprovalRuntime`), the runtime:

1. Pops the pending future (no leak, lesson #6).
2. Emits `approval.cancelled` with `reason="timeout"`.
3. Raises `ApprovalCancelledError`; the dispatcher converts this to
   `tool.error` with `kind="rejected"` and a `brief` that carries
   the cancellation reason.

**Shutdown.** `PluginRegistry.stop` uninstalls the runtime before
`kernel.shutdown`. Pending futures observe
`ApprovalCancelledError(reason="shutdown")` so the loop tears down
cleanly instead of hanging on the per-request timeout.

**Adapter responsibilities.**

1. Subscribe to `approval.request` and render the prompt — display
   `tool_name`, `params` (sanitise!), and `brief`.
2. Offer three actions: approve / approve_for_session / reject. A
   reject MAY collect a free-text `feedback`.
3. Publish `approval.response` with the user's answer. Echo the
   request `id` verbatim. Publish on session `"kernel"`.
4. Subscribe to `approval.cancelled` to withdraw stale prompts.

When an LLM emits **parallel tool calls** that each gate through the
approval runtime, adapters MAY observe multiple `approval.request`
events for the same `(tool_name, params)` before the user has
answered the first prompt. Adapters SHOULD stack or group these in
the UI (one row per `(tool_name, params)` with a count badge), and
once the first answer arrives they SHOULD auto-respond to the
remaining matching requests with the same decision so the user only
clicks once. This is a UI affordance only — the kernel does NOT
deduplicate at the protocol level (the `approve_for_session` cache
short-circuits subsequent identical calls but only AFTER the first
prompt resolves), and proposals to add protocol-level dedup are out
of scope through 1.0 (would require a request-coalescing key that
adapters cannot author for arbitrary new tools).

### LLM providers (v1 contract)

Since 0.2, llm-provider plugins implement the streaming
`LLMProvider` Protocol in `yaya.kernel.llm`. Providers yield an
async iterator of content / tool-call parts and a terminal
`TokenUsage`; the kernel re-emits each stream chunk as an
`llm.call.delta` and the final state as `llm.call.response`.

**SDK-only rule (normative).** LLM-provider plugins MUST use the
official `openai` or `anthropic` Python SDK. Raw `httpx`, community
wrappers, LangChain-style frameworks, and any other LLM client
library are **rejected at review**. The two approved SDKs cover the
market we care about:

- `openai` (`AsyncOpenAI`) — OpenAI, Azure OpenAI, and every
  OpenAI-compatible endpoint (DeepSeek, Moonshot, ollama, lm-studio,
  LiteLLM gateway) via `OPENAI_BASE_URL` + `OPENAI_API_KEY`.
- `anthropic` (`AsyncAnthropic`) — Claude; native tool use, prompt
  caching, and streaming.

Anything else (Gemini, Bedrock, Vertex) is deferred. When we add
support, we still wrap the vendor's official SDK — never a raw HTTP
client. The rule is mechanically enforced by
`scripts/check_banned_frameworks.py` (the `check_llm_plugin_imports`
rule scans every `src/yaya/plugins/llm_*/**/*.py` for direct imports
of `httpx` / `requests` / `aiohttp` and fails CI on a hit). The
SDKs themselves use `httpx` internally; that is fine because the
plugin does not import it.

```python
from typing import Any, ClassVar
from yaya.kernel import Category, KernelContext
from yaya.kernel.llm import (
    APIConnectionError,
    ContentPart,
    LLMProvider,
    StreamedMessage,
    TokenUsage,
    openai_to_chat_provider_error,
)

class OpenAIProvider:
    name: str = "openai"
    model_name: str = "gpt-4o-mini"
    thinking_effort: str = "off"

    async def generate(
        self,
        *,
        system_prompt: str,
        tools: list[dict[str, Any]],
        history: list[dict[str, Any]],
    ) -> StreamedMessage:
        try:
            return await self._stream_with_sdk(system_prompt, tools, history)
        except Exception as exc:
            raise openai_to_chat_provider_error(exc) from exc
```

**Token usage** — `TokenUsage` carries four raw counters
(`input_other`, `input_cache_read`, `input_cache_creation`, `output`)
and two derived values (`input`, `total`). The split exists because
Anthropic bills prompt-cache hits and cache writes separately; for
providers without cache accounting the extras stay zero and `input`
collapses to `input_other`. `model_dump()` includes both the raw and
derived values so the bus payload carries everything a cost tracker
needs.

**Delta stream** — `StreamedMessage.__aiter__()` yields
`ContentPart | ToolCallPart` objects. The kernel re-publishes each
as `llm.call.delta` with either `content` (text chunk) or
`tool_call_partial` (provider-specific partial tool-call dict). After
iteration the kernel reads `StreamedMessage.usage` and publishes one
terminal `llm.call.response` with `text`, `tool_calls`, and `usage`
populated.

**Typed errors** — providers raise `ChatProviderError` subclasses at
the plugin boundary; SDK-specific exceptions are translated via the
converters shipped in `yaya.kernel.llm`:

- `openai_to_chat_provider_error(exc)` — maps `openai.APIConnectionError`,
  `openai.APITimeoutError`, and `openai.APIStatusError` to the
  matching yaya subclass.
- `anthropic_to_chat_provider_error(exc)` — same mapping for the
  `anthropic` SDK. Lazy-imports so a missing install doesn't break
  kernel boot.
- `convert_httpx_error(exc)` — catches raw `httpx` errors that leak
  through the SDK envelope during streaming (a kimi-cli precedent).

Unknown exception types degrade to a generic `ChatProviderError`
with `str(exc)`. `llm.call.error` payloads carry a `kind` field
(`"connection" | "timeout" | "status" | "empty" | "other"`) plus
optional `status_code` for status errors and `request_id` for
correlation.

**Retry hook (shape-only)** — providers that want loop-driven
retries implement `RetryableChatProvider.on_retryable_error(exc,
attempt) -> bool`. The Protocol is frozen in 0.2; the retry runtime
lands in a follow-up PR.

**Backward compatibility** — bundled `llm_openai` and `llm_echo`
predate v1 and remain on the legacy `on_event`-subscribes-to-
`llm.call.request` path. Migration to the v1 contract is a follow-up
PR for each provider — same discipline as `tool_bash` staying on the
legacy path in the Tool-contract PR.

### Category-specific extras

- **`tool`** declares `tool_name: str` and `json_schema: dict` for
  arguments. The kernel routes `tool.call.request` to the plugin whose
  `tool_name` matches. Since 0.2 the preferred path is to declare a
  `Tool` subclass and call `register_tool()` — see "Tools (v1
  contract)" above.
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
  kernel continues. Each `plugin.error` attributed to a plugin
  increments its failure counter; a successful `on_event` invocation
  resets the counter to zero, so **N *consecutive* failures** — not N
  cumulative — triggers unload and emits `plugin.removed`. Default
  N = 3, configurable on the registry.
- A plugin hanging in `on_event` past a deadline (default 30s, per
  category) is cancelled; the same counter increments.
- `on_load` failure prevents registration; the plugin is marked
  `status: failed` in `yaya plugin list` with the stack trace in its
  state directory.
- Status ladder reported by `yaya plugin list` / `snapshot()`:
  `loaded → unloading → failed` for the threshold path (transient
  `unloading` between threshold breach and `on_unload` completion);
  `loaded → unloaded` for orderly `stop()` / `remove()`. `unloading`
  is observable so operators see in-flight unloads and so the registry
  can reject duplicate unload tasks from rival `plugin.error` events
  during the race window.
- **The kernel never crashes because a plugin did**. If the kernel
  itself raises, `kernel.error` fires, and `yaya serve` exits non-zero.

## Sessions and tape

A **session** is the kernel's canonical conversational state: an
append-only log of :class:`~republic.TapeEntry` values backed by a
:class:`~yaya.kernel.session.SessionStore`. Every bus event that
carries a "normal" ``session_id`` (i.e. not the reserved
``"kernel"`` session) is mirrored onto that session's tape by a
kernel subscriber; the LLM context is a derived view over the tape
(see :mod:`yaya.kernel.tape_context`), never a mutable history list
kept in memory.

### Persistence table

The bus auto-persister maps bus events onto tape entries:

| Bus event | Tape kind | Notes |
|---|---|---|
| `user.message.received` | `message` role=`user` | |
| `assistant.message.delta` | *(skipped)* | Too chatty — deltas only |
| `assistant.message.done` | `message` role=`assistant` | Final turn |
| `tool.call.request` | `tool_call` | `{id, name, args}` |
| `tool.call.result` | `tool_result` | Correlated by `tool_call_id` |
| `llm.call.delta` | *(skipped)* | Streaming chunk — too chatty |
| `session.*` | *(skipped)* | Lifecycle mirrors the tape itself |
| any other public kind | `event` | `name=<kind>`, `data=<payload>` |

Plugins can opt an event out of persistence with a kernel-level
`persist=False` key on the payload (accepted as `"__persist__"` too
for extensions that want to keep the top-level payload clean).
Anything on session `"kernel"` is never persisted.

### Tape naming

Every tape is named `md5(workspace_abspath)[:16] + "__" +
md5(session_id)[:16]` so two sessions with the same `session_id` in
different workspaces live on different tapes. MD5 is a
collision-tolerant identifier — not a security primitive — hence
`hashlib.md5(..., usedforsecurity=False)`.

### Fork / compaction / reset

- **Fork** (`Session.fork(child_id)`): returns a child session backed
  by an overlay store. Reads see parent-entries + child-entries;
  writes land only on the child; `reset()` on the child does not
  touch the parent. Tooling for subagents.
- **Compaction**: append a summary anchor via `Session.handoff(name,
  state)` and run subsequent context queries with
  `after_last_anchor` (see :func:`yaya.kernel.tape_context.after_last_anchor`).
  Compaction never rewrites past entries — the summarised prefix
  stays on disk and is filtered out of the LLM context only.
- **Reset** (`Session.reset(archive=True)`): archives the current
  entries to `tapes/.archive/<tape>.jsonl.<stamp>.bak`, clears the
  tape, and re-seeds a `session/start` anchor. Safe for
  long-running conversations that have drifted off-topic.

### Auto-persister guarantees

- **Best-effort, not transactional.** A tape-write failure logs at
  WARNING and emits a `plugin.error` with `source="kernel-session-persister"`;
  the session keeps receiving events. Losing an observational entry
  is strictly better than halting the session worker.
- **No bus recursion.** The persister writes entries directly — it
  never re-publishes on the bus. Failure notifications use a
  non-`"kernel"` source so the bus recursion guard (lesson #2)
  still surfaces them through `plugin.error`.
- **Kernel session skipped.** Events emitted on session `"kernel"`
  (approval prompts, plugin lifecycle, kernel errors, etc.) do not
  land on any tape. Those events belong to the control plane; if
  an adapter needs to render them it subscribes to them directly.

## Multi-connection fanout (#36)

A `Session` (the tape) is durable; a `SessionContext` is the
runtime overlay that lets **multiple connections** — two browser
tabs, web + TUI, phone on the LAN behind the user's own reverse
proxy — attach to the same session and observe a consistent event
stream. GOAL.md caps scope at single-process local-first through
1.0; "multi-connection" means many clients in one yaya process,
never cross-host sync.

### Connection lifecycle

1. Adapter receives a client connection and calls
   `SessionManager.attach(session_id, send_cb, adapter_id=..., since_entry=...)`.
2. The manager lazy-creates a `SessionContext` (backed by the
   matching `Session`) and appends a `Connection` handle with a
   fresh uuid4 hex id, heartbeat timestamp, and adapter label.
3. When `since_entry` is set, the context holds a per-connection
   `asyncio.Lock` and replays every tape entry whose `id` is
   strictly greater than `since_entry` as a `session.replay.entry`
   event. A terminating `session.replay.done` closes the replay.
4. After replay, live bus events tagged with the session id fan
   out to every attached connection via their `send_cb`. Send
   failures translate to a quiet `detach(reason="send_failed")`;
   the bus / surviving connections are unaffected.
5. Adapters keep the connection alive with
   `SessionManager.heartbeat(session_id, connection_id)`. Clients
   silent for more than `heartbeat_timeout_s` (default 60 s) are
   reaped and surface `session.context.detached(reason="timeout")`.
   The detection latency floor is
   `[heartbeat_timeout_s, heartbeat_timeout_s + reap_interval_s)`
   because the reap loop polls every 5 s
   (`_REAP_INTERVAL_S` in `session_context.py`). Operators who want
   sub-second timeouts MUST lower **both** `heartbeat_timeout_s`
   and the reap cadence; tuning only the timeout lands a stale
   connection in the tail of a 5 s poll regardless of how small the
   timeout is set.
6. Normal teardown: the adapter calls
   `SessionManager.detach(session_id, connection_id)`. On kernel
   shutdown, `SessionContext.close` emits
   `session.context.detached(reason="shutdown")` for every
   remaining connection before clearing the registry.

### Replay protocol

- Every tape entry has a monotonic `id`
  (`republic.TapeEntry.id`). The client persists the highest `id`
  it has rendered.
- On reconnect, the adapter calls
  `attach(..., since_entry=last_id)`. The context queries
  `Session.entries()`, filters to `id > since_entry`, wraps each
  survivor in a `session.replay.entry` envelope, and pushes them
  in order. A final `session.replay.done` event marks catch-up
  complete.
- Live events arriving during replay buffer behind the
  per-connection lock so nothing is dropped or duplicated — the
  lock is released synchronously when `session.replay.done` is
  flushed.
- Connections that joined at the start of time use
  `since_entry=0` (entry ids start at 1) to receive the full
  tape; callers cold-starting without replay pass `None`.

### Lifecycle event payloads

Every `session.context.*` / `session.replay.*` envelope rides
`session_id="kernel"` (lesson #2) with `source="kernel-session-context"`:

| kind | payload |
|---|---|
| `session.context.attached` | `{session_id, connection_id, adapter_id}` |
| `session.context.detached` | `{session_id, connection_id, reason}` with `reason ∈ {"client_close","timeout","shutdown","send_failed"}` |
| `session.context.evicted`  | `{session_id, reason}` — reserved for idle-context eviction; the background scheduler lands with the web adapter |
| `session.replay.entry`     | `{session_id, entry_id, kind, payload}` |
| `session.replay.done`      | `{session_id, connection_id, replayed}` |

### Adapter-side wire format

The bundled web adapter serialises each event envelope to JSON
over its WebSocket transport using the `{id, kind, session_id,
ts, source, payload}` shape. The `connection_id` is returned in
the WebSocket's handshake reply so the client can persist it and
send it back on reconnect alongside `since_entry=<last_id>`. See
`docs/dev/web-ui.md` for the concrete WS schema (the web adapter
integration lands in a follow-up that consumes this primitive).

## Sub-agents via the `agent` tool (#34)

Multi-agent in yaya is **not** a new plugin category. Spawning a
sub-agent is done through one bundled `tool` plugin — `agent-tool` —
that reuses the kernel primitives already in place:
`Session.fork()` (tape overlay from #32), the running `AgentLoop` on
the shared event bus, the v1 tool contract with approval runtime.

**Child session id.** The tool generates the child id as
`<parent>::agent::<uuid8>`. The `::agent::` separator is the depth
counter: a root session has depth 0, a first-generation sub-agent
depth 1, and so on. Depth `≥ max_depth` (default 5, overridable via
`[agent_tool] max_depth` in TOML) refuses the spawn with
`ToolError(kind="rejected")` before any fork.

**Approval.** `AgentTool.requires_approval = True` is mandatory — the
user sees one prompt per spawn. Tool calls *within* the sub-agent go
through the same approval runtime; `approve_for_session` entries
granted on the parent are visible to the child through the runtime's
session cache.

**Parent tape isolation.** Child writes land only on the overlay
store owned by the forked manager; the parent's tape is untouched.
Operators who want the full child log use `yaya session show
<child_id>` against the forked manager.

**Events.** Plugin-private extension events, routed on a stable
bridge session id `_bridge:agent-tool` (lesson #2 — never interleave
with a conversation FIFO):

- `x.agent.subagent.started(parent_id, child_id, goal, strategy, tools)`
- `x.agent.subagent.completed(child_id, final_text, steps_used, forbidden_tool_hits)`
- `x.agent.subagent.failed(child_id, reason)` — `reason ∈ {"timeout", "cancelled"}`
- `x.agent.allowlist.narrowed(child_id, attempted, allowed)` — one
  event per run when an allowlist was supplied and a child call fell
  outside it. At 0.2 this is observational only; hard kernel-side
  enforcement is future work.

## Web HTTP API

The bundled `web` adapter plugin exposes an HTTP admin surface on
top of the kernel's `ConfigStore` and `PluginRegistry`. The API is
the browser UI's control plane. It is **unauthenticated** — `yaya
serve` binds `127.0.0.1` only through 1.0, so local-only is the
sole authorization. Operators fronting yaya with a reverse proxy
accept the risk.

All routes live under `/api/`. JSON in, JSON out. `text/plain` error
responses carry a `detail` field per FastAPI's default shape.

### Config endpoints

| Method | Path | Body | Response |
|---|---|---|---|
| `GET` | `/api/config` | — | `{<key>: <value-or-mask>, ...}` |
| `GET` | `/api/config/{key}` | — | `{"key": str, "value": any}` (masked unless `?show=1`) |
| `PATCH` | `/api/config/{key}` | `{"value": any}` | `{"key": str, "ok": true}` |
| `DELETE` | `/api/config/{key}` | — | `{"key": str, "removed": bool}` |

Masking rule (mirrors `yaya config list`): keys whose last dotted
segment is one of `api_key`, `token`, `secret`, `password` collapse
to `****<last4>` (strings) or `****` (non-strings / ≤4-char values).
`?show=1` on `GET /api/config/{key}` bypasses the mask for a
deliberate single-key read.

### Plugin endpoints

| Method | Path | Body | Response |
|---|---|---|---|
| `GET` | `/api/plugins` | — | `{"plugins": [{name, category, status, version, enabled, config_schema, current_config}, ...]}` |
| `PATCH` | `/api/plugins/{name}` | `{"enabled": bool}` | `{"name": str, "enabled": bool, "reload_required": true}` |
| `POST` | `/api/plugins/install` | `{"source": str, "editable": bool}` | `{"source": str, "ok": true}` |
| `DELETE` | `/api/plugins/{name}` | — | `{"name": str, "removed": true}` |

`config_schema` is the plugin's pydantic `ConfigModel` JSON Schema
(or `null` when the plugin does not declare one). `current_config`
is a nested dict of the plugin's `plugin.<ns>.*` keys from
ConfigStore. `enabled` reads `plugin.<ns>.enabled` (default `true`)
and takes effect on the next kernel reload — the admin API does NOT
mutate a running plugin's subscription set.

Install forwards the `source` string through
`yaya.kernel.registry.validate_install_source` before calling
`registry.install`. Disallowed characters / schemes → 400; pip
failures → 500. Removing a bundled plugin → 400 (registry raises
`ValueError`).

### LLM-provider endpoints

| Method | Path | Body | Response |
|---|---|---|---|
| `GET` | `/api/llm-providers` | — | `{"providers": [{name, version, active, config_schema, current_config}, ...]}` |
| `PATCH` | `/api/llm-providers/active` | `{"name": str}` | `{"active": str, "ok": true}` |
| `POST` | `/api/llm-providers/{name}/test` | — | `{"ok": bool, "latency_ms": int, "error"?: str}` |

`active` is derived from config key `provider`; the PATCH endpoint
writes that key and the running strategy picks it up on the next
turn. The target must be a loaded `llm-provider` plugin; category
mismatch → 400, unknown plugin → 404.

`POST /api/llm-providers/{name}/test` fires one `llm.call.request`
with a trivial prompt and a fresh session id, subscribes to both
`llm.call.response` and `llm.call.error`, and waits up to 5 s. The
response carries `ok`, `latency_ms`, and (on failure) the provider's
error string — `"timeout"` when no reply arrived.

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
