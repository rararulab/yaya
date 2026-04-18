"""``yaya hello`` — kernel-bootstrap smoke test.

Boots an :class:`~yaya.kernel.bus.EventBus`, a
:class:`~yaya.kernel.registry.PluginRegistry`, and an
:class:`~yaya.kernel.loop.AgentLoop`, then emits one synthetic
``user.message.received`` event and waits on a sentinel subscriber that
captures it. Proves bus + registry + loop boot without requiring a real
LLM or any adapter plugin.

Exit codes:
    * ``0`` — sentinel observed the round-tripped event.
    * ``1`` — functional error (startup failure or 5-second timeout).
"""

from __future__ import annotations

import asyncio

import typer

from yaya import __version__
from yaya.cli import CLIState
from yaya.cli.output import emit_error, emit_ok
from yaya.kernel import AgentLoop, Event, EventBus, PluginRegistry, new_event

EXAMPLES = """
Examples:
  yaya hello
  yaya --json hello
"""

_SENTINEL_TIMEOUT_S = 5.0
"""Deadline for the sentinel to observe the emitted event."""


async def _run_hello() -> bool:
    """Boot the kernel, round-trip one event, tear down.

    Returns:
        ``True`` if the sentinel fired within the timeout; ``False`` on
        a timeout.

    Raises:
        Any exception from ``start()`` — caller surfaces it as
        ``startup_failed``.
    """
    bus = EventBus()
    registry = PluginRegistry(bus)
    loop = AgentLoop(bus)

    got_event = asyncio.Event()

    async def _sentinel(_ev: Event) -> None:
        got_event.set()

    # Subscribe BEFORE starting registry/loop so the sentinel is in
    # place by the time the synthetic event is published.
    sub = bus.subscribe("user.message.received", _sentinel, source="cli-hello")

    await registry.start()
    await loop.start()

    try:
        await bus.publish(
            new_event(
                "user.message.received",
                {"text": "hello"},
                session_id="cli-hello",
                source="cli-hello",
            )
        )
        try:
            await asyncio.wait_for(got_event.wait(), timeout=_SENTINEL_TIMEOUT_S)
        except TimeoutError:
            return False
        else:
            return True
    finally:
        sub.unsubscribe()
        await loop.stop()
        await registry.stop()
        await bus.close()


def register(app: typer.Typer) -> None:
    """Register the ``hello`` subcommand onto ``app``."""

    @app.command(epilog=EXAMPLES)
    def hello(ctx: typer.Context) -> None:
        """Kernel smoke-test: boot, round-trip one event, shut down."""
        state: CLIState = ctx.obj
        try:
            received = asyncio.run(_run_hello())
        except Exception as exc:
            emit_error(
                state,
                error=f"kernel_startup_failed: {exc}",
                suggestion="run with -v or -vv for a detailed traceback",
            )
            raise typer.Exit(1) from exc

        if not received:
            emit_error(
                state,
                error="event_bus_unresponsive",
                suggestion="check kernel boot; run with --json -v for more",
            )
            raise typer.Exit(1)

        emit_ok(
            state,
            text=f"[green]kernel ok[/] — yaya [bold cyan]{__version__}[/]",
            action="hello",
            received=True,
            version=__version__,
        )
