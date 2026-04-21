"""Tests for the OpenAI LLM-provider plugin.

AC-bindings from ``specs/plugin-llm_openai.spec`` and
``specs/plugin-instance-dispatch.spec``:

* success → ``test_successful_completion_emits_response``
* missing key → ``test_missing_api_key_instance_is_not_owned``
* filter → ``test_non_matching_provider_is_ignored``
* rate limit → ``test_rate_limit_error_emits_error_event``
* instance dispatch → ``test_two_instances_route_to_distinct_clients``
* per-instance rebuild → ``test_config_updated_rebuilds_only_affected_instance``
* add instance → ``test_adding_new_instance_lands_new_client``
* remove instance → ``test_removing_instance_drops_client``

Uses ``unittest.mock`` to stub ``openai.AsyncOpenAI`` so tests stay
offline and deterministic.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from yaya.kernel.bus import EventBus
from yaya.kernel.config_store import ConfigStore
from yaya.kernel.events import Event, new_event
from yaya.kernel.plugin import KernelContext
from yaya.plugins.llm_openai.plugin import OpenAIProvider


async def _make_store(tmp_path: Path, bus: EventBus) -> ConfigStore:
    """Open a fresh :class:`ConfigStore` in a per-test directory."""
    return await ConfigStore.open(bus=bus, path=tmp_path / "config.db")


def _make_ctx(
    bus: EventBus,
    tmp_path: Path,
    plugin: OpenAIProvider,
    *,
    store: ConfigStore | None = None,
) -> KernelContext:
    return KernelContext(
        bus=bus,
        logger=logging.getLogger("plugin.llm-openai"),
        config={},
        state_dir=tmp_path,
        plugin_name=plugin.name,
        config_store=store,
    )


def _fake_completion(
    text: str = "hi there",
    *,
    input_tokens: int = 7,
    output_tokens: int = 3,
) -> SimpleNamespace:
    """Build a stub mirroring the SDK's chat.completion object shape."""
    message = SimpleNamespace(content=text, tool_calls=None)
    choice = SimpleNamespace(message=message)
    usage = SimpleNamespace(prompt_tokens=input_tokens, completion_tokens=output_tokens)
    return SimpleNamespace(choices=[choice], usage=usage)


async def _seed_instance(
    store: ConfigStore,
    instance_id: str,
    *,
    plugin: str = "llm-openai",
    api_key: str = "sk-test",
    base_url: str | None = None,
    model: str | None = None,
) -> None:
    """Write a ``providers.<id>.*`` subtree under ``store``."""
    await store.set(f"providers.{instance_id}.plugin", plugin)
    await store.set(f"providers.{instance_id}.api_key", api_key)
    if base_url is not None:
        await store.set(f"providers.{instance_id}.base_url", base_url)
    if model is not None:
        await store.set(f"providers.{instance_id}.model", model)


