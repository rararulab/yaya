"""``yaya serve`` — boot the kernel + registry + agent loop.

The default command. Stands up an :class:`~yaya.kernel.bus.EventBus`, a
:class:`~yaya.kernel.registry.PluginRegistry`, and an
:class:`~yaya.kernel.loop.AgentLoop` in-process, then blocks until
``SIGINT`` / ``SIGTERM``.

Bind policy: ``127.0.0.1`` only — there is **no** ``--host`` flag
through 1.0 (see ``GOAL.md`` non-goals). Public exposure is the
operator's responsibility via their own reverse proxy.

Port selection: ``--port 0`` asks the OS for a free port using a
throwaway socket bind. Adapter plugins load through the registry and
start their own HTTP servers during ``on_load``; ``serve`` itself does
NOT start uvicorn. If no adapter plugin is loaded, the kernel is still
running — ``yaya doctor`` verifies the bus round-trip; the surface is
simply offline until an adapter lands (issue #16).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
import webbrowser
from pathlib import Path

import click
import typer

from yaya.cli import CLIState
from yaya.cli.output import emit_error, emit_ok, warn
from yaya.kernel import (
    PUBLIC_EVENT_KINDS,
    AgentLoop,
    Category,
    CompactionManager,
    EventBus,
    KernelConfig,
    LLMProvider,
    LLMSummarizer,
    MemoryTapeStore,
    PluginRegistry,
    SessionPersister,
    SessionStore,
    install_compaction_manager,
    install_session_persister,
    load_config,
)

EXAMPLES = """
Examples:
  yaya serve
  yaya serve --port 7456
  yaya serve --no-open
  yaya serve --strategy react
  yaya --json serve
