"""Tests for the echo LLM-provider plugin.

AC-bindings from ``specs/plugin-llm_echo.spec`` and
``specs/plugin-instance-dispatch.spec``:

* AC-01 echo round-trip → ``test_echo_response_for_user_message``
* filter           → ``test_non_matching_provider_is_ignored``
* empty-messages   → ``test_empty_messages_returns_no_input_marker``
* multi-turn       → ``test_echoes_only_last_user_message``
* request_id echo  → ``test_request_id_matches_source_event``
* instance dispatch → ``test_multi_instance_dispatch``
"""

from __future__ import annotations

import logging
from pathlib import Path

from yaya.kernel.bus import EventBus
from yaya.kernel.config_store import ConfigStore
from yaya.kernel.events import Event, new_event
from yaya.kernel.plugin import KernelContext
from yaya.plugins.llm_echo.plugin import EchoLLM


def _make_ctx(
    bus: EventBus,
    tmp_path: Path,
    plugin: EchoLLM,
    *,
    store: ConfigStore | None = None,
) -> KernelContext:
    return KernelContext(
        bus=bus,
        logger=logging.getLogger("plugin.llm-echo"),
        config={},
        state_dir=tmp_path,
        plugin_name=plugin.name,
        config_store=store,
    )


async def _seed_echo_instance(store: ConfigStore, instance_id: str = "llm-echo") -> None:
    """Write the minimal ``providers.<id>.plugin=llm-echo`` row."""
    await store.set(f"providers.{instance_id}.plugin", "llm-echo")


async def _drive(
    tmp_path: Path,
    payload: dict[str, object],
    *,
    response_kind: str = "llm.call.response",
    error_kind: str = "llm.call.error",
    instance_id: str = "llm-echo",
) -> tuple[Event, list[Event], list[Event]]:
    """Publish one ``llm.call.request`` and return (request, responses, errors)."""
    plugin = EchoLLM()
    bus = EventBus()
    store = await ConfigStore.open(bus=bus, path=tmp_path / "config.db")
    try:
        await _seed_echo_instance(store, instance_id)
        ctx = _make_ctx(bus, tmp_path, plugin, store=store)
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
            # ``new_event`` accepts a strict TypedDict; the test feeds a plain
            # dict literal. Casting per call would obscure each scenario's
            # shape — narrow ignore is the lesser evil.
            payload,  # type: ignore[arg-type]
            session_id="sess-echo",
            source="kernel",
        )
        await bus.publish(req)
        await plugin.on_unload(ctx)
        return req, responses, errors
    finally:
        await store.close()


async def test_echo_response_for_user_message(tmp_path: Path) -> None:
    """AC-01: provider=<echo-instance> + user message → ``(echo) <message>`` response."""
    req, responses, errors = await _drive(
        tmp_path,
        {
            "provider": "llm-echo",
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
            "provider": "llm-openai",
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
            "provider": "llm-echo",
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
            "provider": "llm-echo",
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
            "provider": "llm-echo",
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
            "provider": "llm-echo",
            "model": "echo",
            "messages": [{"role": "user", "content": ""}],
            "params": {},
        },
    )
    assert len(responses) == 1
    assert responses[0].payload["text"] == "(echo) (no input)"


def test_subscriptions_returns_request_and_config_updated() -> None:
    """The plugin advertises its subscription kinds including config.updated."""
    subs = EchoLLM().subscriptions()
    assert "llm.call.request" in subs
    assert "config.updated" in subs


async def test_non_list_messages_returns_no_input(tmp_path: Path) -> None:
    """Defensive: a non-list ``messages`` value is treated as empty."""
    _req, responses, _errors = await _drive(
        tmp_path,
        {
            "provider": "llm-echo",
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
            "provider": "llm-echo",
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
    store = await ConfigStore.open(bus=bus, path=tmp_path / "config.db")
    try:
        await _seed_echo_instance(store)
        ctx = _make_ctx(bus, tmp_path, plugin, store=store)
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
    finally:
        await store.close()


async def test_multi_instance_dispatch(tmp_path: Path) -> None:
    """Two echo instances → each instance id routes to this plugin independently."""
    plugin = EchoLLM()
    bus = EventBus()
    store = await ConfigStore.open(bus=bus, path=tmp_path / "config.db")
    try:
        await store.set("providers.echo-a.plugin", "llm-echo")
        await store.set("providers.echo-b.plugin", "llm-echo")
        ctx = _make_ctx(bus, tmp_path, plugin, store=store)
        await plugin.on_load(ctx)
        assert plugin._active_instances == {"echo-a", "echo-b"}

        async def _handler(ev: Event) -> None:
            await plugin.on_event(ev, ctx)

        bus.subscribe("llm.call.request", _handler, source=plugin.name)
        responses: list[Event] = []

        async def _r(ev: Event) -> None:
            responses.append(ev)

        bus.subscribe("llm.call.response", _r, source="observer")
        for pid in ("echo-a", "echo-b"):
            await bus.publish(
                new_event(
                    "llm.call.request",
                    {
                        "provider": pid,
                        "model": "echo",
                        "messages": [{"role": "user", "content": pid}],
                    },
                    session_id=f"sess-{pid}",
                    source="kernel",
                )
            )
        texts = {ev.payload["text"] for ev in responses}
        assert texts == {"(echo) echo-a", "(echo) echo-b"}

        await plugin.on_unload(ctx)
    finally:
        await store.close()


async def test_hot_reload_refreshes_active_instances(tmp_path: Path) -> None:
    """Adding / removing a ``providers.<id>.plugin=llm-echo`` row updates the owned set."""
    plugin = EchoLLM()
    bus = EventBus()
    store = await ConfigStore.open(bus=bus, path=tmp_path / "config.db")
    try:
        ctx = _make_ctx(bus, tmp_path, plugin, store=store)
        await plugin.on_load(ctx)
        assert plugin._active_instances == set()

        async def _handler(ev: Event) -> None:
            await plugin.on_event(ev, ctx)

        bus.subscribe("config.updated", _handler, source=plugin.name)

        await store.set("providers.echo-new.plugin", "llm-echo")
        assert "echo-new" in plugin._active_instances

        await store.unset("providers.echo-new.plugin")
        assert "echo-new" not in plugin._active_instances

        await plugin.on_unload(ctx)
    finally:
        await store.close()


async def test_config_updated_outside_providers_prefix_is_noop(tmp_path: Path) -> None:
    """A ``config.updated`` whose key is outside ``providers.*`` does not trigger a refresh."""
    plugin = EchoLLM()
    bus = EventBus()
    store = await ConfigStore.open(bus=bus, path=tmp_path / "config.db")
    try:
        await _seed_echo_instance(store)
        ctx = _make_ctx(bus, tmp_path, plugin, store=store)
        await plugin.on_load(ctx)
        before = set(plugin._active_instances)

        ev = new_event(
            "config.updated",
            {"key": "plugin.other.api_key", "prefix_match_hint": "plugin.other."},
            session_id="kernel",
            source="kernel-config-store",
        )
        await plugin.on_event(ev, ctx)
        assert plugin._active_instances == before

        # Non-string key — ignored silently.
        empty = new_event(
            "config.updated",
            {"key": 12345, "prefix_match_hint": ""},  # type: ignore[dict-item]
            session_id="kernel",
            source="kernel-config-store",
        )
        await plugin.on_event(empty, ctx)
        assert plugin._active_instances == before

        await plugin.on_unload(ctx)
    finally:
        await store.close()