async def test_successful_completion_emits_response(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful chat completion emits llm.call.response with all fields."""
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    plugin = OpenAIProvider()
    bus = EventBus()
    store = await _make_store(tmp_path, bus)
    try:
        await _seed_instance(store, "llm-openai", api_key="sk-test", model="gpt-4o-mini")
        ctx = _make_ctx(bus, tmp_path, plugin, store=store)
        await plugin.on_load(ctx)
        # Swap the live client for a stub so we don't hit the network.
        stub_client = MagicMock()
        stub_client.chat.completions.create = AsyncMock(return_value=_fake_completion())
        plugin._clients["llm-openai"] = stub_client

        async def _handler(ev: Event) -> None:
            await plugin.on_event(ev, ctx)

        bus.subscribe("llm.call.request", _handler, source=plugin.name)

        captured: list[Event] = []

        async def _observer(ev: Event) -> None:
            captured.append(ev)

        bus.subscribe("llm.call.response", _observer, source="observer")

        req = new_event(
            "llm.call.request",
            {
                "provider": "llm-openai",
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
                "params": {},
            },
            session_id="sess-openai-ok",
            source="kernel",
        )
        await bus.publish(req)

        stub_client.chat.completions.create.assert_awaited_once()
        kwargs = stub_client.chat.completions.create.await_args.kwargs
        assert kwargs["model"] == "gpt-4o-mini"
        assert kwargs["messages"] == [{"role": "user", "content": "hi"}]

        assert len(captured) == 1
        payload = captured[0].payload
        assert payload["text"] == "hi there"
        assert payload["tool_calls"] == []
        assert payload["usage"] == {"input_tokens": 7, "output_tokens": 3}
        assert payload["request_id"] == req.id

        await plugin.on_unload(ctx)
    finally:
        await store.close()


async def test_missing_api_key_instance_is_not_owned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Instance with no api_key (and no env) is skipped; requests naming it fall through silently."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    plugin = OpenAIProvider()
    bus = EventBus()
    store = await _make_store(tmp_path, bus)
    try:
        # Instance declared but no api_key and no env — skipped.
        await store.set("providers.llm-openai.plugin", "llm-openai")
        ctx = _make_ctx(bus, tmp_path, plugin, store=store)
        await plugin.on_load(ctx)

        assert plugin._clients == {}

        async def _handler(ev: Event) -> None:
            await plugin.on_event(ev, ctx)

        bus.subscribe("llm.call.request", _handler, source=plugin.name)
        responses: list[Event] = []
        errors: list[Event] = []

        async def _r(ev: Event) -> None:
            responses.append(ev)

        async def _e(ev: Event) -> None:
            errors.append(ev)

        bus.subscribe("llm.call.response", _r, source="observer")
        bus.subscribe("llm.call.error", _e, source="observer")

        req = new_event(
            "llm.call.request",
            {
                "provider": "llm-openai",
                "model": "gpt-4o-mini",
                "messages": [],
                "params": {},
            },
            session_id="sess-noauth",
            source="kernel",
        )
        await bus.publish(req)

        # Plugin did not answer — unowned instances fall through.
        assert responses == []
        assert errors == []

        await plugin.on_unload(ctx)
    finally:
        await store.close()


async def test_non_matching_provider_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A request for a sibling provider does not emit any event from llm-openai."""
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    plugin = OpenAIProvider()
    bus = EventBus()
    store = await _make_store(tmp_path, bus)
    try:
        await _seed_instance(store, "llm-openai", api_key="sk-test")
        ctx = _make_ctx(bus, tmp_path, plugin, store=store)
        await plugin.on_load(ctx)

        stub_client = MagicMock()
        stub_client.chat.completions.create = AsyncMock(return_value=_fake_completion())
        plugin._clients["llm-openai"] = stub_client

        async def _handler(ev: Event) -> None:
            await plugin.on_event(ev, ctx)

        bus.subscribe("llm.call.request", _handler, source=plugin.name)
        responses: list[Event] = []
        errors: list[Event] = []

        async def _r(ev: Event) -> None:
            responses.append(ev)

        async def _e(ev: Event) -> None:
            errors.append(ev)

        bus.subscribe("llm.call.response", _r, source="observer")
        bus.subscribe("llm.call.error", _e, source="observer")

        await bus.publish(
            new_event(
                "llm.call.request",
                {
                    "provider": "anthropic",
                    "model": "claude-3-5",
                    "messages": [],
                    "params": {},
                },
                session_id="sess-anthropic",
                source="kernel",
            )
        )

        assert responses == []
        assert errors == []
        stub_client.chat.completions.create.assert_not_called()

        await plugin.on_unload(ctx)
    finally:
        await store.close()


async def test_rate_limit_error_emits_error_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An SDK RateLimitError surfaces as llm.call.error with str(exc) + request_id."""
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    import httpx
    import openai

    plugin = OpenAIProvider()
    bus = EventBus()
    store = await _make_store(tmp_path, bus)
    try:
        await _seed_instance(store, "llm-openai", api_key="sk-test")
        ctx = _make_ctx(bus, tmp_path, plugin, store=store)
        await plugin.on_load(ctx)

        rate_limit_exc = openai.RateLimitError(
            message="rate limited",
            response=httpx.Response(429, request=httpx.Request("POST", "http://x")),
            body=None,
        )

        stub_client = MagicMock()
        stub_client.chat.completions.create = AsyncMock(side_effect=rate_limit_exc)
        plugin._clients["llm-openai"] = stub_client

        async def _handler(ev: Event) -> None:
            await plugin.on_event(ev, ctx)

        bus.subscribe("llm.call.request", _handler, source=plugin.name)
        captured: list[Event] = []

        async def _observer(ev: Event) -> None:
            captured.append(ev)

        bus.subscribe("llm.call.error", _observer, source="observer")

        req = new_event(
            "llm.call.request",
            {
                "provider": "llm-openai",
                "model": "gpt-4o-mini",
                "messages": [],
                "params": {},
            },
            session_id="sess-ratelimit",
            source="kernel",
        )
        await bus.publish(req)

        assert len(captured) == 1
        payload = captured[0].payload
        assert "rate limited" in payload["error"]
        assert payload["request_id"] == req.id

        await plugin.on_unload(ctx)
    finally:
        await store.close()


async def test_two_instances_route_to_distinct_clients(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-01: two llm-openai instances with distinct base_url each route their own traffic."""
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    class _StubSDK:
        def __init__(self, **kwargs: Any) -> None:
            self._kwargs = kwargs
            self._base = kwargs.get("base_url", "<default>")
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(
                    create=AsyncMock(return_value=_fake_completion(text=f"from {self._base}")),
                ),
            )

    monkeypatch.setattr("openai.AsyncOpenAI", _StubSDK)

    plugin = OpenAIProvider()
    bus = EventBus()
    store = await _make_store(tmp_path, bus)
    try:
        await _seed_instance(store, "openai-a", api_key="sk-a", base_url="https://a.example", model="gpt-a")
        await _seed_instance(store, "openai-b", api_key="sk-b", base_url="https://b.example", model="gpt-b")
        ctx = _make_ctx(bus, tmp_path, plugin, store=store)
        await plugin.on_load(ctx)
        assert set(plugin._clients) == {"openai-a", "openai-b"}

        async def _handler(ev: Event) -> None:
            await plugin.on_event(ev, ctx)

        bus.subscribe("llm.call.request", _handler, source=plugin.name)
        responses: list[Event] = []

        async def _observer(ev: Event) -> None:
            responses.append(ev)

        bus.subscribe("llm.call.response", _observer, source="observer")

        await bus.publish(
            new_event(
                "llm.call.request",
                {
                    "provider": "openai-a",
                    "model": "ignored",
                    "messages": [{"role": "user", "content": "hi"}],
                },
                session_id="sess-a",
                source="kernel",
            )
        )
        await bus.publish(
            new_event(
                "llm.call.request",
                {
                    "provider": "openai-b",
                    "model": "ignored",
                    "messages": [{"role": "user", "content": "hi"}],
                },
                session_id="sess-b",
                source="kernel",
            )
        )

        assert len(responses) == 2
        # Each client stub embedded its own base_url in the response text.
        texts = {ev.payload["text"] for ev in responses}
        assert texts == {"from https://a.example", "from https://b.example"}
        # Each client's create() was called with its own instance-configured model.
        models_called = {call.kwargs["model"] for call in _model_calls(plugin)}
        assert models_called == {"gpt-a", "gpt-b"}

        await plugin.on_unload(ctx)
    finally:
        await store.close()


