"""E2E: ``yaya serve`` -> ``/ws`` -> ``user.message`` -> ``assistant.done``.

Spawns ``yaya --json serve --port 0 --no-open`` in a subprocess, parses
the ``serve.started`` JSON event for the bound port (the kernel reports
``addr = "127.0.0.1:<port>"``), opens a real websocket client to the
web adapter's ``/ws`` endpoint, sends a ``user.message`` frame, and
asserts the resulting ``assistant.done`` body starts with ``(echo)``
(proving the bundled echo provider drove the turn).

Hard requirements:

* ``OPENAI_API_KEY`` is removed from the child env so
  :mod:`yaya.plugins.strategy_react` picks the echo provider (see
  ``specs/plugin-llm_echo.spec`` AC-AUTO).
* The fixture is responsible for clean teardown — SIGINT on POSIX,
  ``terminate()`` on Windows where ``signal.SIGINT`` to a child does
  not unwind the asyncio loop the same way (lesson #48).
* After the round-trip, the test asserts no traceback / "unhandled"
  marker landed on stderr (AC-02). The assertion is gated on a regex
  rather than substring to tolerate unrelated logger output that
  happens to contain the word as a fragment.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator

import pytest
from websockets.asyncio.client import connect

pytestmark = [pytest.mark.integration]

_STARTUP_TIMEOUT_S = 15.0
"""Upper bound for ``serve.started`` to land. CI cold-starts can be slow."""

_ROUNDTRIP_TIMEOUT_S = 5.0
"""AC-01 budget — the echo provider answers in microseconds locally."""

_SHUTDOWN_TIMEOUT_S = 8.0
"""Upper bound for the kernel to drain after the shutdown signal."""

_TRACEBACK_RE = re.compile(r"^Traceback \(most recent call last\):", re.MULTILINE)
"""Match a real traceback header — substring 'Traceback' alone is too noisy."""

_UNHANDLED_RE = re.compile(r"\bunhandled\b", re.IGNORECASE)
"""Match the word 'unhandled' as a token, not embedded in another word."""


def _wait_for_listen(host: str, port: int, *, deadline: float, proc: subprocess.Popen[str]) -> None:
    """Block until ``host:port`` accepts a TCP connection or the deadline elapses.

    The web adapter binds asynchronously inside ``on_load``; the kernel
    can emit ``serve.started`` before uvicorn has finished its bind.
    Polling with a short connect attempt is the empirical probe — see
    ``docs/wiki/lessons-learned.md`` #11.
    """
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"yaya serve exited (code={proc.returncode}) before binding {host}:{port}")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            try:
                sock.connect((host, port))
            except OSError:
                time.sleep(0.05)
                continue
            return
    proc.kill()
    raise TimeoutError(f"web adapter did not start listening on {host}:{port} before deadline")


def _pick_free_port() -> int:
    """Ask the OS for a free TCP port on loopback.

    Racy by design (the OS may hand the port out again before the web
    adapter binds it), but acceptable for a local test — the test will
    fail loudly with ``ConnectionRefusedError`` if the race materialises.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _spawn_serve(yaya_bin: str) -> tuple[subprocess.Popen[str], int, list[str]]:
    """Spawn ``yaya serve`` and wait for the ``serve.started`` event.

    Returns a triple of ``(process, bound_port, startup_lines)`` where
    ``startup_lines`` is every stdout line consumed during startup
    (kept so a debugging post-mortem can reconstruct the boot log
    without re-reading the pipe).

    Raises:
        RuntimeError: The subprocess exited before emitting
            ``serve.started`` — typically a startup failure.
        TimeoutError: ``serve.started`` did not arrive within
            ``_STARTUP_TIMEOUT_S``.
    """
    env = {**os.environ, "NO_COLOR": "1", "YAYA_NO_AUTO_UPDATE": "1"}
    env.pop("OPENAI_API_KEY", None)  # force the echo provider
    # The kernel's serve.started addr is the kernel port, NOT the web
    # adapter port — those are independent (see serve.py docstring and
    # the YAYA_WEB_PORT note). Pin the adapter port via the documented
    # env knob so the test knows where to connect.
    web_port = _pick_free_port()
    env["YAYA_WEB_PORT"] = str(web_port)

    # creationflags only exists on Windows; Popen rejects unknown kw on
    # POSIX, so branch the kwargs here. CREATE_NEW_PROCESS_GROUP lets
    # us send CTRL_BREAK_EVENT during teardown — SIGINT to a child on
    # Windows raises in the parent, not the child.
    extra_kwargs: dict[str, object] = {}
    if sys.platform == "win32":
        extra_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(
        [yaya_bin, "--json", "serve", "--port", "0", "--no-open"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        **extra_kwargs,  # type: ignore[arg-type]
    )

    assert proc.stdout is not None, "Popen with PIPE must expose stdout"
    assert proc.stderr is not None, "Popen with PIPE must expose stderr"

    # rich.Console.print_json pretty-prints with indent=2 — every JSON
    # object spans multiple lines. Accumulate stdout into a buffer and
    # use json.JSONDecoder.raw_decode to peel off complete top-level
    # objects as they arrive. Whitespace between objects is skipped.
    decoder = json.JSONDecoder()
    buf = ""
    startup_lines: list[str] = []
    deadline = time.monotonic() + _STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        chunk = proc.stdout.readline()
        if not chunk:
            if proc.poll() is not None:
                stderr = proc.stderr.read()
                raise RuntimeError(
                    f"yaya serve exited early (code={proc.returncode})\n"
                    f"stdout so far:\n{''.join(startup_lines)}\n"
                    f"stderr:\n{stderr}",
                )
            time.sleep(0.02)
            continue
        startup_lines.append(chunk)
        buf += chunk
        # Drain every complete JSON object currently in the buffer.
        while True:
            stripped = buf.lstrip()
            if not stripped:
                buf = ""
                break
            try:
                obj, end = decoder.raw_decode(stripped)
            except json.JSONDecodeError:
                # Incomplete object — wait for the next chunk. Keep
                # the leading whitespace stripped to avoid quadratic
                # buf growth.
                buf = stripped
                break
            buf = stripped[end:]
            if isinstance(obj, dict) and obj.get("action") == "serve.started":
                # Sanity-check the kernel addr — but the WS lives on
                # YAYA_WEB_PORT (the adapter), not the kernel port.
                addr = obj.get("addr")
                if not isinstance(addr, str) or ":" not in addr:
                    proc.kill()
                    raise RuntimeError(f"serve.started missing addr: {obj!r}")
                # Wait for the web adapter to actually accept TCP on
                # its port before returning — uvicorn starts in the
                # background and may not be listening yet when the
                # kernel emits serve.started.
                _wait_for_listen("127.0.0.1", web_port, deadline=deadline, proc=proc)
                return proc, web_port, startup_lines

    proc.kill()
    raise TimeoutError(
        f"yaya serve did not emit serve.started within {_STARTUP_TIMEOUT_S}s\n"
        f"stdout collected:\n{''.join(startup_lines)}",
    )


def _shutdown(proc: subprocess.Popen[str]) -> None:
    """Send the platform-correct shutdown signal and wait for exit.

    POSIX: ``SIGINT`` is what an interactive ``Ctrl+C`` produces; the
    kernel registers a signal handler for it via
    :meth:`asyncio.AbstractEventLoop.add_signal_handler`.

    Windows: ``signal.SIGINT`` to ``Popen.send_signal`` raises a
    :class:`KeyboardInterrupt` in the *parent* process, not the child.
    ``CTRL_BREAK_EVENT`` is the only signal a child created in a new
    process group will receive — combined with
    ``CREATE_NEW_PROCESS_GROUP`` from spawn (lesson #48).
    """
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        proc.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=_SHUTDOWN_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)


