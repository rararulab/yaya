"""Kernel error taxonomy.

A small, closed hierarchy every plugin and CLI surface raises against,
so the bus, the registry, and operator-facing tools (logs, ``yaya
config show``) can attribute failures to a category without sniffing
strings. Each class is plain :class:`Exception` underneath — no
metaclass magic — so plugins can subclass freely (e.g.
``OpenAIError(PluginError)``).

We deliberately do **not** shadow :class:`builtins.TimeoutError`. Code
across asyncio and stdlib catches ``TimeoutError`` to mean
"asyncio.TimeoutError"; redefining it in this module would silently
change semantics for anything that did ``from yaya.kernel.errors import
TimeoutError``. Use :class:`YayaTimeoutError` instead.

Layering: stdlib only. No imports from ``cli``, ``plugins``, or
``core``.
"""

from __future__ import annotations

__all__ = [
    "ConfigError",
    "KernelError",
    "PluginError",
    "YayaError",
    "YayaTimeoutError",
]


class YayaError(Exception):
    """Base class for every yaya-defined error.

    Catch this when you want to net all yaya failures without
    swallowing unrelated stdlib exceptions.
    """


class KernelError(YayaError):
    """Crash-worthy kernel bug.

    Raised when a kernel invariant is violated (corrupt event envelope,
    bus state machine in an impossible mode). Production code should
    let this propagate so the process exits — masking it papers over
    real bugs.
    """


class PluginError(YayaError):
    """Recoverable plugin failure.

    Raised by a plugin from inside ``on_load`` / ``on_event`` to flag
    "this request failed but the rest of the kernel should keep
    running". The bus catches it, isolates the offending subscriber,
    and emits a synthetic ``plugin.error`` event whose payload includes
    a stable traceback hash for de-duping noisy plugins in logs.
    """


class ConfigError(YayaError):
    """User-facing config problem.

    Raised by ``load_config()`` callers (or plugins validating their
    sub-tree in ``on_load``) when the resolved config is structurally
    valid but semantically wrong (missing required key, conflicting
    flags). The CLI surfaces these with a non-zero exit code and a
    human-readable message — never a stacktrace.
    """


class YayaTimeoutError(YayaError):
    """Generic yaya-level timeout.

    Distinct from :class:`asyncio.TimeoutError` and
    :class:`builtins.TimeoutError` so call sites that catch the asyncio
    timeout don't accidentally swallow ours. Use this when the bus or
    a plugin enforces an explicit deadline that's NOT an asyncio.wait_for.
    """
