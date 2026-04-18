"""Tests for ``yaya.kernel.errors`` and bus integration of ``PluginError``.

Bound to ``specs/kernel-logging.spec`` scenario:
* plugin-error-hash → ``test_plugin_error_event_carries_hash_and_kind``
"""

from __future__ import annotations

import re

from yaya.kernel import KernelError, PluginError, YayaError, YayaTimeoutError
from yaya.kernel.bus import EventBus
from yaya.kernel.events import Event, new_event


def test_taxonomy_subclasses_yaya_error() -> None:
    """Every taxonomy member descends from ``YayaError``."""
    for cls in (KernelError, PluginError, YayaTimeoutError):
        assert issubclass(cls, YayaError)


def test_yaya_timeout_is_not_builtin_timeout() -> None:
    """``YayaTimeoutError`` must NOT shadow :class:`builtins.TimeoutError`."""
    # Sanity: catching the builtin must not catch ours.
    assert not issubclass(YayaTimeoutError, TimeoutError)


def test_plugin_error_can_be_subclassed() -> None:
    """Plugins own subclasses (e.g. ``OpenAIError``) under :class:`PluginError`."""

    class OpenAIError(PluginError):
        pass

    err = OpenAIError("rate limited")
    assert isinstance(err, PluginError)
    assert isinstance(err, YayaError)
    assert str(err) == "rate limited"


async def test_plugin_error_event_carries_hash_and_kind() -> None:
    """A handler raising :class:`PluginError` produces a ``plugin.error`` with hash + kind."""
    bus = EventBus(handler_timeout_s=1.0)
    seen: list[Event] = []

    async def boom(_: Event) -> None:
        raise PluginError("boom")

    async def collector(ev: Event) -> None:
        seen.append(ev)

    bus.subscribe("user.message.received", boom, source="test-plugin")
    bus.subscribe("plugin.error", collector, source="observer")

    await bus.publish(
        new_event(
            "user.message.received",
            {"text": "trigger"},
            session_id="s1",
            source="adapter",
        )
    )
    # plugin.error is fire-and-forget on the kernel session; give the loop a tick.
    await bus.close()

    assert seen, "expected at least one plugin.error event"
    payload = seen[-1].payload
    assert payload["name"] == "test-plugin"
    assert payload["error"] == "boom"
    assert payload["kind"] == "PluginError"
    assert re.fullmatch(r"[0-9a-f]{8}", str(payload["error_hash"]))


async def test_non_plugin_error_still_attributed() -> None:
    """A bare ``Exception`` reports kind ``"plugin_error"`` (fallback)."""
    bus = EventBus(handler_timeout_s=1.0)
    seen: list[Event] = []

    async def boom(_: Event) -> None:
        raise RuntimeError("kaboom")

    async def collector(ev: Event) -> None:
        seen.append(ev)

    bus.subscribe("user.message.received", boom, source="other-plugin")
    bus.subscribe("plugin.error", collector, source="observer")

    await bus.publish(
        new_event(
            "user.message.received",
            {"text": "trigger"},
            session_id="s2",
            source="adapter",
        )
    )
    await bus.close()

    assert seen
    assert seen[-1].payload["kind"] == "plugin_error"
