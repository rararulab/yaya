"""Tests for the approval runtime (#28).

Covers the AC scenarios in ``specs/kernel-approval.spec``:

* AC-01 approve → tool runs once.
* AC-02 reject → tool.error ``kind="rejected"`` with feedback.
* AC-03 ``approve_for_session`` → exactly one ``approval.request``
  emitted for two identical calls on the same session.
* AC-04 timeout → ``approval.cancelled`` + ``ApprovalCancelledError``.
* AC-05 routing on ``"kernel"`` session (deadlock regression,
  lesson #2).
* AC-06 shutdown cancels pending approvals.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

import pytest

from yaya.kernel.approval import (
    Approval,
    ApprovalCancelledError,
    ApprovalResult,
    ApprovalRuntime,
    _clear_approval_runtimes,
    _fingerprint,
    get_approval_runtime,
    install_approval_runtime,
    uninstall_approval_runtime,
)
from yaya.kernel.bus import EventBus
from yaya.kernel.events import Event, new_event
from yaya.kernel.plugin import KernelContext
from yaya.kernel.tool import (
    TextBlock,
    Tool,
    ToolOk,
    ToolReturnValue,
    _clear_tool_registry,
    install_dispatcher,
    register_tool,
)

# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_module_state() -> None:
    """Reset the tool registry and approval runtime registry per test."""
    _clear_tool_registry()
    _clear_approval_runtimes()


class _GatedTool(Tool):
    """A tool that gates through the approval runtime."""

    name: ClassVar[str] = "gated"
    description: ClassVar[str] = "A gated tool that requires approval."
    requires_approval: ClassVar[bool] = True

    payload: str = ""
    run_counter: ClassVar[list[int]] = []

    async def run(self, ctx: KernelContext) -> ToolReturnValue:
        type(self).run_counter.append(1)
        return ToolOk(brief=f"ran: {self.payload[:60]}", display=TextBlock(text=self.payload))


async def _emit_tool_call(
    bus: EventBus,
    *,
    tool_name: str,
    args: dict[str, Any],
    session_id: str,
    call_id: str,
) -> Event:
    """Publish a ``tool.call.request`` event with ``schema_version="v1"``."""
    ev = new_event(
        "tool.call.request",
        {
            "id": call_id,
            "name": tool_name,
            "args": args,
            "schema_version": "v1",
        },
        session_id=session_id,
        source="test",
    )
    await bus.publish(ev)
    return ev


# ---------------------------------------------------------------------------
# Basic unit tests — runtime state machine.
# ---------------------------------------------------------------------------


async def test_fingerprint_is_deterministic() -> None:
    """Two identical param dicts MUST produce the same fingerprint."""
    assert _fingerprint({"a": 1, "b": "x"}) == _fingerprint({"b": "x", "a": 1})
    assert _fingerprint({"a": 1}) != _fingerprint({"a": 2})


async def test_install_is_idempotent_per_bus() -> None:
    """install_approval_runtime returns the same instance on repeated calls."""
    bus = EventBus()
    try:
        r1 = await install_approval_runtime(bus)
        r2 = await install_approval_runtime(bus)
        assert r1 is r2
        assert get_approval_runtime(bus) is r1
    finally:
        await uninstall_approval_runtime(bus)
        await bus.close()


async def test_request_before_start_raises() -> None:
    """A runtime that has not been started must raise, not hang."""
    bus = EventBus()
    try:
        runtime = ApprovalRuntime(bus)
        approval = Approval(
            id="a1",
            tool_name="x",
            params={},
            brief="x",
            session_id="s",
        )
        with pytest.raises(RuntimeError, match="before start"):
            await runtime.request(approval)
    finally:
        await bus.close()


# ---------------------------------------------------------------------------
# AC-04 — timeout → approval.cancelled + ApprovalCancelledError.
# ---------------------------------------------------------------------------


async def test_timeout_emits_cancelled_and_rejects_tool() -> None:
    """AC-04 — no adapter subscribed → timeout → cancelled + error raised."""
    bus = EventBus()
    cancelled: list[Event] = []

    async def on_cancelled(ev: Event) -> None:
        cancelled.append(ev)

    bus.subscribe("approval.cancelled", on_cancelled, source="observer")
    try:
        runtime = await install_approval_runtime(bus, timeout_s=0.05)
        approval = Approval(
            id="a-timeout",
            tool_name="gated",
            params={"payload": "hi"},
            brief="gated: hi",
            session_id="sess-1",
        )
        with pytest.raises(ApprovalCancelledError) as exc_info:
            await runtime.request(approval)
        assert exc_info.value.approval_id == "a-timeout"
        assert exc_info.value.reason == "timeout"

        # ``approval.cancelled`` is async; give the bus a chance to drain.
        await asyncio.sleep(0.01)
        assert any(ev.payload == {"id": "a-timeout", "reason": "timeout"} for ev in cancelled)
    finally:
        await uninstall_approval_runtime(bus)
        await bus.close()


# ---------------------------------------------------------------------------
# AC-06 — shutdown cancels pending approvals.
# ---------------------------------------------------------------------------


async def test_stop_cancels_pending() -> None:
    """AC-06 — ApprovalRuntime.stop() flushes every pending future."""
    bus = EventBus()
    try:
        runtime = await install_approval_runtime(bus, timeout_s=5.0)
        approval = Approval(
            id="a-shutdown",
            tool_name="gated",
            params={"payload": "hi"},
            brief="gated: hi",
            session_id="sess-1",
        )

        async def wait_for_request() -> ApprovalResult:
            return await runtime.request(approval)

        task = asyncio.create_task(wait_for_request())
        # Let the task publish and register the future before we stop.
        await asyncio.sleep(0.01)
        await runtime.stop()
        with pytest.raises(ApprovalCancelledError) as exc_info:
            await task
        assert exc_info.value.reason == "shutdown"
    finally:
        await bus.close()


# ---------------------------------------------------------------------------
# AC-05 — envelope routing on the kernel session (lesson #2 regression).
# ---------------------------------------------------------------------------


async def test_approval_events_route_on_kernel_session() -> None:
    """AC-05 — approval.request MUST carry session_id="kernel"."""
    bus = EventBus()
    seen: list[Event] = []

    async def auto_approve(ev: Event) -> None:
        seen.append(ev)
        # Reply on the same routing session so the runtime's handler
        # actually picks it up. Adapters do the same.
        await bus.publish(
            new_event(
                "approval.response",
                {"id": ev.payload["id"], "response": "approve"},
                session_id=ev.session_id,
                source="test-adapter",
            )
        )

    bus.subscribe("approval.request", auto_approve, source="test-adapter")
    try:
        runtime = await install_approval_runtime(bus, timeout_s=5.0)
        approval = Approval(
            id="a-routing",
            tool_name="gated",
            params={"payload": "hi"},
            brief="gated: hi",
            session_id="sess-user",
        )
        result = await runtime.request(approval)
        assert result.response == "approve"
        assert len(seen) == 1
        # Routing session id is the reserved kernel id; the originating
        # tool-call session lives inside the Approval model, not the envelope.
        assert seen[0].session_id == "kernel"
        assert seen[0].payload["tool_name"] == "gated"
    finally:
        await uninstall_approval_runtime(bus)
        await bus.close()


# ---------------------------------------------------------------------------
# AC-01 — approve → tool runs once (end-to-end through the dispatcher).
# ---------------------------------------------------------------------------


async def _wait_for(events: list[Event], kind: str, *, count: int = 1, timeout: float = 2.0) -> list[Event]:
    """Poll ``events`` until ``count`` events with ``kind`` have arrived."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        matching = [ev for ev in events if ev.kind == kind]
        if len(matching) >= count:
            return matching
        await asyncio.sleep(0.01)
    matching = [ev for ev in events if ev.kind == kind]
    raise AssertionError(
        f"timed out waiting for {count}x {kind!r}; got {len(matching)} (events={[ev.kind for ev in events]})"
    )


