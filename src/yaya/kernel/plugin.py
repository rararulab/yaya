"""Plugin ABI: :class:`Category`, :class:`Plugin` Protocol, :class:`KernelContext`.

The ABI is the contract every plugin — bundled or third-party — binds to.
``docs/dev/plugin-protocol.md`` is the authoritative prose description;
this module is its Python surface. Kernel layering: no imports from
``cli``, ``plugins``, or ``core``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from yaya.kernel.events import Event, new_event

if TYPE_CHECKING:  # pragma: no cover - type-only import, breaks an import cycle.
    from yaya.kernel.bus import EventBus
    from yaya.kernel.config_store import ConfigStore
    from yaya.kernel.providers import ProvidersView
    from yaya.kernel.registry import PluginRegistry
    from yaya.kernel.session import Session, SessionStore


class Category(StrEnum):
    """Closed set of plugin categories at 1.0.

    A plugin declares exactly one category. Multi-category plugins ship as
    multiple packages — see ``docs/dev/plugin-protocol.md`` for the routing
    rules each category inherits.
    """

    ADAPTER = "adapter"
    TOOL = "tool"
    LLM_PROVIDER = "llm-provider"
    STRATEGY = "strategy"
    MEMORY = "memory"
    SKILL = "skill"


HealthStatus = Literal["ok", "degraded", "failed"]
"""Tri-valued plugin health grade.

Semantics:
    * ``ok`` — the plugin is fully configured and its owned resources
      are reachable.
    * ``degraded`` — the plugin is loaded but some configurable surface
      is missing (no API key, no servers configured). The kernel can
      still run; the plugin may respond "no subscriber" on requests.
    * ``failed`` — a check raised, a required resource is missing, or
      a self-diagnosis detected a broken state. ``yaya doctor`` exits
      non-zero when any plugin reports this.
