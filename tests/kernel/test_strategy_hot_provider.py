"""Zero-restart provider-switch proof for strategy_react (#104).

AC-14 from ``specs/kernel-config-store.spec``. Loads strategy_react
against a live :class:`~yaya.kernel.config_store.ConfigStore`, drives
two ``strategy.decide.request`` events back-to-back, and flips
``plugin.strategy_react.provider`` between them — the emitted
``llm.call.request`` events must name the two different providers.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from yaya.kernel.bus import EventBus
from yaya.kernel.config_store import ConfigStore
from yaya.kernel.events import Event, new_event
from yaya.kernel.plugin import KernelContext
from yaya.plugins.strategy_react.plugin import ReActStrategy

pytestmark = pytest.mark.unit


def _run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


def test_hot_switch_provider_between_decisions(tmp_path: Path) -> None:
    """AC-14: a ``yaya config set`` between decisions flips the emitted provider.

    Wires the real strategy plugin against a live config store scoped
    view. Two decisions are triggered; between them we flip
    ``plugin.strategy_react.provider``. The second decision's
    ``strategy.decide.response`` carries the new provider name.
    """

    async def _body() -> None:
        bus = EventBus()
        store = await ConfigStore.open(bus=bus, path=tmp_path / "config.db")

        captured: list[Event] = []

        async def _sink(ev: Event) -> None:
            captured.append(ev)

        bus.subscribe("strategy.decide.response", _sink, source="test-sink")

        strategy = ReActStrategy()
        # Scoped view — the registry would hand this to the plugin in
        # production; we synthesise it here for a no-registry unit.
        ctx = KernelContext(
            bus=bus,
            logger=_NullLogger(),
            config=store.view(prefix="plugin.strategy_react."),
            state_dir=tmp_path / "state",
            plugin_name="strategy-react",
        )
        ctx.state_dir.mkdir(parents=True, exist_ok=True)
        await strategy.on_load(ctx)

        # Pin the initial provider via the live store.
        await store.set("plugin.strategy_react.provider", "provider-a")
        await store.set("plugin.strategy_react.model", "m-a")

        req1 = new_event(
            "strategy.decide.request",
            {"state": {"messages": [], "last_tool_result": None}},
            session_id="test-session",
            source="test",
        )
        await strategy.on_event(req1, ctx)
        await asyncio.sleep(0.05)

        # Flip providers live — no restart.
        await store.set("plugin.strategy_react.provider", "provider-b")
        await store.set("plugin.strategy_react.model", "m-b")

        req2 = new_event(
            "strategy.decide.request",
            {"state": {"messages": [], "last_tool_result": None}},
            session_id="test-session",
            source="test",
        )
        await strategy.on_event(req2, ctx)
        await asyncio.sleep(0.05)

        responses = [ev for ev in captured if ev.kind == "strategy.decide.response"]
        assert len(responses) >= 2, f"expected 2 responses, got {responses!r}"
        first, second = responses[0], responses[1]
        assert first.payload.get("provider") == "provider-a"
        assert first.payload.get("model") == "m-a"
        assert second.payload.get("provider") == "provider-b"
        assert second.payload.get("model") == "m-b"

        await strategy.on_unload(ctx)
        await store.close()
        await bus.close()

    _run(_body())


def test_llm_openai_rebuilds_client_on_config_updated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-15: llm_openai rebuilds its ``AsyncOpenAI`` client on hot config change.

    A stubbed SDK constructor records every call; we set a new
    ``plugin.llm_openai.base_url`` on the store and assert the plugin
    re-instantiated with the new URL.
    """
    # The package ``__init__`` reexports ``plugin = OpenAIProvider()``
    # which shadows the ``plugin`` submodule at the package level, so
    # we import the class from its declaring module directly.
    from yaya.plugins.llm_openai.plugin import OpenAIProvider

    async def _body() -> None:
        bus = EventBus()
        store = await ConfigStore.open(bus=bus, path=tmp_path / "config.db")

        calls: list[dict[str, object]] = []

        class _StubClient:
            def __init__(self, **kwargs: object) -> None:
                calls.append(kwargs)

            async def close(self) -> None:
                return None

        monkeypatch.setattr("openai.AsyncOpenAI", _StubClient)

        await store.set("plugin.llm_openai.api_key", "sk-first")
        await store.set("plugin.llm_openai.base_url", "https://a.example")

        provider = OpenAIProvider()
        ctx = KernelContext(
            bus=bus,
            logger=_NullLogger(),
            config=store.view(prefix="plugin.llm_openai."),
            state_dir=tmp_path / "state",
            plugin_name="llm-openai",
        )
        ctx.state_dir.mkdir(parents=True, exist_ok=True)
        await provider.on_load(ctx)
        assert calls, "on_load should build the client once"
        assert calls[-1].get("base_url") == "https://a.example"

        # Live config change — hot-reload path.
        hot_event = new_event(
            "config.updated",
            {"key": "plugin.llm_openai.base_url", "prefix_match_hint": "plugin.llm_openai."},
            session_id="kernel",
            source="kernel-config-store",
        )
        # The store's set() already emits config.updated on its own; we
        # mutate the cache first so the scoped view surfaces the new
        # value when the plugin's handler runs.
        await store.set("plugin.llm_openai.base_url", "https://b.example")
        await provider.on_event(hot_event, ctx)
        # The plugin should have rebuilt with the new URL.
        assert calls[-1].get("base_url") == "https://b.example"

        # Non-matching key must NOT rebuild.
        before = len(calls)
        unrelated = new_event(
            "config.updated",
            {"key": "plugin.other.thing", "prefix_match_hint": "plugin.other."},
            session_id="kernel",
            source="kernel-config-store",
        )
        await provider.on_event(unrelated, ctx)
        assert len(calls) == before

        await provider.on_unload(ctx)
        await store.close()
        await bus.close()

    _run(_body())


class _NullLogger:
    """Minimal logger stub — plugins type ``ctx.logger`` as Any at runtime."""

    def debug(self, *_args: object, **_kwargs: object) -> None: ...
    def info(self, *_args: object, **_kwargs: object) -> None: ...
    def warning(self, *_args: object, **_kwargs: object) -> None: ...
    def error(self, *_args: object, **_kwargs: object) -> None: ...