async def test_approve_runs_tool_once() -> None:
    """AC-01 — approve → tool.run fires exactly once; tool.call.result ok=true."""
    _GatedTool.run_counter.clear()
    bus = EventBus()
    install_dispatcher(bus)
    register_tool(_GatedTool)
    observed: list[Event] = []

    async def observer(ev: Event) -> None:
        observed.append(ev)

    bus.subscribe("tool.call.result", observer, source="observer")
    bus.subscribe("tool.error", observer, source="observer")
    bus.subscribe("approval.request", observer, source="observer")

    async def auto_approve(ev: Event) -> None:
        await bus.publish(
            new_event(
                "approval.response",
                {"id": ev.payload["id"], "response": "approve"},
                session_id=ev.session_id,
                source="adapter",
            )
        )

    bus.subscribe("approval.request", auto_approve, source="adapter")
    try:
        await install_approval_runtime(bus, timeout_s=5.0)

        await _emit_tool_call(
            bus,
            tool_name="gated",
            args={"payload": "hello"},
            session_id="sess-A",
            call_id="c1",
        )
        results = await _wait_for(observed, "tool.call.result")
        assert results[0].payload["ok"] is True
        assert len(_GatedTool.run_counter) == 1
        # And an approval.request was observed (proves the gate fired).
        assert any(ev.kind == "approval.request" for ev in observed)
    finally:
        await uninstall_approval_runtime(bus)
        await bus.close()


