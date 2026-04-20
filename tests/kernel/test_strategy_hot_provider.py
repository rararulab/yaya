"""Zero-restart provider-switch proof for strategy_react + llm_openai (#123).

AC-14 / AC-15 from ``specs/kernel-config-store.spec``, flipped to the
post-D4b ``providers.<id>.*`` namespace. Both tests load plugins
against a live :class:`~yaya.kernel.config_store.ConfigStore` and
drive the ``providers.<id>.*`` hot-reload path: instance-scoped
config edits take effect on the next event with no kernel restart.
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


def _ctx_with_providers(
    bus: EventBus,
    store: ConfigStore,
    tmp_path: Path,
    plugin_name: str,
) -> KernelContext:
    """Build a :class:`KernelContext` whose ``providers`` resolves through ``store``."""
    ctx = KernelContext(
        bus=bus,
        logger=_NullLogger(),
        config={},
        state_dir=tmp_path / "state",
        plugin_name=plugin_name,
        config_store=store,
    )
    ctx.state_dir.mkdir(parents=True, exist_ok=True)
    return ctx


def test_hot_switch_provider_between_decisions(tmp_path: Path) -> None:
    """AC-14 (D4b): flipping the active instance id between decisions flips the emitted provider.

    Seeds two ``providers.<id>.*`` instances (``instance-a`` + ``instance-b``),
    drives two ``strategy.decide.request`` events, and flips the
    kernel-level ``provider`` key between them. The second
    ``strategy.decide.response`` carries the second instance's id and
    its ``config["model"]`` — live, without restarting the plugin.
    """

    async def _body() -> None:
        bus = EventBus()
        store = await ConfigStore.open(bus=bus, path=tmp_path / "config.db")

        captured: list[Event] = []

        async def _sink(ev: Event) -> None:
            captured.append(ev)

        bus.subscribe("strategy.decide.response", _sink, source="test-sink")

        # Seed two provider instances.
        await store.set("providers.instance-a.plugin", "llm-openai")
        await store.set("providers.instance-a.model", "m-a")
        await store.set("providers.instance-b.plugin", "llm-openai")
        await store.set("providers.instance-b.model", "m-b")
        await store.set("provider", "instance-a")

        strategy = ReActStrategy()
        ctx = _ctx_with_providers(bus, store, tmp_path, "strategy-react")
        await strategy.on_load(ctx)

        req1 = new_event(
            "strategy.decide.request",
            {"state": {"messages": [], "last_tool_result": None}},
            session_id="test-session",
            source="test",
        )
        await strategy.on_event(req1, ctx)
        await asyncio.sleep(0.05)

        # Hot-switch the active instance.
        await store.set("provider", "instance-b")

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
        assert first.payload.get("provider") == "instance-a"
        assert first.payload.get("model") == "m-a"
        assert second.payload.get("provider") == "instance-b"
        assert second.payload.get("model") == "m-b"

        await strategy.on_unload(ctx)
        await store.close()
        await bus.close()

    _run(_body())


def test_llm_openai_rebuilds_client_on_config_updated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-15 (D4b): llm_openai rebuilds a per-instance client on ``providers.<id>.*`` edits.

    A stubbed ``AsyncOpenAI`` constructor records every call. Seeding
    ``providers.prod.*`` with an initial ``base_url`` builds one
    client at ``on_load``; overwriting ``providers.prod.base_url``
    and delivering ``config.updated`` rebuilds only that instance.
    Unrelated keys (``plugin.other.*``) must NOT rebuild.
    """
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

        # Seed one llm-openai instance.
        await store.set("providers.prod.plugin", "llm-openai")
        await store.set("providers.prod.api_key", "sk-first")
        await store.set("providers.prod.base_url", "https://a.example")

        provider = OpenAIProvider()
        ctx = _ctx_with_providers(bus, store, tmp_path, "llm-openai")
        await provider.on_load(ctx)
        assert calls, "on_load should build one client per owned instance"
        assert calls[-1].get("base_url") == "https://a.example"

        # Hot-edit the instance's base_url → expect a per-instance rebuild.
        await store.set("providers.prod.base_url", "https://b.example")
        hot_event = new_event(
            "config.updated",
            {"key": "providers.prod.base_url"},
            session_id="kernel",
            source="kernel-config-store",
        )
        await provider.on_event(hot_event, ctx)
        assert calls[-1].get("base_url") == "https://b.example"

        # Unrelated key → no rebuild.
        before = len(calls)
        unrelated = new_event(
            "config.updated",
            {"key": "plugin.other.thing"},
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
