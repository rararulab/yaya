"""Echo LLM provider — deterministic, zero-config, dev-only.

Every ``llm.call.request`` with ``provider == "echo"`` gets a
response body prefixed with ``(echo) `` followed by the last user
message in ``messages``. Token usage is reported as zero. The
plugin is bundled so a fresh ``yaya serve`` round-trips the kernel
without any API key — closes the 0.1 onboarding gap (see
``GOAL.md`` §Milestones 0.1).

Layering: imports only from ``yaya.kernel``. No third-party
dependencies — stdlib only, per ``AGENT.md`` §4.

Routing parity with the bundled ``llm_openai`` plugin: subscribes
only to ``llm.call.request``; non-matching providers return
silently so sibling LLM plugins coexist on the same subscription.
Every emitted ``llm.call.response`` echoes ``request_id`` for the
agent loop's ``_RequestTracker`` correlation (lesson #15 in
``docs/wiki/lessons-learned.md``).
"""

from __future__ import annotations

from typing import Any, ClassVar, cast

from yaya.kernel.events import Event
from yaya.kernel.plugin import Category, KernelContext

_NAME = "llm-echo"
_VERSION = "0.1.0"
_PROVIDER_ID = "echo"
_NO_INPUT = "(echo) (no input)"


class EchoLLM:
    """Bundled echo LLM-provider plugin.

    Attributes:
        name: Plugin name (kebab-case).
        version: Semver.
        category: :class:`Category.LLM_PROVIDER`.
        provider_id: The literal ``"echo"`` filter value matched on
            ``ev.payload["provider"]``.
    """

    name: str = _NAME
    version: str = _VERSION
    category: Category = Category.LLM_PROVIDER
    provider_id: str = _PROVIDER_ID
    requires: ClassVar[list[str]] = []

    def subscriptions(self) -> list[str]:
        """Only ``llm.call.request`` — the single request kind for this category."""
        return ["llm.call.request"]

    async def on_load(self, ctx: KernelContext) -> None:
        """Log readiness. The echo provider needs no configuration."""
        ctx.logger.info("llm-echo ready (no API key required)")

    async def on_event(self, ev: Event, ctx: KernelContext) -> None:
        """Route ``llm.call.request`` for ``provider == "echo"``.

        Non-matching providers are ignored so sibling LLM plugins
        own their own traffic on the shared subscription.
        """
        if ev.kind != "llm.call.request":
            return
        if ev.payload.get("provider") != _PROVIDER_ID:
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
        """No-op — the echo provider holds no resources."""


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
