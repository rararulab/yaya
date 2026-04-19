"""Tests for the providers-namespace bootstrap in :mod:`yaya.kernel.registry`.

AC bindings mirror ``specs/kernel-providers-namespace.spec``:

* AC-01 empty-store seed    → :func:`test_bootstrap_seeds_one_instance_per_plugin`
* AC-04 idempotency         → :func:`test_bootstrap_idempotent`
* AC-06 legacy-field lift   → :func:`test_bootstrap_lifts_legacy_fields`

The bootstrap runs inside :meth:`PluginRegistry.start`; tests wire a
fake llm-provider entry point and a pre-opened :class:`ConfigStore`
under ``tmp_path`` so the one-time migration marker does not leak
across tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import patch

import pytest

from yaya.kernel.bus import EventBus
from yaya.kernel.config_store import ConfigStore
from yaya.kernel.events import Event
from yaya.kernel.plugin import Category, KernelContext
from yaya.kernel.providers import (
    PROVIDERS_PREFIX,
    PROVIDERS_SEEDED_MARKER,
    ProvidersView,
)
from yaya.kernel.registry import PluginRegistry

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Stub plugin + entry-point scaffolding — mirrors tests/kernel/test_registry.py
# but kept local so this module does not import from another test.
# ---------------------------------------------------------------------------


class _StubLLMProvider:
    """Minimal llm-provider Plugin stub — no on_event behaviour under test."""

    name = "llm-openai"
    version = "0.0.1"
    category = Category.LLM_PROVIDER
    requires: ClassVar[list[str]] = []

    def subscriptions(self) -> list[str]:
        return ["llm.call.request"]

    async def on_load(self, ctx: KernelContext) -> None:
        return None

    async def on_event(self, ev: Event, ctx: KernelContext) -> None:
        return None

    async def on_unload(self, ctx: KernelContext) -> None:
        return None


class _StubEchoProvider(_StubLLMProvider):
    name = "llm-echo"


class _FakeDist:
    def __init__(self, dist_name: str) -> None:
        self.metadata = {"Name": dist_name}


class _FakeEntryPoint:
    def __init__(self, name: str, obj: Any, *, bundled: bool = False) -> None:
        self.name = name
        self._obj = obj
        self.dist = _FakeDist("yaya" if bundled else "third-party-pkg")

    def load(self) -> Any:
        return self._obj


def _fake_entry_points(eps: list[_FakeEntryPoint]) -> Any:
    def _factory(group: str) -> list[_FakeEntryPoint]:
        _ = group
        return eps

    return _factory


async def _open_store(tmp_path: Path, bus: EventBus | None = None) -> ConfigStore:
    return await ConfigStore.open(bus=bus, path=tmp_path / "config.db")


# ---------------------------------------------------------------------------
# AC-bound tests.
# ---------------------------------------------------------------------------


async def test_bootstrap_seeds_one_instance_per_plugin(tmp_path: Path) -> None:
    """AC-01: start() seeds providers.<name>.{plugin,label} + stamps the marker."""
    bus = EventBus()
    store = await _open_store(tmp_path, bus=bus)

    eps = [_FakeEntryPoint("llm-openai", _StubLLMProvider())]
    try:
        with patch(
            "yaya.kernel.registry.entry_points",
            side_effect=_fake_entry_points(eps),
        ):
            registry = PluginRegistry(bus, state_dir=tmp_path, config_store=store)
            await registry.start()

        rows = await store.list_prefix(PROVIDERS_PREFIX)
        assert rows["providers.llm-openai.plugin"] == "llm-openai"
        assert rows["providers.llm-openai.label"] == "llm-openai (default)"

        marker = await store.get(PROVIDERS_SEEDED_MARKER)
        assert isinstance(marker, int)

        # ``provider`` active key defaulted to the first seeded id.
        assert await store.get("provider") == "llm-openai"

        # ProvidersView surfaces the seeded row.
        view = ProvidersView(store)
        instances = view.list_instances()
        assert [r.id for r in instances] == ["llm-openai"]
        assert instances[0].plugin == "llm-openai"

        await registry.stop()
    finally:
        await store.close()
        await bus.close()


async def test_bootstrap_idempotent(tmp_path: Path) -> None:
    """AC-04: presence of the marker skips subsequent bootstrap passes.

    We run ``start`` / ``stop`` twice against the same DB and assert
    the marker's epoch value is unchanged and no extra rows appear.
    """
    bus = EventBus()
    store = await _open_store(tmp_path, bus=bus)

    eps = [_FakeEntryPoint("llm-openai", _StubLLMProvider())]
    try:
        with patch(
            "yaya.kernel.registry.entry_points",
            side_effect=_fake_entry_points(eps),
        ):
            registry = PluginRegistry(bus, state_dir=tmp_path, config_store=store)
            await registry.start()
            first_marker = await store.get(PROVIDERS_SEEDED_MARKER)
            first_rows = dict(await store.list_prefix(PROVIDERS_PREFIX))
            await registry.stop()

            # Pre-seed a sentinel under the providers.* tree to make
            # the idempotency check concrete — if bootstrap re-ran it
            # would clobber the ``label`` meta row with the default.
            await store.set("providers.llm-openai.label", "operator-edited")

            registry2 = PluginRegistry(bus, state_dir=tmp_path, config_store=store)
            await registry2.start()
            second_marker = await store.get(PROVIDERS_SEEDED_MARKER)
            second_rows = dict(await store.list_prefix(PROVIDERS_PREFIX))
            await registry2.stop()

        assert first_marker == second_marker
        # Operator edit survives — bootstrap did not re-run.
        assert second_rows["providers.llm-openai.label"] == "operator-edited"
        # Every first-pass key is still present.
        for key in first_rows:
            assert key in second_rows
    finally:
        await store.close()
        await bus.close()


async def test_bootstrap_lifts_legacy_fields(tmp_path: Path) -> None:
    """AC-06: legacy ``plugin.<ns>.<field>`` rows lift to ``providers.<name>.<field>``.

    The legacy row is intentionally left in place so in-flight plugins
    still read their existing scoped view until D4b flips them.
    """
    bus = EventBus()
    store = await _open_store(tmp_path, bus=bus)

    # Pre-populate the legacy sub-tree as if an older install set it.
    # Registry plugin-scoped views normalise ``-`` → ``_`` in the ns.
    await store.set("plugin.llm_openai.api_key", "sk-legacy")
    await store.set("plugin.llm_openai.base_url", "https://legacy.example")

    eps = [_FakeEntryPoint("llm-openai", _StubLLMProvider())]
    try:
        with patch(
            "yaya.kernel.registry.entry_points",
            side_effect=_fake_entry_points(eps),
        ):
            registry = PluginRegistry(bus, state_dir=tmp_path, config_store=store)
            await registry.start()

        assert await store.get("providers.llm-openai.api_key") == "sk-legacy"
        assert await store.get("providers.llm-openai.base_url") == "https://legacy.example"
        # Legacy row stays put — D4b cleans up.
        assert await store.get("plugin.llm_openai.api_key") == "sk-legacy"

        await registry.stop()
    finally:
        await store.close()
        await bus.close()


async def test_bootstrap_skips_when_no_llm_provider_loaded(tmp_path: Path) -> None:
    """No llm-provider plugin => no seed + no marker (so a later boot still seeds)."""
    bus = EventBus()
    store = await _open_store(tmp_path, bus=bus)

    # A non-provider plugin must not trigger providers.* seeding.
    class _Tool(_StubLLMProvider):
        name = "noop-tool"
        category = Category.TOOL

    eps = [_FakeEntryPoint("noop-tool", _Tool())]
    try:
        with patch(
            "yaya.kernel.registry.entry_points",
            side_effect=_fake_entry_points(eps),
        ):
            registry = PluginRegistry(bus, state_dir=tmp_path, config_store=store)
            await registry.start()

        rows = await store.list_prefix(PROVIDERS_PREFIX)
        assert rows == {}
        assert await store.get(PROVIDERS_SEEDED_MARKER) is None

        await registry.stop()
    finally:
        await store.close()
        await bus.close()


async def test_bootstrap_respects_preexisting_provider_key(tmp_path: Path) -> None:
    """Bootstrap does not clobber an already-set ``provider`` value."""
    bus = EventBus()
    store = await _open_store(tmp_path, bus=bus)
    await store.set("provider", "llm-echo")

    eps = [_FakeEntryPoint("llm-openai", _StubLLMProvider())]
    try:
        with patch(
            "yaya.kernel.registry.entry_points",
            side_effect=_fake_entry_points(eps),
        ):
            registry = PluginRegistry(bus, state_dir=tmp_path, config_store=store)
            await registry.start()

        # Preexisting value wins.
        assert await store.get("provider") == "llm-echo"

        await registry.stop()
    finally:
        await store.close()
        await bus.close()