def _model_calls(plugin: OpenAIProvider) -> list[Any]:
    """Return every ``chat.completions.create`` await-args across all owned clients."""
    out: list[Any] = []
    for client in plugin._clients.values():
        mock = client.chat.completions.create  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
        out.extend(mock.await_args_list)
    return out


async def test_config_updated_rebuilds_only_affected_instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-03: ``config.updated`` on ``providers.B.api_key`` rebuilds only client B."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    class _StubSDK:
        def __init__(self, **_: Any) -> None:
            return None

        async def close(self) -> None:
            return None

    monkeypatch.setattr("openai.AsyncOpenAI", _StubSDK)

    plugin = OpenAIProvider()
    bus = EventBus()
    store = await _make_store(tmp_path, bus)
    try:
        await _seed_instance(store, "openai-a", api_key="sk-a")
        await _seed_instance(store, "openai-b", api_key="sk-b")
        ctx = _make_ctx(bus, tmp_path, plugin, store=store)
        await plugin.on_load(ctx)

        async def _handler(ev: Event) -> None:
            await plugin.on_event(ev, ctx)

        bus.subscribe("config.updated", _handler, source=plugin.name)

        client_a_before = plugin._clients["openai-a"]
        client_b_before = plugin._clients["openai-b"]

        await store.set("providers.openai-b.api_key", "sk-b-rotated")

        assert plugin._clients["openai-a"] is client_a_before
        assert plugin._clients["openai-b"] is not client_b_before

        await plugin.on_unload(ctx)
    finally:
        await store.close()


