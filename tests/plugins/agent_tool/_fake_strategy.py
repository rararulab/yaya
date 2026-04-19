"""Fake strategy for agent_tool tests.

Mirrors the mcp_bridge ``_fake_server`` pattern: a tiny self-contained
harness that listens on the bus and provides scripted responses, so
tests do not need to boot the real bundled strategy / LLM / tool
plugins. The harness subscribes to ``user.message.received`` on the
bus and emits a terminal ``assistant.message.done`` after an
optional tool-call round-trip.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from yaya.kernel.bus import EventBus, Subscription
from yaya.kernel.events import Event, new_event


@dataclass
class FakeAgentLoop:
    """Stands in for :class:`yaya.kernel.loop.AgentLoop` in tests.

    Subscribes to ``user.message.received``. On each event:

    * When ``answers[text]`` is configured, emits
      ``assistant.message.done(content=answers[text])`` on the same
      session, keyed to complete the sub-agent's run.
    * When ``tool_before_done`` is set, emits a
      ``tool.call.request(name=<tool>, args={})`` first so tests can
      observe the sub-agent's tool traffic (and the allowlist filter
      can register a hit).
    * When ``never_done`` is True, swallows the event without a
      response so tests can exercise the timeout path.
    """

    bus: EventBus
    answers: dict[str, str] = field(default_factory=lambda: {})
    default_answer: str = "ok"
    tool_before_done: str | None = None
    never_done: bool = False
    _sub: Subscription | None = None
    seen_sessions: list[str] = field(default_factory=lambda: [])

    def start(self) -> None:
        """Subscribe to ``user.message.received``."""
        self._sub = self.bus.subscribe(
            "user.message.received",
            self._on_user_message,
            source="_fake_loop",
        )

    def stop(self) -> None:
        """Unsubscribe. Idempotent."""
        if self._sub is not None:
            self._sub.unsubscribe()
            self._sub = None

    async def _on_user_message(self, ev: Event) -> None:
        self.seen_sessions.append(ev.session_id)
        if self.never_done:
            return
        if self.tool_before_done is not None:
            req = new_event(
                "tool.call.request",
                {
                    "id": f"{ev.session_id}:tool",
                    "name": self.tool_before_done,
                    "args": {},
                },
                session_id=ev.session_id,
                source="_fake_loop",
            )
            await self.bus.publish(req)
        raw_text = ev.payload.get("text")
        text = raw_text if isinstance(raw_text, str) else ""
        answer = self.answers.get(text, self.default_answer)
        done = new_event(
            "assistant.message.done",
            {"content": answer, "tool_calls": []},
            session_id=ev.session_id,
            source="_fake_loop",
        )
        await self.bus.publish(done)


def collect(bus: EventBus, kind: str, out: list[Event]) -> Subscription:
    """Install a capture subscription for ``kind`` into ``out``."""

    async def _handler(ev: Event) -> None:
        out.append(ev)

    return bus.subscribe(kind, _handler, source=f"_collect:{kind}")


def payload(ev: Event) -> dict[str, Any]:
    """Shorthand for ``ev.payload`` with the right typing in tests."""
    return ev.payload
