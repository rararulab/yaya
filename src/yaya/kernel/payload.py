"""Typed accessors for ``Event.payload``.

``Event.payload`` is ``dict[str, Any]`` by design: the plugin protocol
(see ``docs/dev/plugin-protocol.md``) specifies a different payload shape
per event kind, and the catalog is open-ended for plugin extensions under
``x.<plugin>.<kind>``. That design forces ``Any`` at the boundary. The
helpers below localise the ``Any`` leak: each returns a concrete type so
call sites in the kernel loop never traffic in ``Unknown``.

These helpers are **kernel-internal**. Plugin authors should not import
them — plugins consuming a specific event kind should build their own
typed view with whatever validation suits them (often nothing beyond
``isinstance``). The helpers exist to keep the kernel's own
``payload.get()`` sites honest without each site reimplementing the
same narrow-and-coerce pattern.

Coercion semantics: when a key is missing or holds a wrong-typed value,
the helpers return the documented default and emit a DEBUG log entry.
This is a deliberate trade-off — plugin-emitted malformed payloads
should not crash the kernel, but they should also not be invisible. A
flood of DEBUG "payload coerced" lines is a signal to investigate the
upstream plugin.
"""

from __future__ import annotations

import logging
from typing import Any, cast

_logger = logging.getLogger(__name__)


def payload_str(payload: dict[str, Any], key: str, default: str = "") -> str:
    """Return ``payload[key]`` as a string, or ``default`` on miss / wrong type.

    Logs at DEBUG when a coercion actually fires so silently-malformed
    plugin payloads are still traceable via ``docs/dev/debug.md``.
    """
    value = payload.get(key)
    if isinstance(value, str):
        return value
    if value is not None:
        _logger.debug("payload_str coerced %s at key=%r → default", type(value).__name__, key)
    return default


def payload_int(payload: dict[str, Any], key: str, default: int) -> int:
    """Return ``payload[key]`` as an int, or ``default`` on miss / wrong type.

    Rejects ``bool`` (Python treats ``True``/``False`` as ``int`` subclass but
    callers almost always mean a true integer).
    """
    value = payload.get(key)
    if isinstance(value, bool):
        _logger.debug("payload_int rejected bool at key=%r → default", key)
        return default
    if isinstance(value, int):
        return value
    if value is not None:
        _logger.debug("payload_int coerced %s at key=%r → default", type(value).__name__, key)
    return default


def payload_dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    """Return ``payload[key]`` as ``dict[str, Any]`` when it is a dict, else ``{}``.

    The runtime guard is the ``isinstance`` check; the cast to
    ``dict[str, Any]`` assumes string keys — the norm for plugin events
    that deserialize from JSON.
    """
    value = payload.get(key)
    if not isinstance(value, dict):
        if value is not None:
            _logger.debug("payload_dict coerced %s at key=%r → {}", type(value).__name__, key)
        return {}
    return cast("dict[str, Any]", value)


def payload_list_of_dicts(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Return ``payload[key]`` as ``list[dict[str, Any]]``, filtering non-dicts.

    Non-dict items inside the list are silently dropped — the strategy
    plugin would have had to emit them and it is cheaper to ignore
    broken elements than to surface a typed error per element. A DEBUG
    line fires once per drop to keep the upstream bug traceable.
    """
    value = payload.get(key)
    if not isinstance(value, list):
        if value is not None:
            _logger.debug(
                "payload_list_of_dicts coerced %s at key=%r → []",
                type(value).__name__,
                key,
            )
        return []
    out: list[dict[str, Any]] = []
    # Tool disagreement: mypy narrows ``value`` to ``list[Any]`` (so the
    # loop element is ``Any``), pyright narrows to ``list[Unknown]`` (so
    # the loop element is ``Unknown``). The pyright-specific suppression
    # below covers the one rule it fires on; mypy ignores ``# pyright:``
    # comments entirely, so both checkers stay happy with no cast and no
    # mypy-side ``# type: ignore``.
    for item in value:  # pyright: ignore[reportUnknownVariableType]
        if isinstance(item, dict):
            out.append(cast("dict[str, Any]", item))
        else:
            _logger.debug(
                "payload_list_of_dicts dropped %s element at key=%r",
                type(item).__name__,  # pyright: ignore[reportUnknownArgumentType]
                key,
            )
    return out
