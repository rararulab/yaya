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
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from yaya.kernel.events import Event, new_event
from yaya.kernel.plugin import Category
from yaya.kernel.providers import (
    PROVIDERS_PREFIX,
    InstanceRow,
    ProvidersView,
    is_valid_instance_id,
)

if TYPE_CHECKING:  # pragma: no cover - type-only imports, avoid cycles.
    from pathlib import Path

    from yaya.kernel.bus import EventBus
    from yaya.kernel.config_store import ConfigStore
    from yaya.kernel.plugin import Plugin
    from yaya.kernel.registry import PluginRegistry
    from yaya.kernel.session import SessionStore

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


class _SessionPatchBody(BaseModel):
    """Body shape for ``PATCH /api/sessions/{id}`` — issue #161."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        ...,
        max_length=200,
        description="New display name; non-empty after strip; capped at 200 chars.",
    )


class _ProviderActiveBody(BaseModel):
    """Body shape for ``PATCH /api/llm-providers/active``.

    Accepts the active *instance id* post-D4c. The field is still
    named ``name`` for backwards compatibility with PR #110 clients
    that haven't migrated yet.
    """

    name: str = Field(..., min_length=1)


class _ProviderCreateBody(BaseModel):
    """Body shape for ``POST /api/llm-providers``.

    ``id`` is optional — when omitted the handler generates
    ``f"{plugin}-{uuid8}"``. ``label`` is optional — the handler
    falls back to ``f"{plugin} ({id})"``. ``config`` is optional
    and merges into ``providers.<id>.<field>`` keys one-by-one.
    """

    id: str | None = Field(default=None, description="Caller-supplied instance id.")
    plugin: str = Field(..., min_length=1, description="Backing llm-provider plugin name.")
    label: str | None = Field(default=None, description="Human-friendly display label.")
    config: dict[str, Any] = Field(default_factory=dict, description="Schema fields for the backing plugin.")


class _ProviderPatchBody(BaseModel):
    """Body shape for ``PATCH /api/llm-providers/<id>``.

    Both fields are optional — the handler merges only those supplied.
    Note the absence of ``plugin`` and ``id``: rebinding an instance to
    a different plugin is explicitly a delete+create, not a patch.

    Extra fields are forbidden so a client bug sending ``plugin`` (a
    rebinding attempt) surfaces as a 422 instead of silently being
    dropped — otherwise the operator sees a successful 200 while the
    backing plugin stays on the old value.
    """

    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None)
    config: dict[str, Any] | None = Field(default=None)


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
    session_store: SessionStore | None = None,
    workspace: Path | None = None,
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
        session_store: Live session store; ``GET /api/sessions`` returns
            ``503`` when absent.
        workspace: Workspace path the session listing is scoped to.
            Must be provided when ``session_store`` is; tests typically
            inject a ``tmp_path``.
    """
    router = APIRouter()
    _register_config_routes(router, config_store)
    _register_plugin_routes(router, registry, config_store)
    _register_provider_routes(router, registry, config_store, bus)
    _register_session_routes(router, session_store, workspace)
    return router


def _session_row_to_json(info: Any) -> dict[str, Any]:
    """Serialise a :class:`SessionInfo` row to the JSON shape the sidebar consumes.

    Factored out of the session route handlers so the list, rename
    response, and any future session-row endpoint emit an identical
    shape. The argument is typed as :data:`Any` to avoid a TYPE_CHECKING
    re-import of :class:`SessionInfo` at runtime.
    """
    return {
        "id": info.session_id,
        "tape_name": info.tape_name,
        "created_at": info.created_at,
        "entry_count": info.entry_count,
        "last_anchor": info.last_anchor,
        "preview": info.preview,
        "name": info.name,
    }


async def _session_delete_handler(
    session_id: str,
    store: SessionStore,
    workspace: Path,
) -> JSONResponse:
    """Archive one tape; returns 204 / 400 / 404 per the #161 contract."""
    if not session_id.strip():
        raise HTTPException(status_code=400, detail="session id may not be empty")
    try:
        await store.archive(session_id, workspace=workspace)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return JSONResponse(None, status_code=204)


