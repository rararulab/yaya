# Wiki Log

Append-only chronological record of ingests, queries, and lint
passes. Never rewrite history; append only. Entry prefix is
`## [YYYY-MM-DD] <kind> | <title>` so `grep "^## \[" log.md`
parses.

## [2026-04-18] ingest | mypy strict tightening (issue #40)
Baseline `uv run mypy` was already clean against `strict = true` +
every individual flag pinned (the heavy lifting landed in earlier
PRs). This pass tightens the bar further: enabled
`disallow_any_unimported = true` and the off-by-default
`enable_error_code` set
(`redundant-expr`, `truthy-bool`, `truthy-iterable`,
`unused-awaitable`, `possibly-undefined`, `explicit-override`).
All 34 source files still pass with zero new violations — the
codebase was already at this bar; pinning the flags keeps it there.
`unused-ignore` deliberately omitted: half the existing ignores are
guardrails for asymmetric mypy/pyright narrowing where mypy may
stop firing in a future version, and `warn_unused_ignores` plus
pyright's `reportUnnecessaryTypeIgnoreComment = "error"` already
cover real staleness. Audited every `# type: ignore` in `src/` and
`tests/` against lesson #21 — every one carries a specific code
suffix and now also a rationale comment.
See: ../../pyproject.toml, lessons-learned.md#21


## [2026-04-17] ingest | Karpathy — LLM Wiki (gist 442a6bf)
Adopted the three-layer pattern (raw sources / wiki / schema) for
`docs/wiki/`. Added `AGENT.md`, `index.md`, this log, and
`sources/karpathy-llm-wiki.md`. Kept the existing
`lessons-learned.md` in place as the first `lessons/*` page.
See: sources/karpathy-llm-wiki.md, AGENT.md

## [2026-04-17] ingest | spec migration — .spec.md → .spec (issue #52)
Ported `kernel-bus-and-abi`, `kernel-agent-loop`, and `kernel-registry`
from Markdown-with-Gherkin `.spec.md` files to the canonical
agent-spec `.spec` format (YAML frontmatter + structured `Test:`
blocks with `Package:` + `Filter:`). `scripts/check_specs.sh` now
lints all four kernel specs in CI; previously it silently skipped
three. Added lesson #19 (author in tool format, not protocol-doc
pseudo-format). Prose examples in `AGENT.md`, `AGENTS.md`,
`CLAUDE.md`, `GOAL.md`, `docs/dev/architecture.md`,
`docs/dev/testing.md`, and package `AGENT.md` files updated from
`.spec.md` to `.spec`.
See: lessons-learned.md#19, ../../specs/

