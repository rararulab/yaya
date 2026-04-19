"""Tests for :mod:`yaya.kernel.config_store`.

AC-bindings mirror ``specs/kernel-config-store.spec``. Every async
test runs its own asyncio loop via :func:`asyncio.run` so the store's
single-worker executor pattern is exercised end-to-end.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from yaya.kernel.bus import EventBus
from yaya.kernel.config import KernelConfig, flatten_kernel_config
from yaya.kernel.config_store import (
    ConfigStore,
    ConfigView,
    default_config_db_path,
)
from yaya.kernel.events import Event

pytestmark = pytest.mark.unit


def _run(coro: object) -> object:
    """Run a single coroutine on a fresh loop."""
    return asyncio.run(coro)  # type: ignore[arg-type]


def _db_path(tmp_path: Path) -> Path:
    return tmp_path / "config.db"


def test_set_get_roundtrip(tmp_path: Path) -> None:
    """AC-01: set + get round-trips every JSON-safe scalar / container."""

    async def _body() -> None:
        store = await ConfigStore.open(bus=None, path=_db_path(tmp_path))
        try:
            await store.set("s", "hi")
            await store.set("i", 42)
            await store.set("lst", [1, 2, 3])
            await store.set("d", {"a": 1, "b": "x"})
            assert await store.get("s") == "hi"
            assert await store.get("i") == 42
            assert await store.get("lst") == [1, 2, 3]
            assert await store.get("d") == {"a": 1, "b": "x"}
        finally:
            await store.close()

    _run(_body())


def test_set_emits_config_updated(tmp_path: Path) -> None:
    """AC-02: ``set`` publishes ``config.updated`` on session_id=``"kernel"``."""

    async def _body() -> None:
        bus = EventBus()
        captured: list[Event] = []

        async def _handler(ev: Event) -> None:
            captured.append(ev)

        sub = bus.subscribe("config.updated", _handler, source="test-subscriber")
        store = await ConfigStore.open(bus=bus, path=_db_path(tmp_path))
        try:
            await store.set("plugin.llm_openai.base_url", "https://x.example")
            await asyncio.sleep(0.05)  # let the bus worker drain.
            assert len(captured) >= 1
            ev = captured[-1]
            assert ev.kind == "config.updated"
            assert ev.session_id == "kernel"
            assert ev.payload["key"] == "plugin.llm_openai.base_url"
            assert ev.payload["prefix_match_hint"] == "plugin.llm_openai."
        finally:
            sub.unsubscribe()
            await store.close()
            await bus.close()

    _run(_body())


def test_view_is_live(tmp_path: Path) -> None:
    """AC-03: a :class:`ConfigView` reflects subsequent writes without refresh."""

    async def _body() -> None:
        store = await ConfigStore.open(bus=None, path=_db_path(tmp_path))
        try:
            view: ConfigView = store.view()
            assert "x" not in view
            await store.set("x", 1)
            assert view["x"] == 1
            assert "x" in view
            assert len(view) >= 1
            assert list(iter(view))
        finally:
            await store.close()

    _run(_body())


def test_unset_idempotent(tmp_path: Path) -> None:
    """AC-04: ``unset`` returns True once, then False."""

    async def _body() -> None:
        store = await ConfigStore.open(bus=None, path=_db_path(tmp_path))
        try:
            await store.set("k", "v")
            assert await store.unset("k") is True
            assert await store.unset("k") is False
        finally:
            await store.close()

    _run(_body())


def test_list_prefix(tmp_path: Path) -> None:
    """AC-05: ``list_prefix`` returns only matching keys in sorted order."""

    async def _body() -> None:
        store = await ConfigStore.open(bus=None, path=_db_path(tmp_path))
        try:
            await store.set("plugin.a.x", 1)
            await store.set("plugin.a.y", 2)
            await store.set("plugin.b.x", 3)
            a = await store.list_prefix("plugin.a.")
            assert set(a.keys()) == {"plugin.a.x", "plugin.a.y"}
            full = await store.list_prefix("")
            assert set(full.keys()) >= {"plugin.a.x", "plugin.a.y", "plugin.b.x"}
        finally:
            await store.close()

    _run(_body())


def test_persistence_across_reopen(tmp_path: Path) -> None:
    """AC-06: closing + reopening the store preserves every value."""
    path = _db_path(tmp_path)

    async def _body_one() -> None:
        store = await ConfigStore.open(bus=None, path=path)
        await store.set("provider", "openai")
        await store.set("model", "gpt-4o-mini")
        await store.close()

    async def _body_two() -> None:
        store = await ConfigStore.open(bus=None, path=path)
        try:
            assert await store.get("provider") == "openai"
            assert await store.get("model") == "gpt-4o-mini"
        finally:
            await store.close()

    _run(_body_one())
    _run(_body_two())


def test_toml_migration_writes_marker(tmp_path: Path) -> None:
    """AC-11: a fresh DB migrates from KernelConfig and stamps the marker."""

    async def _body() -> None:
        cfg = KernelConfig(port=9000, bind_host="127.0.0.1", log_level="DEBUG")
        flat = flatten_kernel_config(cfg)
        store = await ConfigStore.open(bus=None, path=_db_path(tmp_path))
        try:
            written = await store.migrate_from_kernel_config(flat)
            assert written >= 3  # port, bind_host, log_level at minimum.
            assert await store.get("port") == 9000
            assert await store.get("log_level") == "DEBUG"
            marker = await store.get("_meta.migrated_from_toml_at")
            assert isinstance(marker, int)
        finally:
            await store.close()

    _run(_body())


def test_migration_idempotent(tmp_path: Path) -> None:
    """AC-12: second ``migrate_from_kernel_config`` is a no-op once marker exists."""

    async def _body() -> None:
        cfg = KernelConfig(port=9000)
        flat = flatten_kernel_config(cfg)
        store = await ConfigStore.open(bus=None, path=_db_path(tmp_path))
        try:
            first = await store.migrate_from_kernel_config(flat)
            second = await store.migrate_from_kernel_config(flat)
            assert first > 0
            assert second == 0
        finally:
            await store.close()

    _run(_body())


def test_non_json_value_rejected(tmp_path: Path) -> None:
    """AC-13: :meth:`set` raises :class:`TypeError` on non-JSON inputs."""

    async def _body() -> None:
        store = await ConfigStore.open(bus=None, path=_db_path(tmp_path))
        try:
            with pytest.raises(TypeError):
                await store.set("bad", object())
        finally:
            await store.close()

    _run(_body())


def test_set_rejects_empty_key(tmp_path: Path) -> None:
    """Empty keys are structurally invalid — they'd collide under sqlite PRIMARY KEY quirks."""

    async def _body() -> None:
        store = await ConfigStore.open(bus=None, path=_db_path(tmp_path))
        try:
            with pytest.raises(ValueError, match="non-empty"):
                await store.set("", "v")
        finally:
            await store.close()

    _run(_body())


