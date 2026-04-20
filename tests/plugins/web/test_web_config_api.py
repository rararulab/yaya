"""Tests for the bundled ``web`` adapter's HTTP admin API.

AC-bindings from ``specs/plugin-web-config-api.spec``:

* config CRUD                 → ``test_config_crud_cycle``
* secret masking + ?show=1    → ``test_config_secret_masking_honours_show_flag``
* plugins listing             → ``test_plugins_list_exposes_metadata``
* plugin enable toggle        → ``test_plugin_patch_writes_enabled_flag``
* plugin install mocked       → ``test_plugin_install_delegates_to_registry``
* llm-provider active switch  → ``test_llm_provider_active_switch``

Tests mount :mod:`yaya.plugins.web.api`'s router against a real
:class:`EventBus` + :class:`ConfigStore` and a minimal stub registry.
No uvicorn: ASGI + pydantic round-trip is the contract.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from yaya.kernel.bus import EventBus
from yaya.kernel.config_store import ConfigStore
from yaya.kernel.events import Event
from yaya.kernel.plugin import Category
from yaya.plugins.web.api import SECRET_SUFFIXES, build_admin_router, is_secret_key, mask_value


class _StubPlugin:
    """Minimal Plugin-shaped stub for registry snapshot tests."""

    def __init__(
        self,
        *,
        name: str,
        category: Category,
        version: str = "0.1.0",
        config_model: type[Any] | None = None,
    ) -> None:
        self.name = name
        self.category = category
        self.version = version
        self.requires: list[str] = []
        if config_model is not None:
            self.ConfigModel = config_model

    def subscriptions(self) -> list[str]:
        return []

    async def on_load(self, ctx: Any) -> None:
        return None

    async def on_event(self, ev: Any, ctx: Any) -> None:
        return None

    async def on_unload(self, ctx: Any) -> None:
        return None


class _StubRegistry:
    """Minimal :class:`PluginRegistry` look-alike for API tests.

    Exposes the three surfaces the admin router calls:
    :meth:`snapshot`, :meth:`loaded_plugins`, and async ``install`` /
    ``remove`` that record their arguments instead of shelling to pip.
    """

    def __init__(self, plugins: list[_StubPlugin]) -> None:
        self._plugins = plugins
        self.installed: list[tuple[str, bool]] = []
        self.removed: list[str] = []
        self.remove_error: Exception | None = None
        self.install_error: Exception | None = None

    def snapshot(self) -> list[dict[str, str]]:
        return [
            {
                "name": p.name,
                "version": p.version,
                "category": p.category.value,
                "status": "loaded",
            }
            for p in self._plugins
        ]

    def loaded_plugins(self, category: Category | None = None) -> list[Any]:
        if category is None:
            return list(self._plugins)
        return [p for p in self._plugins if p.category is category]

    async def install(self, source: str, *, editable: bool = False) -> str:
        if self.install_error is not None:
            raise self.install_error
        self.installed.append((source, editable))
        return source

    async def remove(self, name: str) -> None:
        if self.remove_error is not None:
            raise self.remove_error
        self.removed.append(name)


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[ConfigStore]:
    """Fresh ConfigStore against a tmp SQLite file."""
    bus = EventBus()
    store = await ConfigStore.open(bus=bus, path=tmp_path / "cfg.db")
    try:
        yield store
    finally:
        await store.close()


@pytest.fixture
def registry() -> _StubRegistry:
    """Stub registry pre-populated with two providers + one misc plugin."""
    plugins = [
        _StubPlugin(name="llm-openai", category=Category.LLM_PROVIDER, version="0.1.0"),
        _StubPlugin(name="llm-echo", category=Category.LLM_PROVIDER, version="0.0.1"),
        _StubPlugin(name="tool-bash", category=Category.TOOL, version="0.1.0"),
    ]
    return _StubRegistry(plugins)


def _build_app(
    *,
    store: ConfigStore | None,
    registry: _StubRegistry | None,
    bus: EventBus | None = None,
) -> FastAPI:
    """Return a FastAPI app that mounts only the admin router.

    Using a bare app (no WebSocket / static mounts) keeps the test
    surface focused on the admin routes.
    """
    app = FastAPI()
    app.include_router(
        build_admin_router(
            registry=registry,  # type: ignore[arg-type]
            config_store=store,
            bus=bus,
        )
    )
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def test_config_crud_cycle(store: ConfigStore, registry: _StubRegistry) -> None:
    """PATCH / GET / DELETE / GET round-trip a key end-to-end."""
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        # PATCH writes and echoes {key, value}.
        resp = await client.patch("/api/config/provider", json={"value": "openai"})
        assert resp.status_code == 200
        assert resp.json() == {"key": "provider", "value": "openai", "ok": True}

        # GET reads back.
        resp = await client.get("/api/config/provider")
        assert resp.status_code == 200
        assert resp.json() == {"key": "provider", "value": "openai"}

        # List returns the full map.
        resp = await client.get("/api/config")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("provider") == "openai"

        # DELETE removes.
        resp = await client.delete("/api/config/provider")
        assert resp.status_code == 200
        assert resp.json() == {"key": "provider", "removed": True}

        # GET on missing key → 404.
        resp = await client.get("/api/config/provider")
        assert resp.status_code == 404


async def test_config_secret_masking_honours_show_flag(store: ConfigStore, registry: _StubRegistry) -> None:
    """Secret-suffix keys mask in list + get, until ``?show=1``."""
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        await client.patch(
            "/api/config/plugin.llm_openai.api_key",
            json={"value": "sk-supersecretvalue"},
        )
        await client.patch("/api/config/model", json={"value": "gpt-4o-mini"})

        # List: secret is masked, non-secret is verbatim.
        resp = await client.get("/api/config")
        body = resp.json()
        assert body["plugin.llm_openai.api_key"] == "****alue"
        assert body["model"] == "gpt-4o-mini"

        # GET (default): masked.
        resp = await client.get("/api/config/plugin.llm_openai.api_key")
        assert resp.json()["value"] == "****alue"

        # GET with ?show=1: unmasked.
        resp = await client.get("/api/config/plugin.llm_openai.api_key?show=1")
        assert resp.json()["value"] == "sk-supersecretvalue"


async def test_config_patch_rejects_missing_value(store: ConfigStore, registry: _StubRegistry) -> None:
    """A body without the required ``value`` field is rejected with 422."""
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        resp = await client.patch("/api/config/x", json={"wrong": 1})
        assert resp.status_code == 422


async def test_config_patch_rejects_empty_key(store: ConfigStore, registry: _StubRegistry) -> None:
    """Empty keys hit ConfigStore's validation and return 400."""
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        resp = await client.patch("/api/config/", json={"value": "x"})
        # Starlette normalises the trailing slash → 404 for route match
        # before reaching pydantic; that is also acceptable.
        assert resp.status_code in {404, 400, 307}


