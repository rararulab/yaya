"""Tests for the echo LLM-provider plugin.

AC-bindings from ``specs/plugin-llm_echo.spec``:

* AC-01 echo round-trip → ``test_echo_response_for_user_message``
* filter           → ``test_non_matching_provider_is_ignored``
* empty-messages   → ``test_empty_messages_returns_no_input_marker``
* multi-turn       → ``test_echoes_only_last_user_message``
* request_id echo  → ``test_request_id_matches_source_event``
"""

from __future__ import annotations

import logging
from pathlib import Path

from yaya.kernel.bus import EventBus
from yaya.kernel.events import Event, new_event
from yaya.kernel.plugin import KernelContext
from yaya.plugins.llm_echo.plugin import EchoLLM


def _make_ctx(bus: EventBus, tmp_path: Path, plugin: EchoLLM) -> KernelContext:
    return KernelContext(
        bus=bus,
        logger=logging.getLogger("plugin.llm-echo"),
        config={},
        state_dir=tmp_path,
        plugin_name=plugin.name,
    )


async def _drive(
    tmp_path: Path,
    payload: dict[str, object],
    *,
    response_kind: str = "llm.call.response",
    error_kind: str = "llm.call.error",
) -> tuple[Event, list[Event], list[Event]]:
    """Publish one ``llm.call.request`` and return (request, responses, errors)."""
    plugin = EchoLLM()
    bus = EventBus()
    ctx = _make_ctx(bus, tmp_path, plugin)
    await plugin.on_load(ctx)

    async def _handler(ev: Event) -> None:
        await plugin.on_event(ev, ctx)

    bus.subscribe("llm.call.request", _handler, source=plugin.name)

    responses: list[Event] = []
    errors: list[Event] = []

    async def _r(ev: Event) -> None:
        responses.append(ev)

    async def _e(ev: Event) -> None:
        errors.append(ev)

    bus.subscribe(response_kind, _r, source="observer")
    bus.subscribe(error_kind, _e, source="observer")

    req = new_event(
        "llm.call.request",
        payload,  # type: ignore[arg-type]
        session_id="sess-echo",
        source="kernel",
    )
    await bus.publish(req)
    await plugin.on_unload(ctx)
    return req, responses, errors


async def test_echo_response_for_user_message(tmp_path: Path) -> None:
    """AC-01: provider=echo + user message → ``(echo) <message>`` response."""
    req, responses, errors = await _drive(
        tmp_path,
        {
            "provider": "echo",
            "model": "echo",
            "messages": [{"role": "user", "content": "hello"}],
            "params": {},
        },
    )
    assert errors == []
    assert len(responses) == 1
    payload = responses[0].payload
    assert payload["text"] == "(echo) hello"
    assert payload["tool_calls"] == []
    assert payload["usage"] == {"input_tokens": 0, "output_tokens": 0}
    assert payload["request_id"] == req.id


async def test_non_matching_provider_is_ignored(tmp_path: Path) -> None:
    """A request for a sibling provider does not emit any event from llm-echo."""
    _req, responses, errors = await _drive(
        tmp_path,
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
            "params": {},
        },
    )
    assert responses == []
    assert errors == []


async def test_empty_messages_returns_no_input_marker(tmp_path: Path) -> None:
    """No user message → deterministic ``(echo) (no input)`` placeholder."""
    _req, responses, errors = await _drive(
        tmp_path,
        {
            "provider": "echo",
            "model": "echo",
            "messages": [],
            "params": {},
        },
    )
    assert errors == []
    assert len(responses) == 1
    assert responses[0].payload["text"] == "(echo) (no input)"


async def test_echoes_only_last_user_message(tmp_path: Path) -> None:
    """Multi-turn history → echoes the most recent user turn only."""
    _req, responses, _errors = await _drive(
        tmp_path,
        {
            "provider": "echo",
            "model": "echo",
            "messages": [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "(echo) first"},
                {"role": "user", "content": "second"},
            ],
            "params": {},
        },
    )
    assert len(responses) == 1
    assert responses[0].payload["text"] == "(echo) second"


async def test_request_id_matches_source_event(tmp_path: Path) -> None:
    """The response carries the originating request's id (lesson #15)."""
    req, responses, _errors = await _drive(
        tmp_path,
        {
            "provider": "echo",
            "model": "echo",
            "messages": [{"role": "user", "content": "ping"}],
            "params": {},
        },
    )
    assert len(responses) == 1
    assert responses[0].payload["request_id"] == req.id


async def test_user_message_with_empty_content_returns_no_input(tmp_path: Path) -> None:
    """A user turn with empty content collapses to the no-input marker."""
    _req, responses, _errors = await _drive(
        tmp_path,
        {
            "provider": "echo",
            "model": "echo",
            "messages": [{"role": "user", "content": ""}],
            "params": {},
        },
    )
    assert len(responses) == 1
    assert responses[0].payload["text"] == "(echo) (no input)"


def test_subscriptions_returns_only_llm_call_request() -> None:
    """The plugin advertises its single subscription kind."""
    assert EchoLLM().subscriptions() == ["llm.call.request"]


async def test_non_list_messages_returns_no_input(tmp_path: Path) -> None:
    """Defensive: a non-list ``messages`` value is treated as empty."""
    _req, responses, _errors = await _drive(
        tmp_path,
        {
            "provider": "echo",
            "model": "echo",
            "messages": "not-a-list",  # malformed payload
            "params": {},
        },
    )
    assert len(responses) == 1
    assert responses[0].payload["text"] == "(echo) (no input)"


async def test_skips_non_dict_and_non_user_messages(tmp_path: Path) -> None:
    """Defensive: non-dict entries and non-user roles are skipped during scan."""
    _req, responses, _errors = await _drive(
        tmp_path,
        {
            "provider": "echo",
            "model": "echo",
            "messages": [
                {"role": "system", "content": "ignored"},
                {"role": "user", "content": "real"},
                {"role": "assistant", "content": "later"},
                "garbage-entry",  # non-dict, last → exercises the skip branch
            ],
            "params": {},
        },
    )
    assert len(responses) == 1
    assert responses[0].payload["text"] == "(echo) real"


async def test_non_request_event_kind_is_ignored(tmp_path: Path) -> None:
    """on_event must early-return on unrelated kinds (defence-in-depth)."""
    plugin = EchoLLM()
    bus = EventBus()
    ctx = _make_ctx(bus, tmp_path, plugin)
    await plugin.on_load(ctx)

    captured: list[Event] = []

    async def _r(ev: Event) -> None:
        captured.append(ev)

    bus.subscribe("llm.call.response", _r, source="observer")

    other = new_event(
        "llm.call.response",
        {"text": "x", "tool_calls": [], "usage": {}},
        session_id="sess-other",
        source="kernel",
    )
    await plugin.on_event(other, ctx)
    assert captured == []
    await plugin.on_unload(ctx)