async def test_adding_new_instance_lands_new_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-04: adding ``providers.C.plugin=llm-openai`` materialises a new client."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    class _StubSDK:
        def __init__(self, **_: Any) -> None:
            return None

        async def close(self) -> None:
            return None

    monkeypatch.setattr("openai.AsyncOpenAI", _StubSDK)

    plugin = OpenAIProvider()
    bus = EventBus()
    store = await _make_store(tmp_path, bus)
    try:
        await _seed_instance(store, "openai-a", api_key="sk-a")
        ctx = _make_ctx(bus, tmp_path, plugin, store=store)
        await plugin.on_load(ctx)

        async def _handler(ev: Event) -> None:
            await plugin.on_event(ev, ctx)

        bus.subscribe("config.updated", _handler, source=plugin.name)
        assert set(plugin._clients) == {"openai-a"}

        # Stage a new instance: api_key first, then plugin meta.
        await store.set("providers.openai-c.api_key", "sk-c")
        # api_key alone with no plugin meta → not owned.
        assert "openai-c" not in plugin._clients
        await store.set("providers.openai-c.plugin", "llm-openai")
        # Now owned — the hot-reload path added it.
        assert "openai-c" in plugin._clients

        await plugin.on_unload(ctx)
    finally:
        await store.close()


async def test_removing_instance_drops_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-05: deleting an instance's plugin meta drops its client."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    class _StubSDK:
        def __init__(self, **_: Any) -> None:
            return None

        async def close(self) -> None:
            return None

    monkeypatch.setattr("openai.AsyncOpenAI", _StubSDK)

    plugin = OpenAIProvider()
    bus = EventBus()
    store = await _make_store(tmp_path, bus)
    try:
        await _seed_instance(store, "openai-a", api_key="sk-a")
        ctx = _make_ctx(bus, tmp_path, plugin, store=store)
        await plugin.on_load(ctx)

        async def _handler(ev: Event) -> None:
            await plugin.on_event(ev, ctx)

        bus.subscribe("config.updated", _handler, source=plugin.name)
        assert "openai-a" in plugin._clients

        await store.unset("providers.openai-a.plugin")
        assert "openai-a" not in plugin._clients

        await plugin.on_unload(ctx)
    finally:
        await store.close()


async def test_repointing_instance_plugin_meta_drops_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-pointing ``providers.<id>.plugin`` away from this plugin drops the client."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    class _StubSDK:
        def __init__(self, **_: Any) -> None:
            return None

        async def close(self) -> None:
            return None

    monkeypatch.setattr("openai.AsyncOpenAI", _StubSDK)

    plugin = OpenAIProvider()
    bus = EventBus()
    store = await _make_store(tmp_path, bus)
    try:
        await _seed_instance(store, "openai-a", api_key="sk-a")
        ctx = _make_ctx(bus, tmp_path, plugin, store=store)
        await plugin.on_load(ctx)

        async def _handler(ev: Event) -> None:
            await plugin.on_event(ev, ctx)

        bus.subscribe("config.updated", _handler, source=plugin.name)
        assert "openai-a" in plugin._clients

        await store.set("providers.openai-a.plugin", "llm-other")
        assert "openai-a" not in plugin._clients

        await plugin.on_unload(ctx)
    finally:
        await store.close()


async def test_base_url_env_lands_on_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``OPENAI_BASE_URL`` is threaded into the SDK constructor when the instance omits it."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://env.example")

    calls: list[dict[str, Any]] = []

    class _StubSDK:
        def __init__(self, **kwargs: Any) -> None:
            calls.append(kwargs)

        async def close(self) -> None:
            return None

    monkeypatch.setattr("openai.AsyncOpenAI", _StubSDK)

    plugin = OpenAIProvider()
    bus = EventBus()
    store = await _make_store(tmp_path, bus)
    try:
        # api_key absent from instance but OPENAI_API_KEY is set; base_url absent
        # from instance but OPENAI_BASE_URL is set.
        await store.set("providers.llm-openai.plugin", "llm-openai")
        ctx = _make_ctx(bus, tmp_path, plugin, store=store)
        await plugin.on_load(ctx)

        assert calls
        assert calls[-1].get("base_url") == "https://env.example"

        await plugin.on_unload(ctx)
    finally:
        await store.close()


async def test_subscribes_to_config_updated() -> None:
    """The plugin declares a subscription to ``config.updated`` for hot reload."""
    plugin = OpenAIProvider()
    assert "config.updated" in plugin.subscriptions()
    assert "llm.call.request" in plugin.subscriptions()


