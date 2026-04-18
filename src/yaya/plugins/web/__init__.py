"""Web adapter plugin — bundled FastAPI + WebSocket bridge.

The ``web`` adapter is the default user surface for yaya. It loads
through the same ``yaya.plugins.v1`` entry-point ABI as any
third-party adapter (see ``docs/dev/plugin-protocol.md`` and
``docs/dev/web-ui.md``) — the kernel has no special case for it.

Responsibilities on ``on_load``:

* Start an in-process uvicorn server bound to ``127.0.0.1:<port>``
  (port from ``YAYA_WEB_PORT``; 0 auto-picks a free port — same
  policy as ``yaya serve`` itself).
* Expose ``GET /`` serving the pre-built static UI shell.
* Expose ``GET /assets/*`` serving JS/CSS shipped in the wheel via
  :mod:`importlib.resources`.
* Expose ``WS /ws`` — the adapter bridge that translates kernel events
  into browser WS frames and browser frames back into kernel events.
* Expose ``GET /api/plugins`` — a thin read-only proxy surfacing the
  registry snapshot so the UI can render plugin status without
  subscribing to lifecycle events.

Responsibilities on ``on_unload``: stop the uvicorn server cleanly
within a 3-second budget; after that, cancel the task.

Entry point: ``web = "yaya.plugins.web:plugin"`` registered under
``[project.entry-points."yaya.plugins.v1"]`` in ``pyproject.toml``.
"""

from __future__ import annotations

from yaya.plugins.web.plugin import WebAdapter

plugin: WebAdapter = WebAdapter()
"""Module-level singleton resolved by the kernel registry."""

__all__ = ["WebAdapter", "plugin"]
