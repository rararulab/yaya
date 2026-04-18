"""Dynamic yaya ``Tool`` generation from MCP tool descriptors.

Each MCP server's ``tools/list`` response turns into N
dynamically-created :class:`~yaya.kernel.tool.Tool` subclasses. The
pydantic model body is built from the tool's ``inputSchema`` via a
best-effort JSON-Schema → Python-typing translation that covers the
common cases (objects with typed properties + required lists, scalar
primitives, arrays with a single item type). Unknown or unsupported
schemas fall back to a single ``args: dict[str, Any]`` passthrough
field — conservative but keeps the plugin usable against any server
the translator does not yet fully model.

The translation is deliberately *not* a full JSON Schema validator —
that is the MCP server's job once we forward the arguments. yaya's
pydantic layer only needs enough structure to surface the parameter
names + types to the LLM function-calling surface.
"""

from __future__ import annotations

from typing import Any, cast

from pydantic import ConfigDict, Field, create_model

from yaya.kernel.tool import JsonBlock, TextBlock, Tool, ToolError, ToolOk, ToolReturnValue
from yaya.plugins.mcp_bridge.client import (
    MCPClient,
    MCPProtocolError,
    MCPServerCrashedError,
    MCPTimeoutError,
    MCPToolDescriptor,
)

__all__ = ["build_mcp_tool_class", "mcp_tool_qualified_name"]


# Mapping from JSON-Schema primitive types to Python types used by the
# pydantic model. ``Any`` is the fallback when the server omits ``type``
# or uses a union we don't bother to unpack at 0.1.
_JSON_TO_PY: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "object": dict,
    "array": list,
    "null": type(None),
}


def mcp_tool_qualified_name(server_name: str, tool_name: str) -> str:
    """Return the yaya-side tool name for an MCP tool.

    The prefix prevents collisions between servers and between MCP
    tools and bundled yaya tools. The separator is an underscore (not
    a dot) because LLM function-calling names on several vendors are
    restricted to ``[a-zA-Z0-9_-]``.
    """
    safe_server = _sanitize(server_name)
    safe_tool = _sanitize(tool_name)
    return f"mcp_{safe_server}_{safe_tool}"


def _sanitize(name: str) -> str:
    """Collapse any run of non-``[a-zA-Z0-9]`` chars into ``_``."""
    out: list[str] = []
    for ch in name:
        if ch.isalnum():
            out.append(ch)
        else:
            out.append("_")
    return "".join(out) or "_"


def _py_type_for(schema: Any) -> Any:
    """Return a reasonable Python annotation for one property schema."""
    if not isinstance(schema, dict):
        return Any
    schema_dict = cast("dict[str, Any]", schema)
    raw_type: Any = schema_dict.get("type")
    if isinstance(raw_type, str):
        return _JSON_TO_PY.get(raw_type, Any)
    # Unions / missing type → Any. Accept broad inputs, let the server
    # do the real validation.
    return Any


def _build_param_model_fields(
    input_schema: dict[str, Any],
) -> dict[str, tuple[Any, Any]]:
    """Return a ``create_model``-friendly ``{name: (type, default)}`` dict.

    If ``input_schema`` describes an object with ``properties``, each
    property becomes a field; ``required`` entries get no default
    (pydantic treats them as required). Anything else → a single
    ``args: dict[str, Any] = {}`` passthrough so the plugin still
    accepts arbitrary arguments.
    """
    if input_schema.get("type") != "object":
        return {"args": (dict[str, Any], {})}
    properties_raw: Any = input_schema.get("properties")
    if not isinstance(properties_raw, dict) or not properties_raw:
        return {"args": (dict[str, Any], {})}
    properties = cast("dict[str, Any]", properties_raw)
    required_raw: Any = input_schema.get("required") or []
    required: set[str] = set()
    if isinstance(required_raw, list):
        required_list: list[Any] = list(required_raw)  # pyright: ignore[reportUnknownArgumentType]
        for item in required_list:
            if isinstance(item, str):
                required.add(item)

    fields: dict[str, tuple[Any, Any]] = {}
    for prop_name, prop_schema in properties.items():
        py_type = _py_type_for(prop_schema)
        description = ""
        if isinstance(prop_schema, dict):
            schema_dict = cast("dict[str, Any]", prop_schema)
            desc: Any = schema_dict.get("description")
            if isinstance(desc, str):
                description = desc
        if prop_name in required:
            fields[prop_name] = (py_type, Field(..., description=description))
        else:
            fields[prop_name] = (py_type | None, Field(default=None, description=description))
    return fields


def _first_text_block(content: list[Any]) -> str:
    """Pull a short text summary out of an MCP tool-call content array."""
    for item in content:
        if isinstance(item, dict):
            item_dict = cast("dict[str, Any]", item)
            t: Any = item_dict.get("type")
            text: Any = item_dict.get("text")
            if t == "text" and isinstance(text, str):
                return text
    return ""


