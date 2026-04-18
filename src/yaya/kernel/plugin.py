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
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from yaya.kernel.events import Event, new_event

if TYPE_CHECKING:  # pragma: no cover - type-only import, breaks an import cycle.
    from yaya.kernel.bus import EventBus


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


__all__ = ["Category", "EventHandler", "KernelContext", "Plugin"]