"""


class HealthCheck(BaseModel):
    """One named sub-check inside a :class:`HealthReport`.

    Plugins with multiple orthogonal surfaces (e.g. ``web`` checks both
    the static bundle path and the HTTP keepalive endpoint) break the
    per-surface result into entries so ``yaya doctor -v`` can show
    the breakdown. The rollup is the containing :class:`HealthReport`'s
    ``status`` — a plugin with one ``failed`` sub-check is itself
    ``failed``; a mix of ``ok`` and ``degraded`` is ``degraded``.
    """

    name: str = Field(description="Sub-check name, stable across runs.")
    status: HealthStatus = Field(description="Per-sub-check grade.")
    message: str = Field(default="", description="Short human-facing diagnostic.")


class HealthReport(BaseModel):
    """A plugin's self-diagnosis, consumed by ``yaya doctor``.

    The ``summary`` field is the one-liner ``yaya doctor`` renders in
    the table row; ``details`` is the optional breakdown surfaced only
    under ``-v`` / ``--json``. Missing ``health_check()`` on a plugin
    is NOT an error — the doctor command synthesises a default
    ``ok`` report with summary ``"no checks registered"``.
    """

    status: HealthStatus
    summary: str = Field(description="One-liner for the CLI table row.")
    details: list[HealthCheck] = Field(default_factory=list[HealthCheck])


@runtime_checkable
class Plugin(Protocol):
    """Runtime-checkable interface every plugin module's ``plugin`` object implements.

    Attributes:
        name: Globally unique, kebab-case.
        version: Semver string.
        category: One of :class:`Category`.
        requires: Names of other plugins this one depends on (load order hint).

    Lifecycle methods run inside the asyncio event loop owned by the kernel.
    A raised exception or a >30s hang surfaces a synthetic ``plugin.error``
    event; it does not crash the kernel.
    """

    name: str
    version: str
    category: Category
    requires: list[str]

    def subscriptions(self) -> list[str]:
        """Return the public or extension event kinds this plugin wants.

        The kernel uses this list to route events. Filtering beyond kind
        (e.g. by ``session_id``) is the plugin's responsibility.
        """
        ...

    async def on_load(self, ctx: KernelContext) -> None:
        """Run once after registration, before any event is delivered."""
        ...

    async def on_event(self, ev: Event, ctx: KernelContext) -> None:
        """Handle one event. Raise to surface a ``plugin.error``."""
        ...

    async def on_unload(self, ctx: KernelContext) -> None:
        """Run on hot-reload or kernel shutdown. Must be idempotent."""
        ...

    # NOTE: ``health_check`` is intentionally **not** declared on the
    # Protocol. Adding it here would break ``runtime_checkable``
    # ``isinstance(obj, Plugin)`` for every plugin that doesn't
    # override it. The doctor command uses ``hasattr(plug,
    # "health_check")`` and synthesises a default
    # :class:`HealthReport` when absent. Plugins that do implement
    # it MUST match the signature::
    #
    #     async def health_check(self, ctx: KernelContext) -> HealthReport: ...
    #
    # Must return quickly (<500 ms); never fire a real LLM / network
    # call. See :class:`HealthReport` and ``docs/dev/plugin-protocol.md``
    # §"Health checks" for the full contract.


# Handler signature used by the registry / bus when subscribing on behalf of
# a plugin. Kept here so it's importable without pulling in the bus.
EventHandler = Callable[[Event], Awaitable[None]]


class KernelContext:
    """Per-plugin view of the kernel.

    Each plugin receives its own :class:`KernelContext` in ``on_load``,
    ``on_event``, and ``on_unload``. The context stamps the plugin's name
    onto any event emitted through :meth:`emit`, so plugins cannot forge
    ``source`` for another plugin or the kernel.
    """

    def __init__(
        self,
        *,
        bus: EventBus,
        logger: Any,
        config: Mapping[str, object],
        state_dir: Path,
        plugin_name: str,
        session: Session | None = None,
        registry: PluginRegistry | None = None,
        config_store: ConfigStore | None = None,
        session_store: SessionStore | None = None,
    ) -> None:
        """Bind the context to a specific plugin.

        Args:
            bus: The kernel's running event bus.
            logger: Pre-scoped logger for this plugin (name already bound).
                At runtime this is a ``loguru.Logger`` returned by
                :func:`yaya.kernel.logging.get_plugin_logger`. Typed as
                :data:`Any` so loguru does not leak into the Plugin ABI —
                plugins call standard ``info`` / ``warning`` / ``error``
                methods which loguru exposes API-compatibly with stdlib.
            config: Read-only plugin configuration.
            state_dir: Writable directory under ``<XDG_DATA_HOME>/yaya/plugins/<name>/``.
            plugin_name: The owning plugin's ``name``; used as ``source`` on emit.
        """
        self._bus = bus
        self._logger = logger
        self._config = config
        self._state_dir = state_dir
        self._plugin_name = plugin_name
        self._session = session
        self._registry = registry
        self._config_store = config_store
        self._session_store = session_store

    @property
    def bus(self) -> EventBus:
        """Underlying :class:`~yaya.kernel.bus.EventBus`.

        Surfaced read-only so kernel-side subsystems that live outside
        the plugin (the approval runtime, future dispatcher
        helpers) can look themselves up by bus identity from code
        executing inside a plugin's context without reaching into a
        private attribute. Plugins should still emit via
        :meth:`emit`; direct bus access is intended for kernel code
        invoked *from* a plugin path (e.g. the default
        :meth:`~yaya.kernel.tool.Tool.pre_approve`).
        """
        return self._bus

    @property
    def logger(self) -> Any:
        """Plugin-scoped logger.

        Returns the loguru ``Logger`` bound with ``plugin=<name>`` —
        typed as :data:`Any` per the constructor's rationale.
        """
        return self._logger

    @property
    def config(self) -> Mapping[str, object]:
        """Read-only plugin configuration."""
        return self._config

    @property
    def state_dir(self) -> Path:
        """Writable per-plugin state directory."""
        return self._state_dir

    @property
    def session(self) -> Session | None:
        """The active :class:`~yaya.kernel.session.Session`, if any.

        Exposed read-only so plugins can call ``ctx.session.tape`` or
        ``ctx.session.info()`` from inside ``on_load`` / ``on_event``
        without reaching into the registry's private state. Plugins
        SHOULD still drive writes via :meth:`emit` — direct
        ``append_*`` calls bypass the bus and are therefore invisible
        to other subscribers. This property is the same kind of
        kernel-side escape hatch as :attr:`bus`, and carries the same
        caveat.

        Returns ``None`` when the kernel was booted without a
        :class:`~yaya.kernel.session.SessionStore` (e.g. the
        ``yaya plugin list`` transient stack) — plugin code must
        handle that gracefully.
        """
        return self._session

    @property
    def registry(self) -> PluginRegistry | None:
        """The owning :class:`~yaya.kernel.registry.PluginRegistry`, if any.

        Kernel-side escape hatch used by the bundled ``web`` adapter
        to expose ``install`` / ``remove`` / ``loaded_plugins`` over
        the local HTTP API. Third-party plugins SHOULD NOT rely on
        this surface — cross-plugin orchestration is expected to go
        through the bus, and a future hardening pass may gate this
        behind an explicit capability flag.

        Returns ``None`` when the context was built outside a running
        registry (tests, ``yaya plugin list`` transient stack).
        """
        return self._registry

    @property
    def providers(self) -> ProvidersView | None:
        """Read-only grouped view over ``providers.<id>.*`` instances.

        Returns a fresh :class:`~yaya.kernel.providers.ProvidersView`
        bound to the live :attr:`config_store` on each access — the
        view is cheap (no caching of its own) and re-parses keys each
        call, so a subsequent :meth:`~yaya.kernel.config_store.ConfigStore.set`
        is visible without cache invalidation. Returns ``None`` when
        the context was built without a store (tests that inject
        :class:`KernelConfig` directly).

        D4a (#116) landed the namespace + bootstrap; D4b flips bundled
        ``llm-provider`` plugins to read via this surface.
        """
        if self._config_store is None:
            return None
        # Lazy import to keep the plugin module free of the
        # config-store dependency at import time — the ABI should
        # load cleanly in contexts where the store is optional.
        from yaya.kernel.providers import ProvidersView

        return ProvidersView(self._config_store)

    @property
    def config_store(self) -> ConfigStore | None:
        """The live :class:`~yaya.kernel.config_store.ConfigStore`, if any.

        Same escape-hatch caveat as :attr:`registry`: the bundled
        ``web`` adapter consults this surface to implement
        ``GET/PATCH/DELETE /api/config``. Normal plugin config reads
        still go through :attr:`config`, which already reflects live
        cache updates.

        Returns ``None`` when the registry was started without a
        store (tests injecting a ``KernelConfig`` directly).
        """
        return self._config_store

    @property
    def session_store(self) -> SessionStore | None:
        """The live :class:`~yaya.kernel.session.SessionStore`, if any.

        Same escape-hatch caveat as :attr:`registry`: the bundled
        ``web`` adapter consults this surface to implement
        ``GET /api/sessions`` so the sidebar can hydrate chat history
        across restarts. Normal plugin code should emit tape writes
        through the bus (the kernel's persister subscribes there).

        Returns ``None`` when the kernel was booted without a
        session store (tests, ``yaya plugin list`` transient stack).
        """
        return self._session_store

    async def emit(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        session_id: str,
    ) -> None:
        """Publish an event on behalf of this plugin.

        Validates ``kind`` against the closed catalog (or accepts any
        ``x.<plugin>.<kind>`` extension), stamps ``source`` with the owning
        plugin's name, then hands the event to the bus.

        Args:
            kind: Public or extension event kind.
            payload: Kind-specific dict per ``docs/dev/plugin-protocol.md``.
            session_id: Routing/ordering key.

        Raises:
            ValueError: If ``kind`` is an unknown public kind.
        """
        event = new_event(
            kind,
            payload,
            session_id=session_id,
            source=self._plugin_name,
        )
        await self._bus.publish(event)


__all__ = [
    "Category",
    "EventHandler",
    "HealthCheck",
    "HealthReport",
    "HealthStatus",
    "KernelContext",
    "Plugin",
]