# ---------------------------------------------------------------------------
# AC-02 — reject → tool.error kind="rejected" with feedback.
# ---------------------------------------------------------------------------


async def test_reject_blocks_tool() -> None:
    """AC-02 — reject → tool.run never fires; tool.error carries feedback."""
    _GatedTool.run_counter.clear()
    bus = EventBus()
    install_dispatcher(bus)
    register_tool(_GatedTool)
    observed: list[Event] = []

    async def observer(ev: Event) -> None:
        observed.append(ev)

    bus.subscribe("tool.call.result", observer, source="observer")
    bus.subscribe("tool.error", observer, source="observer")

    async def auto_reject(ev: Event) -> None:
        await bus.publish(
            new_event(
                "approval.response",
                {
                    "id": ev.payload["id"],
                    "response": "reject",
                    "feedback": "no thanks",
                },
                session_id=ev.session_id,
                source="adapter",
            )
        )

    bus.subscribe("approval.request", auto_reject, source="adapter")
    try:
        await install_approval_runtime(bus, timeout_s=5.0)

        await _emit_tool_call(
            bus,
            tool_name="gated",
            args={"payload": "hello"},
            session_id="sess-B",
            call_id="c2",
        )

        errors = await _wait_for(observed, "tool.error")
        assert errors[0].payload["kind"] == "rejected"
        # Rejection feedback MUST flow into the brief so the agent loop
        # and UI see why the call was refused.
        assert "no thanks" in errors[0].payload["brief"]
        assert _GatedTool.run_counter == []
        # No successful tool.call.result.
        assert not any(ev.kind == "tool.call.result" for ev in observed)
    finally:
        await uninstall_approval_runtime(bus)
        await bus.close()


# ---------------------------------------------------------------------------
# AC-03 — approve_for_session short-circuits future prompts.
# ---------------------------------------------------------------------------


async def test_approve_for_session_short_circuits() -> None:
    """AC-03 — two identical calls → exactly ONE approval.request emitted."""
    _GatedTool.run_counter.clear()
    bus = EventBus()
    install_dispatcher(bus)
    register_tool(_GatedTool)
    request_events: list[Event] = []
    result_events: list[Event] = []

    async def count_requests(ev: Event) -> None:
        request_events.append(ev)

    async def count_results(ev: Event) -> None:
        result_events.append(ev)

    bus.subscribe("approval.request", count_requests, source="observer")
    bus.subscribe("tool.call.result", count_results, source="observer")

    async def adapter_approve_for_session(ev: Event) -> None:
        await bus.publish(
            new_event(
                "approval.response",
                {
                    "id": ev.payload["id"],
                    "response": "approve_for_session",
                },
                session_id=ev.session_id,
                source="adapter",
            )
        )

    bus.subscribe("approval.request", adapter_approve_for_session, source="adapter")
    try:
        await install_approval_runtime(bus, timeout_s=5.0)

        # Two identical calls on the same session.
        await _emit_tool_call(
            bus,
            tool_name="gated",
            args={"payload": "hello"},
            session_id="sess-SC",
            call_id="c3a",
        )
        await _wait_for(result_events, "tool.call.result", count=1)

        await _emit_tool_call(
            bus,
            tool_name="gated",
            args={"payload": "hello"},
            session_id="sess-SC",
            call_id="c3b",
        )
        await _wait_for(result_events, "tool.call.result", count=2)

        assert len(request_events) == 1, (
            f"expected exactly ONE approval.request for two identical calls, got {len(request_events)}"
        )
        assert len(_GatedTool.run_counter) == 2
        for ev in result_events:
            assert ev.payload["ok"] is True
    finally:
        await uninstall_approval_runtime(bus)
        await bus.close()


