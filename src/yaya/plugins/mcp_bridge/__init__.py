"""MCP bridge plugin — load external MCP servers as native yaya tools.

Bundled plugin satisfying the ``tool`` category. Reads the
``[mcp_bridge.servers.<name>]`` config sub-tree, spawns each enabled
server over stdio, discovers its tool list via the standard MCP
``initialize`` + ``tools/list`` handshake, and registers each
discovered tool as a dynamically-built :class:`~yaya.kernel.tool.Tool`
subclass via :func:`yaya.kernel.tool.register_tool`.

Each generated tool defaults to ``requires_approval=True`` because MCP
servers are external code surfaces (see issue #31 hard rule).
Subprocess lifecycle follows lesson #31: ``terminate()`` first, bounded
``wait_for(proc.wait(), grace)``, ``kill()`` fallback. Boot uses 3
attempts with exponential backoff; per-server failures emit
``x.mcp.server.error`` (extension namespace) without tainting the rest
of the bridge.
"""

from yaya.plugins.mcp_bridge.plugin import MCPBridge

plugin: MCPBridge = MCPBridge()
"""Entry-point target — referenced by ``yaya.plugins.v1`` in pyproject.toml."""

__all__ = ["MCPBridge", "plugin"]
