"""Tests for ``yaya.kernel.logging``.

Bound to ``specs/kernel-logging.spec`` scenarios:
* configure → ``test_configure_logging_idempotent``
* redact → ``test_redaction_filter_scrubs_secret_keys``
* json-mode → ``test_json_mode_emits_valid_json_per_line``
* file-rotation → ``test_file_sink_rotates_at_size_limit``
* intercept → ``test_stdlib_logging_intercept_routes_to_loguru``
"""

from __future__ import annotations

import json
import logging as stdlib_logging
from pathlib import Path

import pytest
from loguru import logger as loguru_logger

from yaya.kernel import KernelConfig
from yaya.kernel.logging import (
    DEFAULT_LOG_DIR_ENV,
    JSON_ENV_VAR,
    _redaction_filter,
    configure_logging,
    default_log_dir,
    get_plugin_logger,
)


@pytest.fixture(autouse=True)
def _reset_loguru() -> None:
    """Wipe loguru sinks between tests so handler counts stay deterministic."""
    loguru_logger.remove()
    yield
    loguru_logger.remove()


@pytest.fixture
def _tmp_log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the file sink under ``tmp_path``."""
    target = tmp_path / "logs"
    monkeypatch.setenv(DEFAULT_LOG_DIR_ENV, str(target))
    return target


def test_default_log_dir_honours_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``XDG_STATE_HOME`` flips the default; ``~/.local/state`` is the fallback."""
    monkeypatch.delenv(DEFAULT_LOG_DIR_ENV, raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert default_log_dir() == tmp_path / "state" / "yaya" / "logs"

    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path / "home"))
    assert default_log_dir() == tmp_path / "home" / ".local" / "state" / "yaya" / "logs"


def test_configure_logging_idempotent(_tmp_log_dir: Path) -> None:
    """Calling ``configure_logging`` twice does not stack additional sinks."""
    cfg = KernelConfig(log_level="INFO")
    configure_logging(cfg)
    after_first = len(loguru_logger._core.handlers)  # pyright: ignore[reportAttributeAccessIssue]
    configure_logging(cfg)
    after_second = len(loguru_logger._core.handlers)  # pyright: ignore[reportAttributeAccessIssue]
    assert after_first == after_second


@pytest.mark.parametrize(
    "secret_key",
    ["api_key", "x_token", "SECRET_PASSPHRASE", "PASSWORD", "openai_token"],
)
def test_redaction_filter_scrubs_secret_keys(secret_key: str) -> None:
    """All five regex variants are scrubbed by ``_redaction_filter``."""
    record: dict[str, object] = {"extra": {secret_key: "leak-me", "model": "gpt-4o"}}
    _redaction_filter(record)
    assert record["extra"][secret_key] == "***"  # type: ignore[index]
    assert record["extra"]["model"] == "gpt-4o"  # type: ignore[index]


def test_redaction_filter_scrubs_secret_values() -> None:
    """Strings shaped like ``sk-...`` or ``Bearer ...`` are redacted by value."""
    record: dict[str, object] = {"extra": {"note": "sk-abc123", "auth": "Bearer xyz"}}
    _redaction_filter(record)
    assert record["extra"]["note"] == "***"  # type: ignore[index]
    assert record["extra"]["auth"] == "***"  # type: ignore[index]


def test_json_mode_emits_valid_json_per_line(
    _tmp_log_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``YAYA_LOG_JSON=1`` produces one parsable JSON object per logged line."""
    monkeypatch.setenv(JSON_ENV_VAR, "1")
    configure_logging(KernelConfig(log_level="INFO"))
    plug = get_plugin_logger("test-plugin")
    plug.info("hello structured")
    captured = capsys.readouterr()
    lines = [line for line in captured.err.splitlines() if line.strip()]
    assert lines, f"expected at least one JSON line; got: {captured.err!r}"
    parsed = json.loads(lines[-1])
    assert parsed["message"] == "hello structured"
    assert parsed["plugin"] == "test-plugin"
    assert parsed["level"] == "INFO"


def test_file_sink_rotates_at_size_limit(_tmp_log_dir: Path) -> None:
    """Writing past the rotation budget creates a backup file alongside ``yaya.log``.

    Loguru rotates by renaming the active file to
    ``yaya.<timestamp>.log`` and reopens ``yaya.log`` empty, so the
    glob below covers both ``yaya.log`` itself and any rotated
    siblings — at least two files appear once the rotation triggered.
    """
    configure_logging(KernelConfig(log_level="DEBUG"))
    # Drop the noisy stderr sink so the test isn't I/O-bound on terminal output.
    big = "x" * 16384
    # 10 MB rotation; write ~16 MiB to guarantee a flip without
    # hammering stderr 30k times.
    for _ in range(1024):
        loguru_logger.debug(big)
    loguru_logger.complete()
    files = sorted(_tmp_log_dir.glob("yaya*.log*"))
    assert len(files) >= 2, f"expected rotation backup; saw {[p.name for p in files]}"


def test_stdlib_logging_intercept_routes_to_loguru(
    _tmp_log_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Records emitted via ``logging.getLogger`` reach the loguru stderr sink."""
    configure_logging(KernelConfig(log_level="DEBUG"))
    stdlib_logging.getLogger("third_party").info("forwarded message")
    loguru_logger.complete()
    captured = capsys.readouterr()
    assert "forwarded message" in captured.err


def test_get_plugin_logger_binds_plugin_name(
    _tmp_log_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Plugin-bound logger surfaces the plugin name in the rendered line."""
    configure_logging(KernelConfig(log_level="INFO"))
    get_plugin_logger("llm_openai").info("hi")
    captured = capsys.readouterr()
    assert "llm_openai" in captured.err


def test_configure_logging_survives_readonly_log_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failing file sink does not crash ``configure_logging``."""
    # Point the dir at an existing FILE so mkdir(parents=True) raises.
    blocker = tmp_path / "blocker"
    blocker.write_text("not-a-dir")
    monkeypatch.setenv(DEFAULT_LOG_DIR_ENV, str(blocker / "logs"))
    # mkdir on a path whose parent is a regular file raises NotADirectoryError.
    configure_logging(KernelConfig(log_level="INFO"))
    # Stderr sink must still be live — log a line and ensure no exception.
    loguru_logger.info("still alive")
