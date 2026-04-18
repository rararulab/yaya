"""Config parsing for the MCP bridge plugin.

Parses the ``[mcp_bridge.servers.<name>]`` sub-tree delivered to the plugin
via :attr:`KernelContext.config`. Config surface intentionally
minimal — stdio-transport-only for 0.1. A full surface (SSE, OAuth)
lands later.

Env-var expansion: string values under ``env`` (and, as a convenience,
under ``args``) are run through :func:`os.path.expandvars`. Unresolved
variables survive verbatim so the user can see which reference broke.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, cast

__all__ = ["MCPConfigError", "MCPServerConfig", "parse_mcp_config"]


class MCPConfigError(ValueError):
    """Raised when a single MCP server config is invalid.

    Distinct subclass so :class:`MCPBridge.on_load` can catch it and
    translate one bad server into a ``plugin.error`` without tanking
    the rest of the bridge.
    """


@dataclass(slots=True)
class MCPServerConfig:
    """One validated MCP server definition.

    Attributes:
        name: Stable server identifier (TOML table key, not the binary
            path). Becomes part of every derived tool's yaya-side
            ``name`` so collisions across servers are impossible.
        command: Executable to spawn (resolved via PATH by
            :func:`asyncio.create_subprocess_exec`).
        args: Additional argv entries passed after ``command``.
        env: Extra environment variables merged over :data:`os.environ`.
            Values are ``$VAR`` / ``${VAR}`` expanded at parse time.
        enabled: If False, the server is recorded but not spawned.
        requires_approval: Override the per-tool approval default. None
            means "use the bridge-wide default" (True).
        call_timeout_s: Per-tool-call wall-clock cap in seconds.
    """

    name: str
    command: str
    args: list[str] = field(default_factory=list[str])
    env: dict[str, str] = field(default_factory=dict[str, str])
    enabled: bool = True
    requires_approval: bool | None = None
    call_timeout_s: float = 30.0


def _expand(value: str) -> str:
    """Apply :func:`os.path.expandvars` so ``$VAR`` / ``${VAR}`` resolve."""
    return os.path.expandvars(value)


def _parse_server(name: str, raw: Any) -> MCPServerConfig:  # noqa: C901 - linear validator; splitting hurts readability.
    """Validate and normalise one ``[mcp_bridge.servers.<name>]`` entry.

    Raises:
        MCPConfigError: On any missing or mistyped field. The message
            always starts with the server name so the plugin log surfaces
            the offender.
    """
    if not isinstance(raw, dict):
        raise MCPConfigError(f"{name!r}: expected a table, got {type(raw).__name__}")
    raw_dict = cast("dict[str, Any]", raw)

    command: Any = raw_dict.get("command")
    if not isinstance(command, str) or not command.strip():
        raise MCPConfigError(f"{name!r}: 'command' must be a non-empty string")

    raw_args: Any = raw_dict.get("args", [])
    if not isinstance(raw_args, list):
        raise MCPConfigError(f"{name!r}: 'args' must be a list of strings")
    args_list: list[Any] = list(raw_args)  # pyright: ignore[reportUnknownArgumentType]
    args: list[str] = []
    for idx, item in enumerate(args_list):
        if not isinstance(item, str):
            raise MCPConfigError(f"{name!r}: args[{idx}] must be a string")
        args.append(_expand(item))

    raw_env: Any = raw_dict.get("env", {})
    if not isinstance(raw_env, dict):
        raise MCPConfigError(f"{name!r}: 'env' must be a table of strings")
    env_dict: dict[Any, Any] = dict(raw_env)  # pyright: ignore[reportUnknownArgumentType]
    env: dict[str, str] = {}
    for k, v in env_dict.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise MCPConfigError(f"{name!r}: env entries must be string→string")
        env[k] = _expand(v)

    enabled: Any = raw_dict.get("enabled", True)
    if not isinstance(enabled, bool):
        raise MCPConfigError(f"{name!r}: 'enabled' must be a boolean")

    approval: Any = raw_dict.get("requires_approval")
    if approval is not None and not isinstance(approval, bool):
        raise MCPConfigError(f"{name!r}: 'requires_approval' must be a boolean")

    timeout: Any = raw_dict.get("call_timeout_s", 30.0)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise MCPConfigError(f"{name!r}: 'call_timeout_s' must be a positive number")

    return MCPServerConfig(
        name=name,
        command=_expand(command),
        args=args,
        env=env,
        enabled=enabled,
        requires_approval=approval,
        call_timeout_s=float(timeout),
    )


def parse_mcp_config(config: Any) -> tuple[list[MCPServerConfig], list[tuple[str, str]]]:
    """Parse the plugin's config sub-tree into validated server configs.

    The plugin receives a mapping under the ``mcp_bridge`` namespace.
    Inside it, the schema is::

        servers:
          <server_name>:
            command: "..."
            args: [...]
            env: {...}
            enabled: true
            requires_approval: true
            call_timeout_s: 30

    Args:
        config: The plugin's resolved configuration mapping (as seen by
            :attr:`KernelContext.config`). Tolerates ``None`` /
            empty dict.

    Returns:
        A ``(good, errors)`` tuple. ``good`` is the list of successfully
        parsed configs. ``errors`` is a list of ``(name, message)``
        pairs for servers that failed validation — the caller surfaces
        each as a ``plugin.error`` but keeps the bridge running (lesson
        #10: broken config is surfaced, never silent).
    """
    good: list[MCPServerConfig] = []
    errors: list[tuple[str, str]] = []

    if not isinstance(config, dict):
        return good, errors

    config_dict = cast("dict[str, Any]", config)
    raw_servers: Any = config_dict.get("servers")
    if raw_servers is None:
        return good, errors
    if not isinstance(raw_servers, dict):
        errors.append(("<mcp_bridge>", "'servers' must be a table keyed by server name"))
        return good, errors
    servers_dict: dict[Any, Any] = dict(raw_servers)  # pyright: ignore[reportUnknownArgumentType]

    for name, spec in servers_dict.items():
        if not isinstance(name, str) or not name.strip():
            errors.append(("<mcp_bridge>", f"server name {name!r} is not a non-empty string"))
            continue
        try:
            good.append(_parse_server(name, spec))
        except MCPConfigError as exc:
            errors.append((name, str(exc)))
    return good, errors
