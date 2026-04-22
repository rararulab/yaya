"""``yaya doctor`` — kernel smoke + per-plugin health report.

Boots an :class:`~yaya.kernel.bus.EventBus`, a
:class:`~yaya.kernel.registry.PluginRegistry`, and an
:class:`~yaya.kernel.loop.AgentLoop`, round-trips one synthetic event,
then iterates every loaded plugin and invokes its optional
``health_check(ctx)`` method. The report is rendered as a
colour-coded table (human) or a structured JSON object (agent).

Rename of the pre-0.1 ``yaya hello`` smoke command. The bus + loop
round-trip is preserved verbatim; the per-plugin iteration is the
new contribution — see issue #170.

Exit codes:
    * ``0`` — bus round-trip succeeded and no plugin reported
      ``status="failed"``. A mix of ``ok`` + ``degraded`` still
      exits 0 because ``degraded`` is the common "configured later"
      state (e.g. OpenAI plugin loaded without an API key).
    * ``1`` — the synthetic round-trip timed out, the kernel failed
      to start, or at least one plugin reported ``failed``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any, cast

import typer
from rich.console import Console
from rich.table import Table

from yaya import __version__
from yaya.cli import CLIState
from yaya.cli.output import emit_error, emit_ok
from yaya.kernel import (
    AgentLoop,
    Event,
    EventBus,
    HealthReport,
    KernelContext,
    Plugin,
    PluginRegistry,
    new_event,
)

# Dedicated stdout console. Mirrors the instance used by
# :mod:`yaya.cli.output` without reaching into its private globals.
_stdout = Console()

EXAMPLES = """
Examples:
  yaya doctor
  yaya doctor --timeout 5
  yaya --json doctor
"""

_ROUND_TRIP_TIMEOUT_S: float = 5.0
"""Default deadline for the sentinel to observe the emitted event."""

_HEALTH_TIMEOUT_S: float = 3.0
"""Default per-plugin ``health_check`` timeout.

