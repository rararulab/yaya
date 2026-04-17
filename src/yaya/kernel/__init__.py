"""Public surface of the yaya kernel.

The kernel owns the event bus, the plugin ABI, and the closed public event
catalog. Everything else — adapters, LLM providers, tools, strategies,
memory, skills — is a plugin. See ``docs/dev/plugin-protocol.md`` for the
authoritative contract.
"""

from __future__ import annotations

from yaya.kernel.bus import DEFAULT_HANDLER_TIMEOUT_S, EventBus, EventHandler, Subscription
from yaya.kernel.events import (
    PUBLIC_EVENT_KINDS,
    Event,
    PublicEventKind,
    new_event,
)
from yaya.kernel.loop import AgentLoop, LoopConfig
from yaya.kernel.plugin import Category, KernelContext, Plugin

__all__ = [
    "DEFAULT_HANDLER_TIMEOUT_S",
    "PUBLIC_EVENT_KINDS",
    "AgentLoop",
    "Category",
    "Event",
    "EventBus",
    "EventHandler",
    "KernelContext",
    "LoopConfig",
    "Plugin",
    "PublicEventKind",
    "Subscription",
    "new_event",
]
