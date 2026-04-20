"""Echo LLM provider — deterministic, zero-config, dev-only.

The plugin is **instance-scoped**: after #123 (D4b) it maintains a
set of *instance ids* whose ``providers.<id>.plugin`` meta equals
``llm-echo`` and responds to ``llm.call.request`` only when
``ev.payload["provider"]`` names one of those ids. For historical
callers that still ship ``provider == "echo"`` (the literal plugin
name) the legacy id is accepted as long as a matching instance is
configured — the D4a bootstrap seeds exactly that row, so a fresh
``yaya serve`` round-trips without any API key.

Token usage is reported as zero. The plugin is bundled so a fresh
``yaya serve`` round-trips the kernel without any API key — closes
the 0.1 onboarding gap (see ``GOAL.md`` §Milestones 0.1).

Layering: imports only from ``yaya.kernel``. No third-party
dependencies — stdlib only, per ``AGENT.md`` §4.

Routing parity with the bundled ``llm_openai`` plugin: subscribes
to ``llm.call.request`` and ``config.updated``; non-matching
providers return silently so sibling LLM plugins coexist on the
same subscription. Every emitted ``llm.call.response`` echoes
``request_id`` for the agent loop's ``_RequestTracker`` correlation
(lesson #15 in ``docs/wiki/lessons-learned.md``).
"""

from __future__ import annotations

from typing import Any, ClassVar, cast

from yaya.kernel.events import Event
from yaya.kernel.plugin import Category, KernelContext

_NAME = "llm-echo"
_VERSION = "0.1.0"
_PROVIDERS_PREFIX = "providers."
_NO_INPUT = "(echo) (no input)"


class EchoLLM:
    """Bundled echo LLM-provider plugin.

    Attributes:
        name: Plugin name (kebab-case).
        version: Semver.
        category: :class:`Category.LLM_PROVIDER`.
    """

    name: str = _NAME
    version: str = _VERSION
    category: Category = Category.LLM_PROVIDER
    requires: ClassVar[list[str]] = []

    def __init__(self) -> None:
        # Set of instance ids owned by this plugin. Refreshed from
        # :attr:`ctx.providers` in :meth:`on_load` and on every
        # ``config.updated`` event that touches the ``providers.*``
        # namespace.
        self._active_instances: set[str] = set()

    def subscriptions(self) -> list[str]:
        """Subscribe to llm requests and live-config updates.

        ``config.updated`` drives the hot-reload path: adding,
        removing, or re-pointing a ``providers.<id>.*`` row whose
        plugin meta equals :data:`_NAME` shows up in
        :attr:`_active_instances` on the next turn — no restart.
        """
        return ["llm.call.request", "config.updated"]

    async def on_load(self, ctx: KernelContext) -> None:
        """Seed :attr:`_active_instances` from every owned row.

        Reads :meth:`ctx.providers.instances_for_plugin` and snapshots
        the current set. ``ctx.providers == None`` (config store
        absent — test / transient stacks) leaves the set empty; tests
        that drive this plugin directly can populate
        :attr:`_active_instances` manually.
        """
        ctx.logger.info("llm-echo ready (no API key required)")
        self._refresh(ctx)

    async def on_event(self, ev: Event, ctx: KernelContext) -> None:
        """Route ``llm.call.request`` for any owned instance id.

        Non-matching providers are ignored so sibling LLM plugins own
        their own traffic on the shared subscription.
        ``config.updated`` under ``providers.*`` refreshes the owned
        set; other prefixes are silently skipped.
        """
        if ev.kind == "config.updated":
            self._maybe_hot_reload(ev, ctx)
            return
        if ev.kind != "llm.call.request":
            return
        provider_id = ev.payload.get("provider")
        if not isinstance(provider_id, str) or provider_id not in self._active_instances:
            return

        text = _build_echo(ev.payload)
        await ctx.emit(
            "llm.call.response",
            {
                "text": text,
                "tool_calls": [],
                "usage": {"input_tokens": 0, "output_tokens": 0},
                "request_id": ev.id,
            },
            session_id=ev.session_id,
        )

    async def on_unload(self, ctx: KernelContext) -> None:
        """Clear the owned-instance set; the plugin holds no other resources."""
        self._active_instances.clear()
        del ctx  # unused — kept for Plugin protocol conformance.

    # -- internals ------------------------------------------------------------

    def _refresh(self, ctx: KernelContext) -> None:
        """Re-snapshot the owned-instance set from :attr:`ctx.providers`."""
        providers = ctx.providers
        if providers is None:
            self._active_instances = set()
            return
        self._active_instances = {inst.id for inst in providers.instances_for_plugin(_NAME)}

    def _maybe_hot_reload(self, ev: Event, ctx: KernelContext) -> None:
        """Refresh on any ``config.updated`` under the ``providers.*`` tree.

        The set is cheap to rebuild (a single
        :meth:`ProvidersView.instances_for_plugin` call, which in turn
        is an in-memory dict scan) so we skip fine-grained diffing —
        simpler, and correct across every mutation shape (add, remove,
        re-point ``plugin`` meta).
        """
        key_raw = ev.payload.get("key")
        if not isinstance(key_raw, str) or not key_raw.startswith(_PROVIDERS_PREFIX):
            return
        self._refresh(ctx)


def _build_echo(payload: dict[str, Any]) -> str:
    """Compose the echo response from a ``llm.call.request`` payload.

    Pulls the most recent ``role == "user"`` message's ``content``
    and prefixes it with ``"(echo) "``. When no user message is
    present (or its content is empty) the response is the
    deterministic placeholder defined in :data:`_NO_INPUT`.

    Args:
        payload: The ``llm.call.request`` payload dict.

    Returns:
        The plain-text response body to surface in
        ``llm.call.response.text``.
    """
    raw_messages: Any = payload.get("messages") or []
    if not isinstance(raw_messages, list):
        return _NO_INPUT
    last_user = ""
    # ``raw_messages`` is ``Any`` from the dict; narrowing only filters
    # the non-list branch and per-element ``isinstance`` is the only
    # typing barrier in the loop.
    for msg in reversed(raw_messages):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        if not isinstance(msg, dict):
            continue
        msg_dict = cast("dict[str, Any]", msg)
        if msg_dict.get("role") != "user":
            continue
        content = msg_dict.get("content")
        if isinstance(content, str) and content:
            last_user = content
        break
    if not last_user:
        return _NO_INPUT
    return f"(echo) {last_user}"


__all__ = ["EchoLLM"]
