"""Tests for ``yaya serve``.

A full signal-driven integration is unergonomic under :class:`CliRunner`
because Typer runs the command in the current asyncio loop; instead we
exercise :func:`yaya.cli.commands.serve.run_serve` directly with the
``shutdown_event`` test hook.
"""

from __future__ import annotations

import asyncio
import contextlib
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

    # Monkeypatch shim mirroring `loop.add_signal_handler(sig, callback, *args)`;
    # full annotations would couple the test to asyncio's private signature.
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
        # Stub kernel constructor — annotating `_bus`/`_kwargs` would
        # require importing private kernel types just to throw them away.
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


def test_serve_strategy_typo_exits_two(runner: CliRunner, cli_app) -> None:
    """``--strategy plan-execute`` is rejected by Click with exit 2.

    Before the follow-up to PR #62, ``run_serve`` accepted any string
    and warned at runtime; that was observably inert. The argparser
    now exits 2 at argv time via ``click.Choice``.
    """
    result = runner.invoke(cli_app, ["serve", "--strategy", "plan-execute", "--no-open"])
    assert result.exit_code == 2, result.stdout


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


@pytest.mark.asyncio
async def test_run_serve_teardown_runs_when_startup_signalled_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SIGINT-during-startup (sim. via a pre-set shutdown_event + a blocking
    ``registry.start``) must still run teardown.

    Regression for finding #1 / lesson #29: SIGINT arrives before
    ``add_signal_handler`` would have converted it to ``event.set()``,
    so the old ``except Exception`` path let ``KeyboardInterrupt``
    escape past teardown. The fix moves every lifecycle step inside
    one try/finally and uses ``started`` flags so partial shutdown is
    safe. Here we prove the contract: if ``registry.start`` is
    cancelled, ``bus.close`` still runs.
    """
    from yaya.cli.commands import serve as serve_mod

    bus_closed: list[bool] = []
    real_bus_cls = serve_mod.EventBus

    class _TrackingBus(real_bus_cls):  # type: ignore[misc,valid-type]
        async def close(self) -> None:
            bus_closed.append(True)
            await super().close()

    monkeypatch.setattr(serve_mod, "EventBus", _TrackingBus)

    # Force registry.start to block forever so we can cancel the task.
    async def _blocking_start(self) -> None:  # type: ignore[no-untyped-def]
        await asyncio.Event().wait()  # never fires

    monkeypatch.setattr(serve_mod.PluginRegistry, "start", _blocking_start)

    state = CLIState(json_output=True)
    task = asyncio.create_task(
        run_serve(
            state,
            port=0,
            no_open=True,
            strategy="react",
            dev=False,
            shutdown_event=asyncio.Event(),
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert bus_closed, "bus.close() must run even when startup is cancelled"


@pytest.mark.asyncio
async def test_run_serve_registers_signal_handlers_before_registry_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Signal handlers must be wired BEFORE awaiting ``registry.start``.

    Regression for finding #1 / lesson #29. The prior
    ``teardown_runs_when_startup_signalled_early`` test passed its own
    ``shutdown_event`` (``owned_event=False``), so the ``add_signal_handler``
    branch was never exercised — it failed to prove the ordering rule.
    This test takes the ``owned_event=True`` path (no shutdown_event
    argument), records the call timestamps of
    ``loop.add_signal_handler`` and ``PluginRegistry.start``, and asserts
    the former happens first.

    Fast exit: ``registry.start`` is monkeypatched to raise ``SystemExit``
    so ``run_serve`` unwinds immediately after signal handlers are
    observed — we still assert ordering via timestamps captured before
    the raise.
    """
    from yaya.cli.commands import serve as serve_mod

    events: list[tuple[str, float]] = []

    real_add = asyncio.get_event_loop_policy  # placeholder to satisfy lint
    del real_add

    # Wrap add_signal_handler on the running loop. We monkeypatch via the
    # instance at call time by intercepting ``asyncio.get_running_loop``
    # and returning a proxy whose ``add_signal_handler`` records + delegates.
    real_get_running_loop = asyncio.get_running_loop

    def _proxied_get_running_loop():  # type: ignore[no-untyped-def]
        loop = real_get_running_loop()
        original = loop.add_signal_handler

        def _recording(sig, callback, *args):  # type: ignore[no-untyped-def]
            events.append(("add_signal_handler", asyncio.get_running_loop().time()))
            # Platforms without signal support (Windows ProactorEventLoop)
            # raise NotImplementedError — the ordering is what we verify;
            # actual signal delivery is out of scope for this test.
            with contextlib.suppress(NotImplementedError, RuntimeError):
                original(sig, callback, *args)

        loop.add_signal_handler = _recording  # type: ignore[method-assign]
        return loop

    monkeypatch.setattr(serve_mod.asyncio, "get_running_loop", _proxied_get_running_loop)

    async def _recording_start(self) -> None:  # type: ignore[no-untyped-def]
        events.append(("registry.start", asyncio.get_running_loop().time()))
        # Fail fast so run_serve returns 1 via its startup error branch —
        # we have already captured the ordering we care about.
        raise RuntimeError("fast-exit for ordering test")

    monkeypatch.setattr(serve_mod.PluginRegistry, "start", _recording_start)

    state = CLIState(json_output=True)
    # owned_event=True → signal handlers must be installed.
    code = await run_serve(
        state,
        port=0,
        no_open=True,
        strategy="react",
        dev=False,
    )
    # startup_failed path returns 1.
    assert code == 1

    signal_calls = [i for i, (name, _) in enumerate(events) if name == "add_signal_handler"]
    start_calls = [i for i, (name, _) in enumerate(events) if name == "registry.start"]
    assert signal_calls, "add_signal_handler was not called on the owned-event path"
    assert start_calls, "registry.start was not reached"
    # Every signal-handler install must come strictly before registry.start.
    assert max(signal_calls) < min(start_calls), f"signal handlers installed AFTER registry.start; order: {events}"


