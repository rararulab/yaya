"""Agent tool plugin — spawn sub-agents via forked session + event bus.

Bundled plugin satisfying the ``tool`` category. Exposes a single v1
tool, ``agent``, that forks the caller's
:class:`~yaya.kernel.session.Session` via
:meth:`~yaya.kernel.session.Session.fork` (the overlay semantics from
#32), emits a ``user.message.received`` on the child session so the
kernel's running :class:`~yaya.kernel.loop.AgentLoop` drives the turn,
and waits for the child's ``assistant.message.done`` before returning
the final text to the parent as a :class:`~yaya.kernel.tool.ToolOk`
envelope.

The design follows GOAL.md principle #1 (kernel stays small): no new
plugin category is introduced; a sub-agent is just another tool whose
run body happens to pump the bus. Plugin-private progress events live
under the ``x.agent.*`` extension namespace per principle #3.
"""

from yaya.plugins.agent_tool.plugin import AgentPlugin, AgentTool

plugin: AgentPlugin = AgentPlugin()
"""Entry-point target — referenced by ``yaya.plugins.v1`` in pyproject.toml."""

__all__ = ["AgentPlugin", "AgentTool", "plugin"]
