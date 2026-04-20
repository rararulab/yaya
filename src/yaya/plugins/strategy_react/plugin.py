"""ReAct strategy plugin implementation.

The strategy inspects the :class:`yaya.kernel.events.AgentLoopState`
snapshot the loop hands it and picks the next step. It carries no
per-session state of its own — the loop's ``state.messages`` +
``state.last_tool_result`` is the authoritative context, so repeated
turns remain deterministic.

After #123 (D4b) the ``(provider, model)`` pair is resolved per
decision from :attr:`ctx.providers`: the active instance id comes
from ``ctx.providers.active_id`` (the ``provider`` kernel-level
config key), and the model is read live from that instance's
``config["model"]`` field. Switching ``provider`` to a different
instance id at runtime takes effect on the next
``strategy.decide.request`` without a kernel restart. When
``ctx.providers`` is absent (tests that skip the config store) the
plugin falls back to an env sniff over ``OPENAI_API_KEY``.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, ClassVar, cast

from yaya.kernel.events import Event
from yaya.kernel.plugin import Category, KernelContext

if TYPE_CHECKING:  # pragma: no cover - type-only import.
    from yaya.kernel.providers import ProvidersView

# Fallback provider ids used only when ``ctx.providers`` is absent.
# They mirror the seeded instance ids the D4a bootstrap writes
# (``providers.llm-openai.plugin = llm-openai`` etc.) so the fallback
# name matches what a real kernel would resolve to.
_FALLBACK_OPENAI_PROVIDER = "llm-openai"
_FALLBACK_ECHO_PROVIDER = "llm-echo"
_DEFAULT_MODEL = "gpt-4o-mini"
# The bundled echo provider needs no model id — pick a stable
# placeholder so the ``llm.call.request`` payload type-checks.
_FALLBACK_ECHO_MODEL = "echo"

_NAME = "strategy-react"
_VERSION = "0.1.0"


class ReActStrategy:
    """Bundled ReAct strategy plugin.

    Implements :class:`yaya.kernel.plugin.Plugin` via duck typing — the
    protocol is ``@runtime_checkable`` so the registry's ``isinstance``
    guard accepts any object with the required attributes and methods.

    Thread model: single asyncio event loop. The plugin keeps no
    mutable state across events; every decision is computed from the
    incoming ``state`` snapshot.
    """

    name: str = _NAME
    version: str = _VERSION
    category: Category = Category.STRATEGY
    requires: ClassVar[list[str]] = []

    def subscriptions(self) -> list[str]:
        """Only ``strategy.decide.request`` — the sole request kind for this category."""
        return ["strategy.decide.request"]

    async def on_load(self, ctx: KernelContext) -> None:
        """Log the effective configuration on boot."""
        provider, model = self._provider_and_model(ctx)
        ctx.logger.debug(
            "strategy-react loaded (provider=%s model=%s)",
            provider,
            model,
        )

    async def on_event(self, ev: Event, ctx: KernelContext) -> None:
        """Decide the next step for the turn described by ``ev.payload.state``.

        The loop always publishes ``strategy.decide.request`` with a
        ``state`` key (see ``yaya.kernel.loop.AgentLoop._decide``); a
        request missing that key is a protocol violation and raises so
        the registry's failure accounting surfaces a ``plugin.error``.
        """
        if ev.kind != "strategy.decide.request":
            return
        raw_state = ev.payload.get("state")
        if not isinstance(raw_state, dict):
            # Protocol violation: the loop always publishes with a 'state' key.
            raise ValueError("strategy.decide.request missing 'state' payload")  # noqa: TRY004

        provider, model = self._provider_and_model(ctx)
        decision = _decide(cast("dict[str, Any]", raw_state), provider=provider, model=model)
        decision["request_id"] = ev.id
        await ctx.emit(
            "strategy.decide.response",
            decision,
            session_id=ev.session_id,
        )

    async def on_unload(self, ctx: KernelContext) -> None:
        """No-op — the strategy holds no resources."""

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _provider_and_model(ctx: KernelContext) -> tuple[str, str]:
        """Return the effective ``(provider, model)`` pair.

        Resolution order:

        1. ``ctx.providers.active_id`` (the kernel-level ``provider``
           key) → use that instance id and its ``config["model"]``.
        2. When no active instance but at least one instance is
           configured → first instance id, its ``config["model"]``.
        3. No providers view at all (tests without a config store):
           env sniff — ``OPENAI_API_KEY`` set → ``(llm-openai,
           gpt-4o-mini)``, else ``(llm-echo, echo)``.
        """
        providers = ctx.providers
        if providers is not None:
            resolved = ReActStrategy._resolve_from_providers(providers)
            if resolved is not None:
                return resolved
        # Fallback: no config store or no configured instance. Pick a
        # seeded name matching what D4a bootstrap would stamp.
        if os.environ.get("OPENAI_API_KEY"):
            return _FALLBACK_OPENAI_PROVIDER, _DEFAULT_MODEL
        return _FALLBACK_ECHO_PROVIDER, _FALLBACK_ECHO_MODEL

    @staticmethod
    def _resolve_from_providers(providers: ProvidersView) -> tuple[str, str] | None:
        """Resolve ``(provider, model)`` from a live :class:`ProvidersView`.

        Returns ``None`` when the view is empty — callers then fall
        through to the env-driven fallback so a test stack without a
        config store still lands on a deterministic pair.
        """
        active_id = providers.active_id
        instance = None
        if active_id is not None:
            instance = providers.get_instance(active_id)
        if instance is None:
            all_instances = providers.list_instances()
            if not all_instances:
                return None
            instance = all_instances[0]
        model_raw = instance.config.get("model")
        if isinstance(model_raw, str) and model_raw:
            model = model_raw
        elif instance.plugin == _FALLBACK_ECHO_PROVIDER:
            model = _FALLBACK_ECHO_MODEL
        else:
            model = _DEFAULT_MODEL
        return instance.id, model


def _decide(state: dict[str, Any], *, provider: str, model: str) -> dict[str, Any]:
    """Compute the next step from a loop state snapshot.

    Pure function so it is trivially unit-testable without a bus.

    Args:
        state: The ``AgentLoopState`` dict from ``strategy.decide.request``.
        provider: Configured LLM provider id (e.g. ``"openai"``).
        model: Configured model string.

    Returns:
        A decision payload (less ``request_id``, which the caller adds)
        per ``docs/dev/plugin-protocol.md``. ``next`` is one of
        ``"llm" | "tool" | "done"``. Memory steps are not emitted by
        this seed strategy — the loop supports them, but ReAct 0.1 does
        not use them.
    """
    messages_raw: list[Any] = list(state.get("messages") or [])
    messages: list[dict[str, Any]] = [cast("dict[str, Any]", m) for m in messages_raw if isinstance(m, dict)]

    last_tool_result = state.get("last_tool_result")

    # Find the most recent assistant message (if any).
    last_assistant: dict[str, Any] | None = None
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            last_assistant = msg
            break

    # A tool just ran → feed its result back into the LLM for another pass.
    if last_tool_result is not None and last_assistant is None:
        # Shouldn't happen (we always have an assistant turn before a
        # tool call), but defensively ask the LLM to interpret it.
        return {"next": "llm", "provider": provider, "model": model}

    # Assistant present + pending tool_calls → run the first one.
    if last_assistant is not None:
        tool_calls = last_assistant.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            first = cast("Any", tool_calls[0])
            if isinstance(first, dict):
                return {
                    "next": "tool",
                    "tool_call": cast("dict[str, Any]", first),
                }
        # Assistant finished and has nothing to run → done.
        if last_tool_result is None:
            return {"next": "done"}
        # Assistant just consumed a tool result → loop again via LLM.
        return {"next": "llm", "provider": provider, "model": model}

    # No assistant message yet → ask the LLM.
    return {"next": "llm", "provider": provider, "model": model}


__all__ = ["ReActStrategy"]
