"""Pytest-bdd execution of specs/kernel-session-tape.spec scenarios.

The Gherkin text in ``features/kernel-session-tape.feature`` is the
authoritative BDD contract. Each scenario binds to step definitions
in this module via pytest-bdd; unmatched text fails the test with
``StepDefinitionNotFoundError`` so the spec text and scenario
implementations stay in lock-step.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from yaya.kernel import (
    EventBus,
    KernelContext,
    MemoryTapeStore,
    Session,
    SessionStore,
    after_last_anchor,
    install_session_persister,
    new_event,
)

scenarios("features/kernel-session-tape.feature")


@dataclass
class _Ctx:
    """Per-scenario state container."""

    loop: asyncio.AbstractEventLoop | None = None
    store: SessionStore | None = None
    session: Session | None = None
    child: Session | None = None
    persister: Any | None = None
    bus: EventBus | None = None
    workspace: Path | None = None
    second_workspace: Path | None = None
    second_session: Session | None = None
    anchor_entries: list[Any] = field(default_factory=lambda: [])
    parent_count_before: int = 0
    extras: dict[str, Any] = field(default_factory=lambda: {})
    plugin_errors: list[Any] = field(default_factory=lambda: [])


@pytest.fixture
def sctx(tmp_path: Path) -> _Ctx:
    loop = asyncio.new_event_loop()
    ctx = _Ctx(loop=loop, workspace=tmp_path / "ws")
    assert ctx.workspace is not None
    ctx.workspace.mkdir(parents=True, exist_ok=True)
    yield ctx
    if ctx.persister is not None:
        loop.run_until_complete(ctx.persister.stop())
    if ctx.bus is not None:
        loop.run_until_complete(ctx.bus.close())
    if ctx.store is not None:
        loop.run_until_complete(ctx.store.close())
    loop.close()


def _run(ctx: _Ctx, coro: Any) -> Any:
    assert ctx.loop is not None
    return ctx.loop.run_until_complete(coro)


# -- AC-01 ------------------------------------------------------------------


@given("no tape exists for workspace W and session S")
def _no_tape(sctx: _Ctx) -> None:
    sctx.store = SessionStore(store=MemoryTapeStore())


@when("the session store opens the session")
def _open(sctx: _Ctx) -> None:
    assert sctx.store is not None and sctx.workspace is not None
    sctx.session = _run(sctx, sctx.store.open(sctx.workspace, "S"))


@then("the tape has exactly one anchor named session slash start")
def _one_anchor(sctx: _Ctx) -> None:
    assert sctx.session is not None
    entries = _run(sctx, sctx.session.entries())
    anchors = [e for e in entries if e.kind == "anchor"]
    assert len(anchors) == 1
    assert anchors[0].payload.get("name") == "session/start"


@then("the anchor state includes owner human and workspace equal to the path")
def _anchor_state(sctx: _Ctx) -> None:
    assert sctx.session is not None
    entries = _run(sctx, sctx.session.entries())
    anchors = [e for e in entries if e.kind == "anchor"]
    state = anchors[0].payload.get("state") or {}
    assert state.get("owner") == "human"
    assert state.get("workspace") == str(sctx.workspace)


# -- AC-02 / AC-03 / AC-04 --------------------------------------------------


@given("an open session and a running bus persister")
def _open_with_persister(sctx: _Ctx) -> None:
    assert sctx.workspace is not None
    sctx.bus = EventBus()
    sctx.store = SessionStore(store=MemoryTapeStore())
    sctx.persister = _run(
        sctx,
        install_session_persister(
            bus=sctx.bus,
            store=sctx.store,
            workspace=sctx.workspace,
            kinds=["user.message.received", "assistant.message.done"],
        ),
    )
    sctx.session = _run(sctx, sctx.store.open(sctx.workspace, "default"))


@when(parsers.parse("a user message received event with content {content} is emitted"))
def _emit_user(sctx: _Ctx, content: str) -> None:
    assert sctx.bus is not None
    _run(
        sctx,
        sctx.bus.publish(
            new_event(
                "user.message.received",
                {"text": content},
                session_id="default",
                source="test",
            )
        ),
    )


@when(parsers.parse("an assistant message done event with content {content} is emitted"))
def _emit_assistant(sctx: _Ctx, content: str) -> None:
    assert sctx.bus is not None
    _run(
        sctx,
        sctx.bus.publish(
            new_event(
                "assistant.message.done",
                {"content": content, "tool_calls": []},
                session_id="default",
                source="kernel",
            )
        ),
    )


@then(parsers.parse("the tape contains a message entry role {role} content {content}"))
def _tape_contains_message(sctx: _Ctx, role: str, content: str) -> None:
    assert sctx.session is not None
    entries = _run(sctx, sctx.session.entries())
    messages = [e for e in entries if e.kind == "message"]
    assert any(m.payload.get("role") == role and m.payload.get("content") == content for m in messages), (
        f"no message {role!r}={content!r} in {[m.payload for m in messages]}"
    )


@then(parsers.parse("a message entry role {role} content {content}"))
def _tape_contains_message2(sctx: _Ctx, role: str, content: str) -> None:
    _tape_contains_message(sctx, role, content)


@when("ten assistant message delta events are emitted")
def _emit_ten_deltas(sctx: _Ctx) -> None:
    assert sctx.bus is not None
    for i in range(10):
        _run(
            sctx,
            sctx.bus.publish(
                new_event(
                    "assistant.message.delta",
                    {"content": f"chunk-{i}"},
                    session_id="default",
                    source="llm",
                )
            ),
        )


@then("no new tape entries land on the tape beyond the bootstrap anchor")
def _no_new_entries(sctx: _Ctx) -> None:
    assert sctx.session is not None
    entries = _run(sctx, sctx.session.entries())
    assert all(e.kind != "message" for e in entries)


@when("a user message received event is emitted with payload persist false")
def _emit_persist_false(sctx: _Ctx) -> None:
    assert sctx.bus is not None
    _run(
        sctx,
        sctx.bus.publish(
            new_event(
                "user.message.received",
                {"text": "skip", "persist": False},
                session_id="default",
                source="test",
            )
        ),
    )


@then("the tape contains no new message entry for that event")
def _no_message_entry(sctx: _Ctx) -> None:
    assert sctx.session is not None
    entries = _run(sctx, sctx.session.entries())
    assert all(e.kind != "message" for e in entries)


# -- AC-05 ------------------------------------------------------------------


@given("a session with ten tape entries")
def _ten_entries(sctx: _Ctx, tmp_path: Path) -> None:
    assert sctx.workspace is not None
    tapes_dir = tmp_path / "tapes"
    sctx.store = SessionStore(tapes_dir=tapes_dir)
    sctx.session = _run(sctx, sctx.store.open(sctx.workspace, "reset"))
    for i in range(10):
        _run(sctx, sctx.session.append_message("user", f"m-{i}"))
    sctx.extras["tapes_dir"] = tapes_dir


@when("reset with archive true is called")
def _reset_archive_true(sctx: _Ctx) -> None:
    assert sctx.session is not None
    sctx.extras["archive_path"] = _run(sctx, sctx.session.reset(archive=True))


@then("a jsonl archive file exists under tapes archive")
def _archive_file_exists(sctx: _Ctx) -> None:
    archive_path = sctx.extras.get("archive_path")
    assert archive_path is not None
    assert Path(archive_path).exists()


# -- AC-06 ------------------------------------------------------------------


@given("two workspaces with the same session id default")
def _two_workspaces(sctx: _Ctx, tmp_path: Path) -> None:
    sctx.workspace = tmp_path / "w1"
    sctx.second_workspace = tmp_path / "w2"
    sctx.workspace.mkdir()
    sctx.second_workspace.mkdir()
    sctx.store = SessionStore(store=MemoryTapeStore())


@when("both sessions are opened")
def _open_both(sctx: _Ctx) -> None:
    assert sctx.store is not None
    assert sctx.workspace is not None and sctx.second_workspace is not None
    sctx.session = _run(sctx, sctx.store.open(sctx.workspace, "default"))
    sctx.second_session = _run(sctx, sctx.store.open(sctx.second_workspace, "default"))


@then("their tape names differ")
def _names_differ(sctx: _Ctx) -> None:
    assert sctx.session is not None and sctx.second_session is not None
    assert sctx.session.tape_name != sctx.second_session.tape_name


@then("list sessions for each workspace returns one row")
def _one_row_each(sctx: _Ctx) -> None:
    assert sctx.store is not None
    assert sctx.workspace is not None and sctx.second_workspace is not None
    rows1 = _run(sctx, sctx.store.list_sessions(sctx.workspace))
    rows2 = _run(sctx, sctx.store.list_sessions(sctx.second_workspace))
    assert len(rows1) == 1
    assert len(rows2) == 1


# -- AC-07 ------------------------------------------------------------------


@given("a parent session with five entries")
def _parent_five(sctx: _Ctx) -> None:
    assert sctx.workspace is not None
    sctx.store = SessionStore(store=MemoryTapeStore())
    sctx.session = _run(sctx, sctx.store.open(sctx.workspace, "parent"))
    # Session open seeds (anchor, handoff-event); add messages until we hit 5.
    current = len(_run(sctx, sctx.session.entries()))
    for i in range(max(0, 5 - current)):
        _run(sctx, sctx.session.append_message("user", f"p-{i}"))
    sctx.parent_count_before = len(_run(sctx, sctx.session.entries()))


@when("the parent forks a child subagent")
def _parent_fork(sctx: _Ctx) -> None:
    assert sctx.session is not None
    sctx.child = sctx.session.fork("subagent")


@when("the child appends three entries")
def _child_three(sctx: _Ctx) -> None:
    assert sctx.child is not None
    for i in range(3):
        _run(sctx, sctx.child.append_message("user", f"c-{i}"))


@then("the parent tape still has five entries")
def _parent_still_five(sctx: _Ctx) -> None:
    assert sctx.session is not None
    entries = _run(sctx, sctx.session.entries())
    assert len(entries) == sctx.parent_count_before


@then("the child context sees eight entries")
def _child_eight(sctx: _Ctx) -> None:
    assert sctx.child is not None
    entries = _run(sctx, sctx.child.entries())
    assert len(entries) == sctx.parent_count_before + 3


# -- AC-08 ------------------------------------------------------------------


@given("a session with a compaction anchor followed by two new messages")
def _session_with_compaction(sctx: _Ctx) -> None:
    assert sctx.workspace is not None
    sctx.store = SessionStore(store=MemoryTapeStore())
    sctx.session = _run(sctx, sctx.store.open(sctx.workspace, "compact"))
    _run(sctx, sctx.session.append_message("user", "pre-1"))
    _run(sctx, sctx.session.handoff("compaction/0", state={"summary": "old"}))
    _run(sctx, sctx.session.append_message("user", "post-1"))
    _run(sctx, sctx.session.append_message("assistant", "post-2"))


@when("after last anchor is called")
def _after_last_anchor(sctx: _Ctx) -> None:
    assert sctx.session is not None
    sctx.anchor_entries = _run(
        sctx,
        after_last_anchor(sctx.session.manager, sctx.session.tape_name),
    )


@then("only the two post anchor messages are returned")
def _two_post(sctx: _Ctx) -> None:
    messages = [e for e in sctx.anchor_entries if e.kind == "message"]
    assert len(messages) == 2


# -- AC-09 ------------------------------------------------------------------


@given("a persister whose session store raises on append")
def _raising_persister(sctx: _Ctx) -> None:
    assert sctx.workspace is not None
    sctx.bus = EventBus()

    real_store = SessionStore(store=MemoryTapeStore())
    sctx.store = real_store

    class _Raising:
        async def open(self, workspace: Path, session_id: str) -> Session:
            sess = await real_store.open(workspace, session_id)

            async def _boom(*_a: Any, **_kw: Any) -> None:
                raise RuntimeError("boom")

            sess.append_message = _boom  # type: ignore[assignment]
            return sess

    async def capture(ev: Any) -> None:
        sctx.plugin_errors.append(ev)

    sctx.bus.subscribe("plugin.error", capture, source="test")
    sctx.persister = _run(
        sctx,
        install_session_persister(
            bus=sctx.bus,
            store=_Raising(),
            workspace=sctx.workspace,
            kinds=["user.message.received"],
        ),
    )


@when("a user message received event is emitted")
def _emit_one_user(sctx: _Ctx) -> None:
    assert sctx.bus is not None
    _run(
        sctx,
        sctx.bus.publish(
            new_event(
                "user.message.received",
                {"text": "boom"},
                session_id="default",
                source="test",
            )
        ),
    )
    # Give the kernel-session worker a tick to deliver the synthetic error.
    _run(sctx, asyncio.sleep(0.01))


@then(parsers.parse("a plugin error event is observed with source kernel session persister"))
def _plugin_error_observed(sctx: _Ctx) -> None:
    assert any(ev.payload.get("name") == "kernel-session-persister" for ev in sctx.plugin_errors), sctx.plugin_errors


@then("the bus keeps routing subsequent events")
def _bus_keeps_routing(sctx: _Ctx) -> None:
    # Pub one more event; absence of raise is proof.
    assert sctx.bus is not None
    _run(
        sctx,
        sctx.bus.publish(
            new_event(
                "user.message.received",
                {"text": "still alive"},
                session_id="default",
                source="test",
            )
        ),
    )


# -- AC-10 ------------------------------------------------------------------


@given("a kernel context wired with an open session")
def _ctx_with_session(sctx: _Ctx) -> None:
    assert sctx.workspace is not None
    sctx.store = SessionStore(store=MemoryTapeStore())
    sctx.session = _run(sctx, sctx.store.open(sctx.workspace, "default"))
    sctx.bus = EventBus()
    sctx.extras["kctx"] = KernelContext(
        bus=sctx.bus,
        logger=None,
        config={},
        state_dir=sctx.workspace,
        plugin_name="test-plugin",
        session=sctx.session,
    )


@then("the context session property returns that session")
def _ctx_returns_session(sctx: _Ctx) -> None:
    kctx = sctx.extras.get("kctx")
    assert kctx is not None
    assert kctx.session is sctx.session


@then("the property cannot be overwritten")
def _ctx_session_readonly(sctx: _Ctx) -> None:
    kctx = sctx.extras.get("kctx")
    assert kctx is not None
    with pytest.raises(AttributeError):
        kctx.session = None
