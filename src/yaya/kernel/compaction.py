"""Conversation compaction — Context + pluggable Summarizer (#29).

The agent loop accumulates messages across turns. Without a bound on
context size, long sessions blow past the provider's window and crash
mid-turn. This module adds a small but production-shaped compaction
layer that sits on top of the tape store (#32):

* :class:`Summarizer` — minimal Protocol. One method, ``summarize``,
  returns a free-text summary given a list of
  :class:`~republic.TapeEntry`. Pluggable so callers can wire the
  active LLM provider, a deterministic fake (tests), or a custom
  post-processor.
* :func:`estimate_text_tokens` — tokenizer-agnostic heuristic
  (``len(text) // 4``). Deliberately cheap: binding to a specific
  tokenizer would re-introduce a provider dependency in the kernel
  and make offline compaction tests slow. Overcounts short messages
  and undercounts heavy unicode — acceptable for a threshold check.
* :func:`should_auto_compact` — compares a live :class:`TokenUsage`
  (or the estimator's output) against the configured threshold. The
  exact same function drives :class:`CompactionManager`'s auto path
  and any caller that wants to gate its own manual compaction.
* :func:`compact_session` — core mutation. Drains entries since the
  last anchor, runs the summariser, and appends a
  ``kind="anchor"`` entry carrying ``state={"kind": "compaction",
  "summary": ..., "tokens_before": ...}`` via
  :meth:`~yaya.kernel.session.Session.handoff`. Emits
  ``session.compaction.{started,completed,failed}`` for observability.
* :class:`CompactionManager` — kernel-internal subscriber that
  auto-triggers compaction after every public-kind event when the
  current tape exceeds the configured threshold. Guards against
  concurrent compactions on the same session (single in-flight),
  retries on failure with exponential backoff up to three attempts
  per session, and caps its per-session in-flight map (lesson #6:
  unbounded dicts leak).

**Routing rule** (lesson #2). ``session.compaction.*`` events ALWAYS
route on ``session_id="kernel"``, not on the originating tape's
``session_id``. The originating session worker is typically blocked on
the turn that triggered the check; emitting on its own queue would
deadlock the FIFO. The compaction manager's own handlers run on the
``"kernel"`` worker; adapters correlate events back to the tape via
the ``target_session_id`` field on the payload.

Layering: depends on :mod:`yaya.kernel.bus`, :mod:`yaya.kernel.events`,
:mod:`yaya.kernel.session`, :mod:`yaya.kernel.tape_context`, and the
Python standard library. No imports from ``yaya.cli``,
``yaya.plugins``, or ``yaya.core``.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from republic import TapeEntry

from yaya.kernel.events import new_event
from yaya.kernel.session import Session
from yaya.kernel.tape_context import after_last_anchor

if TYPE_CHECKING:  # pragma: no cover - type-only import
    from yaya.kernel.bus import EventBus, Subscription
    from yaya.kernel.llm import LLMProvider

__all__ = [
    "COMPACTION_ANCHOR_KIND",
    "CompactionManager",
    "LLMSummarizer",
    "Summarizer",
    "compact_session",
    "estimate_text_tokens",
    "install_compaction_manager",
    "should_auto_compact",
]


COMPACTION_ANCHOR_KIND: str = "compaction"
"""Value stamped into an anchor's ``state["kind"]`` for compaction anchors.