def build_mcp_tool_class(
    server_name: str,
    descriptor: MCPToolDescriptor,
    client: MCPClient,
    *,
    requires_approval: bool,
    call_timeout_s: float,
) -> type[Tool]:
    """Return a concrete :class:`Tool` subclass wrapping one MCP tool.

    The subclass is created dynamically so each MCP tool keeps its own
    pydantic schema — the bridge cannot rely on a single generic
    ``args: dict`` shape because the LLM function-calling surface
    wants real parameter names.

    Args:
        server_name: Owning server's config name.
        descriptor: The ``tools/list`` entry for this tool.
        client: Live :class:`MCPClient`. Closed over; callers MUST NOT
            close the client while any generated Tool is still
            registered.
        requires_approval: Whether the dispatcher should gate this tool
            behind the approval runtime. External tool surface → True
            by default.
        call_timeout_s: Wall-clock cap passed to each
            :meth:`MCPClient.call_tool`.

    Returns:
        A :class:`Tool` subclass ready for
        :func:`yaya.kernel.register_tool`.
    """
    qualified_name = mcp_tool_qualified_name(server_name, descriptor.name)
    description = descriptor.description or f"MCP tool {descriptor.name} from server {server_name}"

    fields = _build_param_model_fields(descriptor.input_schema)

    # Build a pydantic model carrying the tool's params. ``create_model``
    # returns a subclass of BaseModel; we mix it into Tool via
    # multiple inheritance below so the subclass ends up with both the
    # tool-side hooks and the validated field set.
    # ``create_model`` expects ``(name, type, default)`` triples for its
    # field kwargs but its stub signature only models the named pydantic
    # internals (``__config__`` etc.). Both checkers complain that our
    # ``(type, default)`` tuples collide with those reserved kwargs;
    # rework via the explicit ``__base__``-free path is not worth the
    # churn — suppress per-line and move on.
    params_base: type[Any] = create_model(  # type: ignore[call-overload]
        f"_MCPToolParams_{qualified_name}",
        __config__=ConfigDict(extra="allow"),
        **fields,  # pyright: ignore[reportArgumentType]
    )

    async def _run(self: Tool, ctx: Any) -> ToolReturnValue:
        # Extract the arguments dict from the bound pydantic model.
        args = self.model_dump(mode="json", exclude_none=True)
        # Strip yaya-only fields that leaked from the Tool base class.
        args.pop("requires_approval", None)
        try:
            raw_result = await client.call_tool(
                descriptor.name,
                args,
                timeout_s=call_timeout_s,
            )
        except MCPTimeoutError as exc:
            return ToolError(
                kind="timeout",
                brief=f"mcp {descriptor.name!r} timed out"[:80],
                display=TextBlock(text=str(exc)),
            )
        except MCPServerCrashedError as exc:
            return ToolError(
                kind="crashed",
                brief=f"mcp server {server_name!r} crashed"[:80],
                display=TextBlock(text=str(exc)),
            )
        except MCPProtocolError as exc:
            return ToolError(
                kind="internal",
                brief=f"mcp {descriptor.name!r} protocol error"[:80],
                display=TextBlock(text=str(exc)),
            )
        except Exception as exc:
            # Lesson #29: every exception path surfaces as a tool.error
            # so the agent loop never sees a raw exception.
            return ToolError(
                kind="crashed",
                brief=f"mcp {descriptor.name!r} raised"[:80],
                display=TextBlock(text=f"{type(exc).__name__}: {exc}"),
            )

        content_raw: Any = raw_result.get("content", [])
        content: list[Any] = list(content_raw) if isinstance(content_raw, list) else []  # pyright: ignore[reportUnknownArgumentType]
        is_error = bool(raw_result.get("isError", False))
        text_summary = _first_text_block(content) or descriptor.name

        if is_error:
            return ToolError(
                kind="internal",
                brief=f"mcp {descriptor.name!r} reported error"[:80],
                display=JsonBlock(data=content),
            )
        return ToolOk(
            brief=text_summary[:80] or descriptor.name,
            display=JsonBlock(data=content),
        )

    # Multi-inherit: Tool (yaya hooks) + params_base (fields/config).
    # Tool already carries ``model_config = ConfigDict(extra="forbid")``;
    # the dynamic params model uses ``extra="allow"`` so arbitrary-shape
    # MCP schemas round-trip. Explicit class-level model_config wins.
    tool_cls: type[Tool] = cast(
        "type[Tool]",
        type(
            f"MCPTool_{qualified_name}",
            (Tool, params_base),  # pyright: ignore[reportUnknownArgumentType]
            {
                "__module__": __name__,
                "__qualname__": f"MCPTool_{qualified_name}",
                "name": qualified_name,
                "description": description,
                "requires_approval": requires_approval,
                "model_config": ConfigDict(extra="allow"),
                "run": _run,
                # Preserve the source descriptor for diagnostics — typed
                # as ClassVar so pydantic does not try to turn it into
                # a field.
                "__mcp_server_name__": server_name,
                "__mcp_tool_name__": descriptor.name,
            },
        ),
    )
    return tool_cls