@pytest.mark.asyncio
async def test_run_serve_calls_webbrowser_in_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finding #10 — ``webbrowser.open`` must go through the default executor.

    We wrap the running loop's ``run_in_executor`` and assert our
    recording wrapper sees ``webbrowser.open`` as the callable. If the
    code regresses to a direct synchronous call, the wrapper never
    fires.
    """
    from yaya.cli.commands import serve as serve_mod

    monkeypatch.setattr(serve_mod, "_has_web_adapter", lambda _snapshot: True)

    # Prevent a real browser launch. Use a named ``def`` (not a lambda)
    # so the recording wrapper can identify the call by ``__name__``.
    def fake_browser_open(_url: str) -> bool:
        return True

    monkeypatch.setattr(serve_mod.webbrowser, "open", fake_browser_open)

    recorded: list[tuple[str, tuple[object, ...]]] = []
    aio_loop = asyncio.get_running_loop()
    original_run_in_executor = aio_loop.run_in_executor

    def _recording(executor, func, *args):  # type: ignore[no-untyped-def]
        recorded.append((getattr(func, "__name__", repr(func)), args))
        return original_run_in_executor(executor, func, *args)

    monkeypatch.setattr(aio_loop, "run_in_executor", _recording)

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
    names = [name for name, _args in recorded]
    assert "fake_browser_open" in names, (
        f"expected webbrowser.open to be dispatched via run_in_executor; recorded={names!r}"
    )
    # Sanity: the URL flowed through unchanged.
    args = next(a for n, a in recorded if n == "fake_browser_open")
    assert args and str(args[0]).startswith("http://127.0.0.1:")


@pytest.mark.asyncio
async def test_serve_resume_stashes_on_config(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--resume <id>`` lands on ``cfg.session.default_id`` (lesson #23).

    A silent no-op on an accepted flag is banned; this test pins the
    stash-on-config behaviour + the ``serve.resume.staged`` emission so a
    future refactor cannot quietly regress to ``del resume``.
    """
    from yaya.kernel import KernelConfig

    cfg = KernelConfig()
    assert cfg.session.default_id is None

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
            kernel_config=cfg,
            resume="mysession",
        )
    )
    await asyncio.sleep(0.2)
    shutdown.set()
    code = await asyncio.wait_for(task, timeout=5.0)
    assert code == 0
    assert cfg.session.default_id == "mysession"

    captured = capsys.readouterr()
    joined = captured.out
    assert '"action": "serve.resume.staged"' in joined
    assert '"session_id": "mysession"' in joined