## [2026-04-17] ingest | BMAD-METHOD (bmad-code-org/BMAD-METHOD)
Adopted two disciplines: the 4-phase gate model (analysis → plan →
solutioning → implementation, gated on explicit artifacts) and
explicit HALT conditions (no stopping at "milestones" or "natural
breakpoints"). Both codified in `docs/dev/workflow.md`. Rejected
the full installer, XML workflow DSL, sprint-status YAML tracker,
party mode, and the BMB/TEA/BMGD/CIS sibling modules — out of
scope for yaya. Retrospective ceremony merged into the existing
Karpathy wiki lint operation; no duplicate cadence.
See: sources/bmad-method.md, ../dev/workflow.md

## [2026-04-18] ingest | ordered config loading (issue #23)
Landed `src/yaya/kernel/config.py`: a pydantic-settings `KernelConfig`
that resolves settings in fixed order — CLI flags → `YAYA_*` env vars
(with `__` delimiter for nested keys) → `$XDG_CONFIG_HOME/yaya/config.toml`
→ built-in defaults. Plugin sub-trees are accessed via
`KernelConfig.plugin_config(name)`; the registry now feeds that sub-tree
into `KernelContext.config` so `ctx.config` is finally populated
(previously hard-coded to `{}`). Two pydantic-settings quirks worth
noting: (1) `env_nested_delimiter` only nests declared fields, so a
custom `_NestedEnvExtras` source lifts `YAYA_<NS>__<KEY>` for arbitrary
plugin namespaces into `model_extra`; (2) `toml_file` is bound inside
`settings_customise_sources` so tests can monkeypatch the module-level
`CONFIG_PATH` between instantiations. New CLI: `yaya config show [--json]`
prints the merged config with secrets (`r".*(token|key|secret|password|passphrase).*"`)
redacted to `"***"`.
See: ../../specs/kernel-config.spec, ../dev/architecture.md

## [2026-04-18] ingest | kernel-bootstrap CLI commands (issue #15)
Landed the three remaining kernel-built-in CLI commands —
`yaya serve`, the rewritten `yaya hello`, and the `yaya plugin
{list, install, remove}` group — completing the 1.0 command surface
from `docs/dev/cli.md`. `serve` boots EventBus + PluginRegistry +
AgentLoop in-process, binds 127.0.0.1 only (no `--host` flag per
GOAL.md non-goals), picks a free port on `--port 0`, and opens the
browser only when a `web`-prefixed adapter plugin is loaded —
otherwise it warns via stderr and keeps the kernel up so `yaya
hello` still round-trips the bus. `plugin install` rejects shell
metacharacters before any subprocess via the registry's existing
`_validate_install_source`, refuses to prompt under `--json` (must
pass `--yes`), and honours `--dry-run`. `plugin remove` surfaces
the bundled-plugin `ValueError` as `ok=false` with a suggestion
pointing at `yaya update`. Signal handling uses
`asyncio.add_signal_handler` so Ctrl+C cleanly stops loop → registry
→ bus in that order.
See: ../../specs/cli-kernel-commands.spec, ../../src/yaya/cli/commands/serve.py, ../../src/yaya/cli/commands/hello.py, ../../src/yaya/cli/commands/plugin.py

## [2026-04-18] ingest | seed plugins (issue #14)
Landed the four non-adapter seed plugins — one per category — to
prove the plugin protocol end-to-end against the kernel registry
that shipped in PR #49. Each plugin is a bundled subpackage under
`src/yaya/plugins/<name>/` loaded through the same
`yaya.plugins.v1` entry-point ABI as third-party packages. `openai`
is the only LLM SDK accepted per AGENT.md §4; `tool_bash` uses
`asyncio.create_subprocess_exec` exclusively (never `shell=True`);
`memory_sqlite` runs stdlib `sqlite3` through `asyncio.to_thread`;
`strategy_react` implements the observe-think-act decision. Every
response event echoes `request_id` per lesson #15. Each plugin
ships a BDD `.spec` (0 WARN from `agent-spec lint`) + unit tests
under `tests/plugins/<name>/`.
See: ../../specs/plugin-strategy_react.spec, ../../specs/plugin-memory_sqlite.spec, ../../specs/plugin-llm_openai.spec, ../../specs/plugin-tool_bash.spec, ../../src/yaya/plugins/

## [2026-04-18] ingest | bundled llm_echo dev provider (issue #24)
Shipped `src/yaya/plugins/llm_echo/` so `yaya serve` round-trips the
kernel end-to-end without any API key — closes the 0.1 onboarding gap
exposed by the Playwright smoke (PR #74). The plugin filters
`llm.call.request` on `provider == "echo"` and replies with
`(echo) <last user message>` at zero token usage; sibling providers
coexist on the same subscription via the same payload-filter pattern
as `llm_openai`. Auto-selection lives in `strategy_react`:
`_provider_and_model` now sniffs `OPENAI_API_KEY` and falls back to
`("echo", "echo")` when the key is unset. Temporary env sniff with
TODO(#23) — provider-selection policy migrates to `ctx.config` once
the config-loading PR lands. Stdlib-only implementation; no LLM SDK.
See: ../../specs/plugin-llm_echo.spec, ../../src/yaya/plugins/llm_echo/, ../../src/yaya/plugins/strategy_react/plugin.py

## [2026-04-18] ingest | pi-web-ui landing (issue #66)
Replaced the 305-line vanilla-JS placeholder under
`src/yaya/plugins/web/static/` with a Vite-built integration of
`@mariozechner/pi-web-ui@0.67.6`. Applied the Dependency Rule
strictly (lesson 27): whitelisted `MessageList`,
`StreamingMessageContainer`, `Input`, `ConsoleBlock`; blacklisted
every pi-web-ui export that assumes browser-owned agent / API
keys / session storage, plus all of `@mariozechner/pi-agent-core`
and `@mariozechner/pi-ai`. A Vite `resolveId` plugin redirects
pi-web-ui's side-effecting `tools/index.js` auto-register module to
a local no-op stub so the bundle no longer pulls provider SDKs,
pdfjs, lmstudio, or ollama. WebSocket protocol unchanged; TS frame
types mirror `events.py` with an exhaustive `assertNever` (lesson
19). CI gains a Web UI job that runs `npm run check/test/build` and
fails if `static/` drifts.
See: ../../specs/plugin-web.spec, ../../src/yaya/plugins/web/, ../dev/web-ui.md
