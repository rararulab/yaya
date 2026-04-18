spec: task
name: "e2e-serve-roundtrip"
tags: [e2e, kernel, plugins, web, llm-provider]
---

## Intent

End-to-end integration test that asserts the 0.1 milestone's
definition of done — kernel + registry + agent loop + strategy_react
+ llm_echo + memory_sqlite + web adapter all hang together as one
process. The proof is a single round-trip:

1. Spawn ``yaya --json serve --port 0 --no-open`` in a child process.
2. Read the ephemeral port from the ``serve.started`` JSON event.
3. Open a real WebSocket client against ``ws://127.0.0.1:<port>/ws``.
4. Send a ``user.message`` frame.
5. Assert an ``assistant.done`` frame arrives within 5 s and its
   ``content`` starts with ``(echo)`` (proves the bundled echo
   provider drove the turn — there is no API key in the env).
6. Send the platform-correct shutdown signal and assert clean
   stderr (no traceback, no "unhandled").

This is the test the 0.1 tracking epic closes on. It runs out of
``tests/e2e/`` against an installed wheel — the same path CI's
``E2E smoke`` job exercises on Linux, macOS, and Windows.

## Decisions

- Test file: ``tests/e2e/test_serve_roundtrip.py``.
- Marker: ``pytest.mark.integration`` (already registered in
  ``pyproject.toml``).
- Ephemeral port discovery: parse the ``addr`` field of the
  ``serve.started`` JSON event emitted on stdout. The port is the
  segment after the final ``:`` in ``"127.0.0.1:<port>"``.
- ``OPENAI_API_KEY`` is removed from the child env so
  :mod:`yaya.plugins.strategy_react` selects the echo provider per
  the AC-AUTO scenario in ``specs/plugin-llm_echo.spec``.
- Shutdown signal: ``SIGINT`` on POSIX,
  ``signal.CTRL_BREAK_EVENT`` on Windows (combined with
  ``CREATE_NEW_PROCESS_GROUP`` at spawn). Lesson #48: ``SIGINT``
  to a Windows child raises ``KeyboardInterrupt`` in the parent,
  not the child.
- Fixture teardown drains stdout/stderr **after** ``proc.wait`` to
  avoid the OS-pipe deadlock that hits when the buffer fills mid-
  test (lesson #11).
- Stderr "no exception" assertion uses regex
  (``^Traceback \(most recent call last\):`` and ``\bunhandled\b``)
  rather than substring — substring matches are too noisy against
  loguru's free-form WARNING lines.
- Adds ``websockets>=13.0`` to the dev dependency group; the wheel
  already pulls it transitively via ``uvicorn[standard]`` for runtime
  use, but the dev pin makes the test's import explicit.
- ``just test-e2e`` runs the full e2e suite (the existing recipe
  already builds the wheel into ``.smoke-venv``); the recipe also
  installs ``websockets`` so the fresh venv has it.
- The CI ``E2E smoke`` job in ``.github/workflows/main.yml`` already
  runs ``pytest tests/e2e -v``; adds ``websockets`` to the
  ``pip install`` line so this new test imports succeed on all three
  OSes.

## Boundaries

### Allowed Changes
- tests/e2e/test_serve_roundtrip.py
- specs/e2e-serve-roundtrip.spec
- pyproject.toml
- uv.lock
- justfile
- .github/workflows/main.yml

### Forbidden
- src/yaya/kernel/
- src/yaya/cli/
- src/yaya/core/
- src/yaya/plugins/
- GOAL.md
- AGENT.md
- docs/dev/plugin-protocol.md

## Completion Criteria

Scenario: AC-01 echo round-trip lands an assistant done frame within five seconds
  Test:
    Package: yaya
    Filter: tests/e2e/test_serve_roundtrip.py::test_serve_roundtrip_echo
  Level: e2e
  Given yaya serve is running as a subprocess on an ephemeral port with OPENAI_API_KEY unset
  When the test opens a WebSocket to /ws and sends a user.message with text hi
  Then an assistant.done frame arrives within five seconds and its content starts with (echo)

Scenario: AC-02 the kernel exits cleanly on SIGINT with no traceback on stderr
  Test:
    Package: yaya
    Filter: tests/e2e/test_serve_roundtrip.py::test_no_stray_exceptions
  Level: e2e
  Given yaya serve is running as a subprocess
  When the test sends the platform shutdown signal and the process exits
  Then the captured stderr contains no Traceback header and no token unhandled

## Out of Scope

- Tool-use roundtrip (``tool_bash`` invocation through the loop).
- Memory persistence across kernel restarts.
- Multi-turn conversation state.
- Streaming ``assistant.delta`` chunks — the echo provider emits a
  single ``assistant.done`` directly.
- Browser-side rendering — covered by the web adapter's vitest suite.
