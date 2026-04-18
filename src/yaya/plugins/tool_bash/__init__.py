"""Bash tool plugin (argv-only, never ``shell=True``).

Bundled plugin satisfying the ``tool`` category for ``name == "bash"``.
Subscribes to ``tool.call.request``, executes the supplied argv list
via :func:`asyncio.create_subprocess_exec`, and emits
``tool.call.result``. A 30 s wall-clock timeout kills the child process
cleanly on overrun.
"""

from yaya.plugins.tool_bash.plugin import BashTool

plugin: BashTool = BashTool()
"""Entry-point target — referenced by ``yaya.plugins.v1`` in pyproject.toml."""

__all__ = ["BashTool", "plugin"]
