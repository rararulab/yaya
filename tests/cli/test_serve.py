"""Tests for ``yaya serve``.

A full signal-driven integration is unergonomic under :class:`CliRunner`
because Typer runs the command in the current asyncio loop; instead we
exercise :func:`yaya.cli.commands.serve.run_serve` directly with the
``shutdown_event`` test hook.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from yaya.cli import CLIState
from yaya.cli.commands.serve import run_serve


def test_serve_rejects_host_flag(runner: CliRunner, cli_app) -> None:
    """``--host`` is deliberately absent — Typer should reject it with exit 2."""
    result = runner.invoke(cli_app, ["serve", "--host", "0.0.0.0"])  # noqa: S104 — hard-reject test
    assert result.exit_code == 2, result.stdout


def test_serve_invokes_run_serve(runner: CliRunner, cli_app) -> None:
    """The Typer wrapper threads args to ``run_serve`` and exits on non-zero."""
    with patch("yaya.cli.commands.serve.run_serve", new_callable=AsyncMock) as fake:
        fake.return_value = 0
        result = runner.invoke(cli_app, ["serve", "--port", "7777", "--no-open"])
    assert result.exit_code == 0, result.stdout
    fake.assert_awaited_once()
    kwargs = fake.await_args.kwargs
    assert kwargs["port"] == 7777
    assert kwargs["no_open"] is True
    assert kwargs["strategy"] == "react"


def test_serve_non_zero_return_propagates(runner: CliRunner, cli_app) -> None:
    """Non-zero ``run_serve`` return surfaces as a Typer exit code."""
    with patch("yaya.cli.commands.serve.run_serve", new_callable=AsyncMock) as fake:
        fake.return_value = 1
        result = runner.invoke(cli_app, ["serve", "--no-open"])
    assert result.exit_code == 1


def test_serve_help_mentions_port_not_host(runner: CliRunner, cli_app) -> None:
    result = runner.invoke(cli_app, ["serve", "--help"])
    assert result.exit_code == 0
    # Normalise the help text so rich box-drawing wrapping does not
    # reintroduce false positives for the ``--host`` grep.
    flat = " ".join(result.stdout.split())
    assert "--port" in flat
    assert "--host" not in flat
    assert "Examples" in flat


@pytest.mark.asyncio
async def test_run_serve_clean_shutdown_via_event(capsys: pytest.CaptureFixture[str]) -> None:
    """Drive shutdown via the test-only ``shutdown_event`` hook."""
    shutdown = asyncio.Event()
    state = CLIState(json_output=True)

    task = asyncio.create_task(
        run_serve(
            state,
            port=0,
            no_open=True,
            strategy="react",
            dev=False,
            shutdown_event=shutdown,
        )
    )
    # Give the boot sequence a moment to fan out events.
    await asyncio.sleep(0.2)
    shutdown.set()
    code = await asyncio.wait_for(task, timeout=5.0)
    assert code == 0

    captured = capsys.readouterr()
    # Two JSON docs should land on stdout: serve.started + shutdown.
    lines = [line for line in captured.out.splitlines() if line.strip()]
    joined = "\n".join(lines)
    assert '"action": "serve.started"' in joined
    assert '"action": "shutdown"' in joined
    # Parse the shutdown doc — it is the final JSON object on stdout.
    last_brace = joined.rfind("{")
    last_close = joined.rfind("}")
    shutdown_payload = json.loads(joined[last_brace : last_close + 1])
    assert shutdown_payload["ok"] is True
    assert shutdown_payload["action"] == "shutdown"


@pytest.mark.asyncio
async def test_run_serve_registers_signal_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a test hook, ``run_serve`` wires SIGINT/SIGTERM handlers.

    We monkeypatch :meth:`asyncio.AbstractEventLoop.add_signal_handler` to
    capture the registration AND to set the event so the command
    returns promptly — exercising the signal-handler branch.
    """
    import signal as signal_mod

    def fake_add(sig, callback, *args):  # type: ignore[no-untyped-def]
        if sig == signal_mod.SIGINT:
            # Fire asynchronously so run_serve has time to block on wait().
            asyncio.get_running_loop().call_later(0.05, callback)
        return None

    aio_loop = asyncio.get_running_loop()
    monkeypatch.setattr(aio_loop, "add_signal_handler", fake_add)

    state = CLIState(json_output=True)
    code = await run_serve(
        state,
        port=0,
        no_open=True,
        strategy="react",
        dev=False,
    )
    # The fake handler sets the shutdown flag; run_serve exits cleanly.
    assert code == 0


