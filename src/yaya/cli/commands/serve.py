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
running — ``yaya hello`` verifies the bus round-trip; the surface is
simply offline until an adapter lands (issue #16).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
import webbrowser

import typer

from yaya.cli import CLIState
from yaya.cli.output import emit_error, emit_ok, warn
from yaya.kernel import AgentLoop, EventBus, KernelConfig, PluginRegistry, load_config

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


def _pick_free_port() -> int:
    """Ask the OS for a free TCP port on the loopback interface.

    Racy by design: the returned port can be claimed by another process
    before an adapter plugin binds it. Acceptable for a local dev tool
    — the adapter will fail loudly if the race materialises.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((_BIND_HOST, 0))
        return int(sock.getsockname()[1])


def _has_web_adapter(snapshot: list[dict[str, str]]) -> bool:
    """Return True when at least one loaded plugin is a web adapter."""
    return any(
        row.get("category") == "adapter" and row.get("name", "").startswith("web") and row.get("status") == "loaded"
        for row in snapshot
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
) -> int:
    """Boot the kernel and wait until a signal (or ``shutdown_event``) fires.

    Args:
        state: Shared CLI state (JSON mode flag, verbosity).
        port: Requested port. ``0`` means auto-pick a free one.
        no_open: If True, suppress the browser launch attempt.
        strategy: Strategy plugin id to prefer once per-plugin config
            plumbing lands. Currently accepted but not yet dispatched.
        dev: If True, reserved for a future vite-HMR proxy mode. The
            web adapter plugin owns the actual proxy behaviour.
        shutdown_event: Test-only hook. When provided, the caller drives
            shutdown by setting the event; signal handlers are NOT
            registered (the test owns the lifecycle).

    Returns:
        Process exit code (``0`` on clean shutdown, non-zero on startup
        failure).
    """
    cfg = kernel_config or load_config()

    # Merge order: explicit --port (non-zero) wins; otherwise fall back
    # to kernel_config.port (env / TOML); only when both are zero do
    # we ask the OS for a free port.
    if port != 0:
        bound_port = port
    elif cfg.port != 0:
        bound_port = cfg.port
    else:
        bound_port = _pick_free_port()

    bus = EventBus()
    registry = PluginRegistry(bus, kernel_config=cfg)
    loop = AgentLoop(bus)

    try:
        await registry.start()
        await loop.start()
    except Exception as exc:
        emit_error(
            state,
            error=f"kernel_startup_failed: {exc}",
            suggestion="run with -v / -vv for a detailed traceback",
        )
        # Best-effort teardown of whatever did come up.
        await loop.stop()
        await registry.stop()
        await bus.close()
        return 1

    # Lesson #23 + #10 — flags that don't yet dispatch must warn, not silently ignore.
    if strategy != "react":
        warn(
            f"[yellow]--strategy {strategy!r} is accepted but not yet dispatched;[/] "
            "config plumbing landed in #23 (ctx.config is now populated from env + "
            "config.toml), but strategy dispatch on top of it ships separately. "
            "Falling back to the default strategy plugin."
        )
    if dev:
        warn(
            "[yellow]--dev is accepted but not yet implemented;[/] "
            "the vite HMR proxy ships with the web adapter plugin (#16)."
        )

    snapshot = registry.snapshot()
    web_present = _has_web_adapter(snapshot)
    if not web_present:
        warn(
            "[yellow]kernel is running but no web adapter plugin is loaded;[/] "
            "install an adapter plugin to interact with yaya. "
            "`yaya hello` verifies the bus round-trip in the meantime."
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
        # Best-effort; do not fail serve if the browser refuses to open.
        try:
            webbrowser.open(f"http://{_BIND_HOST}:{bound_port}/")
        except Exception as exc:
            warn(f"[yellow]failed to open browser:[/] {exc}")

    # Wire the shutdown trigger.
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
            # Python's default SIG behaviour — KeyboardInterrupt raised
            # inside ``await event.wait()`` below is still handled.
            with contextlib.suppress(NotImplementedError, RuntimeError):
                aio_loop.add_signal_handler(sig, _trigger)

    try:
        await event.wait()
    except KeyboardInterrupt, asyncio.CancelledError:
        # Either fallback path surfaces as interrupt; treat both as a
        # clean shutdown signal.
        pass
    finally:
        await loop.stop()
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
            help="Strategy plugin id to activate (default: react). Non-default values warn until ctx.config lands.",
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
            )
        )
        if code != 0:
            raise typer.Exit(code)