@pytest.fixture
def serve_process(yaya_bin: str) -> Iterator[tuple[subprocess.Popen[str], int, dict[str, str]]]:
    """Spawn ``yaya serve``; yield ``(proc, port, captured)``.

    ``captured`` is mutated by the teardown to expose the post-shutdown
    stdout / stderr to tests that need to assert on them. Tests must
    NOT read ``proc.stderr`` themselves before teardown — the pipe
    would deadlock if the buffer fills.
    """
    proc, port, _startup = _spawn_serve(yaya_bin)
    captured: dict[str, str] = {"stdout": "", "stderr": ""}
    try:
        yield proc, port, captured
    finally:
        _shutdown(proc)
        # Drain pipes after exit; safe now that the child is gone.
        # Defensive contextlib.suppress around the read in case the
        # pipes were already closed by the test body.
        import contextlib

        with contextlib.suppress(Exception):
            assert proc.stdout is not None
            assert proc.stderr is not None
            captured["stdout"] = proc.stdout.read() or ""
            captured["stderr"] = proc.stderr.read() or ""


@pytest.mark.asyncio
async def test_serve_roundtrip_echo(
    serve_process: tuple[subprocess.Popen[str], int, dict[str, str]],
) -> None:
    """AC-01 — full round-trip via the echo provider lands an ``assistant.done``."""
    _proc, port, _captured = serve_process
    url = f"ws://127.0.0.1:{port}/ws"

    async def drive() -> dict[str, object]:
        async with connect(url) as ws:
            await ws.send(json.dumps({"type": "user.message", "text": "hi"}))
            async with asyncio.timeout(_ROUNDTRIP_TIMEOUT_S):
                while True:
                    raw = await ws.recv()
                    frame = json.loads(raw)
                    if isinstance(frame, dict) and frame.get("type") == "assistant.done":
                        return frame

    frame = await drive()
    content = frame.get("content")
    assert isinstance(content, str), f"assistant.done.content not a string: {frame!r}"
    assert content.startswith("(echo)"), f"echo provider did not drive the turn: {frame!r}"


def test_no_stray_exceptions(
    serve_process: tuple[subprocess.Popen[str], int, dict[str, str]],
) -> None:
    """AC-02 — clean stderr after shutdown; no tracebacks, no 'unhandled'."""
    proc, _port, captured = serve_process
    # Trigger shutdown inside the test so the captured dict is populated
    # before our assertions run. The fixture's teardown is a no-op once
    # the process has already exited.
    _shutdown(proc)
    # Drain pipes here too — the fixture finalizer will run after this
    # function returns, but the assertions need the bytes NOW.
    assert proc.stdout is not None
    assert proc.stderr is not None
    captured["stdout"] = proc.stdout.read() or ""
    captured["stderr"] = proc.stderr.read() or ""

    stderr = captured["stderr"]
    assert not _TRACEBACK_RE.search(stderr), f"stderr contains a traceback:\n{stderr}"
    assert not _UNHANDLED_RE.search(stderr), f"stderr mentions 'unhandled':\n{stderr}"