Distinct from ``"session/start"`` / other handoff names so the context
selector (:func:`yaya.kernel.tape_context.select_messages`) can recognise
the anchor and inject the stored summary as a system message.
"""


_DEFAULT_CHARS_PER_TOKEN = 4
"""Heuristic from the OpenAI tokenizer FAQ — average across English prose."""

_MAX_RETRIES = 3
"""Per-session retry cap (lesson #29: translate exceptions to terminal events)."""

_INFLIGHT_CAP = 1024
"""Max live session ids in the manager's in-flight / attempts maps (lesson #6)."""

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Summarizer protocol.
# ---------------------------------------------------------------------------


@runtime_checkable
class Summarizer(Protocol):
    """Opinionated single-method Protocol for compaction summarisers.

    Implementations collapse a list of raw tape entries into a plain
    string suitable to be stored on a compaction anchor and later
    injected into the LLM's system prompt. The contract is deliberately
    minimal so tests can provide a deterministic fake and so a future
    ``llm-provider``-backed implementation slots in without touching the
    call-sites.
    """

    async def summarize(
        self,
        entries: list[TapeEntry],
        target_tokens: int,
    ) -> str:
        """Produce a text summary for ``entries``.

        Args:
            entries: Tape entries since the previous anchor. Order is
                chronological. Entries are read-only; implementations
                MUST NOT mutate them.
            target_tokens: Soft ceiling the summariser should try not to
                exceed in the returned string. The heuristic estimator
                and the manager config both speak in "approximate
                tokens" — an implementation is free to ignore this.

        Returns:
            A plain string. Empty output is allowed (rare) and is
            persisted verbatim; callers that want a placeholder should
            supply one themselves.
        """
        ...


# ---------------------------------------------------------------------------
# Heuristics.
# ---------------------------------------------------------------------------


def _entry_text(entry: TapeEntry) -> str:
    """Return a stable text rendering of ``entry`` for token estimation."""
    payload = entry.payload
    if entry.kind == "message":
        content = payload.get("content", "")
        return content if isinstance(content, str) else str(content)
    # Tool calls / results / events: JSON-encode the payload. Undercounts
    # binary blobs, overcounts numbers, but stable enough for a threshold.
    try:
        return json.dumps(payload, ensure_ascii=False, default=str)
    # PEP 758 (py3.12+) tuple-except without parens; ruff format normalises
    # a parenthesised form back to this under ``target-version = "py314"``
    # (lesson #16). Both forms catch the same exception set.
    except TypeError, ValueError:
        return str(payload)


def estimate_text_tokens(entries: list[TapeEntry]) -> int:
    """Return the approximate token count for ``entries``.

    Uses ``len(text) // _DEFAULT_CHARS_PER_TOKEN``; tokenizer-agnostic
    so the kernel never depends on a particular vendor tokenizer. The
    integer output is deterministic for fixed input — the estimator is
    pure, which AC-03 of the spec relies on.

    Args:
        entries: Tape entries to estimate over. An empty list returns 0.

    Returns:
        Non-negative integer estimated token count.
    """
    if not entries:
        return 0
    total_chars = sum(len(_entry_text(e)) for e in entries)
    return total_chars // _DEFAULT_CHARS_PER_TOKEN


def should_auto_compact(
    current_tokens: int,
    *,
    threshold: int,
) -> bool:
    """Return True when ``current_tokens`` exceeds the compaction threshold.

    Exposed as a pure function so strategies and manual flows can consult
    the same policy the auto-manager uses. ``threshold <= 0`` disables
    compaction (returns False regardless of input) — useful for tests
    and for operators who want to observe the threshold subscriber
    without triggering runs.

    Args:
        current_tokens: Approximate token count for the live context.
        threshold: Compaction trigger threshold in the same units.

    Returns:
        ``True`` when compaction should run; ``False`` otherwise.
    """
    if threshold <= 0:
        return False
    return current_tokens >= threshold


# ---------------------------------------------------------------------------
# Core mutation.
# ---------------------------------------------------------------------------


async def compact_session(
    session: Session,
    summarizer: Summarizer,
    *,
    target_tokens: int = 10_000,
    bus: EventBus | None = None,
    _emit_failure: bool = True,
) -> str:
    """Run compaction against ``session`` and append a compaction anchor.

    Steps:

    1. Collect the entries since the last anchor via
       :func:`~yaya.kernel.tape_context.after_last_anchor`. When the
       tape has no post-anchor entries there is nothing to summarise;
       the function returns an empty string and does not write an
       anchor.
    2. Estimate the pre-compaction token count for observability.
    3. Invoke ``summarizer.summarize``. Exceptions propagate after the
       failure event is emitted so callers can retry (lesson #29).
    4. Append an anchor with ``state`` carrying the summary, the
       ``tokens_before`` count, and ``kind="compaction"``. The anchor
       is the contract boundary — post-compaction context queries
       pivot off it (see
       :func:`~yaya.kernel.tape_context.select_messages`).

    Args:
        session: The target :class:`~yaya.kernel.session.Session`.
        summarizer: A :class:`Summarizer` implementation.
        target_tokens: Soft ceiling passed to the summariser. Defaults
            to ``10_000`` to match the issue's spec default.
        bus: When provided, ``session.compaction.{started,completed,
            failed}`` events are published on ``session_id="kernel"``
            so adapters can render progress without touching the
            originating session's FIFO. ``None`` disables events (unit
            tests that do not need a bus).
        _emit_failure: Internal toggle. When ``True`` (default), a
            failed summariser emits a ``session.compaction.failed``
            event before the exception propagates. The
            :class:`CompactionManager` retry path flips this to
            ``False`` so per-attempt failures do not flood adapters;
            the manager emits a single terminal event itself after
            the retry budget is exhausted.

    Returns:
        The summary string that was persisted on the anchor. Empty
        string when the tape had no post-anchor entries.

    Raises:
        Exception: Propagates whatever the summariser raised; the
            failure event is emitted first so observers do not miss
            the incident.
    """
    entries = await after_last_anchor(session.manager, session.tape_name)
    if not entries:
        return ""

    tokens_before = estimate_text_tokens(entries)
    await _emit(
        bus,
        "session.compaction.started",
        {"target_session_id": session.session_id, "tokens_before": tokens_before},
    )

    try:
        summary = await summarizer.summarize(entries, target_tokens)
    except Exception as exc:
        if _emit_failure:
            await _emit(
                bus,
                "session.compaction.failed",
                {
                    "target_session_id": session.session_id,
                    "error": str(exc) or type(exc).__name__,
                },
            )
        raise

    await session.handoff(
        "compaction",
        state={
            "kind": COMPACTION_ANCHOR_KIND,
            "summary": summary,
            "tokens_before": tokens_before,
        },
    )

    # Estimate the post-anchor window (should be empty immediately after
    # the handoff; we read it anyway so the observer sees the real shape).
    tail = await after_last_anchor(session.manager, session.tape_name)
    tokens_after = estimate_text_tokens(tail)
    await _emit(
        bus,
        "session.compaction.completed",
        {
            "target_session_id": session.session_id,
            "tokens_before": tokens_before,
            "tokens_after": tokens_after,
        },
    )
    return summary


async def _emit(
    bus: EventBus | None,
    kind: str,
    payload: dict[str, Any],
) -> None:
    """Publish a compaction event on the ``"kernel"`` session.

    Lesson #2: publishing on the originating session would deadlock the
    FIFO because the triggering handler is still draining that queue.
    The kernel session is reserved for control-plane events of exactly
    this shape; adapters that want to render compactions subscribe
    there and read ``target_session_id`` off the payload.
    """
    if bus is None:
        return
    try:
        await bus.publish(
            new_event(kind, dict(payload), session_id="kernel", source="kernel-compaction"),
        )
    except Exception:
        _logger.exception("failed to publish %s", kind)


# ---------------------------------------------------------------------------
# Auto-trigger manager.
# ---------------------------------------------------------------------------


class CompactionManager:
    """Bus subscriber that triggers compaction when a tape grows past threshold.

    Wired after every public-kind event (the kernel boot path passes the
    full catalog minus compaction events themselves). For each incoming
    event the manager:

    1. Ignores events on ``session_id="kernel"`` (control-plane).
    2. Looks up the session for that id via the bound
       :class:`~yaya.kernel.session.SessionStore`.
    3. Samples the post-last-anchor window and estimates tokens.
    4. If the estimate meets the threshold and no compaction is
       in-flight for that session, spawns a background task to run it.

    Failures retry with exponential backoff up to
    :data:`_MAX_RETRIES` attempts per session. After the cap is hit the
    session is pinned as "compaction-disabled" until the next process
    reload; further triggers on that session skip silently (but the
    failure event was still emitted on the third attempt).

    Not thread-safe. Drive from one asyncio loop.
    """

    def __init__(
        self,
        *,
        bus: EventBus,
        store: Any,
        summarizer: Summarizer,
        workspace: Any,
        threshold_tokens: int,
        target_tokens: int,
    ) -> None:
        """Bind the manager to its dependencies. See :func:`install_compaction_manager`."""
        self._bus = bus
        self._store = store
        self._workspace = workspace
        self._summarizer = summarizer
        self._threshold_tokens = threshold_tokens
        self._target_tokens = target_tokens
        self._subs: list[Subscription] = []
        # Single in-flight task per session id. The OrderedDict gives
        # deterministic FIFO eviction when we hit _INFLIGHT_CAP.
        self._inflight: OrderedDict[str, asyncio.Task[None]] = OrderedDict()
        self._attempts: OrderedDict[str, int] = OrderedDict()
        # Disabled session ids, bounded with the same FIFO policy so a
        # long-lived ``yaya serve`` with many distinct sessions cannot
        # leak entries (lesson #6). ``None`` is the unused-value slot —
        # we only care about membership + insertion order.
        self._disabled: OrderedDict[str, None] = OrderedDict()
        self._installed = False

    async def start(self, kinds: list[str]) -> None:
        """Subscribe the manager to ``kinds``."""
        if self._installed:
            return
        self._installed = True
        for kind in kinds:
            sub = self._bus.subscribe(kind, self._on_event, source="kernel-compaction")
            self._subs.append(sub)

    async def stop(self) -> None:
        """Drop every subscription and cancel in-flight tasks. Idempotent."""
        for sub in self._subs:
            sub.unsubscribe()
        self._subs.clear()
        # Cancel in-flight compactions; shutdown should not block on them.
        for task in list(self._inflight.values()):
            if not task.done():
                task.cancel()
        self._inflight.clear()
        self._attempts.clear()
        self._disabled.clear()
        self._installed = False

    async def _on_event(self, ev: Any) -> None:
        session_id = ev.session_id
        if session_id == "kernel":
            return
        if session_id in self._disabled:
            return
        if session_id in self._inflight:
            return
        session = await self._store.open(self._workspace, session_id)
        entries = await after_last_anchor(session.manager, session.tape_name)
        tokens = estimate_text_tokens(entries)
        if not should_auto_compact(tokens, threshold=self._threshold_tokens):
            return
        # Schedule. An empty contextvars.Context() detaches the background
        # task from the bus's `_IN_WORKER` var — same pattern the agent
        # loop uses so follow-up publishes land on the right workers.
        task = asyncio.create_task(
            self._run_with_retry(session),
            context=contextvars.Context(),
        )
        self._track_inflight(session_id, task)

    def _track_inflight(self, session_id: str, task: asyncio.Task[None]) -> None:
        """Record a new in-flight task, evicting the oldest if at cap.

        Trade-off (accepted, #93 P3): when the cap evicts a still-running
        record, the guard at :meth:`_on_event` no longer sees the
        session as in-flight and may schedule a second, parallel
        compaction for it. The only way to hit this is to have
        :data:`_INFLIGHT_CAP` (1024) distinct sessions with compactions
        in flight simultaneously — far above expected single-user
        scale. We document rather than code around it; the alternative
        (tracking evicted-but-live tasks in a second set) complicates
        shutdown with no realistic payoff at 0.1.
        """
        if len(self._inflight) >= _INFLIGHT_CAP:
            # FIFO eviction: drop the oldest record (lesson #6). The task
            # is NOT cancelled — it continues in the background. We just
            # stop tracking it so the map stays bounded.
            oldest_id, _ = self._inflight.popitem(last=False)
            self._attempts.pop(oldest_id, None)
        self._inflight[session_id] = task

        def _discard(_t: asyncio.Task[None], sid: str = session_id) -> None:
            self._inflight.pop(sid, None)

        task.add_done_callback(_discard)

    async def _run_with_retry(self, session: Session) -> None:
        """Run compaction with bounded exponential backoff.

        Per-attempt failures are suppressed via ``_emit_failure=False``;
        after the retry budget is exhausted a single terminal
        ``session.compaction.failed`` event is emitted carrying the
        ``attempts`` count. Adapters see one failure per retry chain,
        not one per attempt (#93 P2).
        """
        sid = session.session_id
        attempt = self._attempts.get(sid, 0)
        last_error: str | None = None
        while attempt < _MAX_RETRIES:
            attempt += 1
            self._attempts[sid] = attempt
            try:
                await compact_session(
                    session,
                    self._summarizer,
                    target_tokens=self._target_tokens,
                    bus=self._bus,
                    _emit_failure=False,
                )
            except asyncio.CancelledError:  # pragma: no cover - shutdown path
                raise
            except Exception as exc:
                last_error = str(exc) or type(exc).__name__
                _logger.warning(
                    "compaction attempt %d for session %r failed: %s",
                    attempt,
                    sid,
                    exc,
                )
                if attempt >= _MAX_RETRIES:
                    # Terminal: disable the session, purge its attempts
                    # row (bounded: matches _inflight cap policy), and
                    # emit the single terminal failure event carrying
                    # the attempts count.
                    self._mark_disabled(sid)
                    await _emit(
                        self._bus,
                        "session.compaction.failed",
                        {
                            "target_session_id": sid,
                            "error": last_error,
                            "attempts": attempt,
                        },
                    )
                    return
                # Exponential backoff: 0.1s, 0.2s, 0.4s, ...
                await asyncio.sleep(0.1 * (2 ** (attempt - 1)))
            else:
                # Success: reset the per-session attempts so a future
                # threshold breach starts fresh.
                self._attempts.pop(sid, None)
                return

    def _mark_disabled(self, session_id: str) -> None:
        """Add ``session_id`` to the disabled set with FIFO eviction.

        Also purges the per-session attempts row — once a session is
        pinned as disabled, further triggers short-circuit before the
        retry loop runs, so the counter would otherwise leak forever.
        """
        if len(self._disabled) >= _INFLIGHT_CAP and session_id not in self._disabled:
            # Drop the oldest disabled id so the map stays bounded.
            self._disabled.popitem(last=False)
        self._disabled[session_id] = None
        # Disabled sessions never re-enter the retry loop; the attempts
        # row is dead state that must not leak across the lifetime of
        # the manager.
        self._attempts.pop(session_id, None)


class LLMSummarizer:
    """Default :class:`Summarizer` that delegates to an :class:`~yaya.kernel.llm.LLMProvider`.

    Formats the raw tape entries into a ``user`` message and asks the
    provider to produce a concise summary bounded by ``target_tokens``.
    The provider's streamed content parts are joined into a single
    string — terminal tool-call parts are ignored because compaction
    should not trigger side effects.

    This is the kernel's bridge between the abstract :class:`Summarizer`
    Protocol and the v1 ``llm-provider`` contract (#93 P1). Lifting the
    provider lookup into the boot path (``yaya serve``) keeps the
    manager itself provider-agnostic — tests still wire a deterministic
    fake.
    """

    def __init__(
        self,
        provider: LLMProvider,
        *,
        system_prompt: str | None = None,
    ) -> None:
        """Bind the summariser to a provider.

        Args:
            provider: Any object implementing the
                :class:`~yaya.kernel.llm.LLMProvider` Protocol.
            system_prompt: Override for the default system prompt. The
                default asks for a compact, faithful summary within the
                ``target_tokens`` budget passed to :meth:`summarize`.
        """
        self._provider = provider
        self._system_prompt = system_prompt or _DEFAULT_SUMMARY_SYSTEM_PROMPT

    async def summarize(
        self,
        entries: list[TapeEntry],
        target_tokens: int,
    ) -> str:
        """Produce a text summary by streaming the provider's response.

        Args:
            entries: Tape entries since the previous anchor.
            target_tokens: Soft ceiling embedded in the user prompt.

        Returns:
            The joined content from the provider's stream. Tool-call
            chunks are skipped — compaction must be side-effect-free.
        """
        rendered = "\n\n".join(_entry_text(e) for e in entries)
        prompt = (
            f"Summarize the conversation below in at most ~{target_tokens} tokens. "
            "Preserve user intent, key decisions, outstanding tasks, and tool "
            "outputs that affect later turns. Drop pleasantries and redundant "
            "context.\n\n"
            f"---\n{rendered}\n---"
        )
        history: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        stream = await self._provider.generate(
            system_prompt=self._system_prompt,
            tools=[],
            history=history,
        )
        chunks: list[str] = []
        async for part in stream:
            # ContentPart carries ``text``; ToolCallPart is ignored by
            # design (see class docstring).
            text = getattr(part, "text", None)
            if isinstance(text, str) and text:
                chunks.append(text)
        return "".join(chunks)


_DEFAULT_SUMMARY_SYSTEM_PROMPT = (
    "You are a compaction summarizer. Produce a concise, faithful summary "
    "of the supplied conversation fragment. Be terse; never add facts that "
    "are not present; keep tool results verbatim when they affect later "
    "turns."
)


async def install_compaction_manager(
    *,
    bus: EventBus,
    store: Any,
    summarizer: Summarizer,
    workspace: Any,
    kinds: list[str],
    threshold_tokens: int,
    target_tokens: int,
) -> CompactionManager:
    """Construct, start, and return a :class:`CompactionManager`.

    The caller owns teardown via :meth:`CompactionManager.stop`.

    Args:
        bus: The running :class:`~yaya.kernel.bus.EventBus`.
        store: A :class:`~yaya.kernel.session.SessionStore` the manager
            re-opens sessions against when an event lands.
        summarizer: Any :class:`Summarizer`. The kernel wires the active
            llm-provider; tests wire a deterministic fake.
        workspace: Workspace path the manager uses when opening
            sessions.
        kinds: Event kinds to subscribe to. The compaction events
            themselves MUST be excluded.
        threshold_tokens: Auto-compaction trigger, in approximate tokens.
        target_tokens: Soft ceiling passed to the summariser.

    Returns:
        The running :class:`CompactionManager`.
    """
    mgr = CompactionManager(
        bus=bus,
        store=store,
        summarizer=summarizer,
        workspace=workspace,
        threshold_tokens=threshold_tokens,
        target_tokens=target_tokens,
    )
    await mgr.start(kinds)
    return mgr