def test_set_rejects_non_str_dict_key(tmp_path: Path) -> None:
    """Dict values with non-str keys fail the JSON shape guard."""

    async def _body() -> None:
        store = await ConfigStore.open(bus=None, path=_db_path(tmp_path))
        try:
            with pytest.raises(TypeError, match="str"):
                await store.set("d", {1: "x"})  # type: ignore[dict-item]
        finally:
            await store.close()

    _run(_body())


def test_default_config_db_path_honours_yaya_state_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``YAYA_STATE_DIR`` wins over every other resolver input."""
    monkeypatch.setenv("YAYA_STATE_DIR", str(tmp_path / "override"))
    assert default_config_db_path() == tmp_path / "override" / "config.db"


def test_default_config_db_path_honours_xdg_state_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When YAYA_STATE_DIR is unset, XDG_STATE_HOME wins."""
    monkeypatch.delenv("YAYA_STATE_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert default_config_db_path() == tmp_path / "xdg" / "yaya" / "config.db"


def test_default_config_db_path_home_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """With neither env var set, the resolver lands under ~/.local/state/yaya.

    POSIX-only — on Windows the resolver deliberately lands under
    ``%LOCALAPPDATA%`` because ``~/.local/state`` is meaningless there
    (see :func:`yaya.kernel.config_store.default_config_db_path`).
    """
    import sys

    if sys.platform == "win32":
        pytest.skip("POSIX-only fallback; Windows uses LOCALAPPDATA")
    monkeypatch.delenv("YAYA_STATE_DIR", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path / "home"))
    assert default_config_db_path() == tmp_path / "home" / ".local" / "state" / "yaya" / "config.db"