@pytest.mark.asyncio
async def test_run_serve_startup_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kernel boot crash surfaces ok=false, exit code 1."""
    from yaya.cli.commands import serve as serve_mod

    class _Boom:
        def __init__(self, _bus, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            pass

        async def start(self) -> None:
            raise RuntimeError("boom")

        async def stop(self) -> None:
            return None

    monkeypatch.setattr(serve_mod, "PluginRegistry", _Boom)

    state = CLIState(json_output=True)
    code = await run_serve(
        state,
        port=0,
        no_open=True,
        strategy="react",
        dev=False,
        shutdown_event=asyncio.Event(),
    )
    assert code == 1


@pytest.mark.asyncio
async def test_run_serve_opens_browser_when_web_adapter_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a web adapter is loaded and --no-open is off, browser opens."""
    from yaya.cli.commands import serve as serve_mod

    monkeypatch.setattr(
        serve_mod,
        "_has_web_adapter",
        lambda _snapshot: True,
    )
    calls: list[str] = []
    monkeypatch.setattr(serve_mod.webbrowser, "open", lambda url: calls.append(url))

    shutdown = asyncio.Event()
    state = CLIState(json_output=True)
    task = asyncio.create_task(
        run_serve(
            state,
            port=0,
            no_open=False,
            strategy="react",
            dev=False,
            shutdown_event=shutdown,
        )
    )
    await asyncio.sleep(0.2)
    shutdown.set()
    await asyncio.wait_for(task, timeout=5.0)
    assert calls, "expected webbrowser.open to be called"
    assert calls[0].startswith("http://127.0.0.1:")


@pytest.mark.asyncio
async def test_run_serve_warns_when_no_adapter(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a web adapter plugin, serve warns but stays up.

    The bundled ``web`` adapter ships in the project's default entry-point
    group, so we force ``_has_web_adapter`` to ``False`` to reconstruct
    the "no adapter installed" state a fresh third-party install would
    see. The test asserts the warning path survives regardless of what
    plugins happen to be bundled.
    """
    from yaya.cli.commands import serve as serve_mod

    monkeypatch.setattr(serve_mod, "_has_web_adapter", lambda _snapshot: False)

    shutdown = asyncio.Event()
    state = CLIState(json_output=False)

    task = asyncio.create_task(
        run_serve(
            state,
            port=0,
            no_open=True,
            strategy="react",
            dev=False,
            shutdown_event=shutdown,
        )
    )
    await asyncio.sleep(0.2)
    shutdown.set()
    await asyncio.wait_for(task, timeout=5.0)

    captured = capsys.readouterr()
    # Warning routed to stderr by ``warn()``.
    assert "no web adapter" in captured.err


@pytest.mark.asyncio
async def test_run_serve_warns_on_non_default_strategy(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--strategy <other> must warn until dispatch is wired."""
    shutdown = asyncio.Event()
    state = CLIState(json_output=False)
    task = asyncio.create_task(
        run_serve(
            state,
            port=0,
            no_open=True,
            strategy="plan-execute",
            dev=False,
            shutdown_event=shutdown,
        )
    )
    await asyncio.sleep(0.2)
    shutdown.set()
    await asyncio.wait_for(task, timeout=5.0)
    captured = capsys.readouterr()
    assert "--strategy 'plan-execute'" in captured.err
    assert "not yet dispatched" in captured.err


@pytest.mark.asyncio
async def test_run_serve_warns_on_dev_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--dev is a placeholder until the vite HMR proxy lands."""
    shutdown = asyncio.Event()
    state = CLIState(json_output=False)
    task = asyncio.create_task(
        run_serve(
            state,
            port=0,
            no_open=True,
            strategy="react",
            dev=True,
            shutdown_event=shutdown,
        )
    )
    await asyncio.sleep(0.2)
    shutdown.set()
    await asyncio.wait_for(task, timeout=5.0)
    captured = capsys.readouterr()
    assert "--dev is accepted" in captured.err
    assert "not yet implemented" in captured.err
