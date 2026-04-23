"""Tests for the ReAct strategy plugin (text Thought/Action/Observation).

Classical ReAct — the strategy parses ``Thought: / Action: /
Action Input:`` triples (or ``Thought: / Final Answer:``) from the
assistant's free-form text. It does NOT look at
``assistant.tool_calls``; tool intent rides in prose only (#151).

AC-bindings:

* no assistant yet → ``test_no_assistant_yet_returns_llm``
* well-formed Action → ``test_assistant_with_react_action_returns_tool``
* provider-style tool-call block → ``test_assistant_with_tool_call_block_returns_tool``
* Final Answer → ``test_assistant_with_final_answer_returns_done``
* post-Observation → ``test_post_observation_returns_llm``
* malformed → nudge → ``test_malformed_assistant_triggers_nudge``
* second malformed → terminate → ``test_second_malformed_terminates``
* parser robustness → ``test_parse_assistant_*``
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from yaya.kernel.bus import EventBus
from yaya.kernel.config_store import ConfigStore
from yaya.kernel.events import Event, new_event
from yaya.kernel.plugin import KernelContext
from yaya.plugins.strategy_react import plugin as react_plugin
from yaya.plugins.strategy_react.plugin import ReActStrategy


def _make_ctx(
    bus: EventBus,
    tmp_path: Path,
    *,
    store: ConfigStore | None = None,
) -> KernelContext:
    return KernelContext(
        bus=bus,
        logger=logging.getLogger("plugin.strategy-react"),
        config={},
        state_dir=tmp_path,
        plugin_name=react_plugin.name,
        config_store=store,
    )


async def _drive(
    bus: EventBus,
    plugin: Any,
    tmp_path: Path,
    payload: dict[str, Any],
    *,
    session_id: str,
    store: ConfigStore | None = None,
) -> list[Event]:
    """Publish one strategy.decide.request and return the captured responses."""
    ctx = _make_ctx(bus, tmp_path, store=store)
    await plugin.on_load(ctx)

    async def _handler(ev: Event) -> None:
        await plugin.on_event(ev, ctx)

    bus.subscribe("strategy.decide.request", _handler, source=plugin.name)

    captured: list[Event] = []

    async def _observer(ev: Event) -> None:
        captured.append(ev)

    bus.subscribe("strategy.decide.response", _observer, source="observer")

    req = new_event(
        "strategy.decide.request",
        payload,
        session_id=session_id,
        source="kernel",
    )
    await bus.publish(req)
    return captured


async def test_no_assistant_yet_returns_llm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No assistant message → llm with provider + model + request_id.

    Pins ``OPENAI_API_KEY`` so the strategy's env-sniff fallback (used
    when ``ctx.providers`` is absent) resolves to the ``llm-openai``
    branch.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    bus = EventBus()
    captured = await _drive(
        bus,
        react_plugin,
        tmp_path,
        {"state": {"messages": [{"role": "user", "content": "hi"}]}},
        session_id="sess-llm-1",
    )
    assert len(captured) == 1
    got = captured[0].payload
    assert got["next"] == "llm"
    assert got["provider"] == "llm-openai"
    assert got["model"] == "gpt-4o-mini"
    assert "request_id" in got


async def test_assistant_with_react_action_returns_tool(tmp_path: Path) -> None:
    """Well-formed ReAct Action → tool with synthesized call id."""
    bus = EventBus()
    react_text = 'Thought: I should echo the value.\nAction: bash\nAction Input: {"cmd": ["echo", "x"]}'
    captured = await _drive(
        bus,
        react_plugin,
        tmp_path,
        {
            "state": {
                "step": 2,
                "messages": [
                    {"role": "user", "content": "run something"},
                    {"role": "assistant", "content": react_text},
                ],
            }
        },
        session_id="sess-action-1",
    )
    assert len(captured) == 1
    got = captured[0].payload
    assert got["next"] == "tool"
    assert got["tool_call"] == {"id": "rx-2", "name": "bash", "args": {"cmd": ["echo", "x"]}}
    assert "request_id" in got


async def test_assistant_with_tool_call_block_returns_tool(tmp_path: Path) -> None:
    """Provider-style tool-call block → tool even when prose says Final Answer."""
    bus = EventBus()
    assistant_text = (
        "Final Answer: I will search Mercari Japan for XPS.\n"
        "[TOOL_CALL]\n"
        '{"tool": "mercari_jp_search", "tool_input": {"keyword": "XPS"}}\n'
        "[/TOOL_CALL]"
    )
    captured = await _drive(
        bus,
        react_plugin,
        tmp_path,
        {
            "state": {
                "step": 7,
                "messages": [
                    {"role": "user", "content": "帮我看看mercari上的xps"},
                    {"role": "assistant", "content": assistant_text},
                ],
            }
        },
        session_id="sess-tool-call-block",
    )
    assert len(captured) == 1
    got = captured[0].payload
    assert got["next"] == "tool"
    assert got["tool_call"] == {"id": "rx-7", "name": "mercari_jp_search", "args": {"keyword": "XPS"}}
    assert "request_id" in got


async def test_post_observation_returns_llm(tmp_path: Path) -> None:
    """Assistant Action followed by an Observation user msg → llm for next turn."""
    bus = EventBus()
    captured = await _drive(
        bus,
        react_plugin,
        tmp_path,
        {
            "state": {
                "step": 3,
                "messages": [
                    {"role": "user", "content": "go"},
                    {
                        "role": "assistant",
                        "content": "Thought: run it.\nAction: bash\nAction Input: {}",
                    },
                    {"role": "user", "content": 'Observation: {"ok": true}'},
                ],
            }
        },
        session_id="sess-post-obs",
    )
    assert len(captured) == 1
    got = captured[0].payload
    assert got["next"] == "llm"
    assert "request_id" in got


async def test_assistant_with_final_answer_returns_done(tmp_path: Path) -> None:
    """``Final Answer:`` in the last assistant → done."""
    bus = EventBus()
    captured = await _drive(
        bus,
        react_plugin,
        tmp_path,
        {
            "state": {
                "messages": [
                    {"role": "user", "content": "hi"},
                    {
                        "role": "assistant",
                        "content": "Thought: greet back.\nFinal Answer: hello!",
                    },
                ],
            }
        },
        session_id="sess-final",
    )
    assert len(captured) == 1
    got = captured[0].payload
    assert got["next"] == "done"
    assert "request_id" in got


async def test_malformed_assistant_triggers_nudge(tmp_path: Path) -> None:
    """Assistant text without an Action/Final Answer → llm + corrective nudge."""
    bus = EventBus()
    captured = await _drive(
        bus,
        react_plugin,
        tmp_path,
        {
            "state": {
                "messages": [
                    {"role": "user", "content": "compute"},
                    {"role": "assistant", "content": "I'll just answer directly: 42"},
                ],
            }
        },
        session_id="sess-nudge",
    )
    assert len(captured) == 1
    got = captured[0].payload
    assert got["next"] == "llm"
    append = got.get("messages_append")
    assert isinstance(append, list) and len(append) == 1
    nudge = append[0]
    assert nudge["role"] == "user"
    assert nudge["content"].startswith("[yaya:react-format-nudge] ")


async def test_second_malformed_terminates(tmp_path: Path) -> None:
    """If a nudge is already in the recent history, a second parse failure → done."""
    bus = EventBus()
    captured = await _drive(
        bus,
        react_plugin,
        tmp_path,
        {
            "state": {
                "messages": [
                    {"role": "user", "content": "compute"},
                    {"role": "assistant", "content": "garbage"},
                    {
                        "role": "user",
                        "content": "[yaya:react-format-nudge] please resend",
                    },
                    {"role": "assistant", "content": "still garbage"},
                ],
            }
        },
        session_id="sess-terminate",
    )
    assert len(captured) == 1
    got = captured[0].payload
    assert got["next"] == "done"


def test_parse_assistant_final_answer() -> None:
    from yaya.plugins.strategy_react.plugin import _parse_assistant

    out = _parse_assistant("Thought: done\nFinal Answer: the answer is 42")
    assert out == ("final", "the answer is 42")


def test_parse_assistant_well_formed_action() -> None:
    from yaya.plugins.strategy_react.plugin import _parse_assistant

    out = _parse_assistant('Thought: ok\nAction: bash\nAction Input: {"cmd": ["ls"]}')
    assert out == ("action", "bash", {"cmd": ["ls"]})


def test_parse_assistant_action_input_in_code_fence() -> None:
    from yaya.plugins.strategy_react.plugin import _parse_assistant

    out = _parse_assistant('Thought: ok\nAction: bash\nAction Input: ```json\n{"cmd": ["echo", "hi"]}\n```')
    assert out == ("action", "bash", {"cmd": ["echo", "hi"]})


def test_parse_assistant_tool_call_block() -> None:
    from yaya.plugins.strategy_react.plugin import _parse_assistant

    out = _parse_assistant(
        "Final Answer: I will search Mercari Japan.\n"
        "[TOOL_CALL]\n"
        '{"tool": "mercari_jp_search", "tool_input": {"keyword": "XPS"}}\n'
        "[/TOOL_CALL]"
    )
    assert out == ("action", "mercari_jp_search", {"keyword": "XPS"})


def test_parse_assistant_malformed_tool_call_block_reports_error() -> None:
    from yaya.plugins.strategy_react.plugin import _parse_assistant

    out = _parse_assistant("Final Answer: I will search Mercari Japan.\n[TOOL_CALL]\nnot-json\n[/TOOL_CALL]")
    assert out[0] == "error"
    assert "TOOL_CALL" in out[1]


def test_parse_assistant_tool_call_block_accepts_json_fence_and_args_alias() -> None:
    from yaya.plugins.strategy_react.plugin import _parse_assistant

    out = _parse_assistant('[TOOL_CALL]\n```json\n{"tool": "bash", "args": {"cmd": ["echo", "x"]}}\n```\n[/TOOL_CALL]')
    assert out == ("action", "bash", {"cmd": ["echo", "x"]})


def test_parse_assistant_tool_call_block_defaults_missing_input_to_empty_object() -> None:
    from yaya.plugins.strategy_react.plugin import _parse_assistant

    out = _parse_assistant('[TOOL_CALL]\n{"tool": "bash"}\n[/TOOL_CALL]')
    assert out == ("action", "bash", {})


def test_parse_assistant_tool_call_block_rejects_bad_shape() -> None:
    from yaya.plugins.strategy_react.plugin import _parse_assistant

    assert _parse_assistant("[TOOL_CALL]\n[]\n[/TOOL_CALL]")[0] == "error"
    assert _parse_assistant('[TOOL_CALL]\n{"tool": "", "tool_input": {}}\n[/TOOL_CALL]')[0] == "error"
    assert _parse_assistant('[TOOL_CALL]\n{"tool": "bash", "tool_input": []}\n[/TOOL_CALL]')[0] == "error"


def test_parse_assistant_missing_labels_reports_error() -> None:
    from yaya.plugins.strategy_react.plugin import _parse_assistant

    out = _parse_assistant("just some prose with no labels at all")
    assert out[0] == "error"


def test_parse_assistant_invalid_json_reports_error() -> None:
    from yaya.plugins.strategy_react.plugin import _parse_assistant

    out = _parse_assistant("Thought: x\nAction: bash\nAction Input: not-json")
    assert out[0] == "error"


def test_parse_assistant_non_object_input_rejected() -> None:
    from yaya.plugins.strategy_react.plugin import _parse_assistant

    out = _parse_assistant('Thought: x\nAction: bash\nAction Input: ["cmd","ls"]')
    assert out[0] == "error"
    assert "JSON object" in out[1]


def test_parse_assistant_final_wins_over_action() -> None:
    """Paper treats Final Answer as strictly terminal."""
    from yaya.plugins.strategy_react.plugin import _parse_assistant

    mixed = "Thought: confused\nAction: bash\nAction Input: {}\nFinal Answer: done"
    out = _parse_assistant(mixed)
    assert out[0] == "final"
    assert out[1] == "done"

    # Final Answer that precedes an Action should NOT swallow the
    # trailing Action/Action Input lines into the answer body.
    final_then_action = "Final Answer: hi\nAction: bash\nAction Input: {}"
    out2 = _parse_assistant(final_then_action)
    assert out2[0] == "final"
    assert out2[1] == "hi"


async def test_missing_state_raises(tmp_path: Path) -> None:
    """Missing state key raises ValueError so the kernel synthesizes plugin.error."""
    bus = EventBus()
    ctx = _make_ctx(bus, tmp_path)
    await react_plugin.on_load(ctx)
    req = new_event(
        "strategy.decide.request",
        {},
        session_id="sess-missing",
        source="kernel",
    )
    with pytest.raises(ValueError, match="state"):
        await react_plugin.on_event(req, ctx)


async def test_provider_and_model_reads_instance_config(tmp_path: Path) -> None:
    """AC-06: strategy reads ``model`` from the active instance's config."""
    bus = EventBus()
    store = await ConfigStore.open(bus=bus, path=tmp_path / "config.db")
    try:
        await store.set("providers.prod.plugin", "llm-openai")
        await store.set("providers.prod.model", "gpt-4.1")
        await store.set("provider", "prod")

        ctx = _make_ctx(bus, tmp_path, store=store)
        provider, model = ReActStrategy._provider_and_model(ctx)
        assert provider == "prod"
        assert model == "gpt-4.1"
    finally:
        await store.close()


