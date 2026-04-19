"""``yaya config`` — live KV store CLI.

The legacy ``yaya config show`` (TOML + env snapshot) was replaced in
issue #104 by a live SQLite KV store. The four commands here are
the user surface for that store — get / set / unset / list — each
talking to :class:`yaya.kernel.config_store.ConfigStore`.

Commands:

* ``yaya config get <key>`` — print value (JSON).
* ``yaya config set <key> <value>`` — upsert. Values parsed as JSON
  first; on parse failure we store the raw string so quoting quirks
  stay forgiving for humans.
* ``yaya config unset <key>`` — remove. Exit 0 whether the key
  existed (idempotent).
* ``yaya config list [prefix]`` — list keys, optionally filtered by
  prefix. ``-v`` / ``--values`` includes values; secret-suffix keys
  are masked unless ``--show-secrets`` is passed.

All four honour the global ``--json`` flag for machine-readable
output per the rararulab agent-friendly-cli contract.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import typer
from rich.console import Console

from yaya.cli import CLIState
from yaya.cli.output import emit_error, emit_ok
from yaya.kernel.config_store import ConfigStore

EXAMPLES = """
Examples:
  yaya config set provider openai
  yaya config get provider
  yaya config list plugin.llm_openai.
  yaya config unset plugin.llm_openai.api_key