async def _session_patch_handler(
    session_id: str,
    name: str,
    store: SessionStore,
    workspace: Path,
) -> JSONResponse:
    """Rename one tape via an append-only anchor; returns the refreshed row."""
    if not name.strip():
        raise HTTPException(status_code=400, detail="name may not be empty")
    infos = await store.list_sessions(workspace)
    match = next((info for info in infos if info.session_id == session_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
    session = await store.open(workspace, session_id)
    try:
        await session.rename(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    refreshed = await store.list_sessions(workspace)
    row = next((info for info in refreshed if info.session_id == session_id), None)
    if row is None:  # pragma: no cover — we just wrote to it.
        raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
    return JSONResponse(_session_row_to_json(row))


def _register_session_routes(
    router: APIRouter,
    session_store: SessionStore | None,
    workspace: Path | None,
) -> None:
    """Attach the ``/api/sessions*`` routes so the sidebar can hydrate history."""

    @router.get("/api/sessions")
    async def _sessions_list() -> JSONResponse:
        """Return persisted sessions (newest first) for the current workspace.

        Each row carries ``{id, tape_name, created_at, entry_count,
        last_anchor}``. Sorted by ``created_at`` descending so the UI
        can render in display order without additional work.
        """
        store = cast("SessionStore", _require(session_store, "session store"))
        ws = cast("Path", _require(workspace, "session workspace"))
        infos = await store.list_sessions(ws)
        rows = [_session_row_to_json(info) for info in infos]
        rows.sort(key=lambda r: r["created_at"] or "", reverse=True)
        return JSONResponse({"sessions": rows})

    @router.get("/api/sessions/{session_id}/messages")
    async def _sessions_messages(session_id: str) -> JSONResponse:
        """Return the projected ``{role, content}`` history for ``session_id``.

        The id accepted here is the same one ``/api/sessions`` emits —
        i.e. the hashed tape suffix (``md5(original_id)[:16]``). The
        endpoint resolves that id to the on-disk tape, pulls every
        entry, and runs it through the loop's canonical projection
        helper (``project_entries_to_messages``) so the chat pane's
        history view matches what the agent loop will see on the next
        turn. Tool-call replay fidelity is deliberately out of scope
        for v1 — tool observations already appear as
        ``role="user"`` ``Observation: ...`` messages in the tape
        since the ReAct strategy persists them that way, so they
        render as plain user bubbles and stay faithful to what the
        LLM was shown.

        Returns ``404`` when the id does not resolve to a known tape,
        ``503`` when the store / workspace pair was not wired.
        """
        # Local import avoids a module-level import cycle: ``loop`` pulls
        # in the full agent machinery which the plugin layer otherwise
        # does not need at import time.
        from yaya.kernel.loop import project_entries_to_messages

        store = cast("SessionStore", _require(session_store, "session store"))
        ws = cast("Path", _require(workspace, "session workspace"))
        infos = await store.list_sessions(ws)
        match = next((info for info in infos if info.session_id == session_id), None)
        if match is None:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
        session = await store.open(ws, session_id)
        entries = await session.entries()
        messages = project_entries_to_messages(entries)
        return JSONResponse({"messages": messages})

    @router.delete("/api/sessions/{session_id}")
    async def _sessions_delete(session_id: str) -> JSONResponse:
        """Archive a tape and remove it from the live list (#161)."""
        store = cast("SessionStore", _require(session_store, "session store"))
        ws = cast("Path", _require(workspace, "session workspace"))
        return await _session_delete_handler(session_id, store, ws)

    @router.patch("/api/sessions/{session_id}")
    async def _sessions_patch(session_id: str, body: _SessionPatchBody) -> JSONResponse:
        """Rename a session via an append-only ``session/renamed`` anchor (#161)."""
        store = cast("SessionStore", _require(session_store, "session store"))
        ws = cast("Path", _require(workspace, "session workspace"))
        return await _session_patch_handler(session_id, body.name, store, ws)


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


def _plugin_by_name(
    registry: PluginRegistry,
    name: str,
    *,
    category: Category | None = None,
) -> Plugin | None:
    """Return the loaded plugin named ``name``, optionally filtered by category.

    Returns ``None`` when the plugin is absent or (with
    ``category`` set) belongs to a different category. Used by the
    provider-instance endpoints to validate that a caller-supplied
    ``plugin`` field refers to a currently-loaded ``llm-provider``.
    """
    for plugin in registry.loaded_plugins(category):
        if plugin.name == name:
            return plugin
    return None


def _build_instance_row(
    instance: InstanceRow,
    registry: PluginRegistry,
    active_id: str | None,
    *,
    show_secrets: bool,
) -> dict[str, Any]:
    """Assemble the JSON row returned by the instance endpoints.

    Secrets in ``instance.config`` mask by suffix unless
    ``show_secrets`` is true. ``config_schema`` is pulled from the
    backing plugin when it is currently loaded; a provider whose
    plugin is uninstalled simply has ``config_schema = None``.
    """
    plugin = _plugin_by_name(registry, instance.plugin, category=Category.LLM_PROVIDER)
    schema = _plugin_config_schema(plugin) if plugin is not None else None
    config_out: dict[str, Any] = {}
    for field, value in instance.config.items():
        if show_secrets or not is_secret_key(field):
            config_out[field] = value
        else:
            config_out[field] = mask_value(value)
    label = instance.label or f"{instance.plugin} ({instance.id})"
    return {
        "id": instance.id,
        "plugin": instance.plugin,
        "label": label,
        "active": instance.id == active_id,
        "config": config_out,
        "config_schema": schema,
    }


def _collect_instance_rows(
    registry: PluginRegistry,
    config_store: ConfigStore,
    *,
    show_secrets: bool,
) -> list[dict[str, Any]]:
    """Build the bare-array response served by ``GET /api/llm-providers``.

    Rows are sorted by instance id (the :class:`ProvidersView` order)
    so the UI can render a stable list across refreshes.
    """
    view = ProvidersView(config_store)
    active = view.active_id
    return [_build_instance_row(inst, registry, active, show_secrets=show_secrets) for inst in view.list_instances()]


def _lookup_instance_or_404(store: ConfigStore, instance_id: str) -> InstanceRow:
    """Return the instance row or raise ``HTTPException(404)``."""
    view = ProvidersView(store)
    row = view.get_instance(instance_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"llm-provider instance not found: {instance_id}")
    return row


async def _write_instance_fields(
    store: ConfigStore,
    instance_id: str,
    *,
    plugin: str | None,
    label: str | None,
    config: dict[str, Any] | None,
) -> None:
    """Persist the supplied fields under ``providers.<instance_id>.*``.

    Best-effort across multiple ``ConfigStore.set`` calls: the store
    does not expose a batch write today. If a mid-way failure occurs
    the instance may be partially materialized — operators clean up
    via ``yaya config unset providers.<id>.*``, and callers should
    surface that cleanup instruction in any 4xx/5xx they emit.
    Documented in ``specs/plugin-web-instance-crud.spec``.
    """
    prefix = f"{PROVIDERS_PREFIX}{instance_id}."
    if plugin is not None:
        await store.set(f"{prefix}plugin", plugin)
    if label is not None:
        await store.set(f"{prefix}label", label)
    if config is not None:
        for field, value in config.items():
            if not field or "." in field:
                # A dotted field would corrupt the providers.<id>.<field>
                # grouping the same way a dotted id would. Reject early
                # with 400 so the operator sees the violation.
                raise HTTPException(
                    status_code=400,
                    detail=f"config field {field!r} may not be empty or contain dots",
                )
            await store.set(f"{prefix}{field}", value)


async def _delete_instance_keys(store: ConfigStore, instance_id: str) -> None:
    """Remove every ``providers.<instance_id>.*`` key from the store."""
    prefix = f"{PROVIDERS_PREFIX}{instance_id}."
    rows = await store.list_prefix(prefix)
    for key in rows:
        await store.unset(key)


def _generate_instance_id(plugin: str, store: ConfigStore) -> str:
    """Return a fresh ``f"{plugin}-{uuid8}"`` id not already in the store.

    The chance of a collision is vanishing (8 hex chars = 2**32) but
    retrying once on the off chance keeps the handler idempotent.
    """
    view = ProvidersView(store)
    existing = {inst.id for inst in view.list_instances()}
    for _ in range(4):
        candidate = f"{plugin}-{uuid.uuid4().hex[:8]}"
        # Plugin names may contain underscores; the id validator
        # rejects underscores so we normalise here.
        candidate = candidate.replace("_", "-")
        if candidate in existing:
            continue
        if is_valid_instance_id(candidate):
            return candidate
    # Extremely unlikely — fall back to a fresh uuid hex payload that
    # always matches the validator (hex + 32 chars).
    return f"instance-{uuid.uuid4().hex[:12]}"


async def _create_instance_handler(
    body: _ProviderCreateBody,
    registry: PluginRegistry,
    store: ConfigStore,
) -> JSONResponse:
    """Materialise a new instance and return the built row."""
    plugin = _plugin_by_name(registry, body.plugin, category=Category.LLM_PROVIDER)
    if plugin is None:
        raise HTTPException(
            status_code=400,
            detail=f"{body.plugin!r} is not a loaded llm-provider plugin",
        )
    instance_id = body.id if body.id is not None else _generate_instance_id(body.plugin, store)
    if not is_valid_instance_id(instance_id):
        raise HTTPException(
            status_code=400,
            detail=(
                f"invalid instance id {instance_id!r}: must be 3-64 lowercase alphanumeric "
                "characters / dashes, starting and ending with alphanumeric, no dots"
            ),
        )
    view = ProvidersView(store)
    # TOCTOU: two concurrent POSTs with the same id can both see None +
    # both write. SQLite serializes writes; last-writer wins. Acceptable
    # for 127.0.0.1-only surface through 1.0. Compare-and-swap would
    # need a ConfigStore primitive.
    if view.get_instance(instance_id) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"llm-provider instance already exists: {instance_id}",
        )
    label = body.label if body.label is not None else f"{body.plugin} ({instance_id})"
    try:
        await _write_instance_fields(
            store,
            instance_id,
            plugin=body.plugin,
            label=label,
            config=body.config,
        )
    except HTTPException:
        raise
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{exc}; a partial instance may have been written — "
                f"run `yaya config unset providers.{instance_id}.*` to clean up"
            ),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"failed to write instance {instance_id!r}: {exc}; "
                f"a partial instance may have been written — "
                f"run `yaya config unset providers.{instance_id}.*` to clean up"
            ),
        ) from exc
    refreshed = _lookup_instance_or_404(store, instance_id)
    row = _build_instance_row(refreshed, registry, ProvidersView(store).active_id, show_secrets=False)
    return JSONResponse(row, status_code=201)


