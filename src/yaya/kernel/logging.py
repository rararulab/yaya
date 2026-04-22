"""Loguru-based structured logging for the yaya kernel.

A single :func:`configure_logging` entry point wires loguru sinks for
stderr (level driven by :class:`KernelConfig.log_level`) and a rotated
file under ``$XDG_STATE_HOME/yaya/logs/yaya.log``. Stdlib ``logging``
records are intercepted and routed into loguru so third-party libs
still appear in the unified stream. A redaction filter scrubs any
``record["extra"]`` field whose key looks like a secret
(``token`` / ``key`` / ``secret`` / ``password`` / ``passphrase``)
before the record reaches a sink — operators dumping logs into a bug
report don't have to second-guess.

Layering: depends on :mod:`yaya.kernel.config` and the standard
library; no imports from ``cli``, ``plugins``, ``core``, ``bus``,
``loop``, or ``registry``.
"""

from __future__ import annotations

import json
import logging as _stdlib_logging
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, override

from loguru import logger

if TYPE_CHECKING:
    from loguru import Record

from yaya.kernel.config import KernelConfig

__all__ = [
    "DEFAULT_LOG_DIR_ENV",
    "JSON_ENV_VAR",
    "configure_logging",
    "default_log_dir",
    "get_plugin_logger",
    "logger",
]


JSON_ENV_VAR = "YAYA_LOG_JSON"
"""Env var that flips on JSON-per-line formatting on the stderr sink."""

DEFAULT_LOG_DIR_ENV = "YAYA_LOG_DIR"
"""Test-only override for the rotated file sink directory.

Production code should leave this unset and let
:func:`default_log_dir` resolve XDG. Tests set it to ``tmp_path`` so
parallel runs do not collide on a single rotation file.
"""


# Anything matching this regex (case-insensitive) is rendered as ``***``.
# Mirrors the redaction predicate used by ``yaya config show`` so
# operators see one consistent rule.
_SECRET_KEY_RE = re.compile(r".*(token|key|secret|password|passphrase).*", re.IGNORECASE)
# String values that look like secrets even when the key doesn't —
# e.g. an API key handed to ``logger.info("hit {}", "sk-abc...")``.
_SECRET_VALUE_RE = re.compile(r"^(sk-|Bearer\s+).+", re.IGNORECASE)
_REDACTED = "***"


_CONFIGURED: dict[str, bool] = {"done": False}
"""Module-level idempotency flag.

We do not want a second :func:`configure_logging` to stack additional
sinks on top of the existing ones (would double every log line). The
flag plus :func:`logger.remove` make the function safe to call from
any process-entry — CLI root callback, test fixture, ``yaya doctor``.
"""


def default_log_dir() -> Path:
    """Return ``$XDG_STATE_HOME/yaya/logs`` (or the ``~/.local/state`` fallback).

    Tests can override the resolved directory by setting the
    :data:`DEFAULT_LOG_DIR_ENV` env var (``YAYA_LOG_DIR``); production
    code never sets it.
    """
    override = os.environ.get(DEFAULT_LOG_DIR_ENV)
    if override:
        return Path(override)
    raw = os.environ.get("XDG_STATE_HOME") or ""
    base = Path(raw) if raw else Path.home() / ".local" / "state"
    return base / "yaya" / "logs"


def _redact_value(value: Any) -> Any:
    """Replace value with ``***`` if it looks like an inline secret."""
    if isinstance(value, str) and _SECRET_VALUE_RE.match(value):
        return _REDACTED
    return value


def _redaction_filter(record: Record) -> bool:
    """Loguru filter: scrub secret-looking keys in ``record["extra"]``.

    Loguru calls filters before formatting and lets us mutate
    ``record`` in place. We walk the bound ``extra`` dict — which is
    where ``logger.bind(api_key=...)`` lands — and replace any
    secret-looking value with ``***``. Returns True so the record is
    still emitted (filter == "let through?", not "redact?").
    """
    extra: Any = record.get("extra")
    if not isinstance(extra, dict):
        return True
    for key in list(extra.keys()):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        if not isinstance(key, str):
            continue
        if _SECRET_KEY_RE.match(key):
            extra[key] = _REDACTED
        else:
            extra[key] = _redact_value(extra[key])
    return True


def _json_sink(message: object) -> None:
    """Loguru sink that writes one compact JSON object per line to stderr.

    ``message`` here is a loguru ``Message`` — a string subclass whose
    ``.record`` attribute carries the full structured record. We
    serialise the fields a downstream log shipper actually wants
    (timestamp, level, plugin, message, extra) instead of letting
    loguru's own ``serialize=True`` emit its larger default schema.
    """
    record = getattr(message, "record", None)
    if record is None:  # pragma: no cover - loguru always sets .record
        sys.stderr.write(str(message))
        return
    extra = dict(record.get("extra") or {})
    payload = {
        "ts": record["time"].isoformat(),
        "level": record["level"].name,
        "logger": record["name"],
        "message": record["message"],
        "plugin": extra.pop("plugin", None),
        "extra": extra,
    }
    sys.stderr.write(json.dumps(payload, default=str) + "\n")


