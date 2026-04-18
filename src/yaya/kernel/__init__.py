"""Public surface of the yaya kernel.

The kernel owns the event bus, the plugin ABI, and the closed public event
catalog. Everything else — adapters, LLM providers, tools, strategies,
memory, skills — is a plugin. See ``docs/dev/plugin-protocol.md`` for the
authoritative contract.
"""

from __future__ import annotations

from yaya.kernel.bus import DEFAULT_HANDLER_TIMEOUT_S, EventBus, EventHandler, Subscription
from yaya.kernel.config import CONFIG_PATH, KernelConfig, default_config_path, load_config
from yaya.kernel.errors import (
    ConfigError,
    KernelError,
    PluginError,
    YayaError,
    YayaTimeoutError,
)
from yaya.kernel.events import (
    PUBLIC_EVENT_KINDS,
    Event,
    PublicEventKind,
    new_event,
)
from yaya.kernel.logging import configure_logging, get_plugin_logger
from yaya.kernel.loop import AgentLoop, LoopConfig
from yaya.kernel.plugin import Category, KernelContext, Plugin
from yaya.kernel.registry import PluginRegistry, PluginStatus, validate_install_source

__all__ = [
    "CONFIG_PATH",
    "DEFAULT_HANDLER_TIMEOUT_S",
    "PUBLIC_EVENT_KINDS",
    "AgentLoop",
    "Category",
    "ConfigError",
    "Event",
    "EventBus",
    "EventHandler",
    "KernelConfig",
    "KernelContext",
    "KernelError",
    "LoopConfig",
    "Plugin",
    "PluginError",
    "PluginRegistry",
    "PluginStatus",
    "PublicEventKind",
    "Subscription",
    "YayaError",
    "YayaTimeoutError",
    "configure_logging",
    "default_config_path",
    "get_plugin_logger",
    "load_config",
    "new_event",
    "validate_install_source",
]