async def test_provider_and_model_switches_on_active_change(tmp_path: Path) -> None:
    """AC-02: flipping ``provider`` routes the next decision to the new instance."""
    bus = EventBus()
    store = await ConfigStore.open(bus=bus, path=tmp_path / "config.db")
    try:
        await store.set("providers.a.plugin", "llm-openai")
        await store.set("providers.a.model", "gpt-a")
        await store.set("providers.b.plugin", "llm-openai")
        await store.set("providers.b.model", "gpt-b")
        await store.set("provider", "a")

        ctx = _make_ctx(bus, tmp_path, store=store)
        provider, model = ReActStrategy._provider_and_model(ctx)
        assert (provider, model) == ("a", "gpt-a")

        await store.set("provider", "b")
        provider, model = ReActStrategy._provider_and_model(ctx)
        assert (provider, model) == ("b", "gpt-b")
    finally:
        await store.close()


async def test_provider_and_model_falls_back_to_first_instance(tmp_path: Path) -> None:
    """When ``provider`` is unset but instances exist, fall back to the first."""
    bus = EventBus()
    store = await ConfigStore.open(bus=bus, path=tmp_path / "config.db")
    try:
        await store.set("providers.only.plugin", "llm-openai")
        await store.set("providers.only.model", "gpt-only")
        ctx = _make_ctx(bus, tmp_path, store=store)
        provider, model = ReActStrategy._provider_and_model(ctx)
        assert (provider, model) == ("only", "gpt-only")
    finally:
        await store.close()