"""

_BIND_HOST = "127.0.0.1"
"""Hard-coded bind — see module docstring and GOAL.md non-goals."""

_STRATEGY_CHOICES = ["react"]
"""Accepted ``--strategy`` values. Additions here must land alongside
real dispatch wiring (lesson #23) — unknown values exit 2 at argv time."""


def _pick_free_port() -> int:
    """Ask the OS for a free TCP port on the loopback interface.

    Racy by design: the returned port can be claimed by another process
    before an adapter plugin binds it. Acceptable for a local dev tool
    — the adapter will fail loudly if the race materialises.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((_BIND_HOST, 0))
        return int(sock.getsockname()[1])


def _read_resume_marker() -> str | None:
    """Return the session id recorded by ``yaya session resume`` if present.

    The marker is a plain-text file written by
    :func:`yaya.cli.commands.session._write_resume_marker` into the parent of
    :func:`yaya.kernel.session.default_session_dir`. Absent, empty, or
    unreadable markers return ``None`` — the flag path simply stays
    unarmed.
    """
    try:
        from yaya.kernel import default_session_dir
    except ImportError:  # pragma: no cover — defensive, kernel always present
        return None
    marker = default_session_dir().parent / "resume.target"
    try:
        value = marker.read_text(encoding="utf-8").strip()
    except OSError, UnicodeDecodeError:
        return None
    return value or None


def _has_web_adapter(snapshot: list[dict[str, str]]) -> bool:
    """Return True when at least one loaded plugin is a web adapter."""
    return any(
        row.get("category") == "adapter" and row.get("name", "").startswith("web") and row.get("status") == "loaded"
        for row in snapshot
    )


def _make_session_store(cfg: KernelConfig) -> SessionStore:
    """Build the :class:`SessionStore` for ``yaya serve``.

    Mirrors :func:`yaya.cli.commands.session._make_store` so the boot
    path and the ``yaya session ...`` subcommands agree on the backing
    store selection (see lesson #6 on parallel store construction).
    """
    if cfg.session.store == "memory":
        return SessionStore(store=MemoryTapeStore())
    if cfg.session.dir is not None:
        return SessionStore(tapes_dir=cfg.session.dir)
    return SessionStore()


# Kinds the compaction manager subscribes to — the closed public catalog
# minus the compaction events themselves (an event emitted by the
# manager must not trigger another compaction pass) and the kernel
# control-plane kinds which route on ``session_id="kernel"``.
_COMPACTION_SKIP_KINDS: frozenset[str] = frozenset({
    "session.compaction.started",
    "session.compaction.completed",
    "session.compaction.failed",
    "kernel.ready",
    "kernel.shutdown",
    "kernel.error",
    "plugin.loaded",
    "plugin.reloaded",
    "plugin.removed",
    "plugin.error",
})


async def _maybe_install_compaction(
    state: CLIState,
    *,
    cfg: KernelConfig,
    bus: EventBus,
    registry: PluginRegistry,
    store: SessionStore,
    workspace: Path,
) -> CompactionManager | None:
    """Wire auto-compaction when ``cfg.compaction.auto`` is set.

    Resolves a loaded :class:`~yaya.kernel.llm.LLMProvider` plugin via
    :meth:`PluginRegistry.loaded_plugins`; when none is available we
    warn and return ``None`` rather than silently disabling the
    feature (lesson #23 — an accepted knob must capture state or warn).

    Returns:
        The running :class:`CompactionManager` on success; ``None`` when
        auto-compaction is off or no provider is available.
    """
    if not cfg.compaction.auto:
        return None
    providers = registry.loaded_plugins(Category.LLM_PROVIDER)
    # The first loaded provider wins — this is the same rule the agent
    # loop will apply once multi-provider routing ships. Callers wanting
    # a specific provider pin it via ``plugins_disabled`` in config.
    provider: LLMProvider | None = None
    for plugin in providers:
        if isinstance(plugin, LLMProvider):
            provider = plugin
            break
    if provider is not None and len(providers) > 1:
        # Load order (bundled-first → third-party-alpha) is non-obvious
        # to users running two llm-providers side by side. Surface the
        # selected provider name so the choice is not silent (#95 N2).
        provider_name = getattr(provider, "name", provider.__class__.__name__)
        warn(
            f"[yellow]multiple llm-providers loaded; auto-compaction will use "
            f"{provider_name!r}[/] — pin a specific provider via config if this "
            "is not what you want."
        )
    if provider is None:
        warn(
            "[yellow]compaction.auto=true but no llm-provider plugin is loaded;[/] "
            "auto-compaction is disabled for this run. Install a provider "
            "plugin (e.g. yaya-llm-openai) or set compaction.auto=false."
        )
        del state  # unused; parameter preserved for future structured emit
        return None
    summarizer = LLMSummarizer(provider)
    kinds = sorted(k for k in PUBLIC_EVENT_KINDS if k not in _COMPACTION_SKIP_KINDS)
    return await install_compaction_manager(
        bus=bus,
        store=store,
        summarizer=summarizer,
        workspace=workspace,
        kinds=kinds,
        threshold_tokens=cfg.compaction.threshold_tokens,
        target_tokens=cfg.compaction.target_tokens,
    )


async def run_serve(  # noqa: C901 — linear lifecycle, each branch is a distinct transition
    state: CLIState,
    *,
    port: int,
    no_open: bool,
    strategy: str,
    dev: bool,
    shutdown_event: asyncio.Event | None = None,
    kernel_config: KernelConfig | None = None,
    resume: str | None = None,
) -> int:
    """Boot the kernel and wait until a signal (or ``shutdown_event``) fires.

    Args:
        state: Shared CLI state (JSON mode flag, verbosity).
        port: Requested port. ``0`` means auto-pick a free one.
        no_open: If True, suppress the browser launch attempt.
        strategy: Strategy plugin id. Currently only ``"react"`` is
            accepted; Click rejects unknown values at argv time, so
            this argument is effectively a single-element enum.
        dev: If True, reserved for a future vite-HMR proxy mode. The
            web adapter plugin owns the actual proxy behaviour.
        shutdown_event: Test-only hook. When provided, the caller drives
            shutdown by setting the event; signal handlers are NOT
            registered (the test owns the lifecycle).
        kernel_config: Optional pre-loaded kernel config (test hook).

    Returns:
        Process exit code (``0`` on clean shutdown, non-zero on startup
        failure).
    """
    # accepted for forward-compat; click.Choice already narrowed the value to "react".
    del strategy

    cfg = kernel_config or load_config()

    # ``--resume`` is accepted today and stashed on ``cfg.session.default_id``.
    # The persister installed below writes tapes; replay/rehydration of a
    # prior session is tracked as a separate follow-up (#153 out-of-scope:
    # UI click-to-resume). Two input paths feed the same config slot: the
    # explicit flag (this call) and the marker file written by
    # ``yaya session resume <id>`` on a previous invocation (lesson #23 —
    # an accepted flag must capture state or warn; silent no-op is banned).
    resume_target = resume or _read_resume_marker()
    if resume_target is not None:
        cfg.session.default_id = resume_target
        emit_ok(
            state,
            text=(
                "[yellow]--resume accepted; session kernel boot wiring lands in "
                "a follow-up — flag stashed on config.session.default_id.[/]"
            ),
            action="serve.resume.staged",
            session_id=resume_target,
        )

    # Merge order: explicit --port (non-zero) wins; otherwise fall back
    # to kernel_config.port (env / TOML); only when both are zero do
    # we ask the OS for a free port.
    if port != 0:
        bound_port = port
    elif cfg.port != 0:
        bound_port = cfg.port
    else:
        bound_port = _pick_free_port()

    # Lesson #23 — flags that don't yet dispatch must warn. ``--strategy``
    # typos are rejected by Click at argv time (see _STRATEGY_CHOICES),
    # so the only observably-inert flag left here is ``--dev``.
    if dev:
        warn(
            "[yellow]--dev is accepted but not yet implemented;[/] "
            "the vite HMR proxy ships with the web adapter plugin (#16)."
        )

    # Install the shutdown trigger BEFORE awaiting any startup coroutine.
    # ``KeyboardInterrupt`` is a ``BaseException``, not ``Exception`` —
    # a SIGINT arriving between ``EventBus()`` and ``registry.start()``
    # would otherwise escape past any ``except Exception`` and skip
    # teardown (lesson #29). Converting SIGINT into ``event.set()`` up
    # front keeps the invariant that the outer ``try/finally`` always
    # runs teardown for whatever did come up.
    owned_event = shutdown_event is None
    event = shutdown_event if shutdown_event is not None else asyncio.Event()

    if owned_event:
        aio_loop = asyncio.get_running_loop()

        def _trigger() -> None:
            if not event.is_set():
                event.set()

        for sig_name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, sig_name, None)
            if sig is None:
                continue
            # add_signal_handler is not available on every platform
            # (Windows ProactorEventLoop); when absent we fall back to
            # Python's default SIG behaviour — ``KeyboardInterrupt``
            # inside ``await event.wait()`` is caught explicitly below.
            with contextlib.suppress(NotImplementedError, RuntimeError):
                aio_loop.add_signal_handler(sig, _trigger)

    bus = EventBus()
    # Build the session store BEFORE the registry so the adapter
    # plugin's ``on_load`` (which captures ``ctx.session_store``) sees
    # a live reference instead of ``None``. Adapter plugins hydrate
    # chat history from this store; deferring construction would force
    # a rehydration hook that does not exist today.
    session_store: SessionStore = _make_session_store(cfg)
    registry = PluginRegistry(bus, kernel_config=cfg, session_store=session_store)
    loop = AgentLoop(bus, session_store=session_store, workspace=Path.cwd())
    session_persister: SessionPersister | None = None
    compaction_manager: CompactionManager | None = None
    registry_started = False
    loop_started = False

    try:
        try:
            await registry.start()
            registry_started = True
            await loop.start()
            loop_started = True
            # Persister install runs AFTER the loop starts so the
            # subscription chain lands on a fully-wired bus. Failures
            # here are fatal: the user explicitly picked a durable
            # backend, and silently running without persistence would
            # recreate the #153 bug.
            session_persister = await install_session_persister(
                bus=bus,
                store=session_store,
                workspace=Path.cwd(),
                kinds=sorted(PUBLIC_EVENT_KINDS),
            )
            # Compaction wiring runs AFTER the persister so the
            # llm-provider lookup has a non-empty load set and so
            # compaction tape writes land through the same store.
            # Failures here are non-fatal — the warn/skip path
            # preserves kernel uptime (lesson #29: broken subsystem
            # taints only itself).
            if cfg.compaction.auto:
                compaction_manager = await _maybe_install_compaction(
                    state,
                    cfg=cfg,
                    bus=bus,
                    registry=registry,
                    store=session_store,
                    workspace=Path.cwd(),
                )
        except Exception as exc:
            emit_error(
                state,
                error=f"kernel_startup_failed: {exc}",
                suggestion="run with -v / -vv for a detailed traceback",
            )
            return 1

        snapshot = registry.snapshot()
        web_present = _has_web_adapter(snapshot)
        if not web_present:
            warn(
                "[yellow]kernel is running but no web adapter plugin is loaded;[/] "
                "install an adapter plugin to interact with yaya. "
                "`yaya doctor` verifies the bus round-trip in the meantime."
            )

        emit_ok(
            state,
            text=(
                f"[green]yaya kernel live[/] on "
                f"[bold]{_BIND_HOST}:{bound_port}[/] (pid {os.getpid()})\n"
                "[dim]note: the web adapter may bind a different port; "
                "check http://127.0.0.1:<adapter-port>/api/health or set "
                "YAYA_WEB_PORT to pin it.[/]\n"
                "press Ctrl+C to stop."
            ),
            action="serve.started",
            addr=f"{_BIND_HOST}:{bound_port}",
            pid=os.getpid(),
        )

        if not no_open and web_present:
            # Best-effort; ``webbrowser.open`` can block for seconds on
            # macOS (launching Safari), so run it on the default
            # executor instead of stalling the event loop.
            # best-effort: executor thread may outlive Ctrl+C since asyncio.run()'s
            # default-executor shutdown does not forcibly join in-flight work.
            # Acceptable for a local dev tool; flagged in PR #85 re-review.
            try:
                await asyncio.get_running_loop().run_in_executor(
                    None,
                    webbrowser.open,
                    f"http://{_BIND_HOST}:{bound_port}/",
                )
            except Exception as exc:
                warn(f"[yellow]failed to open browser:[/] {exc}")

        # Either fallback path surfaces as an interrupt; treat both as
        # a clean shutdown signal — no log spam, just teardown.
        with contextlib.suppress(KeyboardInterrupt, asyncio.CancelledError):
            await event.wait()
    finally:
        # Stop compaction before the registry so in-flight compactions
        # cancel cleanly against a still-live llm-provider (lesson #29).
        if compaction_manager is not None:
            with contextlib.suppress(Exception):
                await compaction_manager.stop()
        # Stop the persister before closing the store — it still has
        # subscriptions that could fire during registry.stop() and
        # attempt a write on a closed tape otherwise.
        if session_persister is not None:
            with contextlib.suppress(Exception):
                await session_persister.stop()
        with contextlib.suppress(Exception):
            await session_store.close()
        if loop_started:
            await loop.stop()
        if registry_started:
            await registry.stop()
        await bus.close()

    emit_ok(
        state,
        text="[dim]kernel stopped.[/]",
        action="shutdown",
        reason="signal",
    )
    return 0


def register(app: typer.Typer) -> None:
    """Register the ``serve`` subcommand onto ``app``."""

    @app.command(epilog=EXAMPLES)
    def serve(
        ctx: typer.Context,
        port: int = typer.Option(
            0,
            "--port",
            min=0,
            max=65535,
            help="Port to bind on 127.0.0.1. 0 (default) auto-picks a free port.",
        ),
        no_open: bool = typer.Option(
            False,
            "--no-open",
            help="Do not auto-launch a browser.",
        ),
        dev: bool = typer.Option(
            False,
            "--dev",
            help="Reserved for the web adapter's vite HMR proxy (#16); warns when set.",
        ),
        strategy: str = typer.Option(
            "react",
            "--strategy",
            click_type=click.Choice(_STRATEGY_CHOICES),
            help="Strategy plugin id to activate. Only 'react' is accepted today.",
        ),
        resume: str | None = typer.Option(
            None,
            "--resume",
            help="Resume the named session (see `yaya session list`).",
        ),
    ) -> None:
        """Boot the yaya kernel and wait for shutdown."""
        state: CLIState = ctx.obj
        code = asyncio.run(
            run_serve(
                state,
                port=port,
                no_open=no_open,
                strategy=strategy,
                dev=dev,
                resume=resume,
            )
        )
        if code != 0:
            raise typer.Exit(code)
