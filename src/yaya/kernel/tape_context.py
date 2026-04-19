"""Default tape-context selection: :class:`~republic.TapeEntry` → LLM messages.

A :class:`~republic.TapeContext` is the "selection strategy" that
derives an LLM chat history from the append-only tape. The default
policy here mirrors the ``bub`` reference (see
``vendor/bub/src/bub/builtin/context.py``) but re-implemented in-tree
so the yaya kernel never depends on ``bub``:

* ``message`` entries pass through (payload is already a
  ``{"role", "content"}`` dict plus optional extras).
* ``tool_call`` entries become one assistant message with the
  ``tool_calls`` array set.
* ``tool_result`` entries expand to one ``role="tool"`` message per
  result, correlated by order with the previous ``tool_call`` entry.
* ``anchor`` entries are boundary markers — skipped in the default
  context so compaction / session-start anchors do not leak into
  prompts. Callers that want anchor-aware context render it
  themselves from the same tape.
* ``event`` entries are skipped unless their ``meta`` flags
  ``include_in_context=True`` — most observational events (bus
  mirrors, plugin errors, …) should not inflate the LLM prompt.

The selection is stateless and pure so it plays well with
``TapeContext`` composition (``after_last_anchor`` etc.) and with
replay-style testing.

Layering: depends only on :mod:`republic` and the Python standard
library. No imports from ``yaya.cli``, ``yaya.plugins``, or
``yaya.core``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any, cast

from republic import TapeContext, TapeEntry
from republic.tape import AsyncTapeManager

__all__ = [
    "after_last_anchor",
    "default_tape_context",
    "select_messages",
]


def default_tape_context() -> TapeContext:
    """Return the default :class:`~republic.TapeContext` for yaya.

    The returned context routes entries through :func:`select_messages`
    with no anchor filter, so callers who want
    "post-compaction" views should compose it with an explicit
    ``after_last_anchor`` or ``after_anchor`` query — see
    :func:`after_last_anchor` for the helper.
    """
    return TapeContext(select=select_messages)


def select_messages(
    entries: Iterable[TapeEntry],
    _context: TapeContext,
) -> list[dict[str, Any]]:
    """Project ``entries`` onto an OpenAI-style message list.

    Args:
        entries: Ordered tape entries (parent-first when the tape
            overlays a fork, chronological otherwise).
        _context: The calling :class:`~republic.TapeContext`. Unused
            at this layer — kept in the signature so the callable is
            compatible with ``TapeContext.select``.

    Returns:
        A list of plain dicts suitable to hand to an LLM provider.
    """
    messages: list[dict[str, Any]] = []
    pending_calls: list[dict[str, Any]] = []

    for entry in entries:
        kind = entry.kind
        if kind == "message":
            _append_message(messages, entry)
        elif kind == "tool_call":
            pending_calls = _append_tool_call(messages, entry)
        elif kind == "tool_result":
            _append_tool_result(messages, pending_calls, entry)
            pending_calls = []
        elif kind == "event" and bool(entry.meta.get("include_in_context")):
            _append_event(messages, entry)
        elif kind == "anchor" and _is_compaction_anchor(entry):
            # Elide everything accumulated so far: the compaction
            # summary REPLACES the pre-anchor prefix. The surviving
            # messages are the summary (as a system message) plus
            # whatever comes after the anchor.
            messages = []
            pending_calls = []
            _append_compaction_summary(messages, entry)
        # other anchor / error / anything else: intentionally skipped.
    return messages


def _is_compaction_anchor(entry: TapeEntry) -> bool:
    """True when ``entry`` is an anchor emitted by the compaction runtime.

    Compaction anchors carry ``state={"kind": "compaction", ...}`` per
    the contract in :mod:`yaya.kernel.compaction`. All other anchors
    (``session/start``, fork markers, user-authored handoffs) are
    skipped by :func:`select_messages` so their boundary role does not
    leak into the LLM prompt.
    """
    if entry.kind != "anchor":
        return False
    state = entry.payload.get("state")
    if not isinstance(state, dict):
        return False
    return cast("dict[str, Any]", state).get("kind") == "compaction"


def _append_compaction_summary(
    messages: list[dict[str, Any]],
    entry: TapeEntry,
) -> None:
    """Inject the compaction summary as a ``role="system"`` message.

    Preserves the "summary replaces the elided prefix" contract: when a
    context is rebuilt from entries that cross a compaction anchor, the
    reader sees the summary in-place, not the raw pre-compaction log.
    The summary text is whatever the :class:`~yaya.kernel.compaction.Summarizer`
    returned — rendered verbatim so providers can decide how to frame
    it.
    """
    state = entry.payload.get("state")
    if not isinstance(state, dict):
        return
    summary_value: Any = cast("dict[str, Any]", state).get("summary", "")
    summary = summary_value if isinstance(summary_value, str) else str(summary_value)
    messages.append({
        "role": "system",
        "content": f"[compacted history]\n{summary}" if summary else "[compacted history]",
    })


async def after_last_anchor(
    manager: AsyncTapeManager,
    tape_name: str,
) -> list[TapeEntry]:
    """Return every tape entry appended after the most recent anchor.

    Post-compaction context queries hand these entries to
    :func:`select_messages` to rebuild the LLM history without the
    summarised prefix. Uses ``republic``'s built-in query composer so
    fork overlays are honoured.

    Args:
        manager: The live tape manager.
        tape_name: Stable identifier for the tape within ``manager``.

    Returns:
        A list of entries in insertion order. Empty when the tape
        has no anchors yet.
    """
    entries = await manager.query_tape(tape_name).last_anchor().all()
    # Republic emits a synthetic ``event(name="handoff")`` after each
    # anchor so observers without anchor support still see the
    # boundary. It never represents user-authored context, so strip it
    # out of the post-anchor view — otherwise compaction re-summarises
    # its own marker on every pass.
    return [e for e in cast("list[TapeEntry]", entries) if not _is_handoff_event(e)]


def _is_handoff_event(entry: TapeEntry) -> bool:
    """True when ``entry`` is republic's synthetic post-anchor handoff event."""
    return entry.kind == "event" and entry.payload.get("name") == "handoff"


