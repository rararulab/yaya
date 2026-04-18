"""Tests for the tool contract v1 — pydantic params, envelope, registry, dispatcher."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from pydantic import Field, TypeAdapter

from yaya.kernel.bus import EventBus
from yaya.kernel.events import Event, new_event
from yaya.kernel.plugin import KernelContext
from yaya.kernel.tool import (
    DisplayBlock,
    JsonBlock,
    MarkdownBlock,
    TextBlock,
    Tool,
    ToolAlreadyRegisteredError,
    ToolError,
    ToolOk,
    ToolReturnValue,
    _clear_tool_registry,
    dispatch,
    get_tool,
    install_dispatcher,
    mark_legacy_tool,
    register_tool,
    registered_tools,
)

# ---------------------------------------------------------------------------
# Sample tools used across tests.
# ---------------------------------------------------------------------------


class EchoTool(Tool):
    """Echoes the input ``text`` as a TextBlock."""

    name: ClassVar[str] = "echo"
    description: ClassVar[str] = "Echo the input text."

    text: str

    async def run(self, ctx: KernelContext) -> ToolReturnValue:
        return ToolOk(
            brief=f"echo: {self.text[:60]}",
            display=TextBlock(text=self.text),
        )


class CountTool(Tool):
    """Typed int field — used to test validation rejection."""

    name: ClassVar[str] = "count"
    description: ClassVar[str] = "Count up to n."

    count: int = Field(ge=0)

    async def run(self, ctx: KernelContext) -> ToolReturnValue:
        return ToolOk(brief=f"n={self.count}", display=TextBlock(text=str(self.count)))


class GatedTool(Tool):
    """requires_approval=True; pre_approve returns False → rejected."""

    name: ClassVar[str] = "gated"
    description: ClassVar[str] = "A tool that always refuses approval."
    requires_approval: ClassVar[bool] = True

    async def pre_approve(self, ctx: KernelContext) -> bool:
        return False

    async def run(self, ctx: KernelContext) -> ToolReturnValue:
        # Should never be reached; the dispatcher rejects first.
        return ToolOk(brief="should-not-run", display=TextBlock(text=""))


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    """Reset the module-level tool registry between tests."""
    _clear_tool_registry()


# ---------------------------------------------------------------------------
# Schema / spec roundtrip.
# ---------------------------------------------------------------------------


def test_openai_function_spec_includes_name_description_and_schema() -> None:
    """AC-03 — openai_function_spec() produces an OpenAI-compatible descriptor."""
    spec = EchoTool.openai_function_spec()
    assert spec["name"] == "echo"
    assert spec["description"] == "Echo the input text."
    params = spec["parameters"]
    assert params["type"] == "object"
    assert "text" in params["properties"]
    assert params["properties"]["text"]["type"] == "string"
    assert "text" in params["required"]


def test_openai_function_spec_requires_name_and_description() -> None:
    """A Tool missing its ClassVars refuses to produce a spec."""

    class Anon(Tool):
        x: int = 0

        async def run(self, ctx: KernelContext) -> ToolReturnValue:
            return ToolOk(brief="", display=TextBlock(text=""))

    with pytest.raises(ValueError, match="name"):
        Anon.openai_function_spec()


def test_tool_params_roundtrip_through_json_schema() -> None:
    """A payload shaped per the schema instantiates the Tool subclass."""
    spec = EchoTool.openai_function_spec()
    # Sanity: the schema is the same schema used for validation.
    assert spec["parameters"] == EchoTool.model_json_schema() | {"title": "EchoTool"} or (
        spec["parameters"] == {k: v for k, v in EchoTool.model_json_schema().items() if k != "title"}
    )
    tool = EchoTool.model_validate({"text": "hello"})
    assert tool.text == "hello"


# ---------------------------------------------------------------------------
# Envelope serialization.
# ---------------------------------------------------------------------------


def test_tool_ok_serialization_roundtrips() -> None:
    """ToolOk.model_dump() → model_validate() returns an equivalent instance."""
    ok = ToolOk(brief="done", display=TextBlock(text="hi"))
    dumped = ok.model_dump(mode="json")
    assert dumped["ok"] is True
    assert dumped["brief"] == "done"
    assert dumped["display"] == {"kind": "text", "text": "hi"}
    back = ToolOk.model_validate(dumped)
    assert back == ok


def test_tool_error_serialization_roundtrips() -> None:
    """ToolError round-trips through model_dump/model_validate."""
    err = ToolError(kind="timeout", brief="tick", display=TextBlock(text="over"))
    dumped = err.model_dump(mode="json")
    assert dumped["ok"] is False
    assert dumped["kind"] == "timeout"
    back = ToolError.model_validate(dumped)
    assert back == err


def test_tool_return_value_union_discriminates_on_ok() -> None:
    """A TypeAdapter over ToolReturnValue picks the right class by ``ok``."""
    adapter: TypeAdapter[ToolReturnValue] = TypeAdapter(ToolReturnValue)
    ok_dump = {"ok": True, "brief": "ran", "display": {"kind": "text", "text": "hi"}}
    err_dump = {
        "ok": False,
        "kind": "validation",
        "brief": "nope",
        "display": {"kind": "text", "text": "bad"},
    }
    assert isinstance(adapter.validate_python(ok_dump), ToolOk)
    assert isinstance(adapter.validate_python(err_dump), ToolError)


def test_brief_length_is_capped_by_pydantic() -> None:
    """ToolOk.brief is validated against the 80-char ceiling."""
    with pytest.raises(Exception, match="at most 80"):
        ToolOk(brief="x" * 81, display=TextBlock(text=""))


def test_display_block_subclasses_roundtrip() -> None:
    """MarkdownBlock and JsonBlock serialize and deserialize symmetrically."""
    md = MarkdownBlock(markdown="# hi")
    assert md.model_dump(mode="json") == {"kind": "markdown", "markdown": "# hi"}
    js = JsonBlock(data={"a": [1, 2]})
    assert js.model_dump(mode="json") == {"kind": "json", "data": {"a": [1, 2]}}
    assert isinstance(md, DisplayBlock)


# ---------------------------------------------------------------------------
# Registry.
# ---------------------------------------------------------------------------


def test_register_and_lookup() -> None:
    """register_tool + get_tool is the round trip every plugin uses."""
    register_tool(EchoTool)
    assert get_tool("echo") is EchoTool
    assert "echo" in registered_tools()


def test_double_register_different_class_raises() -> None:
    """Registering a different class under the same name fails loud."""
    register_tool(EchoTool)

    class OtherEcho(Tool):
        name: ClassVar[str] = "echo"
        description: ClassVar[str] = "alt"
        text: str = ""

        async def run(self, ctx: KernelContext) -> ToolReturnValue:
            return ToolOk(brief="", display=TextBlock(text=""))

    with pytest.raises(ToolAlreadyRegisteredError):
        register_tool(OtherEcho)


def test_double_register_same_class_is_idempotent() -> None:
    """Registering the same class twice is a no-op (hot-reload path)."""
    register_tool(EchoTool)
    register_tool(EchoTool)  # must not raise
    assert get_tool("echo") is EchoTool


def test_register_without_name_raises() -> None:
    """A Tool subclass missing its name ClassVar is rejected."""

    class Nameless(Tool):
        description: ClassVar[str] = "x"

        async def run(self, ctx: KernelContext) -> ToolReturnValue:
            return ToolOk(brief="", display=TextBlock(text=""))

    with pytest.raises(ValueError, match="name"):
        register_tool(Nameless)


def test_legacy_collision_logs_warning_no_crash(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Registering a v1 tool whose name is already claimed by legacy on_event warns."""
    import logging

    mark_legacy_tool("echo")
    with caplog.at_level(logging.WARNING, logger="yaya.kernel.tool"):
        register_tool(EchoTool)
    assert any("legacy" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# Dispatcher — end-to-end over the bus.
# ---------------------------------------------------------------------------


async def _collect(bus: EventBus, kind: str, into: list[Event]) -> None:
    async def handler(ev: Event) -> None:
        into.append(ev)

    bus.subscribe(kind, handler, source="test-observer")


async def test_dispatcher_happy_path() -> None:
    """AC-02 — a well-formed v1 request produces a tool.call.result carrying a ToolOk envelope."""
    register_tool(EchoTool)
    bus = EventBus()
    results: list[Event] = []
    await _collect(bus, "tool.call.result", results)
    install_dispatcher(bus)

    await bus.publish(
        new_event(
            "tool.call.request",
            {
                "id": "call-1",
                "name": "echo",
                "args": {"text": "hello world"},
                "schema_version": "v1",
            },
            session_id="s",
            source="loop",
        )
    )
    await bus.close()

    assert len(results) == 1
    payload = results[0].payload
    assert payload["ok"] is True
    assert payload["id"] == "call-1"
    assert "request_id" in payload
    env = payload["envelope"]
    assert env["ok"] is True
    assert env["brief"].startswith("echo: ")
    assert env["display"] == {"kind": "text", "text": "hello world"}


async def test_dispatcher_validation_failure_emits_tool_error_and_skips_run() -> None:
    """AC-01 — bad params produce tool.error kind=validation; run() is not called."""
    ran = False

    class CheckTool(Tool):
        name: ClassVar[str] = "count"
        description: ClassVar[str] = "Count up to n."
        count: int = Field(ge=0)

        async def run(self, ctx: KernelContext) -> ToolReturnValue:
            nonlocal ran
            ran = True
            return ToolOk(brief="ran", display=TextBlock(text=""))

    register_tool(CheckTool)
    bus = EventBus()
    errors: list[Event] = []
    results: list[Event] = []
    await _collect(bus, "tool.error", errors)
    await _collect(bus, "tool.call.result", results)
    install_dispatcher(bus)

    await bus.publish(
        new_event(
            "tool.call.request",
            {
                "id": "call-2",
                "name": "count",
                "args": {"count": "abc"},
                "schema_version": "v1",
            },
            session_id="s",
            source="loop",
        )
    )
    await bus.close()

    assert ran is False
    assert len(results) == 0
    assert len(errors) == 1
    err = errors[0].payload
    assert err["kind"] == "validation"
    assert err["id"] == "call-2"
    assert "detail" in err and "errors" in err["detail"]


async def test_dispatcher_unknown_name_emits_not_found() -> None:
    """A v1 request for an unregistered tool surfaces tool.error kind=not_found."""
    bus = EventBus()
    errors: list[Event] = []
    await _collect(bus, "tool.error", errors)
    install_dispatcher(bus)

    await bus.publish(
        new_event(
            "tool.call.request",
            {
                "id": "call-3",
                "name": "nope",
                "args": {},
                "schema_version": "v1",
            },
            session_id="s",
            source="loop",
        )
    )
    await bus.close()

    assert len(errors) == 1
    assert errors[0].payload["kind"] == "not_found"


async def test_dispatcher_skips_legacy_payloads() -> None:
    """A tool.call.request without schema_version is left to legacy subscribers."""
    register_tool(EchoTool)
    bus = EventBus()
    results: list[Event] = []
    errors: list[Event] = []
    await _collect(bus, "tool.call.result", results)
    await _collect(bus, "tool.error", errors)
    install_dispatcher(bus)

    await bus.publish(
        new_event(
            "tool.call.request",
            {"id": "call-legacy", "name": "echo", "args": {"text": "x"}},
            session_id="s",
            source="loop",
        )
    )
    await bus.close()

    assert results == []
    assert errors == []


async def test_dispatcher_rejected_by_pre_approve() -> None:
    """requires_approval=True + pre_approve=False → tool.error kind=rejected."""
    register_tool(GatedTool)
    bus = EventBus()
    errors: list[Event] = []
    results: list[Event] = []
    await _collect(bus, "tool.error", errors)
    await _collect(bus, "tool.call.result", results)
    install_dispatcher(bus)

    await bus.publish(
        new_event(
            "tool.call.request",
            {
                "id": "call-gated",
                "name": "gated",
                "args": {},
                "schema_version": "v1",
            },
            session_id="s",
            source="loop",
        )
    )
    await bus.close()

    assert len(errors) == 1
    assert errors[0].payload["kind"] == "rejected"
    assert results == []


async def test_dispatcher_catches_run_exception() -> None:
    """A raising run() is coerced to a ToolError(kind='crashed') envelope."""

    class BoomTool(Tool):
        name: ClassVar[str] = "boom"
        description: ClassVar[str] = "always raises"

        async def run(self, ctx: KernelContext) -> ToolReturnValue:
            raise RuntimeError("bang")

    register_tool(BoomTool)
    bus = EventBus()
    results: list[Event] = []
    await _collect(bus, "tool.call.result", results)
    install_dispatcher(bus)

    await bus.publish(
        new_event(
            "tool.call.request",
            {
                "id": "call-boom",
                "name": "boom",
                "args": {},
                "schema_version": "v1",
            },
            session_id="s",
            source="loop",
        )
    )
    await bus.close()

    assert len(results) == 1
    env = results[0].payload["envelope"]
    assert env["ok"] is False
    assert env["kind"] == "crashed"


async def test_dispatcher_coerces_bad_return_type() -> None:
    """A run() returning a non-ToolReturnValue is coerced to kind='internal'."""

    class BadTool(Tool):
        name: ClassVar[str] = "bad"
        description: ClassVar[str] = "returns wrong type"

        async def run(self, ctx: KernelContext) -> ToolReturnValue:
            return "not a ToolReturnValue"  # type: ignore[return-value]

    register_tool(BadTool)
    bus = EventBus()
    results: list[Event] = []
    await _collect(bus, "tool.call.result", results)
    install_dispatcher(bus)

    await bus.publish(
        new_event(
            "tool.call.request",
            {
                "id": "call-bad",
                "name": "bad",
                "args": {},
                "schema_version": "v1",
            },
            session_id="s",
            source="loop",
        )
    )
    await bus.close()

    assert len(results) == 1
    env = results[0].payload["envelope"]
    assert env["ok"] is False
    assert env["kind"] == "internal"


async def test_base_tool_run_raises_not_implemented() -> None:
    """The Tool base class refuses to run — subclasses must override."""

    class Barebones(Tool):
        name: ClassVar[str] = "bare"
        description: ClassVar[str] = "no run override"

    ctx: Any = object()
    with pytest.raises(NotImplementedError):
        await Barebones().run(ctx)


async def test_dispatch_function_can_be_called_directly(tmp_path: Any) -> None:
    """dispatch() is callable without install_dispatcher for library use."""
    register_tool(EchoTool)
    bus = EventBus()
    results: list[Event] = []
    await _collect(bus, "tool.call.result", results)

    ctx = KernelContext(
        bus=bus,
        logger=__import__("logging").getLogger("test"),
        config={},
        state_dir=tmp_path,
        plugin_name="kernel",
    )
    ev = new_event(
        "tool.call.request",
        {
            "id": "direct",
            "name": "echo",
            "args": {"text": "ok"},
            "schema_version": "v1",
        },
        session_id="s",
        source="loop",
    )
    await dispatch(ev, ctx)
    await bus.close()

    assert len(results) == 1
    assert results[0].payload["id"] == "direct"