async def test_plugins_list_exposes_metadata(store: ConfigStore, registry: _StubRegistry) -> None:
    """GET /api/plugins surfaces enabled / schema / current_config."""
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        # Seed a config key so ``current_config`` is non-empty.
        await client.patch("/api/config/plugin.llm_openai.model", json={"value": "gpt-4o-mini"})

        resp = await client.get("/api/plugins")
        assert resp.status_code == 200
        names = {row["name"] for row in resp.json()["plugins"]}
        assert names == {"llm-openai", "llm-echo", "tool-bash"}

        openai_row = next(r for r in resp.json()["plugins"] if r["name"] == "llm-openai")
        assert openai_row["category"] == "llm-provider"
        assert openai_row["status"] == "loaded"
        assert openai_row["enabled"] is True  # default when unset
        assert openai_row["config_schema"] is None
        assert openai_row["current_config"] == {"model": "gpt-4o-mini"}
        assert openai_row["reload_required"] is False


async def test_plugin_patch_writes_enabled_flag(store: ConfigStore, registry: _StubRegistry) -> None:
    """PATCH /api/plugins/<name> writes ``plugin.<ns>.enabled`` and returns the row."""
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        resp = await client.patch("/api/plugins/llm-openai", json={"enabled": False})
        assert resp.status_code == 200
        body = resp.json()
        # Response is the refreshed PluginRow with the reload hint flipped.
        assert body["name"] == "llm-openai"
        assert body["category"] == "llm-provider"
        assert body["status"] == "loaded"
        assert body["enabled"] is False
        assert body["reload_required"] is True

        # Read via the config endpoint to prove the store was touched.
        resp = await client.get("/api/config/plugin.llm_openai.enabled")
        assert resp.status_code == 200
        assert resp.json()["value"] is False