def _append_message(messages: list[dict[str, Any]], entry: TapeEntry) -> None:
    payload = entry.payload
    messages.append(dict(payload))


def _append_tool_call(messages: list[dict[str, Any]], entry: TapeEntry) -> list[dict[str, Any]]:
    calls = _normalise_calls(entry.payload.get("calls"))
    if calls:
        messages.append({"role": "assistant", "content": "", "tool_calls": calls})
    return calls


def _append_tool_result(
    messages: list[dict[str, Any]],
    pending_calls: list[dict[str, Any]],
    entry: TapeEntry,
) -> None:
    results_value = entry.payload.get("results")
    if not isinstance(results_value, list):
        return
    # mypy already infers ``list``; pyright keeps "Unknown" element types
    # because republic ships without ``py.typed``. The helper hides the
    # narrowed type from pyright so the resulting list elements are Any.
    results: list[Any] = _as_any_list(results_value)
    for index, result in enumerate(results):
        messages.append(_build_tool_result_message(result, pending_calls, index))


def _build_tool_result_message(
    result: object,
    pending_calls: list[dict[str, Any]],
    index: int,
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "tool", "content": _render_result(result)}
    if index >= len(pending_calls):
        return message
    call = pending_calls[index]
    call_id = call.get("id")
    if isinstance(call_id, str) and call_id:
        message["tool_call_id"] = call_id
    function = call.get("function")
    if isinstance(function, dict):
        name = cast("dict[str, Any]", function).get("name")
        if isinstance(name, str) and name:
            message["name"] = name
    return message


def _append_event(messages: list[dict[str, Any]], entry: TapeEntry) -> None:
    name = str(entry.payload.get("name", "event"))
    data = entry.payload.get("data", {})
    messages.append({
        "role": "system",
        "content": f"[event:{name}] {json.dumps(data, ensure_ascii=False, default=str)}",
    })


def _normalise_calls(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    # See ``_append_tool_result`` for the helper rationale.
    items: list[Any] = _as_any_list(value)
    calls: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            calls.append(dict(cast("dict[str, Any]", item)))
    return calls


def _as_any_list(value: Any) -> list[Any]:
    """Promote an opaque iterable to ``list[Any]`` for both mypy and pyright.

    pyright's narrowing turns ``isinstance(_, list)`` into ``list[Unknown]``
    even when the source type is ``object``; mypy in turn rejects an
    explicit ``cast`` as redundant. Hiding the conversion behind a
    function boundary lets the return annotation be the source of truth
    for both.
    """
    return list(cast("list[Any]", value))


def _render_result(result: object) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except TypeError:
        return str(result)