def test_close_is_idempotent(tmp_path: Path) -> None:
    """Calling ``close`` twice must not raise."""

    async def _body() -> None:
        store = await ConfigStore.open(bus=None, path=_db_path(tmp_path))
        await store.close()
        await store.close()

    _run(_body())


def test_operations_after_close_raise(tmp_path: Path) -> None:
    """Writes against a closed store error out with ``RuntimeError``."""

    async def _body() -> None:
        store = await ConfigStore.open(bus=None, path=_db_path(tmp_path))
        await store.close()
        with pytest.raises(RuntimeError):
            await store.set("k", "v")

    _run(_body())


def test_view_prefix_strips_namespace(tmp_path: Path) -> None:
    """Scoped view surfaces keys with the prefix stripped for plugin reads."""

    async def _body() -> None:
        store = await ConfigStore.open(bus=None, path=_db_path(tmp_path))
        try:
            await store.set("plugin.llm_openai.api_key", "sk-xxx")
            await store.set("plugin.llm_openai.base_url", "https://x.example")
            await store.set("plugin.other.thing", 1)
            scoped = store.view(prefix="plugin.llm_openai.")
            assert scoped["api_key"] == "sk-xxx"
            assert "base_url" in scoped
            assert "thing" not in scoped
            assert set(iter(scoped)) == {"api_key", "base_url"}
            assert len(scoped) == 2
        finally:
            await store.close()

    _run(_body())


def test_view_prefix_non_str_contains_returns_false(tmp_path: Path) -> None:
    """``in`` with a non-string key must return False instead of raising."""

    async def _body() -> None:
        store = await ConfigStore.open(bus=None, path=_db_path(tmp_path))
        try:
            v = store.view()
            assert 42 not in v
        finally:
            await store.close()

    _run(_body())


def test_unset_missing_key_no_cache_hit(tmp_path: Path) -> None:
    """Unsetting an absent key returns False without emitting an event."""

    async def _body() -> None:
        store = await ConfigStore.open(bus=None, path=_db_path(tmp_path))
        try:
            assert await store.unset("nope") is False
        finally:
            await store.close()

    _run(_body())


def test_corrupt_json_loaded_as_raw(tmp_path: Path) -> None:
    """A manually-corrupted row surfaces as the raw string on load."""
    import sqlite3

    path = _db_path(tmp_path)

    async def _seed() -> None:
        store = await ConfigStore.open(bus=None, path=path)
        await store.set("k", "ok")
        await store.close()

    _run(_seed())

    conn = sqlite3.connect(str(path))
    conn.execute("UPDATE config SET value = ? WHERE key = ?", ("not-json{", "k"))
    conn.commit()
    conn.close()

    async def _check() -> None:
        store = await ConfigStore.open(bus=None, path=path)
        try:
            # The raw string bleeds through rather than crashing the loader.
            value = await store.get("k")
            assert value == "not-json{"
        finally:
            await store.close()

    _run(_check())


def test_flatten_kernel_config_includes_plugin_extras() -> None:
    """``flatten_kernel_config`` keys plugin sub-trees under ``plugin.<ns>.``."""
    cfg = KernelConfig(port=1234, llm_openai={"model": "gpt-4o", "base_url": "u"})  # type: ignore[call-arg]
    flat = flatten_kernel_config(cfg)
    assert flat["port"] == 1234
    assert flat["plugin.llm_openai.model"] == "gpt-4o"
    assert flat["plugin.llm_openai.base_url"] == "u"


def test_migration_preserves_list_values(tmp_path: Path) -> None:
    """``plugins_disabled=["web"]`` round-trips through the store."""

    async def _body() -> None:
        cfg = KernelConfig(plugins_disabled=["web"])
        flat = flatten_kernel_config(cfg)
        store = await ConfigStore.open(bus=None, path=_db_path(tmp_path))
        try:
            await store.migrate_from_kernel_config(flat)
            assert await store.get("plugins_disabled") == ["web"]
        finally:
            await store.close()

    _run(_body())