class _InterceptHandler(_stdlib_logging.Handler):
    """Route stdlib ``logging`` records into the loguru pipeline.

    Adopted verbatim from loguru's documented intercept recipe so any
    third-party library that calls ``logging.getLogger(...).info(...)``
    still ends up in our sinks (and our redaction filter).
    """

    @override
    def emit(self, record: _stdlib_logging.LogRecord) -> None:
        # Map the numeric stdlib level to a loguru level when possible;
        # fall back to the numeric value so unknown levels still land.
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = str(record.levelno)
        # Hop frames until we exit the stdlib logging machinery so the
        # loguru record carries the actual caller's filename / line.
        frame: Any = _stdlib_logging.currentframe()
        depth = 2
        while frame and frame.f_code.co_filename == _stdlib_logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def _install_stdlib_intercept(level: str) -> None:
    """Re-install the stdlib intercept handler on the root logger.

    ``propagate`` stays True on each yaya logger so pytest's ``caplog``
    can still observe records via the standard ``logging`` pipeline —
    a hard requirement for tests that mix loguru with caplog.
    """
    root = _stdlib_logging.getLogger()
    # Drop any previous intercept handlers so a second call doesn't
    # double-route every record into loguru.
    for handler in list(root.handlers):
        if isinstance(handler, _InterceptHandler):
            root.removeHandler(handler)
    root.addHandler(_InterceptHandler())
    root.setLevel(level)


def configure_logging(config: KernelConfig) -> None:
    """Wire loguru sinks for stderr and a rotated file. Idempotent.

    Args:
        config: Resolved kernel config. ``log_level`` drives the stderr
            sink level; the file sink is always DEBUG so post-mortems
            have full detail.

    Sinks:
      * **stderr** — coloured rich text when ``stderr.isatty()``,
        plain otherwise. Switched to one-JSON-per-line when
        ``YAYA_LOG_JSON=1`` is set so structured-log consumers can
        ingest the stream verbatim.
      * **file** — ``$XDG_STATE_HOME/yaya/logs/yaya.log``, DEBUG
        level, 10 MiB rotation x 5 backups. Always plain text — file
        sinks are for humans.
    """
    # Wipe loguru's built-in default sink AND any sink we previously
    # added so the function is safe to call multiple times. The
    # idempotency flag below prevents the work itself from doubling
    # but the explicit ``logger.remove()`` is what makes the second
    # call observable as a clean state.
    logger.remove()

    level = (config.log_level or "INFO").upper()
    json_mode = os.environ.get(JSON_ENV_VAR, "").strip() in {"1", "true", "True", "TRUE"}

    if json_mode:
        logger.add(
            _json_sink,
            level=level,
            filter=_redaction_filter,
        )
    else:
        # ``colorize=None`` lets loguru auto-detect the TTY; explicit
        # for clarity at the call site.
        colorize = sys.stderr.isatty()
        logger.add(
            sys.stderr,
            level=level,
            filter=_redaction_filter,
            colorize=colorize,
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss}</green> "
                "<level>{level: <8}</level> "
                "<cyan>{extra[plugin]}</cyan> "
                "<level>{message}</level>"
            )
            if colorize
            else "{time:YYYY-MM-DD HH:mm:ss} {level: <8} {extra[plugin]} {message}",
        )

    log_dir = default_log_dir()
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        logger.add(
            log_dir / "yaya.log",
            level="DEBUG",
            rotation="10 MB",
            retention=5,
            filter=_redaction_filter,
            enqueue=False,
            format="{time:YYYY-MM-DD HH:mm:ss} {level: <8} {name} {extra[plugin]} {message}",
        )
    except OSError as exc:
        # File sink is best-effort — a read-only filesystem (CI sandbox,
        # docker layer) must not break the kernel. Log to stderr and
        # keep the in-memory sink running.
        logger.warning("file log sink disabled: {}", exc)

    # Bind a default ``plugin`` extra so format strings that reference
    # ``{extra[plugin]}`` don't KeyError before any plugin logs.
    logger.configure(extra={"plugin": "kernel"})

    _install_stdlib_intercept(level)
    _CONFIGURED["done"] = True


def get_plugin_logger(name: str) -> Any:
    """Return a loguru logger pre-bound to ``plugin=name``.

    Plugins call this through :class:`KernelContext.logger`; they do
    not import :mod:`loguru` directly. The returned object exposes the
    standard ``info`` / ``warning`` / ``error`` / ``debug`` API and
    therefore satisfies the structural ``logging.Logger`` contract
    plugins are typed against.

    Args:
        name: The plugin's ``name`` attribute. Used as the ``plugin``
            field on every record so ``grep plugin=llm_openai`` works.

    Returns:
        A loguru ``Logger`` instance. Typed as :data:`Any` to avoid
        leaking loguru into the plugin ABI surface — see
        :class:`KernelContext.logger` for the rationale.
    """
    return logger.bind(plugin=name)
