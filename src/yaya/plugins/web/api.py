"""HTTP admin API for the bundled web adapter.

Adds REST endpoints on top of the kernel's live
:class:`~yaya.kernel.config_store.ConfigStore` and
:class:`~yaya.kernel.registry.PluginRegistry` so the browser UI can
drive config edits, plugin enable / install / remove, and LLM-provider
selection without a restart.

Security posture: the routes are **unauthenticated**. The kernel
binds ``127.0.0.1`` only through 1.0 (``GOAL.md`` non-goals), so the
local-only assumption is the only authorization. Operators running
``yaya`` behind a reverse proxy accept the risk of exposing these
routes; a future PR layers a capability token when public bind lands.

Layering: imports only :mod:`yaya.kernel` plus stdlib + ``fastapi`` /
``pydantic``. No reaching into ``yaya.cli`` or ``yaya.core``.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING, Any, cast

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from yaya.kernel.events import Event, new_event
from yaya.kernel.plugin import Category

if TYPE_CHECKING:  # pragma: no cover - type-only imports, avoid cycles.
    from yaya.kernel.bus import EventBus
    from yaya.kernel.config_store import ConfigStore
    from yaya.kernel.plugin import Plugin
    from yaya.kernel.registry import PluginRegistry

__all__ = [
    "SECRET_SUFFIXES",
    "build_admin_router",
    "is_secret_key",
    "mask_value",
]

SECRET_SUFFIXES: tuple[str, ...] = ("api_key", "token", "secret", "password")
"""Dotted-suffix tokens that mark a config key as secret-bearing.

Mirrors :mod:`yaya.cli.commands.config` so the CLI ``config list``
output and the HTTP ``/api/config`` response redact the same set of
keys. A single source would be nicer; the duplication is kept here
to preserve the kernel-vs-plugin layering boundary (``cli/`` cannot
import from plugin code, and plugin code cannot import from
``cli/``).
"""

_PROVIDER_CONFIG_KEY = "provider"
"""Config key the strategy plugin reads to pick the active LLM provider."""

_TEST_PROMPT_MODEL_DEFAULT = "gpt-4o-mini"
"""Model name used by ``POST /api/llm-providers/<name>/test``.