"""

# Suffixes that identify secret-bearing keys. Masking happens in the
# list command's display path only — the underlying store never
# redacts, so ``yaya config get api_key`` (single-key lookup) returns
# the full value. Rationale: a deliberate single-key lookup is an
# opt-in reveal; a broad ``list`` dump is ambient and must not leak.
_SECRET_SUFFIXES = ("api_key", "token", "secret", "password")

_stdout = Console()
_stderr = Console(stderr=True)


def _is_secret_key(key: str) -> bool:
    """Return True when ``key`` ends with a known secret suffix.

    Matches the last dotted segment so ``plugin.llm_openai.api_key``
    and the bare ``api_key`` both redact, without over-masking a key
    like ``apikeys_allowed`` that merely contains ``key`` as a
    substring.
    """
    last = key.rsplit(".", 1)[-1].lower()
    return last in _SECRET_SUFFIXES


def _mask_secret(value: Any) -> str:
    """Render a secret value as ``****<last4>`` or ``****`` for short strings.

    Only strings get the last-4-chars reveal; non-strings always
    collapse to ``****`` so we do not leak structure.
    """
    if not isinstance(value, str):
        return "****"
    if len(value) <= 4:
        return "****"
    return f"****{value[-4:]}"


def _run(coro: Any) -> Any:
    """Run an async helper in a fresh event loop.

    Typer's sync callbacks must own the loop lifetime — reusing a
    running loop (e.g. ``asyncio.get_event_loop``) risks "loop
    already running" errors under pytest-asyncio contexts.
    """
    return asyncio.run(coro)


def _do_get(state: CLIState, key: str) -> None:
    """Implementation of ``yaya config get``. Exits 1 when ``key`` is absent."""
    value = _run(_get(key))
    if value is _MISSING:
        if state.json_output:
            emit_error(
                state,
                error="key_not_found",
                suggestion=f"'yaya config list' to see available keys (tried {key!r})",
                key=key,
            )
        else:
            _stderr.print(f"[red]key not found:[/] {key}")
        raise typer.Exit(code=1)
    if state.json_output:
        emit_ok(state, text="", action="config.get", key=key, value=value)
        return
    _stdout.print(json.dumps(value, ensure_ascii=False))


def _do_set(state: CLIState, key: str, value: str) -> None:
    """Implementation of ``yaya config set``. Parses ``value`` as JSON-first."""
    parsed: Any
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = value
    try:
        _run(_set(key, parsed))
    except (TypeError, ValueError) as exc:
        emit_error(
            state,
            error=str(exc),
            suggestion="supply a JSON-encodable value (scalar, list, or dict)",
            key=key,
        )
        raise typer.Exit(code=1) from exc
    if state.json_output:
        emit_ok(state, text="", action="config.set", key=key, value=parsed)
        return
    _stdout.print(f"[green]set[/] {key}")


def _do_unset(state: CLIState, key: str) -> None:
    """Implementation of ``yaya config unset``. Always exits 0."""
    removed = _run(_unset(key))
    if state.json_output:
        emit_ok(state, text="", action="config.unset", key=key, removed=removed)
        return
    _stdout.print(f"[green]removed[/] {key}" if removed else f"[dim]not set[/] {key}")


def _render_list_entry(key: str, value: Any, *, show_values: bool, show_secrets: bool) -> Any:
    """Return the value to render for ``key`` under list -v flags.

    Factored out so ``--json`` and human modes use one redaction rule
    and the CLI register function stays under the ruff complexity gate.
    """
    if not show_values:
        return None
    if _is_secret_key(key) and not show_secrets:
        return _mask_secret(value)
    return value


def _do_list(state: CLIState, prefix: str, *, show_values: bool, show_secrets: bool) -> None:
    """Implementation of ``yaya config list``."""
    entries = _run(_list_prefix(prefix))
    if state.json_output:
        rendered: dict[str, Any] = {
            key: _render_list_entry(key, value, show_values=show_values, show_secrets=show_secrets)
            for key, value in entries.items()
        }
        emit_ok(state, text="", action="config.list", prefix=prefix, entries=rendered)
        return
    if not entries:
        _stdout.print("[dim](no keys)[/]")
        return
    for key, value in entries.items():
        if not show_values:
            _stdout.print(key)
            continue
        if _is_secret_key(key) and not show_secrets:
            displayed = _mask_secret(value)
        else:
            displayed = json.dumps(value, ensure_ascii=False)
        _stdout.print(f"{key} = {displayed}")


def register(app: typer.Typer) -> None:
    """Register the ``config`` subcommand group onto ``app``."""
    config_app = typer.Typer(
        name="config",
        help="Inspect and edit the live yaya config store.",
        no_args_is_help=True,
    )

    @config_app.command(name="get", epilog=EXAMPLES)
    def get(
        ctx: typer.Context,
        key: str = typer.Argument(..., help="Dotted config key (e.g. 'plugin.llm_openai.base_url')."),
    ) -> None:
        """Print the JSON-encoded value for ``key``. Exit 1 when missing."""
        _do_get(ctx.obj, key)

    @config_app.command(name="set", epilog=EXAMPLES)
    def set_(
        ctx: typer.Context,
        key: str = typer.Argument(..., help="Dotted config key."),
        value: str = typer.Argument(..., help="Value; parsed as JSON, else stored as string."),
    ) -> None:
        """Upsert ``key`` → ``value``.

        ``value`` is tried as JSON first (so ``true`` / ``42`` /
        ``"foo"`` / ``[1,2]`` land as typed primitives). On parse
        failure we fall back to the raw string so bare identifiers
        like ``openai`` work without shell-quoting a pair of double
        quotes.
        """
        _do_set(ctx.obj, key, value)

    @config_app.command(name="unset", epilog=EXAMPLES)
    def unset(
        ctx: typer.Context,
        key: str = typer.Argument(..., help="Dotted config key."),
    ) -> None:
        """Remove ``key``. Idempotent — exit 0 whether or not it existed."""
        _do_unset(ctx.obj, key)

    @config_app.command(name="list", epilog=EXAMPLES)
    def list_(
        ctx: typer.Context,
        prefix: str = typer.Argument("", help="Optional dotted prefix filter."),
        show_values: bool = typer.Option(
            False,
            "-v",
            "--values",
            help="Include values in the output (masked for secret keys).",
        ),
        show_secrets: bool = typer.Option(
            False,
            "--show-secrets",
            help="Do NOT mask values for secret-suffixed keys.",
        ),
    ) -> None:
        """List keys (and optionally values) under ``prefix``."""
        _do_list(ctx.obj, prefix, show_values=show_values, show_secrets=show_secrets)

    app.add_typer(config_app)


# ---------------------------------------------------------------------------
# Async helpers. Kept module-level so tests (and a future non-CLI caller)
# can drive them without standing up a Typer app.
# ---------------------------------------------------------------------------


class _Missing:
    """Sentinel distinguishing "key absent" from "key present with value None"."""


_MISSING = _Missing()


async def _get(key: str) -> Any:
    """Return the value for ``key`` or :data:`_MISSING` when absent."""
    store = await ConfigStore.open(bus=None)
    try:
        # ``default=_MISSING`` lets the caller tell "key absent" from
        # "key present with JSON null" without shadowing a real None.
        return await store.get(key, default=_MISSING)
    finally:
        await store.close()


async def _set(key: str, value: Any) -> None:
    """Set ``key`` → ``value`` in the default store."""
    store = await ConfigStore.open(bus=None)
    try:
        await store.set(key, value)
    finally:
        await store.close()


async def _unset(key: str) -> bool:
    """Unset ``key``; return True when a row was removed."""
    store = await ConfigStore.open(bus=None)
    try:
        return await store.unset(key)
    finally:
        await store.close()


async def _list_prefix(prefix: str) -> dict[str, Any]:
    """Return every key under ``prefix`` (empty string = whole store)."""
    store = await ConfigStore.open(bus=None)
    try:
        return await store.list_prefix(prefix)
    finally:
        await store.close()


__all__ = ["register"]