# ---------------------------------------------------------------------------
# Approve-for-session cache does NOT cross sessions.
# ---------------------------------------------------------------------------


async def test_approve_for_session_is_per_session() -> None:
    """Different session ids must NOT share the approve-for-session cache."""
    _GatedTool.run_counter.clear()
    bus = EventBus()
    install_dispatcher(bus)
    register_tool(_GatedTool)
    request_events: list[Event] = []
    result_events: list[Event] = []

    async def count_requests(ev: Event) -> None:
        request_events.append(ev)

    async def count_results(ev: Event) -> None:
        result_events.append(ev)

    bus.subscribe("approval.request", count_requests, source="observer")
    bus.subscribe("tool.call.result", count_results, source="observer")

    async def adapter(ev: Event) -> None:
        await bus.publish(
            new_event(
                "approval.response",
                {
                    "id": ev.payload["id"],
                    "response": "approve_for_session",
                },
                session_id=ev.session_id,
                source="adapter",
            )
        )

    bus.subscribe("approval.request", adapter, source="adapter")
    try:
        await install_approval_runtime(bus, timeout_s=5.0)

        await _emit_tool_call(
            bus,
            tool_name="gated",
            args={"payload": "hello"},
            session_id="sess-A",
            call_id="cA",
        )
        await _wait_for(result_events, "tool.call.result", count=1)
        await _emit_tool_call(
            bus,
            tool_name="gated",
            args={"payload": "hello"},
            session_id="sess-B",
            call_id="cB",
        )
        await _wait_for(result_events, "tool.call.result", count=2)

        # Each session prompts at least once.
        assert len(request_events) == 2
        # And the tool actually ran on each session — guards against a
        # regression where the cache silently swallows the second call's
        # ``run`` invocation (lesson #10: silent no-op primitives).
        assert len(_GatedTool.run_counter) == 2
    finally:
        await uninstall_approval_runtime(bus)
        await bus.close()


# ---------------------------------------------------------------------------
# Runtime correlation: response for unknown id is dropped, not crashed.
# ---------------------------------------------------------------------------


async def test_unknown_response_id_is_dropped(caplog: pytest.LogCaptureFixture) -> None:
    """A stray approval.response with no pending future is logged and ignored."""
    import logging as _logging

    bus = EventBus()
    try:
        runtime = await install_approval_runtime(bus, timeout_s=5.0)
        with caplog.at_level(_logging.WARNING, logger="yaya.kernel.approval"):
            await bus.publish(
                new_event(
                    "approval.response",
                    {"id": "ghost", "response": "approve"},
                    session_id="kernel",
                    source="adapter",
                )
            )
        # No pending futures after the stray delivery.
        assert not runtime._pending
        assert any("unknown/resolved" in rec.getMessage() for rec in caplog.records)
    finally:
        await uninstall_approval_runtime(bus)
        await bus.close()


# ---------------------------------------------------------------------------
# Runtime correlation: invalid response value is coerced to reject.
# ---------------------------------------------------------------------------


async def test_start_stop_idempotent() -> None:
    """start() and stop() can be called multiple times safely."""
    bus = EventBus()
    try:
        runtime = ApprovalRuntime(bus)
        await runtime.start()
        await runtime.start()  # second start: no-op branch.
        await runtime.stop()
        await runtime.stop()  # second stop: no-op branch.
    finally:
        await bus.close()


