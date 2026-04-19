"""Pytest-bdd execution of specs/kernel-compaction.spec scenarios.

The Gherkin text in ``features/kernel-compaction.feature`` is the
authoritative BDD contract. Each scenario binds to step definitions
in this module via pytest-bdd; unmatched text fails the test with
``StepDefinitionNotFoundError`` so the spec text and scenario
implementations stay in lock-step.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when
from typer.testing import CliRunner

from yaya.kernel import (
    COMPACTION_ANCHOR_KIND,
    EventBus,
    MemoryTapeStore,
    Session,
    SessionStore,
    compact_session,
    default_tape_context,
    estimate_text_tokens,
    install_compaction_manager,
    new_event,
    select_messages,
)

scenarios("features/kernel-compaction.feature")


class _FakeSummarizer:
    """Echoes the number of entries it summarised."""

    def __init__(self, tag: str = "SUMMARY") -> None:
        self.tag = tag
        self.calls: list[int] = []

    async def summarize(self, entries: list[Any], target_tokens: int) -> str:
        self.calls.append(len(entries))
        return f"{self.tag}:{len(entries)}"


class _ExplodingSummarizer:
    async def summarize(self, entries: list[Any], target_tokens: int) -> str:
        raise RuntimeError("summariser down")


@dataclass
class _Ctx:
    loop: asyncio.AbstractEventLoop | None = None
    store: SessionStore | None = None
    session: Session | None = None
    parent: Session | None = None
    bus: EventBus | None = None
    summarizer: _FakeSummarizer | None = None
    summary_result: str | None = None
    estimate_first: int = 0
    estimate_second: int = 0
    entries_snapshot: list[Any] = field(default_factory=lambda: [])
    pre_count: int = 0
    messages: list[dict[str, Any]] = field(default_factory=lambda: [])
    failed_events: list[Any] = field(default_factory=lambda: [])
    parent_entry_count_before: int = 0
    manager: Any | None = None
    slow_gate: asyncio.Event | None = None
    slow_calls: int = 0
    cli_result: Any | None = None
    workspace: Path | None = None


@pytest.fixture
def cctx(tmp_path: Path) -> _Ctx:
    loop = asyncio.new_event_loop()
    ctx = _Ctx(loop=loop, workspace=tmp_path / "ws")
    assert ctx.workspace is not None
    ctx.workspace.mkdir(parents=True, exist_ok=True)
    yield ctx
    if ctx.manager is not None:
        loop.run_until_complete(ctx.manager.stop())
    if ctx.bus is not None:
        loop.run_until_complete(ctx.bus.close())
    if ctx.store is not None:
        loop.run_until_complete(ctx.store.close())
    loop.close()


def _run(ctx: _Ctx, coro: Any) -> Any:
    assert ctx.loop is not None
    return ctx.loop.run_until_complete(coro)


# -- AC-01 ------------------------------------------------------------------


@given("a session with five user messages since the last anchor")
def _five_messages(cctx: _Ctx) -> None:
    async def _prepare() -> None:
        cctx.store = SessionStore(store=MemoryTapeStore())
        assert cctx.workspace is not None
        cctx.session = await cctx.store.open(cctx.workspace, "ac01")
        for i in range(5):
            await cctx.session.append_message("user", f"msg-{i}")

    _run(cctx, _prepare())


@when("session compact runs with a fake summariser")
def _compact_fake(cctx: _Ctx) -> None:
    async def _do() -> None:
        assert cctx.session is not None
        cctx.summarizer = _FakeSummarizer()
        cctx.summary_result = await cctx.session.compact(cctx.summarizer)

    _run(cctx, _do())


@then("the tape has a new anchor with state kind equal to compaction")
def _check_anchor_compaction(cctx: _Ctx) -> None:
    async def _assert() -> None:
        assert cctx.session is not None
        entries = await cctx.session.entries()
        anchors = [e for e in entries if e.kind == "anchor"]
        state = anchors[-1].payload.get("state")
        assert isinstance(state, dict)
        assert state.get("kind") == COMPACTION_ANCHOR_KIND

    _run(cctx, _assert())


@then("the anchor state carries the summary string")
def _check_summary_string(cctx: _Ctx) -> None:
    async def _assert() -> None:
        assert cctx.session is not None
        entries = await cctx.session.entries()
        anchors = [e for e in entries if e.kind == "anchor"]
        state = anchors[-1].payload.get("state")
        assert isinstance(state, dict)
        assert state.get("summary") == cctx.summary_result
        assert cctx.summary_result  # non-empty

    _run(cctx, _assert())


# -- AC-02 ------------------------------------------------------------------


@given("a fresh session with only the bootstrap anchor")
def _fresh_session(cctx: _Ctx) -> None:
    async def _prepare() -> None:
        cctx.store = SessionStore(store=MemoryTapeStore())
        assert cctx.workspace is not None
        cctx.session = await cctx.store.open(cctx.workspace, "ac02")

    _run(cctx, _prepare())


@then("the summary string is empty")
def _empty_summary(cctx: _Ctx) -> None:
    assert cctx.summary_result == ""


@then("the tape still has only the bootstrap anchor")
def _only_bootstrap(cctx: _Ctx) -> None:
    async def _assert() -> None:
        assert cctx.session is not None
        entries = await cctx.session.entries()
        anchors = [e for e in entries if e.kind == "anchor"]
        assert len(anchors) == 1
        assert anchors[0].payload.get("name") == "session/start"

    _run(cctx, _assert())


# -- AC-03 ------------------------------------------------------------------


@given("a fixed list of tape entries")
def _fixed_entries(cctx: _Ctx) -> None:
    async def _prepare() -> None:
        cctx.store = SessionStore(store=MemoryTapeStore())
        assert cctx.workspace is not None
        cctx.session = await cctx.store.open(cctx.workspace, "ac03")
        for i in range(4):
            await cctx.session.append_message("user", f"fixed-{i}")
        cctx.entries_snapshot = await cctx.session.entries()

    _run(cctx, _prepare())


@when("estimate text tokens runs twice over the same entries")
def _estimate_twice(cctx: _Ctx) -> None:
    cctx.estimate_first = estimate_text_tokens(cctx.entries_snapshot)
    cctx.estimate_second = estimate_text_tokens(cctx.entries_snapshot)


@then("both calls return the same positive integer")
def _same_positive(cctx: _Ctx) -> None:
    assert cctx.estimate_first == cctx.estimate_second
    assert cctx.estimate_first > 0


# -- AC-04 ------------------------------------------------------------------


@given("a session with two pre compaction messages then a compaction anchor")
def _two_msgs_plus_compaction(cctx: _Ctx) -> None:
    async def _prepare() -> None:
        cctx.store = SessionStore(store=MemoryTapeStore())
        assert cctx.workspace is not None
        cctx.session = await cctx.store.open(cctx.workspace, "ac04")
        await cctx.session.append_message("user", "pre-1")
        await cctx.session.append_message("assistant", "pre-2")
        await cctx.session.compact(_FakeSummarizer(tag="GIST"))

    _run(cctx, _prepare())


@when("default context is rendered from the tape")
def _render_default_context(cctx: _Ctx) -> None:
    async def _do() -> None:
        assert cctx.session is not None
        entries = await cctx.session.entries()
        cctx.messages = list(select_messages(entries, default_tape_context()))

    _run(cctx, _do())


@then("the returned messages start with a role system summary message")
def _starts_with_system_summary(cctx: _Ctx) -> None:
    system_msgs = [m for m in cctx.messages if m.get("role") == "system"]
    assert len(system_msgs) == 1
    assert "GIST" in system_msgs[0]["content"]


# -- AC-05 ------------------------------------------------------------------


@given("a session with one pre compaction message")
def _one_pre_message(cctx: _Ctx) -> None:
    async def _prepare() -> None:
        cctx.store = SessionStore(store=MemoryTapeStore())
        cctx.bus = EventBus()

        async def _sink(ev: Any) -> None:
            cctx.failed_events.append(ev)

        cctx.bus.subscribe("session.compaction.failed", _sink, source="test")
        assert cctx.workspace is not None
        cctx.session = await cctx.store.open(cctx.workspace, "ac05")
        await cctx.session.append_message("user", "please-fail")
        cctx.pre_count = len(await cctx.session.entries())

    _run(cctx, _prepare())


@when("session compact runs with an exploding summariser")
def _run_exploding(cctx: _Ctx) -> None:
    async def _do() -> None:
        assert cctx.session is not None
        with pytest.raises(RuntimeError):
            await compact_session(cctx.session, _ExplodingSummarizer(), bus=cctx.bus)

    _run(cctx, _do())


@then("a session compaction failed event is emitted")
def _failed_emitted(cctx: _Ctx) -> None:
    async def _drain() -> None:
        assert cctx.bus is not None
        await cctx.bus.close()
        cctx.bus = None  # prevent double-close in fixture

    _run(cctx, _drain())
    assert any(ev.kind == "session.compaction.failed" for ev in cctx.failed_events)


@then("no compaction anchor is appended")
def _no_anchor_appended(cctx: _Ctx) -> None:
    async def _assert() -> None:
        assert cctx.session is not None
        entries = await cctx.session.entries()
        assert len(entries) == cctx.pre_count

    _run(cctx, _assert())


# -- AC-06 ------------------------------------------------------------------


@given("a running compaction manager with a low threshold")
def _running_manager_low(cctx: _Ctx) -> None:
    async def _prepare() -> None:
        cctx.store = SessionStore(store=MemoryTapeStore())
        cctx.bus = EventBus()
        cctx.summarizer = _FakeSummarizer()
        assert cctx.workspace is not None
        cctx.session = await cctx.store.open(cctx.workspace, "ac06")
        for _ in range(5):
            await cctx.session.append_message("user", "x" * 40)
        cctx.manager = await install_compaction_manager(
            bus=cctx.bus,
            store=cctx.store,
            summarizer=cctx.summarizer,
            workspace=cctx.workspace,
            kinds=["user.message.received"],
            threshold_tokens=10,
            target_tokens=100,
        )

    _run(cctx, _prepare())


@when("a user message received event pushes the tape past threshold")
def _publish_trigger(cctx: _Ctx) -> None:
    async def _do() -> None:
        assert cctx.bus is not None
        await cctx.bus.publish(
            new_event(
                "user.message.received",
                {"text": "trigger"},
                session_id="ac06",
                source="test",
            ),
        )
        # Scheduling rounds: the manager creates a background task.
        for _ in range(10):
            assert cctx.summarizer is not None
            if cctx.summarizer.calls:
                break
            await asyncio.sleep(0.05)

    _run(cctx, _do())


@then("the summariser is invoked at least once")
def _summariser_called(cctx: _Ctx) -> None:
    assert cctx.summarizer is not None
    assert cctx.summarizer.calls


# -- AC-07 ------------------------------------------------------------------


@given("a compaction manager with a slow summariser")
def _slow_manager(cctx: _Ctx) -> None:
    async def _prepare() -> None:
        cctx.store = SessionStore(store=MemoryTapeStore())
        cctx.bus = EventBus()
        cctx.slow_gate = asyncio.Event()
        assert cctx.workspace is not None
        cctx.session = await cctx.store.open(cctx.workspace, "ac07")
        for _ in range(4):
            await cctx.session.append_message("user", "y" * 60)

        gate = cctx.slow_gate

        class _Slow:
            async def summarize(self, entries: list[Any], target_tokens: int) -> str:
                cctx.slow_calls += 1
                await gate.wait()
                return "slow"

        cctx.manager = await install_compaction_manager(
            bus=cctx.bus,
            store=cctx.store,
            summarizer=_Slow(),
            workspace=cctx.workspace,
            kinds=["user.message.received"],
            threshold_tokens=10,
            target_tokens=100,
        )

    _run(cctx, _prepare())


@when("three user message received events arrive back to back")
def _three_events(cctx: _Ctx) -> None:
    async def _do() -> None:
        assert cctx.bus is not None
        for i in range(3):
            await cctx.bus.publish(
                new_event(
                    "user.message.received",
                    {"text": f"trig-{i}"},
                    session_id="ac07",
                    source="test",
                ),
            )
        await asyncio.sleep(0.2)

    _run(cctx, _do())


@then("the summariser is invoked exactly once while the first call is pending")
def _exactly_once(cctx: _Ctx) -> None:
    assert cctx.slow_calls == 1
    assert cctx.slow_gate is not None
    cctx.slow_gate.set()


# -- AC-08 ------------------------------------------------------------------


@given("a parent session with three user messages")
def _parent_three(cctx: _Ctx) -> None:
    async def _prepare() -> None:
        cctx.store = SessionStore(store=MemoryTapeStore())
        assert cctx.workspace is not None
        cctx.parent = await cctx.store.open(cctx.workspace, "ac08")
        for i in range(3):
            await cctx.parent.append_message("user", f"p-{i}")
        cctx.parent_entry_count_before = len(await cctx.parent.entries())

    _run(cctx, _prepare())


@when("the parent forks a child and the child compacts")
def _parent_fork_child_compact(cctx: _Ctx) -> None:
    async def _do() -> None:
        assert cctx.parent is not None
        child = cctx.parent.fork("ac08-child")
        await child.compact(_FakeSummarizer())

    _run(cctx, _do())


@then("the parent tape entry count is unchanged")
def _parent_unchanged(cctx: _Ctx) -> None:
    async def _assert() -> None:
        assert cctx.parent is not None
        assert len(await cctx.parent.entries()) == cctx.parent_entry_count_before

    _run(cctx, _assert())


# -- AC-09 ------------------------------------------------------------------


@given("a seeded session with a couple of messages")
def _seeded_session(
    cctx: _Ctx,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The CLI reads $YAYA_STATE_DIR for the default file-backed tape dir.
    monkeypatch.setenv("YAYA_STATE_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    tapes_dir = tmp_path / "tapes"

    async def _seed() -> None:
        store = SessionStore(tapes_dir=tapes_dir)
        try:
            s = await store.open(tmp_path, "default")
            await s.append_message("user", "hello")
            await s.append_message("assistant", "hi there")
        finally:
            await store.close()

    _run(cctx, _seed())


@when(parsers.parse("yaya json session compact default runs"))
def _invoke_cli(cctx: _Ctx) -> None:
    from yaya.cli import app

    runner = CliRunner()
    cctx.cli_result = runner.invoke(
        app,
        ["--json", "session", "compact", "default"],
    )


@then("the exit code is zero")
def _exit_zero(cctx: _Ctx) -> None:
    assert cctx.cli_result is not None
    assert cctx.cli_result.exit_code == 0, cctx.cli_result.stdout


@then("the json output has action equal to session dot compact")
def _action_eq(cctx: _Ctx) -> None:
    assert cctx.cli_result is not None
    payload = json.loads(cctx.cli_result.stdout)
    assert payload["action"] == "session.compact"
