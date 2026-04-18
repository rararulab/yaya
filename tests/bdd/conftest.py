"""Shared pytest-bdd fixtures.

Steps that recur across kernel feature files live here so each
feature's test module can stay focused on its own scenario-specific
wiring.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_bdd import given

from yaya.kernel.bus import EventBus
from yaya.kernel.events import Event


@dataclass
class BDDContext:
    """Per-scenario state container passed between step defs.

    Each step reads or mutates fields here instead of using module
    globals, so scenarios are isolated. pytest-bdd provides one
    instance per scenario via the ``ctx`` fixture below.
    """

    bus: EventBus | None = None
    received: dict[str, list[Event]] = field(default_factory=lambda: {})
    errors: list[Event] = field(default_factory=lambda: [])
    handlers: dict[str, Callable[[Event], Awaitable[None]]] = field(default_factory=lambda: {})
    published_events: list[Event] = field(default_factory=lambda: [])
    publish_error: Exception | None = None
    extras: dict[str, Any] = field(default_factory=lambda: {})


@pytest.fixture
def ctx() -> BDDContext:
    return BDDContext()


@pytest.fixture
def loop() -> asyncio.AbstractEventLoop:
    """A dedicated loop per scenario so publishes are isolated.

    pytest-bdd step defs are synchronous functions; each When that
    exercises the bus schedules its coroutine on this loop via
    ``loop.run_until_complete``.
    """
    new_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(new_loop)
    try:
        yield new_loop
    finally:
        asyncio.set_event_loop(None)
        new_loop.close()


# -- common Given steps -----------------------------------------------------


@given("a running EventBus")
def _a_running_event_bus(ctx: BDDContext) -> None:
    ctx.bus = EventBus()


@given("a running EventBus with a single drain worker per session")
def _a_running_bus_single_drain(ctx: BDDContext) -> None:
    # Default EventBus behaviour; the clause exists to read naturally
    # in the scenario text and to document the invariant.
    ctx.bus = EventBus()