async def test_legacy_plugin_keys_emit_warning_when_no_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A stale ``plugin.llm_openai.*`` row with no owned instance emits a legacy-key hint."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    plugin = OpenAIProvider()
    bus = EventBus()
    store = await _make_store(tmp_path, bus)
    try:
        await store.set("plugin.llm_openai.api_key", "sk-legacy")
        ctx = _make_ctx(bus, tmp_path, plugin, store=store)
        caplog.set_level(logging.WARNING, logger="plugin.llm-openai")
        await plugin.on_load(ctx)
        messages = " ".join(record.getMessage() for record in caplog.records)
        assert "legacy plugin.llm_openai.*" in messages
        await plugin.on_unload(ctx)
    finally:
        await store.close()


async def test_config_updated_ignores_non_providers_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``config.updated`` whose key is outside ``providers.*`` is ignored."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    class _StubSDK:
        def __init__(self, **_: Any) -> None:
            return None

        async def close(self) -> None:
            return None

    monkeypatch.setattr("openai.AsyncOpenAI", _StubSDK)

    plugin = OpenAIProvider()
    bus = EventBus()
    store = await _make_store(tmp_path, bus)
    try:
        await _seed_instance(store, "openai-a", api_key="sk-a")
        ctx = _make_ctx(bus, tmp_path, plugin, store=store)
        await plugin.on_load(ctx)
        client_before = plugin._clients["openai-a"]

        # ``providers.`` with no instance id — ignored.
        ev_malformed = new_event(
            "config.updated",
            {"key": "providers.", "prefix_match_hint": "providers."},
            session_id="kernel",
            source="kernel-config-store",
        )
        await plugin.on_event(ev_malformed, ctx)
        assert plugin._clients["openai-a"] is client_before

        # Non-string key — ignored silently.
        empty = new_event(
            "config.updated",
            {"key": 12345, "prefix_match_hint": ""},  # type: ignore[dict-item]
            session_id="kernel",
            source="kernel-config-store",
        )
        await plugin.on_event(empty, ctx)
        assert plugin._clients["openai-a"] is client_before

        await plugin.on_unload(ctx)
    finally:
        await store.close()


async def test_hot_reload_preserves_in_flight_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A rebuild must NOT close the previous client's pool (regression: #106 F1)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    class _StubSDK:
        def __init__(self, **_: Any) -> None:
            return None

        async def close(self) -> None:
            return None

    monkeypatch.setattr("openai.AsyncOpenAI", _StubSDK)

    plugin = OpenAIProvider()
    bus = EventBus()
    store = await _make_store(tmp_path, bus)
    try:
        await _seed_instance(store, "llm-openai", api_key="sk-real")
        ctx = _make_ctx(bus, tmp_path, plugin, store=store)
        await plugin.on_load(ctx)

        async def _handler(ev: Event) -> None:
            await plugin.on_event(ev, ctx)

        bus.subscribe("config.updated", _handler, source=plugin.name)

        # Swap in a mock whose close() must never be awaited — if it
        # were, an in-flight dispatch would observe a dead pool.
        never_close = MagicMock()
        never_close.close = AsyncMock()
        plugin._clients["llm-openai"] = never_close

        await store.set("providers.llm-openai.base_url", "https://new.example")
        never_close.close.assert_not_awaited()
        assert plugin._clients["llm-openai"] is not never_close
        await plugin.on_unload(ctx)
    finally:
        await store.close()


async def test_on_unload_tolerates_missing_clients(tmp_path: Path) -> None:
    """Calling on_unload on a never-loaded plugin is a no-op."""
    plugin = OpenAIProvider()
    bus = EventBus()
    ctx = _make_ctx(bus, tmp_path, plugin)
    await plugin.on_unload(ctx)


# ---------------------------------------------------------------------------
# _tool_call_to_dict — normalise SDK / kernel / fallback shapes (#147).
# ---------------------------------------------------------------------------


class _FakeFn:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakePydanticToolCall:
    """Shadow of the SDK's ChatCompletionMessageToolCall pydantic model.

    Has ``model_dump`` returning the SDK's nested shape:
    ``{id, type, function: {name, arguments}}`` where ``arguments``
    is a JSON string.
    """

    def __init__(self, call_id: str, name: str, arguments: str) -> None:
        self._id = call_id
        self._name = name
        self._args = arguments

    def model_dump(self) -> dict[str, Any]:
        return {
            "id": self._id,
            "type": "function",
            "function": {"name": self._name, "arguments": self._args},
        }


class _FakeAttrToolCall:
    """SDK-like object that is not a pydantic model — attribute access only."""

    def __init__(self, call_id: str, name: str) -> None:
        self.id = call_id
        self.function = _FakeFn(name, "")


