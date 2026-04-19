"""Tests for Category, Plugin Protocol, KernelContext."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import ClassVar

import pytest

from yaya.kernel.bus import EventBus
from yaya.kernel.events import Event
from yaya.kernel.plugin import Category, KernelContext, Plugin


def test_category_round_trips_as_str() -> None:
    """Category is a StrEnum and its values match the protocol table verbatim."""
    assert Category.ADAPTER == "adapter"
    assert Category.TOOL == "tool"
    assert Category.LLM_PROVIDER == "llm-provider"
    assert Category.STRATEGY == "strategy"
    assert Category.MEMORY == "memory"
    assert Category.SKILL == "skill"
    assert str(Category.ADAPTER.value) == "adapter"


def test_plugin_protocol_is_runtime_checkable() -> None:
    """A plain object matching the Plugin shape passes isinstance()."""

    class _Stub:
        name = "stub"
        version = "0.0.1"
        category = Category.TOOL
        requires: ClassVar[list[str]] = []

        def subscriptions(self) -> list[str]:
            return []

        async def on_load(self, ctx: KernelContext) -> None:
            return None

        async def on_event(self, ev: Event, ctx: KernelContext) -> None:
            return None

        async def on_unload(self, ctx: KernelContext) -> None:
            return None

    assert isinstance(_Stub(), Plugin)


async def test_kernel_context_emit_stamps_source_and_routes(tmp_path: Path) -> None:
    """KernelContext.emit builds an Event with source=plugin-name and publishes via the bus."""
    bus = EventBus()
    received: list[Event] = []

    async def handler(ev: Event) -> None:
        received.append(ev)

    bus.subscribe("user.message.received", handler, source="test")

    ctx = KernelContext(
        bus=bus,
        logger=logging.getLogger("plugin.web"),
        config={"k": "v"},
        state_dir=tmp_path,
        plugin_name="web",
    )

    await ctx.emit("user.message.received", {"text": "hi"}, session_id="s-1")

    assert len(received) == 1
    assert received[0].source == "web"
    assert received[0].kind == "user.message.received"
    assert received[0].payload == {"text": "hi"}
    # Read-only config surface + writable state_dir.
    assert ctx.config["k"] == "v"
    assert ctx.state_dir == tmp_path
    assert ctx.logger.name == "plugin.web"


async def test_kernel_context_providers_returns_view_when_store_attached(tmp_path: Path) -> None:
    """``ctx.providers`` returns a ProvidersView when a ConfigStore is wired.

    Mirrors how :class:`PluginRegistry` builds its context — binds a
    real store so the live-parse contract is exercised end to end.
    """
    from yaya.kernel.config_store import ConfigStore
    from yaya.kernel.providers import ProvidersView

    bus = EventBus()
    store = await ConfigStore.open(bus=bus, path=tmp_path / "config.db")
    try:
        await store.set("providers.only.plugin", "llm-openai")
        await store.set("providers.only.label", "Only")
        ctx = KernelContext(
            bus=bus,
            logger=logging.getLogger("plugin.stub"),
            config=store.view(),
            state_dir=tmp_path,
            plugin_name="stub",
            config_store=store,
        )
        view = ctx.providers
        assert isinstance(view, ProvidersView)
        assert [r.id for r in view.list_instances()] == ["only"]
    finally:
        await store.close()
        await bus.close()


def test_kernel_context_providers_is_none_without_store(tmp_path: Path) -> None:
    """``ctx.providers`` gracefully returns ``None`` in the no-store fallback."""
    bus = EventBus()
    ctx = KernelContext(
        bus=bus,
        logger=logging.getLogger("plugin.nostore"),
        config={},
        state_dir=tmp_path,
        plugin_name="nostore",
    )
    assert ctx.providers is None


async def test_kernel_context_emit_rejects_unknown_public_kind(tmp_path: Path) -> None:
    """Emitting a non-catalog, non-extension kind surfaces ValueError to the caller."""
    bus = EventBus()
    ctx = KernelContext(
        bus=bus,
        logger=logging.getLogger("plugin.broken"),
        config={},
        state_dir=tmp_path,
        plugin_name="broken",
    )
    with pytest.raises(ValueError, match=r"closed catalog|PublicEventKind"):
        await ctx.emit("not.a.kind", {}, session_id="s")