async def test_non_string_id_is_dropped(caplog: pytest.LogCaptureFixture) -> None:
    """approval.response whose id is not a string is dropped with a warning."""
    import logging as _logging

    bus = EventBus()
    try:
        await install_approval_runtime(bus, timeout_s=5.0)
        with caplog.at_level(_logging.WARNING, logger="yaya.kernel.approval"):
            await bus.publish(
                new_event(
                    "approval.response",
                    {"id": 42, "response": "approve"},  # type: ignore[typeddict-item]
                    session_id="kernel",
                    source="adapter",
                )
            )
        assert any("without string 'id'" in rec.getMessage() for rec in caplog.records)
    finally:
        await uninstall_approval_runtime(bus)
        await bus.close()


async def test_invalid_response_coerces_to_reject() -> None:
    """An approval.response with a garbage ``response`` flips to reject."""
    bus = EventBus()
    try:
        runtime = await install_approval_runtime(bus, timeout_s=5.0)
        approval = Approval(
            id="a-bad",
            tool_name="x",
            params={},
            brief="x",
            session_id="s",
        )

        async def bad_adapter(ev: Event) -> None:
            await bus.publish(
                new_event(
                    "approval.response",
                    {"id": ev.payload["id"], "response": "yolo"},
                    session_id=ev.session_id,
                    source="adapter",
                )
            )

        bus.subscribe("approval.request", bad_adapter, source="adapter")
        result = await runtime.request(approval)
        assert result.response == "reject"
    finally:
        await uninstall_approval_runtime(bus)
        await bus.close()


# ---------------------------------------------------------------------------
# P2-1 — stop() emits approval.cancelled(reason="shutdown") for each pending.
# ---------------------------------------------------------------------------


async def test_stop_emits_shutdown_cancelled_for_each_pending() -> None:
    """Every pending approval at shutdown produces a matching cancelled event.

    Pre-fix the ``"shutdown"`` literal in :class:`ApprovalCancelledError`
    was reachable but the ``approval.cancelled`` envelope's matching
    literal was unreachable: ``stop()`` only set the future's exception
    and skipped emit. Adapters that watched ``approval.cancelled`` to
    withdraw stale prompts therefore never saw shutdown reasons.
    """
    bus = EventBus()
    cancelled: list[Event] = []

    async def on_cancelled(ev: Event) -> None:
        cancelled.append(ev)

    bus.subscribe("approval.cancelled", on_cancelled, source="observer")
    try:
        runtime = await install_approval_runtime(bus, timeout_s=5.0)
        approvals = [
            Approval(id=f"a-shut-{i}", tool_name="gated", params={"i": i}, brief="x", session_id="s") for i in range(2)
        ]

        async def wait_for(a: Approval) -> ApprovalResult:
            return await runtime.request(a)

        tasks = [asyncio.create_task(wait_for(a)) for a in approvals]
        # Let both requests register their futures before shutdown.
        await asyncio.sleep(0.01)
        await runtime.stop()
        for task in tasks:
            with pytest.raises(ApprovalCancelledError) as exc_info:
                await task
            assert exc_info.value.reason == "shutdown"

        # Drain so the publish bookkeeping settles before we assert.
        await asyncio.sleep(0.01)
        observed_ids = {ev.payload["id"] for ev in cancelled if ev.payload.get("reason") == "shutdown"}
        assert observed_ids == {"a-shut-0", "a-shut-1"}
    finally:
        await bus.close()


# ---------------------------------------------------------------------------
# P2-2 — subclass pre_approve crash translates to tool.error(kind="rejected").
# ---------------------------------------------------------------------------


class _CrashyApprovalTool(Tool):
    """A tool whose ``pre_approve`` raises an unexpected exception.

    The dispatcher must translate the failure into a terminal
    ``tool.error`` so the caller's ``tool.call.request`` future does not
    orphan (lesson #29).
    """

    name: ClassVar[str] = "crashy"
    description: ClassVar[str] = "pre_approve always crashes."
    requires_approval: ClassVar[bool] = True

    async def pre_approve(self, ctx: KernelContext, *, session_id: str) -> bool:
        _ = ctx, session_id
        raise RuntimeError("boom")

    async def run(self, ctx: KernelContext) -> ToolReturnValue:  # pragma: no cover - never reached
        _ = ctx
        return ToolOk(brief="unreachable", display=TextBlock(text="never"))


