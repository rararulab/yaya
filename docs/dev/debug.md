# Debug Playbook

yaya is designed so that every problem shows up in logs. Two concepts
make this work:

1. **Every bus event is logged** at DEBUG level with a stable, parseable
   structure.
2. **Correlation IDs** thread through every log line, so one turn is
   grep-able end-to-end across kernel and plugins.

This document is the first place to look when something is wrong. It
is written for both humans and AI agents investigating a report — the
"symptoms → search → interpret" playbooks below are meant to be
followed mechanically.

## Where are the logs?

| Sink | Level | Location | Use |
|---|---|---|---|
| stderr | INFO by default | the terminal running `yaya serve` | quick tailing |
| file | DEBUG always | `$XDG_STATE_HOME/yaya/logs/yaya.log` (fallback `~/.local/state/yaya/logs/yaya.log`) | everything — source of truth |
| JSON | whatever you set | set `YAYA_LOG_JSON=1` | agent / `jq` consumption |

Rotation: 10 MiB × 5 backups. Older turns may be in `yaya.log.1..5`;
use `less +G yaya.log*` to tail across rotations.

## IDs that matter

Every log line is bound with whichever of these apply. When you file a
bug or hand off a trace, quote the IDs.

| ID | Comes from | Groups |
|---|---|---|
| `session_id` | `SessionContext` (#36) | one chat thread across turns and devices |
| `turn_id` | `AgentLoop` (#12) | one user message → final assistant message |
| `tool_call_id` | LLM response / `Tool` contract (#27) | one tool invocation |
| `approval_id` | `ApprovalRuntime` (#28) | one HITL prompt + response |
| `plugin` | `KernelContext.logger` | which plugin emitted the line |
| `connection_id` | `ConnectionHandle` (#36) | which WebSocket / Telegram client |
| `request_id` | LLM vendor response header | one API call to OpenAI / Anthropic |

Rule: if a log line during an active turn is missing `session_id` +
`turn_id`, that is a bug — open an issue.

## Debug by symptom

### "The assistant said nothing"

1. Find the latest `turn_id`:

    ```
    grep "session.turn.started" yaya.log | tail -1
    ```

2. Filter the whole turn:

    ```
    grep "turn_id=<id>" yaya.log
    ```

3. Walk the canonical sequence; the first missing step is the failure:

    - `user.message.received` — did the message reach the kernel?
    - `strategy.decide.request` / `.response` — did the strategy plugin
      make a decision?
    - `llm.call.request` / `.delta` / `.response` — did the provider
      respond?
    - `assistant.message.done` — was the terminating emit reached?

### "Tool call didn't run"

1. Find the `tool_call_id` the LLM proposed:

    ```
    grep "llm.call.response" yaya.log | grep "tool_call"
    ```

2. Trace it forward:

    - `approval.request` — was the user prompted?
    - `approval.response` — did they approve, reject, or was it cancelled?
    - `tool.call.request` — was the tool dispatched?
    - `tool.call.result` — did it return?
    - `tool.error` — validation or execution error? Read its `kind` field.

### "Plugin crashed"

Every `PluginError` surfaces three ways:

- A `plugin.error` event on the bus (visible in tape and in logs).
- A log line at `ERROR` level with `plugin=<name>` and full traceback.
- An increment of the plugin's failure counter.

Investigate:

```
grep "plugin.error" yaya.log
grep "plugin=<name>" yaya.log | grep ERROR
```

After 3 consecutive failures the plugin auto-unloads; confirm with a
`plugin.removed` entry in the log. Fix the root cause, then
`yaya plugin install <name>` to re-register.

### "Session is stuck mid-turn"

Inspect the live context:

```
yaya session show <session-id> --live
```

Common causes:

- `approval.request` with no matching `approval.response` → user never
  answered. Check the web UI for a pending approval card.
- `llm.call.request` with no `.response` → upstream provider hang. Check
  the vendor status page with the `request_id`.
- `session.turn.queued` pileup while `session.turn.started` has no
  matching `session.turn.done` → a turn is genuinely stuck.

If truly hung: SIGINT the kernel; `yaya session resume <id>` on
restart. The tape preserves everything; only the live
`SessionContext` is lost, and it is always reconstructible from tape.

### "Answers contradict earlier turns"

Compaction (#29) dropped context that mattered. Dump the tape:

```
yaya session show <session-id> --format jsonl | less
```

Look for `anchor` entries with `kind=compaction/<n>`; the `state` field
there is the summary the LLM sees going forward. If the summary lost
a detail, tune `compaction.threshold` or raise
`compaction.max_preserved_messages` in config.

### "LLM provider errors"

Provider SDK errors are mapped to typed events (#26). Filter:

```
grep "llm.call.error" yaya.log | grep turn_id=<id>
```

Interpret the `kind`:

| `kind` | Meaning | First move |
|---|---|---|
| `connection` | Network-level failure | check connectivity, vendor status |
| `timeout` | Provider didn't respond in time | raise `llm.timeout` in config; inspect load |
| `status` | HTTP 4xx / 5xx from vendor | use `request_id` on vendor dashboard |
| `empty` | Vendor returned nothing usable | check tokens / rate limit / prompt shape |
| `other` | Unclassified | read the full traceback at ERROR level |

### "Two tabs show different things"

Each browser tab is a `ConnectionHandle` on one `SessionContext` (#36).
All connections on one session receive `broadcast_to_session` fanout.

Checks:

- `session.context.attached` has both connection_ids for the same
  session_id?
- Outbound events carry a `session_id`? (Events without one are not
  fanned out.)
- Neither WS closed silently? Search for
  `session.context.detached` with the laggy tab's connection_id.

### "Everything feels slow"

Every bus event is logged with `ts_start` and `ts_end` in DEBUG mode
(the kernel records timing around each handler call).

```
grep "handler=slow" yaya.log
# or programmatically:
grep "event=" yaya.log | jq 'select(.duration_ms > 500)'
```

- Slow `llm.call` → provider side; consider a smaller model or prompt.
- Slow tool → the plugin's `run()` is blocking the event loop; fix the
  plugin to be properly async.
- Slow startup → cold-start regression.

### "Plugin cannot be installed / entry-point missing"

1. Check `yaya plugin list --json` — does the plugin appear?
2. If not, check the discovery log:

    ```
    grep "entry_point=yaya.plugins.v1" yaya.log
    ```

3. If it appears but status is `failed`:

    ```
    grep "plugin=<name>" yaya.log | grep "on_load"
    ```

    `on_load` raised — read the traceback. Common causes: missing config
    section, missing dependency, permission error on state dir.

## What every log line should look like

Human mode:

```
2026-04-17T12:34:56.789Z | DEBUG    | plugin=llm_openai session=wksp-ab::default turn=t-01H... | llm.call.request model=gpt-4o tokens_estimate=1234
```

JSON mode (`YAYA_LOG_JSON=1`):

```json
{
  "ts": "2026-04-17T12:34:56.789Z",
  "level": "DEBUG",
  "msg": "llm.call.request",
  "plugin": "llm_openai",
  "session_id": "wksp-ab::default",
  "turn_id": "t-01H...",
  "model": "gpt-4o",
  "tokens_estimate": 1234
}
```

Secret fields (names matching `.*(token|key|secret|password).*`) are
redacted at serialisation time; the raw values never land in logs.

## Filing a bug

Attach:

- The `turn_id` and `session_id`.
- The log slice: `grep "turn_id=<id>" yaya.log > slice.log`.
- The tape dump: `yaya session show <session_id> --format jsonl > tape.jsonl`.
- Output of `yaya version` and `yaya plugin list --json`.

Do **not** attach the full `config.toml` — the log redactor blanks
tokens, but the config file is raw.

## Common mistakes

1. **Grepping without an ID.** Logs are dense. Always narrow by
   `turn_id`, `session_id`, or `plugin` first.
2. **Trusting stderr alone.** stderr defaults to INFO. Anything
   interesting lives in the DEBUG file sink.
3. **Confusing `Session` with `SessionContext`.** `Session` (#32) is
   the tape — durable, never lost. `SessionContext` (#36) is the live
   connection state — evicted after idle. Data you cannot find in
   stderr is almost always still in the tape.
4. **Restarting to "fix" a stuck turn instead of reading the logs.**
   A stuck turn almost always has a clear missing-event signature in
   the log. Find it; file an issue if the missing event is a kernel
   bug.
5. **Reading tape without `--format jsonl`.** The default human render
   strips detail; structured view is the source of truth.

## See also

- `logging + error taxonomy` contract: issue #30.
- `session + tape` primitives: issue #32.
- `session context` runtime: issue #36.
- Tool / approval / LLM contracts: issues #27 / #28 / #26.
- The closed event catalog: `docs/dev/plugin-protocol.md`.