async def _patch_instance_handler(
    instance_id: str,
    body: _ProviderPatchBody,
    registry: PluginRegistry,
    store: ConfigStore,
) -> JSONResponse:
    """Merge ``label`` / ``config`` updates into an existing instance."""
    _lookup_instance_or_404(store, instance_id)
    try:
        await _write_instance_fields(
            store,
            instance_id,
            plugin=None,
            label=body.label,
            config=body.config,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    refreshed = _lookup_instance_or_404(store, instance_id)
    row = _build_instance_row(refreshed, registry, ProvidersView(store).active_id, show_secrets=False)
    return JSONResponse(row)


async def _delete_instance_handler(
    instance_id: str,
    store: ConfigStore,
) -> JSONResponse:
    """Remove an instance, blocking when it would leave the kernel broken.

    Returns ``204`` on success. 409s cover the two "operator trap"
    cases: deleting the active instance (would break the strategy
    loop) and deleting the last instance of a given plugin (would
    strand the operator's only credentials for that plugin — force an
    explicit add-then-remove).
    """
    row = _lookup_instance_or_404(store, instance_id)
    view = ProvidersView(store)
    if view.active_id == instance_id:
        raise HTTPException(
            status_code=409,
            detail="switch active provider before deleting this one",
        )
    siblings = [inst for inst in view.instances_for_plugin(row.plugin) if inst.id != instance_id]
    if not siblings:
        raise HTTPException(
            status_code=409,
            detail=f"this is the only instance of {row.plugin}; add another before deleting",
        )
    await _delete_instance_keys(store, instance_id)
    return JSONResponse(None, status_code=204)


def _register_provider_routes(
    router: APIRouter,
    registry: PluginRegistry | None,
    config_store: ConfigStore | None,
    bus: EventBus | None,
) -> None:
    """Attach the ``/api/llm-providers*`` endpoints to ``router``.

    Post D4c the endpoints operate on *instances* in the
    ``providers.<id>.*`` namespace, not on plugin rows. The shape is:

    * ``GET /api/llm-providers`` — list bare array of instances;
    * ``POST /api/llm-providers`` — create instance;
    * ``PATCH /api/llm-providers/<id>`` — partial merge of label / config;
    * ``DELETE /api/llm-providers/<id>`` — remove instance (with
      safety 409s);
    * ``PATCH /api/llm-providers/active`` — switch active instance id;
    * ``POST /api/llm-providers/<id>/test`` — connection probe.
    """

    @router.get("/api/llm-providers")
    async def _providers_list(show: int = 0) -> JSONResponse:
        """List every configured provider instance.

        Returns a bare JSON array. Secrets in each instance's
        ``config`` are masked unless ``?show=1`` is set — matches the
        masking posture of ``GET /api/config``.
        """
        reg = cast("PluginRegistry", _require(registry, "plugin registry"))
        store = cast("ConfigStore", _require(config_store, "config store"))
        rows = _collect_instance_rows(reg, store, show_secrets=bool(show))
        return JSONResponse(rows)

    @router.post("/api/llm-providers")
    async def _providers_create(body: _ProviderCreateBody) -> JSONResponse:
        """Create a new instance; returns ``201`` + the created row."""
        reg = cast("PluginRegistry", _require(registry, "plugin registry"))
        store = cast("ConfigStore", _require(config_store, "config store"))
        return await _create_instance_handler(body, reg, store)

    @router.patch("/api/llm-providers/active")
    async def _providers_set_active(body: _ProviderActiveBody) -> JSONResponse:
        """Switch the active instance and return the refreshed list.

        Validates the target id resolves to an existing instance and
        that the instance's backing plugin is currently loaded — a
        dangling plugin reference would cause the strategy loop to
        dispatch into the void.
        """
        reg = cast("PluginRegistry", _require(registry, "plugin registry"))
        store = cast("ConfigStore", _require(config_store, "config store"))
        instance = _lookup_instance_or_404(store, body.name)
        if _plugin_by_name(reg, instance.plugin, category=Category.LLM_PROVIDER) is None:
            raise HTTPException(
                status_code=400,
                detail=f"instance {body.name!r} backing plugin {instance.plugin!r} is not a loaded llm-provider",
            )
        await store.set(_PROVIDER_CONFIG_KEY, body.name)
        rows = _collect_instance_rows(reg, store, show_secrets=False)
        return JSONResponse(rows)

    @router.patch("/api/llm-providers/{instance_id}")
    async def _providers_patch(instance_id: str, body: _ProviderPatchBody) -> JSONResponse:
        """Partial update of an instance's label / config."""
        reg = cast("PluginRegistry", _require(registry, "plugin registry"))
        store = cast("ConfigStore", _require(config_store, "config store"))
        return await _patch_instance_handler(instance_id, body, reg, store)

    @router.delete("/api/llm-providers/{instance_id}")
    async def _providers_delete(instance_id: str) -> JSONResponse:
        """Remove an instance. ``204`` on success; 409 when unsafe."""
        _require(registry, "plugin registry")
        store = cast("ConfigStore", _require(config_store, "config store"))
        return await _delete_instance_handler(instance_id, store)

    @router.post("/api/llm-providers/{instance_id}/test")
    async def _providers_test(instance_id: str) -> JSONResponse:
        """Fire a one-shot prompt for a configured instance."""
        reg = cast("PluginRegistry", _require(registry, "plugin registry"))
        bus_obj = cast("EventBus", _require(bus, "event bus"))
        store = cast("ConfigStore", _require(config_store, "config store"))
        instance = _lookup_instance_or_404(store, instance_id)
        if _plugin_by_name(reg, instance.plugin, category=Category.LLM_PROVIDER) is None:
            raise HTTPException(
                status_code=400,
                detail=f"instance {instance_id!r} backing plugin {instance.plugin!r} is not a loaded llm-provider",
            )
        return await _run_test_prompt(instance_id, bus_obj)


async def _run_test_prompt(provider_id: str, bus_obj: EventBus) -> JSONResponse:
    """Fire one ``llm.call.request`` tagged with ``provider_id`` and wait for a reply.

    The endpoint publishes ``llm.call.request`` with a fresh request
    id, subscribes to both ``llm.call.response`` and
    ``llm.call.error``, and waits up to :data:`_TEST_PROMPT_TIMEOUT_S`
    seconds for a reply tagged with that request id. Routes on a
    ``_bridge:web-api-test:<uuid>`` session (lesson #2) so the probe
    never interleaves with a real conversation's tape.
    """
    session_id = f"_bridge:web-api-test:{uuid.uuid4().hex[:8]}"
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
