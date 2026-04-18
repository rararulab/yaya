"""Tests for the strategy_react env-driven provider fallback.

AC-binding from ``specs/plugin-llm_echo.spec`` (`@AC-AUTO`):

* no key → echo  → ``test_picks_echo_when_no_api_key``
* key set → openai → ``test_picks_openai_when_api_key_set``

The fallback is a temporary env sniff that lives inside
``ReActStrategy._provider_and_model``; once #23 lands the
config-loading layer, the policy moves to ``ctx.config`` and these
tests migrate with it.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from yaya.kernel.bus import EventBus
from yaya.kernel.events import Event, new_event
from yaya.kernel.plugin import KernelContext
from yaya.plugins.strategy_react import plugin as react_plugin


def _make_ctx(bus: EventBus, tmp_path: Path) -> KernelContext:
    return KernelContext(
        bus=bus,
        logger=logging.getLogger("plugin.strategy-react"),
        config={},
        state_dir=tmp_path,
        plugin_name=react_plugin.name,
    )


async def _drive_first_turn(tmp_path: Path) -> Event:
    """Publish a first-turn ``strategy.decide.request`` and return the response."""
    bus = EventBus()
    ctx = _make_ctx(bus, tmp_path)
    await react_plugin.on_load(ctx)

    async def _handler(ev: Event) -> None:
        await react_plugin.on_event(ev, ctx)

    bus.subscribe("strategy.decide.request", _handler, source=react_plugin.name)
    captured: list[Event] = []

    async def _observer(ev: Event) -> None:
        captured.append(ev)

    bus.subscribe("strategy.decide.response", _observer, source="observer")

    req = new_event(
        "strategy.decide.request",
        {"state": {"messages": [{"role": "user", "content": "hi"}]}},
        session_id="sess-provider",
        source="kernel",
    )
    await bus.publish(req)
    assert len(captured) == 1
    return captured[0]


async def test_picks_echo_when_no_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``OPENAI_API_KEY`` → strategy falls back to the echo dev provider."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    response = await _drive_first_turn(tmp_path)
    payload = response.payload
    assert payload["next"] == "llm"
    assert payload["provider"] == "echo"
    assert payload["model"] == "echo"


async def test_picks_openai_when_api_key_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``OPENAI_API_KEY`` present → strategy picks the openai provider."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    response = await _drive_first_turn(tmp_path)
    payload = response.payload
    assert payload["next"] == "llm"
    assert payload["provider"] == "openai"
    assert payload["model"] == "gpt-4o-mini"