async def test_provider_and_model_echo_instance_gets_echo_model(tmp_path: Path) -> None:
    """An echo-backed instance with no explicit model falls through to ``echo``."""
    bus = EventBus()
    store = await ConfigStore.open(bus=bus, path=tmp_path / "config.db")
    try:
        await store.set("providers.local-echo.plugin", "llm-echo")
        await store.set("provider", "local-echo")
        ctx = _make_ctx(bus, tmp_path, store=store)
        provider, model = ReActStrategy._provider_and_model(ctx)
        assert (provider, model) == ("local-echo", "echo")
    finally:
        await store.close()


async def test_provider_and_model_unknown_active_falls_back_to_first(tmp_path: Path) -> None:
    """An ``active_id`` that does not resolve to an instance falls back to the first."""
    bus = EventBus()
    store = await ConfigStore.open(bus=bus, path=tmp_path / "config.db")
    try:
        await store.set("providers.only.plugin", "llm-openai")
        await store.set("providers.only.model", "gpt-only")
        await store.set("provider", "missing-id")
        ctx = _make_ctx(bus, tmp_path, store=store)
        provider, model = ReActStrategy._provider_and_model(ctx)
        assert (provider, model) == ("only", "gpt-only")
    finally:
        await store.close()


# Tool-call pairing tests (previously here) were function-calling
# specific and no longer apply — ReAct pairing is handled implicitly
# by "message after last assistant → llm" (covered by
# test_post_observation_returns_llm).


def test_system_prompt_forbids_content_in_thought() -> None:
    """The system prompt must flag Thought as internal-only and Final Answer as the sole user channel.

    Regression for #183: when the model treated ``Thought: <final reasoning>``
    literally and produced lists / tables inside Thought, the web UI hid the
    real answer inside the 'Show reasoning' fold. The prompt must now tell
    the model explicitly that Thought is not user-visible.
    """
    from yaya.plugins.strategy_react.plugin import _build_system_prompt

    prompt = _build_system_prompt([])

    # Hard-constrain Thought:
    assert "Internal scratchpad only." in prompt
    assert "collapsed 'Show reasoning' fold" in prompt
    assert "no content the user needs" in prompt
    # Final Answer carries every user-facing artefact:
    assert "every list," in prompt and "table," in prompt
    # Old loose phrasing must be gone.
    assert "<final reasoning>" not in prompt
    assert "<reason about what to do next>" not in prompt