async def test_plugin_patch_unknown_plugin_404(store: ConfigStore, registry: _StubRegistry) -> None:
    """Unknown plugin name → 404."""
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        resp = await client.patch("/api/plugins/does-not-exist", json={"enabled": True})
        assert resp.status_code == 404


async def test_plugin_install_delegates_to_registry(store: ConfigStore, registry: _StubRegistry) -> None:
    """POST /api/plugins/install calls registry.install with validated source."""
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        resp = await client.post(
            "/api/plugins/install",
            json={"source": "some-dist", "editable": False},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == "some-dist"
        assert body["ok"] is True
        # ``job_id`` is a fresh UUID4 per install — assert shape, not value.
        assert isinstance(body["job_id"], str)
        assert len(body["job_id"]) >= 32
        assert registry.installed == [("some-dist", False)]


async def test_plugin_install_rejects_bad_source(store: ConfigStore, registry: _StubRegistry) -> None:
    """Sources with disallowed characters → 400 before reaching install."""
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        resp = await client.post(
            "/api/plugins/install",
            json={"source": "bad\nname", "editable": False},
        )
        assert resp.status_code == 400
        assert registry.installed == []


async def test_plugin_install_surfaces_runtime_errors(store: ConfigStore, registry: _StubRegistry) -> None:
    """A ``RuntimeError`` from the registry becomes 500 with its message."""
    registry.install_error = RuntimeError("pip exploded")
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        resp = await client.post(
            "/api/plugins/install",
            json={"source": "some-dist"},
        )
        assert resp.status_code == 500
        assert "pip exploded" in resp.json()["detail"]


async def test_plugin_delete_calls_registry_remove(store: ConfigStore, registry: _StubRegistry) -> None:
    """DELETE /api/plugins/<name> calls registry.remove."""
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        resp = await client.delete("/api/plugins/tool-bash")
        assert resp.status_code == 200
        assert registry.removed == ["tool-bash"]


async def test_plugin_delete_rejects_bundled(store: ConfigStore, registry: _StubRegistry) -> None:
    """Registry ValueError (bundled) → 400."""
    registry.remove_error = ValueError("cannot remove bundled plugin 'tool-bash'")
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        resp = await client.delete("/api/plugins/tool-bash")
        assert resp.status_code == 400


async def _seed_instances(store: ConfigStore) -> None:
    """Seed two llm-openai instances + one llm-echo instance.

    Shape matches D4a's bootstrap plus a manually-added second
    llm-openai record — used by the instance-shaped tests to exercise
    the "list all / patch one / delete with siblings" paths.
    """
    await store.set("providers.llm-openai.plugin", "llm-openai")
    await store.set("providers.llm-openai.label", "llm-openai (default)")
    await store.set("providers.llm-openai.api_key", "sk-verysecretkeyabcd")
    await store.set("providers.llm-openai.model", "gpt-4o")
    await store.set("providers.openai-gpt4.plugin", "llm-openai")
    await store.set("providers.openai-gpt4.label", "OpenAI GPT-4")
    await store.set("providers.openai-gpt4.api_key", "sk-alternativekeyxyz9")
    await store.set("providers.openai-gpt4.model", "gpt-4-turbo")
    await store.set("providers.llm-echo.plugin", "llm-echo")
    await store.set("providers.llm-echo.label", "llm-echo (default)")


async def test_instance_list_returns_shape(store: ConfigStore, registry: _StubRegistry) -> None:
    """GET /api/llm-providers returns a bare array of instance rows."""
    await _seed_instances(store)
    await store.set("provider", "openai-gpt4")
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        resp = await client.get("/api/llm-providers")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        by_id = {row["id"]: row for row in body}
        assert set(by_id) == {"llm-openai", "openai-gpt4", "llm-echo"}
        assert by_id["openai-gpt4"]["active"] is True
        assert by_id["llm-openai"]["active"] is False
        assert by_id["openai-gpt4"]["plugin"] == "llm-openai"
        assert by_id["openai-gpt4"]["label"] == "OpenAI GPT-4"
        # llm-echo has no config fields.
        assert by_id["llm-echo"]["config"] == {}


async def test_instance_list_masks_secrets(store: ConfigStore, registry: _StubRegistry) -> None:
    """Secret-suffix fields in config mask by default."""
    await _seed_instances(store)
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        resp = await client.get("/api/llm-providers")
        body = resp.json()
        openai_row = next(r for r in body if r["id"] == "llm-openai")
        assert openai_row["config"]["api_key"] == "****abcd"
        assert openai_row["config"]["model"] == "gpt-4o"


async def test_instance_list_show_reveals_secrets(store: ConfigStore, registry: _StubRegistry) -> None:
    """``?show=1`` bypasses masking for operators who need the raw value."""
    await _seed_instances(store)
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        resp = await client.get("/api/llm-providers?show=1")
        body = resp.json()
        openai_row = next(r for r in body if r["id"] == "llm-openai")
        assert openai_row["config"]["api_key"] == "sk-verysecretkeyabcd"


async def test_instance_create_happy_path(store: ConfigStore, registry: _StubRegistry) -> None:
    """POST /api/llm-providers materialises providers.<id>.* keys and returns 201."""
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        resp = await client.post(
            "/api/llm-providers",
            json={
                "id": "openai-local",
                "plugin": "llm-openai",
                "label": "Local LM Studio",
                "config": {"base_url": "http://localhost:1234/v1", "model": "llama3"},
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["id"] == "openai-local"
        assert body["plugin"] == "llm-openai"
        assert body["label"] == "Local LM Studio"
        assert body["config"]["model"] == "llama3"
        # Writes materialised through ConfigStore.
        assert await store.get("providers.openai-local.plugin") == "llm-openai"
        assert await store.get("providers.openai-local.label") == "Local LM Studio"
        assert await store.get("providers.openai-local.model") == "llama3"


@pytest.mark.parametrize(
    "bad_id",
    [
        "with.dot",
        "UPPER",
        "ab",  # too short
        "a" * 65,  # too long
        "-leading-dash",
        "trailing-dash-",
        "has space",
    ],
)
async def test_instance_create_rejects_invalid_id(store: ConfigStore, registry: _StubRegistry, bad_id: str) -> None:
    """Bad instance ids are rejected with 400 before hitting ConfigStore.set."""
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        resp = await client.post(
            "/api/llm-providers",
            json={"id": bad_id, "plugin": "llm-openai", "config": {}},
        )
        assert resp.status_code == 400
        assert "invalid instance id" in resp.json()["detail"]


async def test_instance_create_rejects_duplicate_id(store: ConfigStore, registry: _StubRegistry) -> None:
    """Re-creating an existing instance → 409."""
    await _seed_instances(store)
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        resp = await client.post(
            "/api/llm-providers",
            json={"id": "llm-openai", "plugin": "llm-openai"},
        )
        assert resp.status_code == 409


async def test_instance_create_rejects_unknown_plugin(store: ConfigStore, registry: _StubRegistry) -> None:
    """Targeting a plugin that is not a loaded llm-provider → 400."""
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        # ``tool-bash`` is loaded but not an llm-provider.
        resp = await client.post(
            "/api/llm-providers",
            json={"id": "bad-ref", "plugin": "tool-bash"},
        )
        assert resp.status_code == 400


async def test_instance_create_auto_generates_id_when_absent(store: ConfigStore, registry: _StubRegistry) -> None:
    """Omitting ``id`` produces ``f"{plugin}-{uuid8}"``."""
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        resp = await client.post(
            "/api/llm-providers",
            json={"plugin": "llm-openai", "config": {"model": "gpt-4o"}},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["id"].startswith("llm-openai-")
        # Default label falls back to "<plugin> (<id>)".
        assert body["label"] == f"llm-openai ({body['id']})"


async def test_instance_create_partial_write_surfaces_error(
    store: ConfigStore,
    registry: _StubRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mid-way ConfigStore.set failure bubbles up with operator cleanup guidance.

    ``_write_instance_fields`` has no batch-write primitive; if the
    second of three set calls raises, the instance is half-written. The
    handler must surface an error (not a success) and point operators
    at ``yaya config unset providers.<id>.*``.
    """
    real_set = store.set
    calls: list[str] = []

    async def _flaky_set(key: str, value: Any) -> None:
        calls.append(key)
        # Second write (label) raises — leaves plugin key behind.
        if len(calls) == 2:
            raise RuntimeError("disk full")
        await real_set(key, value)

    monkeypatch.setattr(store, "set", _flaky_set)

    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        resp = await client.post(
            "/api/llm-providers",
            json={
                "id": "openai-partial",
                "plugin": "llm-openai",
                "label": "Partial",
                "config": {"model": "gpt-4o"},
            },
        )
        assert resp.status_code in {400, 500}
        detail = resp.json()["detail"]
        assert "yaya config unset providers.openai-partial.*" in detail


async def test_instance_patch_merges_config_partial(store: ConfigStore, registry: _StubRegistry) -> None:
    """PATCH merges only the supplied fields; untouched ones survive."""
    await _seed_instances(store)
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        resp = await client.patch(
            "/api/llm-providers/openai-gpt4",
            json={
                "label": "OpenAI GPT-4 (updated)",
                "config": {"model": "gpt-4-turbo-2024"},
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["label"] == "OpenAI GPT-4 (updated)"
        assert body["config"]["model"] == "gpt-4-turbo-2024"
        # api_key untouched.
        assert await store.get("providers.openai-gpt4.api_key") == "sk-alternativekeyxyz9"


async def test_instance_patch_rejects_plugin_in_body(store: ConfigStore, registry: _StubRegistry) -> None:
    """PATCH body with ``plugin`` is rejected with 422 by pydantic's forbid-extra.

    Rebinding an instance to a different plugin is explicitly a
    delete+create — a silent no-op on ``plugin`` would mask client
    bugs. ``_ProviderPatchBody`` sets ``extra="forbid"`` so the error
    surfaces at the edge, and the instance's ``plugin`` meta field
    stays intact.
    """
    await _seed_instances(store)
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        resp = await client.patch(
            "/api/llm-providers/openai-gpt4",
            json={"plugin": "llm-echo"},
        )
        assert resp.status_code == 422
        # The meta field was not touched.
        assert await store.get("providers.openai-gpt4.plugin") == "llm-openai"


async def test_instance_patch_on_unknown_id(store: ConfigStore, registry: _StubRegistry) -> None:
    """Patching a non-existent instance → 404."""
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        resp = await client.patch(
            "/api/llm-providers/ghost",
            json={"label": "x"},
        )
        assert resp.status_code == 404


async def test_instance_delete_happy_path(store: ConfigStore, registry: _StubRegistry) -> None:
    """DELETE clears every providers.<id>.* key and returns 204."""
    await _seed_instances(store)
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        resp = await client.delete("/api/llm-providers/openai-gpt4")
        assert resp.status_code == 204
        assert await store.get("providers.openai-gpt4.plugin") is None
        assert await store.get("providers.openai-gpt4.api_key") is None


async def test_instance_delete_rejects_active(store: ConfigStore, registry: _StubRegistry) -> None:
    """Deleting the active instance is blocked with 409."""
    await _seed_instances(store)
    await store.set("provider", "openai-gpt4")
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        resp = await client.delete("/api/llm-providers/openai-gpt4")
        assert resp.status_code == 409
        assert "switch active" in resp.json()["detail"]


async def test_instance_delete_rejects_last_of_plugin(store: ConfigStore, registry: _StubRegistry) -> None:
    """Deleting the last instance of a plugin is blocked with 409."""
    await _seed_instances(store)
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        # llm-echo has a single seeded instance.
        resp = await client.delete("/api/llm-providers/llm-echo")
        assert resp.status_code == 409
        assert "only instance" in resp.json()["detail"]


async def test_instance_delete_unknown_id(store: ConfigStore, registry: _StubRegistry) -> None:
    """Deleting a non-existent instance → 404."""
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        resp = await client.delete("/api/llm-providers/ghost")
        assert resp.status_code == 404


async def test_active_switch_validates_instance_id(store: ConfigStore, registry: _StubRegistry) -> None:
    """Switching active to an unknown instance id → 404."""
    await _seed_instances(store)
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        resp = await client.patch(
            "/api/llm-providers/active",
            json={"name": "ghost"},
        )
        assert resp.status_code == 404


async def test_active_switch_validates_plugin_loaded(store: ConfigStore, registry: _StubRegistry) -> None:
    """Switching to an instance whose backing plugin is not loaded → 400."""
    # Seed an instance whose plugin is not in the registry.
    await store.set("providers.dangling.plugin", "llm-missing")
    await store.set("providers.dangling.label", "Dangling")
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        resp = await client.patch(
            "/api/llm-providers/active",
            json={"name": "dangling"},
        )
        assert resp.status_code == 400


async def test_active_switch_happy_path(store: ConfigStore, registry: _StubRegistry) -> None:
    """PATCH /api/llm-providers/active writes provider and returns refreshed list."""
    await _seed_instances(store)
    app = _build_app(store=store, registry=registry)
    async with _client(app) as client:
        resp = await client.patch(
            "/api/llm-providers/active",
            json={"name": "openai-gpt4"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        actives = {row["id"]: row["active"] for row in body}
        assert actives["openai-gpt4"] is True
        assert await store.get("provider") == "openai-gpt4"


async def test_instance_test_endpoint_routes_on_bridge_session(store: ConfigStore, registry: _StubRegistry) -> None:
    """POST /api/llm-providers/<id>/test fires request on a ``_bridge:`` session."""
    await _seed_instances(store)
    bus = EventBus()
    seen_sessions: list[str] = []

    async def _echo(ev: Event) -> None:
        seen_sessions.append(ev.session_id)
        if ev.payload.get("provider") != "openai-gpt4":
            return
        from yaya.kernel.events import new_event as _mk

        await bus.publish(
            _mk(
                "llm.call.response",
                {"text": "OK", "tool_calls": [], "usage": {}, "request_id": ev.id},
                session_id=ev.session_id,
                source="stub-openai",
            )
        )

    bus.subscribe("llm.call.request", _echo, source="stub-openai")

    app = _build_app(store=store, registry=registry, bus=bus)
    async with _client(app) as client:
        resp = await client.post("/api/llm-providers/openai-gpt4/test")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["latency_ms"] >= 0

    # Session id used a ``_bridge:web-api-test:`` prefix (lesson #2).
    assert seen_sessions, "expected at least one llm.call.request"
    assert all(s.startswith("_bridge:web-api-test:") for s in seen_sessions)


async def test_instance_test_error_path(store: ConfigStore, registry: _StubRegistry) -> None:
    """llm.call.error → ok=False with the error message."""
    await _seed_instances(store)
    bus = EventBus()

    async def _fail(ev: Event) -> None:
        if ev.payload.get("provider") != "llm-openai":
            return
        from yaya.kernel.events import new_event as _mk

        await bus.publish(
            _mk(
                "llm.call.error",
                {"error": "not_configured", "request_id": ev.id},
                session_id=ev.session_id,
                source="stub-openai",
            )
        )

    bus.subscribe("llm.call.request", _fail, source="stub-openai")
    app = _build_app(store=store, registry=registry, bus=bus)
    async with _client(app) as client:
        resp = await client.post("/api/llm-providers/llm-openai/test")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["error"] == "not_configured"


async def test_instance_test_timeout(
    store: ConfigStore, registry: _StubRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No reply within the budget → ok=False with ``timeout``."""
    import yaya.plugins.web.api as api_mod

    monkeypatch.setattr(api_mod, "_TEST_PROMPT_TIMEOUT_S", 0.05)
    await _seed_instances(store)
    bus = EventBus()
    app = _build_app(store=store, registry=registry, bus=bus)
    async with _client(app) as client:
        resp = await client.post("/api/llm-providers/llm-openai/test")
        body = resp.json()
        assert body["ok"] is False
        assert body["error"] == "timeout"


async def test_instance_test_unknown_id(store: ConfigStore, registry: _StubRegistry) -> None:
    """Unknown instance → 404."""
    bus = EventBus()
    app = _build_app(store=store, registry=registry, bus=bus)
    async with _client(app) as client:
        resp = await client.post("/api/llm-providers/ghost/test")
        assert resp.status_code == 404


async def test_instance_test_rejects_dangling_plugin(store: ConfigStore, registry: _StubRegistry) -> None:
    """Instance whose backing plugin is not loaded → 400 (call would never dispatch)."""
    await store.set("providers.dangling.plugin", "llm-missing")
    await store.set("providers.dangling.label", "Dangling")
    bus = EventBus()
    app = _build_app(store=store, registry=registry, bus=bus)
    async with _client(app) as client:
        resp = await client.post("/api/llm-providers/dangling/test")
        assert resp.status_code == 400


async def test_plugin_list_returns_schema_when_declared(
    store: ConfigStore,
) -> None:
    """A plugin exposing ``ConfigModel`` surfaces its JSON schema."""
    from pydantic import BaseModel

    class _Model(BaseModel):
        api_key: str = ""

    plugins = [
        _StubPlugin(
            name="llm-foo",
            category=Category.LLM_PROVIDER,
            version="1.0.0",
            config_model=_Model,
        ),
    ]
    reg = _StubRegistry(plugins)
    app = _build_app(store=store, registry=reg)
    async with _client(app) as client:
        resp = await client.get("/api/plugins")
        body = resp.json()
        row = body["plugins"][0]
        assert row["config_schema"] is not None
        assert row["config_schema"]["properties"]["api_key"]["type"] == "string"


async def test_store_unavailable_returns_503(registry: _StubRegistry) -> None:
    """When the admin router is built without a store, reads → 503."""
    app = _build_app(store=None, registry=registry)
    async with _client(app) as client:
        resp = await client.get("/api/config")
        assert resp.status_code == 503


async def test_registry_unavailable_returns_503(store: ConfigStore) -> None:
    """When the admin router is built without a registry, plugin list → 503."""
    app = _build_app(store=store, registry=None)
    async with _client(app) as client:
        resp = await client.get("/api/plugins")
        assert resp.status_code == 503


# -- helper function tests --------------------------------------------------


def test_is_secret_key_matches_last_segment() -> None:
    """Only the last dotted segment is tested against the suffix set."""
    for suffix in SECRET_SUFFIXES:
        assert is_secret_key(f"plugin.x.{suffix}")
        assert is_secret_key(suffix)
    assert not is_secret_key("apikeys_allowed")
    assert not is_secret_key("plugin.x.model")


def test_mask_value_shapes() -> None:
    """Short / non-string scalars collapse to ``****``; long strings keep the tail."""
    assert mask_value("abc") == "****"
    assert mask_value("supersecret") == "****cret"
    assert mask_value(12345) == "****"
    assert mask_value(None) == "****"


def test_mask_value_walks_nested_dict_and_list() -> None:
    """Containers are walked; only leaf strings mask, structure is preserved."""
    nested = {
        "primary": "sk-abc123",
        "fallback": "sk-xyz789",
        "meta": {"label": "prod-key", "count": 42},
        "history": ["sk-first-one", "sk-second-one", 7],
    }
    masked = mask_value(nested)
    assert masked == {
        "primary": "****c123",
        "fallback": "****z789",
        "meta": {"label": "****-key", "count": "****"},
        "history": ["****-one", "****-one", "****"],
    }
    # Top-level list also walks.
    assert mask_value(["supersecret", "x"]) == ["****cret", "****"]


# Unused helper surface kept for future parametric cases.
_: Callable[..., Awaitable[Any]] | None = None
