"""ReAct strategy plugin (observe → think → act).

This bundled plugin answers ``strategy.decide.request`` with a minimal
ReAct-style decision sequence:

* No assistant message yet in the request's ``state`` → ask the LLM
  (``next = "llm"``, with the configured provider + model).
* Most-recent assistant message carries ``tool_calls`` → run the first
  one (``next = "tool"``).
* Most-recent step was a tool result → ask the LLM to consume it
  (``next = "llm"``).
* Assistant message without tool calls → finish the turn
  (``next = "done"``).

Routing conforms to ``docs/dev/plugin-protocol.md``: subscribe only to
``strategy.decide.request``; every response echoes ``request_id`` back
(see lesson #15 in ``docs/wiki/lessons-learned.md``).
"""

from yaya.plugins.strategy_react.plugin import ReActStrategy

plugin: ReActStrategy = ReActStrategy()
"""Entry-point target — referenced by ``yaya.plugins.v1`` in pyproject.toml."""

__all__ = ["ReActStrategy", "plugin"]