async def test_pre_approve_crash_translates_to_tool_error() -> None:
    """An arbitrary subclass exception in ``pre_approve`` must surface as tool.error."""
    bus = EventBus()
    install_dispatcher(bus)
    register_tool(_CrashyApprovalTool)
    errors: list[Event] = []

    async def collect(ev: Event) -> None:
        errors.append(ev)

    bus.subscribe("tool.error", collect, source="observer")
    try:
        await install_approval_runtime(bus, timeout_s=5.0)
        await _emit_tool_call(
            bus,
            tool_name="crashy",
            args={},
            session_id="sess-crash",
            call_id="c-crash",
        )

        # Wait for the dispatcher's error emit. No tool.call.result lands
        # because the dispatcher returns early after emitting tool.error.
        deadline = asyncio.get_running_loop().time() + 2.0
        while asyncio.get_running_loop().time() < deadline:
            if errors:
                break
            await asyncio.sleep(0.01)
        assert errors, "dispatcher must emit tool.error when pre_approve crashes"
        payload = errors[0].payload
        assert payload["kind"] == "rejected"
        assert "pre_approve crashed" in payload["brief"]
        assert payload["id"] == "c-crash"
    finally:
        await uninstall_approval_runtime(bus)
        await bus.close()


# ---------------------------------------------------------------------------
# P2-3 — install with a different timeout raises rather than silently dropping.
# ---------------------------------------------------------------------------


async def test_install_with_different_timeout_raises() -> None:
    """Re-installing with a non-matching ``timeout_s`` must fail loud."""
    bus = EventBus()
    try:
        await install_approval_runtime(bus, timeout_s=5.0)
        # Same value (or unspecified) is fine — idempotent path.
        await install_approval_runtime(bus, timeout_s=5.0)
        await install_approval_runtime(bus)
        # Different value must raise.
        with pytest.raises(RuntimeError, match="already installed"):
            await install_approval_runtime(bus, timeout_s=10.0)
    finally:
        await uninstall_approval_runtime(bus)
        await bus.close()


# ---------------------------------------------------------------------------
# AC-05 (extended) — cross-session round-trip end-to-end through dispatcher.
# ---------------------------------------------------------------------------


async def test_cross_session_roundtrip_does_not_deadlock() -> None:
    """End-to-end: tool call on sess-A, approval response on kernel session.

    Reproduces the failure mode lesson #2 was written for. A naïve
    implementation that routes ``approval.response`` on the originating
    tool-call session deadlocks here because the session worker is
    blocked inside ``runtime.request``. The runtime routes responses on
    ``"kernel"`` so a separate worker resolves the future and the
    original session worker can wake up to publish ``tool.call.result``.
    """
    _GatedTool.run_counter.clear()
    bus = EventBus()
    install_dispatcher(bus)
    register_tool(_GatedTool)
    results: list[Event] = []

    async def collect_results(ev: Event) -> None:
        results.append(ev)

    bus.subscribe("tool.call.result", collect_results, source="observer")

    async def adapter(ev: Event) -> None:
        # Adapter receives approval.request on session "kernel" and
        # publishes approval.response on the same routing session.
        assert ev.session_id == "kernel"
        await bus.publish(
            new_event(
                "approval.response",
                {"id": ev.payload["id"], "response": "approve"},
                session_id="kernel",
                source="adapter",
            )
        )

    bus.subscribe("approval.request", adapter, source="adapter")
    try:
        await install_approval_runtime(bus, timeout_s=5.0)
        await _emit_tool_call(
            bus,
            tool_name="gated",
            args={"payload": "cross-session"},
            session_id="sess-A",
            call_id="c-cross",
        )

        async def wait_for_result() -> Event:
            while True:
                for ev in results:
                    if ev.payload.get("id") == "c-cross":
                        return ev
                await asyncio.sleep(0.01)

        ev = await asyncio.wait_for(wait_for_result(), timeout=1.0)
        assert ev.payload["ok"] is True
        # The result rides the ORIGINATING tool-call session, not the
        # approval routing session — that is the contract end-to-end.
        assert ev.session_id == "sess-A"
        assert len(_GatedTool.run_counter) == 1
    finally:
        await uninstall_approval_runtime(bus)
        await bus.close()
