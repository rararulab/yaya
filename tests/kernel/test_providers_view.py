"""Tests for :mod:`yaya.kernel.providers`.

AC bindings mirror ``specs/kernel-providers-namespace.spec``:

* AC-02 list_instances        → :func:`test_list_instances_returns_seeded_rows`
* AC-03 instances_for_plugin  → :func:`test_instances_for_plugin_filters`
* AC-05 active_id             → :func:`test_active_id_reads_provider_key`

Extras cover the malformed-key drop, empty-store shape, and the
``get_instance`` miss branch so the view contract is pinned.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from yaya.kernel.config_store import ConfigStore
from yaya.kernel.providers import InstanceRow, ProvidersView

pytestmark = pytest.mark.unit


def _run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _db(tmp_path: Path) -> Path:
    return tmp_path / "config.db"


def test_list_instances_returns_seeded_rows(tmp_path: Path) -> None:
    """AC-02: ``list_instances`` surfaces every ``providers.<id>`` subtree.

    Meta fields (``plugin`` / ``label``) land on the typed attributes;
    remaining fields live in :attr:`InstanceRow.config`. Ordering is
    lexicographic on ``id`` so consumers have a deterministic view.
    """

    async def _body() -> None:
        store = await ConfigStore.open(bus=None, path=_db(tmp_path))
        try:
            await store.set("providers.beta.plugin", "llm-echo")
            await store.set("providers.beta.label", "Echo")
            await store.set("providers.beta.model", "echo-1")
            await store.set("providers.alpha.plugin", "llm-openai")
            await store.set("providers.alpha.label", "OpenAI")
            await store.set("providers.alpha.api_key", "sk-xxx")
            await store.set("providers.alpha.base_url", "https://api.example")

            view = ProvidersView(store)
            rows = view.list_instances()

            assert [r.id for r in rows] == ["alpha", "beta"]
            alpha = rows[0]
            assert isinstance(alpha, InstanceRow)
            assert alpha.plugin == "llm-openai"
            assert alpha.label == "OpenAI"
            assert alpha.config == {
                "api_key": "sk-xxx",
                "base_url": "https://api.example",
            }
            beta = rows[1]
            assert beta.plugin == "llm-echo"
            assert beta.config == {"model": "echo-1"}
        finally:
            await store.close()

    _run(_body())


def test_instances_for_plugin_filters(tmp_path: Path) -> None:
    """AC-03: ``instances_for_plugin`` keeps only rows whose ``plugin`` meta matches."""

    async def _body() -> None:
        store = await ConfigStore.open(bus=None, path=_db(tmp_path))
        try:
            await store.set("providers.prod.plugin", "llm-openai")
            await store.set("providers.prod.label", "Prod")
            await store.set("providers.staging.plugin", "llm-openai")
            await store.set("providers.staging.label", "Staging")
            await store.set("providers.local.plugin", "llm-echo")
            await store.set("providers.local.label", "Local echo")

            view = ProvidersView(store)
            openai_rows = view.instances_for_plugin("llm-openai")
            echo_rows = view.instances_for_plugin("llm-echo")
            missing = view.instances_for_plugin("does-not-exist")

            assert [r.id for r in openai_rows] == ["prod", "staging"]
            assert [r.id for r in echo_rows] == ["local"]
            assert missing == []
        finally:
            await store.close()

    _run(_body())


def test_active_id_reads_provider_key(tmp_path: Path) -> None:
    """AC-05: ``active_id`` echoes the live ``provider`` key; flips on write."""

    async def _body() -> None:
        store = await ConfigStore.open(bus=None, path=_db(tmp_path))
        try:
            view = ProvidersView(store)
            # Unset: active_id returns None so callers can fall back.
            assert view.active_id is None

            await store.set("provider", "alpha")
            assert view.active_id == "alpha"

            # Live reflection: a subsequent set flips without
            # re-constructing the view.
            await store.set("provider", "beta")
            assert view.active_id == "beta"

            # Empty-string provider is treated as unset — the
            # flat-namespace contract says ``provider`` names a
            # configured instance, which can never have empty id.
            await store.set("provider", "")
            assert view.active_id is None
        finally:
            await store.close()

    _run(_body())


def test_get_instance_returns_none_when_missing(tmp_path: Path) -> None:
    """``get_instance`` miss path keeps callers honest about the absent case."""

    async def _body() -> None:
        store = await ConfigStore.open(bus=None, path=_db(tmp_path))
        try:
            await store.set("providers.only.plugin", "llm-openai")
            view = ProvidersView(store)
            assert view.get_instance("only") is not None
            assert view.get_instance("does-not-exist") is None
        finally:
            await store.close()

    _run(_body())


def test_malformed_keys_are_ignored(tmp_path: Path) -> None:
    """Rows that do not fit ``providers.<id>.<field>`` are dropped silently.

    The grouping pass needs a field segment to attribute; a stray
    write like ``providers.foo`` (no field) should not synthesize an
    instance row with empty meta — that would leak a malformed row
    into every listing.
    """

    async def _body() -> None:
        store = await ConfigStore.open(bus=None, path=_db(tmp_path))
        try:
            # Well-formed.
            await store.set("providers.good.plugin", "llm-openai")
            # Malformed: no dotted field — ignored.
            await store.set("providers.bare", "ignored")
            # Unrelated namespace — ignored.
            await store.set("plugin.llm_openai.api_key", "sk-yyy")

            view = ProvidersView(store)
            ids = [r.id for r in view.list_instances()]
            assert ids == ["good"]
        finally:
            await store.close()

    _run(_body())


def test_empty_store_list_instances_is_empty(tmp_path: Path) -> None:
    """A fresh store with no ``providers.*`` rows yields the empty list."""

    async def _body() -> None:
        store = await ConfigStore.open(bus=None, path=_db(tmp_path))
        try:
            view = ProvidersView(store)
            assert view.list_instances() == []
            assert view.active_id is None
        finally:
            await store.close()

    _run(_body())


def test_instance_row_label_defaults_to_empty_string(tmp_path: Path) -> None:
    """Missing ``label`` meta falls back to empty string; callers use ``id`` for display."""

    async def _body() -> None:
        store = await ConfigStore.open(bus=None, path=_db(tmp_path))
        try:
            await store.set("providers.nolabel.plugin", "llm-openai")
            await store.set("providers.nolabel.api_key", "sk-zzz")
            view = ProvidersView(store)
            row = view.get_instance("nolabel")
            assert row is not None
            assert row.label == ""
            assert row.plugin == "llm-openai"
            assert row.config == {"api_key": "sk-zzz"}
        finally:
            await store.close()

    _run(_body())