Configurable via ``--timeout`` on the command line. A single hung
plugin must NOT block the whole doctor run — :func:`asyncio.wait_for`
enforces this deadline and maps the overrun to ``degraded`` so the
rest of the plugins still report.
"""

_STATUS_COLOUR: dict[str, str] = {
    "ok": "green",
    "degraded": "yellow",
    "failed": "red",
}


@dataclass(slots=True)
class _RoundTripResult:
    """Outcome of the synthetic-event round-trip."""

    ok: bool
    latency_ms: float | None


@dataclass(slots=True)
class _PluginResult:
    """One row in the doctor table / JSON ``plugins`` list."""

    name: str
    category: str
    status: str
    summary: str
    details: list[dict[str, Any]]


async def _run_round_trip(
    bus: EventBus,
    *,
    timeout_s: float,
) -> _RoundTripResult:
    """Emit ``user.message.received``, wait for the sentinel, return timing.

    Subscribes BEFORE publishing so the sentinel is always registered
    by the time the kernel's bus drains the event. Returns
    ``ok=False`` with ``latency_ms=None`` on timeout; caller decides
    whether that is an exit-1 condition.
    """
    got = asyncio.Event()

    async def _sentinel(_ev: Event) -> None:
        got.set()

    sub = bus.subscribe("user.message.received", _sentinel, source="cli-doctor")
    try:
        start = perf_counter()
        await bus.publish(
            new_event(
                "user.message.received",
                {"text": "doctor"},
                session_id="cli-doctor",
                source="cli-doctor",
            )
        )
        try:
            await asyncio.wait_for(got.wait(), timeout=timeout_s)
        except TimeoutError:
            return _RoundTripResult(ok=False, latency_ms=None)
        latency_ms = (perf_counter() - start) * 1000.0
        return _RoundTripResult(ok=True, latency_ms=round(latency_ms, 2))
    finally:
        sub.unsubscribe()


async def _check_one(
    plugin: Plugin,
    ctx: KernelContext | None,
    *,
    timeout_s: float,
) -> _PluginResult:
    """Invoke ``plugin.health_check`` or synthesise the default report.

    Timeout policy: a single plugin whose check hangs longer than
    ``timeout_s`` is reported as ``degraded`` with ``"check timed out
    after Ns"`` — the doctor run proceeds. A check that raises is
    reported as ``failed`` with the exception's string.

    Args:
        plugin: The loaded plugin instance.
        ctx: The plugin's live :class:`KernelContext`. ``None`` is
            tolerated (tests) — the default-report path does not
            consult it.
        timeout_s: Hard per-check deadline.
    """
    category = str(plugin.category)
    if not hasattr(plugin, "health_check"):
        # Default: plugins that do not opt into health_check are
        # considered ``ok``. Rationale in HealthReport docstring.
        return _PluginResult(
            name=plugin.name,
            category=category,
            status="ok",
            summary="no checks registered",
            details=[],
        )
    if ctx is None:
        return _PluginResult(
            name=plugin.name,
            category=category,
            status="degraded",
            summary="no kernel context available",
            details=[],
        )
    # health_check is optional and intentionally NOT on the Plugin
    # Protocol (see plugin.py). Resolve via getattr + cast so type
    # checkers see a concrete awaitable signature.
    health_check = cast(
        "Callable[[KernelContext], Awaitable[HealthReport]]",
        getattr(plugin, "health_check"),  # noqa: B009 - pyright narrows this path.
    )
    try:
        report: HealthReport = await asyncio.wait_for(
            health_check(ctx),
            timeout=timeout_s,
        )
    except TimeoutError:
        return _PluginResult(
            name=plugin.name,
            category=category,
            status="degraded",
            summary=f"check timed out after {timeout_s}s",
            details=[],
        )
    except Exception as exc:
        return _PluginResult(
            name=plugin.name,
            category=category,
            status="failed",
            summary=f"health_check raised: {exc}",
            details=[],
        )
    return _PluginResult(
        name=plugin.name,
        category=category,
        status=report.status,
        summary=report.summary,
        details=[c.model_dump() for c in report.details],
    )


async def _run_doctor(
    *,
    round_trip_timeout_s: float,
    health_timeout_s: float,
) -> tuple[_RoundTripResult, list[_PluginResult]]:
    """Boot kernel, run round-trip, iterate plugins, tear down."""
    bus = EventBus()
    registry = PluginRegistry(bus)
    loop = AgentLoop(bus)
    registry_started = False
    loop_started = False
    try:
        await registry.start()
        registry_started = True
        await loop.start()
        loop_started = True

        round_trip = await _run_round_trip(bus, timeout_s=round_trip_timeout_s)

        plugin_results: list[_PluginResult] = []
        for plug in registry.loaded_plugins():
            ctx = registry.context_for(plug.name)
            plugin_results.append(await _check_one(plug, ctx, timeout_s=health_timeout_s))
        return round_trip, plugin_results
    finally:
        if loop_started:
            await loop.stop()
        if registry_started:
            await registry.stop()
        await bus.close()


def _render_table(
    round_trip: _RoundTripResult,
    plugin_results: list[_PluginResult],
) -> None:
    """Render the rich table for human (text-mode) output."""
    rt_text = (
        f"[green]round-trip ok[/] ({round_trip.latency_ms} ms)"
        if round_trip.ok
        else "[red]round-trip failed[/] — event bus unresponsive"
    )
    _stdout.print(rt_text)

    table = Table(
        title=f"yaya {__version__} — plugin health",
        show_lines=False,
        expand=False,
    )
    table.add_column("plugin", style="bold")
    table.add_column("category")
    table.add_column("status")
    table.add_column("summary")
    for row in plugin_results:
        colour = _STATUS_COLOUR.get(row.status, "white")
        table.add_row(
            row.name,
            row.category,
            f"[{colour}]{row.status}[/]",
            row.summary,
        )
    _stdout.print(table)


def _compute_exit_code(
    round_trip: _RoundTripResult,
    plugin_results: list[_PluginResult],
) -> int:
    """Resolve the exit-code rule documented in the module docstring."""
    if not round_trip.ok:
        return 1
    if any(r.status == "failed" for r in plugin_results):
        return 1
    return 0


def register(app: typer.Typer) -> None:
    """Register the ``doctor`` subcommand onto ``app``."""

    @app.command(epilog=EXAMPLES)
    def doctor(
        ctx: typer.Context,
        timeout: float = typer.Option(
            _HEALTH_TIMEOUT_S,
            "--timeout",
            min=0.1,
            help=(
                "Seconds each plugin's health_check has before being marked degraded. Also bounds the bus round-trip."
            ),
        ),
    ) -> None:
        """Boot the kernel, round-trip one event, report every plugin's health."""
        state: CLIState = ctx.obj
        try:
            round_trip, plugin_results = asyncio.run(
                _run_doctor(
                    round_trip_timeout_s=max(timeout, _ROUND_TRIP_TIMEOUT_S),
                    health_timeout_s=timeout,
                )
            )
        except Exception as exc:
            emit_error(
                state,
                error=f"kernel_startup_failed: {exc}",
                suggestion="run with -v or -vv for a detailed traceback",
            )
            raise typer.Exit(1) from exc

        exit_code = _compute_exit_code(round_trip, plugin_results)

        if state.json_output:
            payload: dict[str, Any] = {
                "action": "doctor",
                "roundtrip": {
                    "ok": round_trip.ok,
                    "latency_ms": round_trip.latency_ms,
                },
                "plugins": [
                    {
                        "name": r.name,
                        "category": r.category,
                        "status": r.status,
                        "summary": r.summary,
                        "details": r.details,
                    }
                    for r in plugin_results
                ],
                "version": __version__,
            }
            if exit_code == 0:
                emit_ok(state, **payload)
            else:
                error_msg = "event_bus_unresponsive" if not round_trip.ok else "plugin_failed"
                emit_error(
                    state,
                    error=error_msg,
                    suggestion="inspect plugins[].status; run without --json for the table",
                    **payload,
                )
            if exit_code != 0:
                raise typer.Exit(exit_code)
            return

        _render_table(round_trip, plugin_results)
        if exit_code != 0:
            raise typer.Exit(exit_code)