Provider plugins that ignore ``model`` simply echo; the important
signal is the request/response round-trip latency.
"""

_TEST_PROMPT_TIMEOUT_S: float = 5.0
"""Upper bound the ``/test`` endpoint waits for ``llm.call.response``."""


def is_secret_key(key: str) -> bool:
    """Return True when ``key`` ends with a secret suffix.

    Matches the last dotted segment so ``plugin.llm_openai.api_key``
    and the bare ``api_key`` both redact; does NOT over-match keys
    that merely contain a suffix as a substring (``apikeys_allowed``
    stays visible).
    """
    last = key.rsplit(".", 1)[-1].lower()
    return last in SECRET_SUFFIXES


def mask_value(value: Any) -> Any:
    """Render a secret value, preserving container structure.

    Leaf strings collapse to ``****<last4>`` (or ``****`` when shorter
    than five chars). Dicts and lists are walked recursively so the
    UI can render a nested config value (e.g. ``{primary: ...,
    fallback: ...}``) with each leaf masked independently. Non-string,
    non-container scalars collapse to ``****`` so their type does not
    leak.
    """
    if isinstance(value, str):
        if len(value) <= 4:
            return "****"
        return f"****{value[-4:]}"
    if isinstance(value, dict):
        walked = cast("dict[str, Any]", value)
        return {k: mask_value(v) for k, v in walked.items()}
    if isinstance(value, list):
        # mypy infers ``list[Any]`` after the isinstance narrow, so the
        # cast would be redundant; pyright needs the explicit annotation
        # to drop the partially-unknown-list warning. A bare
        # ``list[Any]`` local satisfies both — the ignore pins the
        # single cross-checker skew on this one line.
        items: list[Any] = value  # pyright: ignore[reportUnknownVariableType]
        return [mask_value(v) for v in items]
    return "****"


class _ConfigPatchBody(BaseModel):
    """Body shape for ``PATCH /api/config/{key}``."""

    value: Any = Field(..., description="New value; JSON-encodable.")


class _PluginPatchBody(BaseModel):
    """Body shape for ``PATCH /api/plugins/{name}``."""

    enabled: bool = Field(..., description="Target enabled state.")


class _PluginInstallBody(BaseModel):
    """Body shape for ``POST /api/plugins/install``."""

    source: str = Field(..., min_length=1)
    editable: bool = Field(default=False)


class _ProviderActiveBody(BaseModel):
    """Body shape for ``PATCH /api/llm-providers/active``."""

    name: str = Field(..., min_length=1)


def _plugin_config_schema(plugin: Plugin) -> dict[str, Any] | None:
    """Return ``plugin.ConfigModel``'s JSON schema when declared.

    Plugins opt in by exposing a class attribute ``ConfigModel`` that
    subclasses :class:`pydantic.BaseModel`. Anything else (missing
    attribute, non-pydantic class, malformed schema call) resolves to
    ``None`` so a broken plugin does not taint the ``GET`` response.
    """
    model_cls = getattr(plugin, "ConfigModel", None)
    if model_cls is None:
        return None
    schema_fn = getattr(model_cls, "model_json_schema", None)
    if not callable(schema_fn):
        return None
    try:
        schema = schema_fn()
    except Exception:
        return None
    if isinstance(schema, dict):
        return cast("dict[str, Any]", schema)
    return None


def _plugin_current_config(
    plugin: Plugin,
    store: ConfigStore | None,
) -> dict[str, Any]:
    """Collect the ``plugin.<ns>.*`` keys into a nested dict.

    The ConfigStore stores flat dotted keys; the API returns the keys
    with their plugin prefix stripped so the UI can render the plugin's
    config namespace without re-parsing.
    """
    if store is None:
        return {}
    ns = plugin.name.replace("-", "_")
    prefix = f"plugin.{ns}."
    view = store.view(prefix)
    return {key: view[key] for key in view}


async def _plugin_enabled(plugin: Plugin, store: ConfigStore | None) -> bool:
    """Read ``plugin.<ns>.enabled`` with a sensible default.

    Missing keys default to ``True`` — a freshly installed plugin is
    enabled on next reload unless the operator explicitly toggles it
    off.
    """
    if store is None:
        return True
    ns = plugin.name.replace("-", "_")
    raw = await store.get(f"plugin.{ns}.enabled", True)
    return bool(raw)


def _validate_install_source_guard(source: str) -> None:
    """Delegate to the registry's validator but raise ``HTTPException``.

    The registry raises :class:`ValueError` with a shell-safe message;
    we surface that as ``400`` without leaking argv-level details.
    """
    from yaya.kernel.registry import validate_install_source

    try:
        validate_install_source(source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def build_admin_router(
    *,
    registry: PluginRegistry | None,
    config_store: ConfigStore | None,
    bus: EventBus | None,
) -> APIRouter:
    """Assemble the HTTP admin router.

    Factored out of the adapter's :meth:`_build_app` so tests can
    mount the routes against a stub registry / in-memory
    :class:`ConfigStore` without booting uvicorn.

    Args:
        registry: Live plugin registry; endpoints that need it return
            ``503`` when ``None`` so tests and the ``yaya plugin list``
            transient path still expose ``/api/health``.
        config_store: Live config store; same ``503`` treatment when
            absent.
        bus: Event bus used by ``POST /api/llm-providers/{name}/test``
            to fire ``llm.call.request``.
    """
    router = APIRouter()
    _register_config_routes(router, config_store)
    _register_plugin_routes(router, registry, config_store)
    _register_provider_routes(router, registry, config_store, bus)
    return router


def _require(obj: Any, label: str) -> Any:
    """Raise 503 when a dependency is missing, else return it."""
    if obj is None:
        raise HTTPException(status_code=503, detail=f"{label} unavailable")
    return obj


def _register_config_routes(
    router: APIRouter,
    config_store: ConfigStore | None,
) -> None:
    """Attach the four ``/api/config*`` endpoints to ``router``."""

    @router.get("/api/config")
    async def _config_list() -> JSONResponse:
        """Return every config key with secret values masked."""
        store = cast("ConfigStore", _require(config_store, "config store"))
        rows = await store.list_prefix("")
        out: dict[str, Any] = {key: (mask_value(value) if is_secret_key(key) else value) for key, value in rows.items()}
        return JSONResponse(out)

    @router.get("/api/config/{key:path}")
    async def _config_get(key: str, show: int = 0) -> JSONResponse:
        """Return one key. ``?show=1`` bypasses masking for secrets."""
        store = cast("ConfigStore", _require(config_store, "config store"))
        value = await store.get(key, default=_SENTINEL)
        if value is _SENTINEL:
            raise HTTPException(status_code=404, detail=f"key not found: {key}")
        if is_secret_key(key) and not show:
            return JSONResponse({"key": key, "value": mask_value(value)})
        return JSONResponse({"key": key, "value": value})

    @router.patch("/api/config/{key:path}")
    async def _config_patch(key: str, body: _ConfigPatchBody) -> JSONResponse:
        """Upsert a config key. Body: ``{value: ...}``."""
        store = cast("ConfigStore", _require(config_store, "config store"))
        try:
            await store.set(key, body.value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # Echo ``{key, value}`` per the TypeScript client contract in
        # ``src/yaya/plugins/web/src/api.ts``. ``ok`` is kept for
        # pre-existing clients; the new field is ``value``.
        return JSONResponse({"key": key, "value": body.value, "ok": True})

    @router.delete("/api/config/{key:path}")
    async def _config_delete(key: str) -> JSONResponse:
        """Remove a config key; idempotent."""
        store = cast("ConfigStore", _require(config_store, "config store"))
        removed = await store.unset(key)
        return JSONResponse({"key": key, "removed": bool(removed)})


async def _read_enabled_by_name(name: str, store: ConfigStore | None) -> bool:
    """Read ``plugin.<ns>.enabled`` by name, defaulting to ``True``.

    Variant of :func:`_plugin_enabled` that does not require a live
    Plugin object — used on the unloaded branch of
    :func:`_build_plugin_row` so failed-load plugins do not show as
    ``enabled=True`` when the config says otherwise.
    """
    if store is None:
        return True
    ns = name.replace("-", "_")
    raw = await store.get(f"plugin.{ns}.enabled", True)
    return bool(raw)


async def _build_plugin_row(
    row: dict[str, str],
    by_name: dict[str, Plugin],
    config_store: ConfigStore | None,
) -> dict[str, Any]:
    """Assemble one ``/api/plugins`` row with live metadata.

    ``reload_required`` defaults to ``False`` — it is the row's
    pristine state. :func:`_mark_reload_required` flips it to ``True``
    on the row returned by ``PATCH /api/plugins/<name>`` so the UI can
    surface a "reload to apply" hint without a separate round trip.
    """
    name = row["name"]
    plugin = by_name.get(name)
    if plugin is None:
        return {
            **row,
            "enabled": await _read_enabled_by_name(name, config_store),
            "config_schema": None,
            "current_config": {},
            "reload_required": False,
        }
    return {
        "name": name,
        "category": row["category"],
        "status": row["status"],
        "version": row["version"],
        "enabled": await _plugin_enabled(plugin, config_store),
        "config_schema": _plugin_config_schema(plugin),
        "current_config": _plugin_current_config(plugin, config_store),
        "reload_required": False,
    }


async def _apply_plugin_patch(
    reg: PluginRegistry,
    store: ConfigStore,
    name: str,
    enabled: bool,
) -> JSONResponse:
    """Persist the enabled flip and return the refreshed row.

    Extracted from the ``PATCH /api/plugins/{name}`` handler to keep
    the route body under the cyclomatic-complexity budget.
    """
    by_name: dict[str, Plugin] = {p.name: p for p in reg.loaded_plugins()}
    snap_row = next((r for r in reg.snapshot() if r["name"] == name), None)
    if name not in by_name and snap_row is None:
        raise HTTPException(status_code=404, detail=f"plugin not found: {name}")
    ns = name.replace("-", "_")
    await store.set(f"plugin.{ns}.enabled", enabled)
    if snap_row is None:
        # Defensive — snap_row is None only when the plugin is loaded
        # but not in the snapshot, which the registry invariants
        # prevent. Synthesize the minimal row so the response shape
        # stays consistent.
        loaded = by_name[name]
        snap_row = {
            "name": loaded.name,
            "version": loaded.version,
            "category": loaded.category.value,
            "status": "loaded",
        }
    refreshed = await _build_plugin_row(snap_row, by_name, store)
    refreshed["reload_required"] = True
    return JSONResponse(refreshed)


def _register_plugin_routes(
    router: APIRouter,
    registry: PluginRegistry | None,
    config_store: ConfigStore | None,
) -> None:
    """Attach the four ``/api/plugins*`` endpoints to ``router``."""

    @router.get("/api/plugins")
    async def _plugins_list() -> JSONResponse:
        """List every registered plugin with live metadata."""
        reg = cast("PluginRegistry", _require(registry, "plugin registry"))
        by_name: dict[str, Plugin] = {p.name: p for p in reg.loaded_plugins()}
        rows = [await _build_plugin_row(row, by_name, config_store) for row in reg.snapshot()]
        return JSONResponse({"plugins": rows})

    @router.patch("/api/plugins/{name}")
    async def _plugins_patch(name: str, body: _PluginPatchBody) -> JSONResponse:
        """Toggle a plugin's ``enabled`` flag and return the refreshed row.

        ``reload_required=True`` on the returned row is a UI hint —
        ephemeral per-process state, NOT persisted to the config
        store. The kernel must be reloaded for the enabled flip to
        take effect.
        """
        reg = cast("PluginRegistry", _require(registry, "plugin registry"))
        store = cast("ConfigStore", _require(config_store, "config store"))
        return await _apply_plugin_patch(reg, store, name, body.enabled)

    @router.post("/api/plugins/install")
    async def _plugins_install(body: _PluginInstallBody) -> JSONResponse:
        """Install a plugin package via the registry's install path.

        The install is **synchronous** today — the handler blocks until
        ``registry.install`` returns. The ``job_id`` in the response is
        a correlation id the UI can pin to a progress row; when a
        future PR moves installs to a background queue the same field
        will carry a pollable job handle and the response will become
        ``202 Accepted`` instead of ``200``.
        """
        reg = cast("PluginRegistry", _require(registry, "plugin registry"))
        _validate_install_source_guard(body.source)
        try:
            await reg.install(body.source, editable=body.editable)
        except ValueError as exc:  # pragma: no cover - guard above screens these.
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return JSONResponse({
            "job_id": str(uuid.uuid4()),
            "source": body.source,
            "ok": True,
        })

    @router.delete("/api/plugins/{name}")
    async def _plugins_delete(name: str) -> JSONResponse:
        """Uninstall a plugin package via the registry's remove path."""
        reg = cast("PluginRegistry", _require(registry, "plugin registry"))
        try:
            await reg.remove(name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return JSONResponse({"name": name, "removed": True})


async def _collect_provider_rows(
    registry: PluginRegistry,
    config_store: ConfigStore | None,
) -> list[dict[str, Any]]:
    """Build the list of LLM-provider rows served by the API.

    Extracted from the ``GET /api/llm-providers`` handler so the
    ``PATCH /api/llm-providers/active`` handler can return the
    refreshed list in the same round trip — the UI atomically
    re-renders from one response body.
    """
    providers = registry.loaded_plugins(Category.LLM_PROVIDER)
    active_name: str | None = None
    if config_store is not None:
        raw = await config_store.get(_PROVIDER_CONFIG_KEY)
        if isinstance(raw, str) and raw:
            active_name = raw
    return [
        {
            "name": plugin.name,
            "version": plugin.version,
            "active": plugin.name == active_name,
            "config_schema": _plugin_config_schema(plugin),
            "current_config": _plugin_current_config(plugin, config_store),
        }
        for plugin in providers
    ]


def _register_provider_routes(
    router: APIRouter,
    registry: PluginRegistry | None,
    config_store: ConfigStore | None,
    bus: EventBus | None,
) -> None:
    """Attach the three ``/api/llm-providers*`` endpoints to ``router``."""

    @router.get("/api/llm-providers")
    async def _providers_list() -> JSONResponse:
        """List every loaded LLM-provider plugin with the active flag.

        Returns a bare JSON array (``LlmProviderRow[]``) to match the
        TypeScript client contract in
        ``src/yaya/plugins/web/src/api.ts``.
        """
        reg = cast("PluginRegistry", _require(registry, "plugin registry"))
        rows = await _collect_provider_rows(reg, config_store)
        return JSONResponse(rows)

    @router.patch("/api/llm-providers/active")
    async def _providers_set_active(body: _ProviderActiveBody) -> JSONResponse:
        """Switch the active provider and return the refreshed list.

        Validation uses :meth:`loaded_plugins` (not ``snapshot``) so
        only currently-loaded providers are selectable — a plugin that
        is installed but failed to load cannot be made active.
        """
        reg = cast("PluginRegistry", _require(registry, "plugin registry"))
        store = cast("ConfigStore", _require(config_store, "config store"))
        loaded_providers = reg.loaded_plugins(Category.LLM_PROVIDER)
        loaded_provider_names = {p.name for p in loaded_providers}
        if body.name not in loaded_provider_names:
            # Distinguish "not a provider (present elsewhere in the
            # snapshot)" from "unknown plugin" so the UI can render a
            # useful error.
            snap_match = next((r for r in reg.snapshot() if r["name"] == body.name), None)
            if snap_match is None:
                raise HTTPException(status_code=404, detail=f"plugin not found: {body.name}")
            raise HTTPException(
                status_code=400,
                detail=f"{body.name!r} is not a loaded llm-provider plugin",
            )
        await store.set(_PROVIDER_CONFIG_KEY, body.name)
        rows = await _collect_provider_rows(reg, store)
        return JSONResponse(rows)

    @router.post("/api/llm-providers/{name}/test")
    async def _providers_test(name: str) -> JSONResponse:
        """Fire a one-shot prompt and return latency / error."""
        reg = cast("PluginRegistry", _require(registry, "plugin registry"))
        bus_obj = cast("EventBus", _require(bus, "event bus"))
        providers = reg.loaded_plugins(Category.LLM_PROVIDER)
        plugin = next((p for p in providers if p.name == name), None)
        if plugin is None:
            raise HTTPException(status_code=404, detail=f"llm-provider not found: {name}")
        return await _run_test_prompt(plugin.name, bus_obj)


async def _run_test_prompt(plugin_name: str, bus_obj: EventBus) -> JSONResponse:
    """Fire one ``llm.call.request`` and wait for a reply.

    The endpoint publishes ``llm.call.request`` with a fresh request
    id, subscribes to both ``llm.call.response`` and
    ``llm.call.error``, and waits up to :data:`_TEST_PROMPT_TIMEOUT_S`
    seconds for a reply tagged with that request id.
    """
    provider_id = plugin_name[len("llm-") :] if plugin_name.startswith("llm-") else plugin_name
    session_id = f"test-{uuid.uuid4().hex[:8]}"
    request = new_event(
        "llm.call.request",
        {
            "provider": provider_id,
            "model": _TEST_PROMPT_MODEL_DEFAULT,
            "messages": [{"role": "user", "content": "say OK"}],
        },
        session_id=session_id,
        source="web-admin-test",
    )

    future: asyncio.Future[tuple[str, Any]] = asyncio.get_running_loop().create_future()

    async def _on_response(ev: Event) -> None:
        if future.done() or ev.payload.get("request_id") != request.id:
            return
        future.set_result(("ok", ev.payload))

    async def _on_error(ev: Event) -> None:
        if future.done() or ev.payload.get("request_id") != request.id:
            return
        future.set_result(("error", ev.payload))

    sub_ok = bus_obj.subscribe("llm.call.response", _on_response, source="web-admin-test")
    sub_err = bus_obj.subscribe("llm.call.error", _on_error, source="web-admin-test")
    started = time.monotonic()
    try:
        await bus_obj.publish(request)
        try:
            kind, payload = await asyncio.wait_for(future, timeout=_TEST_PROMPT_TIMEOUT_S)
        except TimeoutError:
            latency_ms = int((time.monotonic() - started) * 1000)
            return JSONResponse({"ok": False, "latency_ms": latency_ms, "error": "timeout"})
    finally:
        sub_ok.unsubscribe()
        sub_err.unsubscribe()
    latency_ms = int((time.monotonic() - started) * 1000)
    if kind == "ok":
        return JSONResponse({"ok": True, "latency_ms": latency_ms})
    return JSONResponse({
        "ok": False,
        "latency_ms": latency_ms,
        "error": str(payload.get("error", "unknown")),
    })


class _Sentinel:
    """Private marker for "key missing" in ``GET /api/config/{key}``.

    Using a dedicated sentinel lets the handler distinguish a stored
    ``None`` from a missing key without re-querying the store.
    """


_SENTINEL = _Sentinel()


# Explicit re-export so mypy does not complain about the unused import
# in the TYPE_CHECKING block.
if TYPE_CHECKING:  # pragma: no cover
    _: ValidationError | None = None
