"""Unit tests for the kernel's closed event catalog and envelope factory."""

from __future__ import annotations

import pytest

from yaya.kernel.events import (
    PUBLIC_EVENT_KINDS,
    AssistantMessageDonePayload,
    Event,
    LlmCallErrorPayload,
    LlmCallRequestPayload,
    LlmCallResponsePayload,
    ToolCallResultPayload,
    UserMessageReceivedPayload,
    new_event,
)


def test_envelope_fields() -> None:
    """new_event populates every envelope field mandated by plugin-protocol.md."""
    ev = new_event(
        "user.message.received",
        {"text": "hello"},
        session_id="s-1",
        source="web",
    )

    assert isinstance(ev, Event)
    assert ev.kind == "user.message.received"
    assert ev.session_id == "s-1"
    assert ev.source == "web"
    assert ev.payload == {"text": "hello"}
    assert isinstance(ev.id, str) and len(ev.id) == 32  # uuid4 hex.
    assert isinstance(ev.ts, float) and ev.ts > 0


def test_rejects_unknown_public_kind() -> None:
    """Kinds outside the closed catalog and not prefixed 'x.' raise ValueError."""
    with pytest.raises(ValueError, match=r"closed catalog|PublicEventKind"):
        new_event("nonsense.unknown", {}, session_id="s", source="x")


def test_accepts_extension_namespace() -> None:
    """x.<plugin>.<kind> events are routed without being validated against the catalog."""
    ev = new_event("x.foo.bar", {"anything": 1}, session_id="s", source="foo")
    assert ev.kind == "x.foo.bar"
    assert ev.payload == {"anything": 1}


def test_public_catalog_matches_protocol_document() -> None:
    """Guardrail: every public kind documented in plugin-protocol.md is present."""
    expected = {
        "user.message.received",
        "user.interrupt",
        "assistant.message.delta",
        "assistant.message.done",
        "llm.call.request",
        "llm.call.response",
        "llm.call.error",
        "tool.call.request",
        "tool.call.start",
        "tool.call.result",
        "memory.query",
        "memory.write",
        "memory.result",
        "strategy.decide.request",
        "strategy.decide.response",
        "plugin.loaded",
        "plugin.reloaded",
        "plugin.removed",
        "plugin.error",
        "kernel.ready",
        "kernel.shutdown",
        "kernel.error",
    }
    assert expected == PUBLIC_EVENT_KINDS


@pytest.mark.parametrize(
    ("td", "required", "optional"),
    [
        (UserMessageReceivedPayload, {"text"}, {"attachments"}),
        (AssistantMessageDonePayload, {"content", "tool_calls"}, set()),
        (
            LlmCallRequestPayload,
            {"provider", "model", "messages", "params"},
            {"tools"},
        ),
        (LlmCallResponsePayload, {"usage"}, {"text", "tool_calls", "request_id"}),
        (LlmCallErrorPayload, {"error"}, {"retry_after_s", "request_id"}),
        (ToolCallResultPayload, {"id", "ok"}, {"value", "error", "request_id"}),
    ],
)
def test_typed_dict_required_optional_partition(
    td: type,
    required: set[str],
    optional: set[str],
) -> None:
    """Required/optional field partition must match docs/dev/plugin-protocol.md."""
    assert set(td.__required_keys__) == required
    assert set(td.__optional_keys__) == optional


def test_new_event_ids_are_unique() -> None:
    """Two calls with identical inputs produce distinct ids."""
    a = new_event("kernel.ready", {"version": "0.0.1"}, session_id="s", source="kernel")
    b = new_event("kernel.ready", {"version": "0.0.1"}, session_id="s", source="kernel")
    assert a.id != b.id