def test_tool_call_to_dict_sdk_pydantic_shape_with_string_arguments() -> None:
    """Normalises ``{id, type, function: {name, arguments}}`` with JSON-string args."""
    from yaya.plugins.llm_openai.plugin import _tool_call_to_dict

    tc = _FakePydanticToolCall(
        call_id="call_abc",
        name="bash",
        arguments='{"cmd": ["ls", "-la"]}',
    )
    out = _tool_call_to_dict(tc)
    assert out == {"id": "call_abc", "name": "bash", "args": {"cmd": ["ls", "-la"]}}


def test_tool_call_to_dict_sdk_shape_with_malformed_json_falls_back_to_empty_args() -> None:
    """Parsing failure leaves ``args`` empty so downstream dispatch is defensive."""
    from yaya.plugins.llm_openai.plugin import _tool_call_to_dict

    tc = _FakePydanticToolCall(call_id="call_x", name="bash", arguments="{not-json")
    out = _tool_call_to_dict(tc)
    assert out == {"id": "call_x", "name": "bash", "args": {}}


def test_tool_call_to_dict_kernel_shape_is_preserved_unchanged() -> None:
    """A dict already in ``{id, name, args}`` shape round-trips."""
    from yaya.plugins.llm_openai.plugin import _tool_call_to_dict

    kernel_shape: dict[str, Any] = {"id": "call_y", "name": "bash", "args": {"cmd": ["pwd"]}}
    assert _tool_call_to_dict(kernel_shape) == kernel_shape


def test_tool_call_to_dict_sdk_dict_with_dict_arguments() -> None:
    """Some providers hand back ``arguments`` already as a dict — accept it."""
    from yaya.plugins.llm_openai.plugin import _tool_call_to_dict

    raw: dict[str, Any] = {
        "id": "call_z",
        "type": "function",
        "function": {"name": "bash", "arguments": {"cmd": ["echo", "hi"]}},
    }
    out = _tool_call_to_dict(raw)
    assert out == {"id": "call_z", "name": "bash", "args": {"cmd": ["echo", "hi"]}}


def test_tool_call_to_dict_non_dict_non_pydantic_fallback() -> None:
    """Bare attribute-only object uses ``getattr`` and empty args."""
    from yaya.plugins.llm_openai.plugin import _tool_call_to_dict

    tc = _FakeAttrToolCall(call_id="call_q", name="bash")
    out = _tool_call_to_dict(tc)
    assert out == {"id": "call_q", "name": "bash", "args": {}}


# ---------------------------------------------------------------------------
# _strip_reasoning_tags — inline <think> removal (#149).
# ---------------------------------------------------------------------------


def test_strip_reasoning_tags_passthrough_when_no_think() -> None:
    from yaya.plugins.llm_openai.plugin import _strip_reasoning_tags

    assert _strip_reasoning_tags("hello world") == "hello world"
    assert _strip_reasoning_tags("") == ""


def test_strip_reasoning_tags_single_block() -> None:
    from yaya.plugins.llm_openai.plugin import _strip_reasoning_tags

    raw = "<think>plan the command</think>\n\nok."
    assert _strip_reasoning_tags(raw) == "ok."


def test_strip_reasoning_tags_multiline_block_is_removed_entirely() -> None:
    from yaya.plugins.llm_openai.plugin import _strip_reasoning_tags

    raw = "<think>\nline1\nline2\n</think>\n\nfinal answer"
    assert _strip_reasoning_tags(raw) == "final answer"


def test_strip_reasoning_tags_multiple_blocks() -> None:
    from yaya.plugins.llm_openai.plugin import _strip_reasoning_tags

    raw = "<think>first</think> A <think>second</think> B"
    assert _strip_reasoning_tags(raw) == "A  B"


def test_strip_reasoning_tags_only_thinking_collapses_to_empty() -> None:
    """A content field that's nothing but reasoning comes back empty.

    This is the pattern that caused #149 — turn-one assistant content
    was ``"<think>...</think>\\n\\n"`` followed by only tool_calls,
    so stripping the think block yields an empty string. The loop
    tolerates empty ``last_llm_text`` via its ``or state.last_llm_text``
    fallback, but at least the replayed history no longer echoes
    reasoning tags back to the model.
    """
    from yaya.plugins.llm_openai.plugin import _strip_reasoning_tags

    raw = "<think>planning</think>\n\n"
    assert _strip_reasoning_tags(raw) == ""
