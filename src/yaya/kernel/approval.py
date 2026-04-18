"""Approval runtime — HITL gate for tool calls (#28).

Any :class:`~yaya.kernel.tool.Tool` subclass that sets
``requires_approval: ClassVar[bool] = True`` routes through this
runtime before its :meth:`~yaya.kernel.tool.Tool.run` body executes.
The runtime lives as a per-bus singleton wired up by
:func:`install_approval_runtime` at kernel boot; the dispatcher looks
it up via :func:`get_approval_runtime` and calls :meth:`ApprovalRuntime.request`
from inside the tool dispatch path.

Flow
----

1. Tool dispatcher detects ``requires_approval=True`` and calls
   ``pre_approve(ctx, session_id=...)``.
2. The default :meth:`~yaya.kernel.tool.Tool.pre_approve` builds an
   :class:`Approval`, hands it to the runtime, and awaits the user's
   response (or timeout).
3. The runtime emits ``approval.request`` on the bus. Adapters
   (web / TUI / tg) render the prompt.
4. The adapter's UI → adapter publishes ``approval.response`` with the
   matching ``id``. The runtime's subscriber resolves the pending
   future and returns the :class:`ApprovalResult`.

Session-aware short-circuit
---------------------------

When the user picks ``approve_for_session``, the runtime caches the
tuple ``(tool_name, params_fingerprint)`` under the originating
session id. Subsequent identical calls on the same session skip the
prompt and return ``approve_for_session`` immediately. The cache is
in-memory, never persisted, and never auto-evicted in 0.2 — session
lifecycle is owned by the adapter and a proper ``SessionContext``
cleanup hook lands later (tracked alongside the session scope work).

Deadlock avoidance (lesson #2)
-------------------------------

The dispatcher (and therefore this runtime's :meth:`request`) runs
**inside** the originating session's drain worker. The worker is
blocked on ``await pending_future``; a ``approval.response`` delivered
on the **same** session would land in the same worker queue behind
the blocked handler and deadlock until the 60s timeout fires.

To break the cycle, the runtime emits and subscribes on the reserved
``"kernel"`` session id. Approval requests go out on ``"kernel"``,
adapters forward the user's reply on ``"kernel"``, and the response
handler runs inside the kernel-session worker — a different worker
than the one waiting on the future. The original session worker then
wakes up, the tool runs (or is rejected), and the dispatch path
continues. The ``session_id`` field carried **inside** the payload
still identifies the tool call's origin for UI grouping; only the
event envelope's routing session changes.

Layering: no imports from ``cli``, ``plugins``, or ``core``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from yaya.kernel.errors import YayaError
from yaya.kernel.events import Event, new_event

if TYPE_CHECKING:  # pragma: no cover - type-only imports, keep runtime light.
    from yaya.kernel.bus import EventBus, Subscription

_logger = logging.getLogger(__name__)

# Routing session for every ``approval.*`` envelope. See the module
# docstring (lesson #2): the originating tool-call session's drain
# worker is blocked inside ``request()``, so the response MUST deliver
# on a different worker to resolve the future.
_APPROVAL_SESSION: str = "kernel"

# Subscription source for the runtime's ``approval.response`` handler.
# Not ``"kernel"`` — the bus recursion guard short-circuits
# ``plugin.error`` re-emission for kernel-origin failures (see
# ``bus._report_handler_failure``). A distinct source lets handler
# failures still surface observably while the emit path remains
# kernel-attributed (the runtime publishes on behalf of the kernel).
_APPROVAL_SOURCE: str = "kernel-approval"

DEFAULT_APPROVAL_TIMEOUT_S: float = 60.0
"""Default deadline for a single approval prompt.

60s is long enough for a user context switch (read the args, decide)
yet short enough that a stale browser tab cannot wedge the tool
dispatch path forever. Overridable per :class:`ApprovalRuntime`.
"""

Response = Literal["approve", "approve_for_session", "reject"]


# ---------------------------------------------------------------------------
# Models.
# ---------------------------------------------------------------------------


class Approval(BaseModel):
    """Single approval prompt addressed to the user.

    Attributes:
        id: uuid4 hex chosen by the runtime. Echoed back on the
            matching :class:`ApprovalResult` for correlation (lesson
            #15).
        tool_name: :attr:`~yaya.kernel.tool.Tool.name` of the tool
            asking for approval. Surfaced to adapters verbatim.
        params: Full param dict (``tool.model_dump()``). Displayed to
            the user so they know what the tool will do before they
            grant consent.
        brief: ≤80-char one-liner summarising the intended action.
            Adapters use this as the prompt headline.
        session_id: Originating tool-call session id, NOT the bus
            routing session (which is always ``"kernel"`` — see
            module docstring). Used for the ``approve_for_session``
            cache key and for UI grouping.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    tool_name: str
    params: dict[str, Any]
    brief: str = Field(max_length=80)
    session_id: str


class ApprovalResult(BaseModel):
    """User's answer to an :class:`Approval` prompt.

    Attributes:
        id: Echoes :attr:`Approval.id` so the runtime can resolve the
            matching pending future across concurrent prompts.
        response: The user's choice. ``approve_for_session`` primes the
            session allowlist so future identical calls skip the prompt.
        feedback: Optional free-text from the user — surfaced on
            :class:`ToolRejectedError` so the LLM can see WHY the call
            was refused and adapt.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    response: Response
    feedback: str = ""


# ---------------------------------------------------------------------------
# Errors.
# ---------------------------------------------------------------------------


class ApprovalCancelledError(YayaError):
    """Approval timed out or the runtime was shut down mid-request.

    Carries the :class:`Approval.id` so operators can match the
    cancellation to the emitted ``approval.cancelled`` event.
    """

    def __init__(self, approval_id: str, reason: Literal["timeout", "shutdown"]) -> None:
        """Bind the cancellation to its approval id + reason."""
        self.approval_id = approval_id
        self.reason = reason
        super().__init__(f"approval {approval_id!r} cancelled: {reason}")


class ToolRejectedError(YayaError):
    """User explicitly rejected a tool call.

    Carries the ``feedback`` string so the agent loop / LLM can see
    why and either replan or stop.
    """

    def __init__(self, feedback: str) -> None:
        """Bind the rejection to the user's free-text reason."""
        self.feedback = feedback
        super().__init__(f"tool rejected: {feedback}" if feedback else "tool rejected")


# ---------------------------------------------------------------------------
# Fingerprint (D3).
# ---------------------------------------------------------------------------


def _fingerprint(params: dict[str, Any]) -> str:
    """Stable short hash of a tool's param dict.

    Used as the ``approve_for_session`` cache key. ``sort_keys=True``
    + ``default=str`` give a deterministic canonical form even for
    values pydantic produced but ``json`` would otherwise refuse
    (e.g. ``Path``, ``datetime``). 16 hex chars = 64 bits of hash —
    ample for a within-session cache.
    """
    blob = json.dumps(params, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Runtime.
# ---------------------------------------------------------------------------


class ApprovalRuntime:
    """Session-aware approval gate driven by bus events.

    One instance per :class:`~yaya.kernel.bus.EventBus`. Installed by
    :func:`install_approval_runtime` at kernel boot; the tool
    dispatcher resolves it via :func:`get_approval_runtime` from
    inside :meth:`~yaya.kernel.tool.Tool.pre_approve`.

    Not thread-safe. Bound to the loop that created it.
    """

    def __init__(
        self,
        bus: EventBus,
        *,
        timeout_s: float = DEFAULT_APPROVAL_TIMEOUT_S,
    ) -> None:
        """Bind the runtime to ``bus``.

        Args:
            bus: A running :class:`~yaya.kernel.bus.EventBus`.
            timeout_s: Per-request deadline. On expiry the runtime
                emits ``approval.cancelled`` and raises
                :class:`ApprovalCancelledError`.
        """
        self._bus = bus
        self._timeout_s = timeout_s
        self._pending: dict[str, asyncio.Future[ApprovalResult]] = {}
        # session_id → {(tool_name, fingerprint)}. Grows within the
        # process lifetime; cleanup is tracked separately (see module
        # docstring).
        self._session_allowlist: dict[str, set[tuple[str, str]]] = {}
        self._sub: Subscription | None = None
        self._started: bool = False

    async def start(self) -> None:
        """Subscribe to ``approval.response``.

        Idempotent: a second call is a no-op so fixtures composing
        multiple layers can call it defensively.
        """
        if self._started:
            return
        self._started = True
        self._sub = self._bus.subscribe(
            "approval.response",
            self._on_approval_response,
            source=_APPROVAL_SOURCE,
        )

    async def stop(self) -> None:
        """Unsubscribe and cancel every in-flight approval.

        Pending requests observe :class:`ApprovalCancelledError` with
        ``reason="shutdown"``. Safe to call multiple times.
        """
        if not self._started:
            return
        self._started = False
        if self._sub is not None:
            self._sub.unsubscribe()
            self._sub = None
        # Cancel every awaiter so the dispatch path unwinds instead
        # of hanging on the 60s timeout during shutdown.
        for approval_id, fut in list(self._pending.items()):
            if not fut.done():
                fut.set_exception(ApprovalCancelledError(approval_id, "shutdown"))
        self._pending.clear()

    async def request(self, approval: Approval) -> ApprovalResult:
        """Emit ``approval.request`` and await the user's reply.

        Short-circuits when the session has already approved an
        identical ``(tool_name, fingerprint)`` tuple — returns a synthetic
        :class:`ApprovalResult` with ``response="approve_for_session"``
        without touching the bus. Exactly one ``approval.request``
        event is emitted per unique tuple within a session (AC for the
        cache-hit scenario).

        Args:
            approval: The prompt to surface to the user.

        Returns:
            The :class:`ApprovalResult` carried on the matching
            ``approval.response`` event.

        Raises:
            ApprovalCancelledError: On timeout or shutdown. A matching
                ``approval.cancelled`` event is emitted with the same
                ``id`` and ``reason``.
        """
        if not self._started:
            # Guard against a caller that forgot start(). Shouldn't
            # happen in production because the registry installs the
            # runtime before kernel.ready, but keep the failure loud.
            raise RuntimeError(
                "ApprovalRuntime.request() called before start(); "
                "install_approval_runtime must run before the dispatcher receives tool.call.request"
            )

        fingerprint = _fingerprint(approval.params)
        key = (approval.tool_name, fingerprint)
        allowlist = self._session_allowlist.get(approval.session_id)
        if allowlist is not None and key in allowlist:
            _logger.debug(
                "approval short-circuit approve_for_session session=%r tool=%r fp=%s",
                approval.session_id,
                approval.tool_name,
                fingerprint,
            )
            return ApprovalResult(
                id=approval.id,
                response="approve_for_session",
                feedback="",
            )

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[ApprovalResult] = loop.create_future()
        self._pending[approval.id] = fut

        await self._bus.publish(
            new_event(
                "approval.request",
                {
                    "id": approval.id,
                    "tool_name": approval.tool_name,
                    "params": approval.params,
                    "brief": approval.brief,
                },
                session_id=_APPROVAL_SESSION,
                source=_APPROVAL_SOURCE,
            )
        )

        try:
            result = await asyncio.wait_for(fut, timeout=self._timeout_s)
        except TimeoutError:
            # Lesson #6 — never leave a key in _pending on a failure path.
            self._pending.pop(approval.id, None)
            await self._emit_cancelled(approval.id, "timeout")
            _logger.info(
                "approval timed out id=%s tool=%r session=%r",
                approval.id,
                approval.tool_name,
                approval.session_id,
            )
            raise ApprovalCancelledError(approval.id, "timeout") from None
        except ApprovalCancelledError:
            # Raised directly by stop() when the runtime shuts down
            # while we're awaiting. Pending key already cleared by
            # stop(); no extra bookkeeping here.
            raise

        # Normal path — the response handler already popped the key.
        if result.response == "approve_for_session":
            self._session_allowlist.setdefault(approval.session_id, set()).add(key)

        return result

    async def _on_approval_response(self, ev: Event) -> None:
        """Resolve the pending future for ``ev.payload.id``.

        Runs inside the kernel-session drain worker (see module
        docstring). Lesson #15 — we log unknown ids with enough context
        for operators to diagnose lost correlation.
        """
        payload = ev.payload
        approval_id_raw = payload.get("id")
        if not isinstance(approval_id_raw, str):
            _logger.warning(
                "approval.response without string 'id' — dropping (payload=%r)",
                payload,
            )
            return
        approval_id = approval_id_raw

        fut = self._pending.pop(approval_id, None)
        if fut is None or fut.done():
            _logger.warning(
                "approval.response for unknown/resolved id=%s — dropping (source=%r)",
                approval_id,
                ev.source,
            )
            return

        response_raw = payload.get("response")
        response: Response
        if response_raw == "approve":
            response = "approve"
        elif response_raw == "approve_for_session":
            response = "approve_for_session"
        elif response_raw == "reject":
            response = "reject"
        else:
            _logger.warning(
                "approval.response id=%s carries invalid response=%r — rejecting by default",
                approval_id,
                response_raw,
            )
            response = "reject"
        feedback_raw = payload.get("feedback", "")
        feedback = feedback_raw if isinstance(feedback_raw, str) else ""

        fut.set_result(
            ApprovalResult(
                id=approval_id,
                response=response,
                feedback=feedback,
            )
        )

    async def _emit_cancelled(
        self,
        approval_id: str,
        reason: Literal["timeout", "shutdown"],
    ) -> None:
        """Publish ``approval.cancelled`` so adapters can drop stale prompts."""
        await self._bus.publish(
            new_event(
                "approval.cancelled",
                {"id": approval_id, "reason": reason},
                session_id=_APPROVAL_SESSION,
                source=_APPROVAL_SOURCE,
            )
        )


# ---------------------------------------------------------------------------
# Module-level registry (install / get).
# ---------------------------------------------------------------------------

# Bus id → ApprovalRuntime. We key by ``id(bus)`` rather than by bus
# reference so tests that swap buses in/out of a module-level slot do
# not collide; the caller is responsible for calling
# :func:`uninstall_approval_runtime` on teardown.
_runtimes: dict[int, ApprovalRuntime] = {}


async def install_approval_runtime(
    bus: EventBus,
    *,
    timeout_s: float = DEFAULT_APPROVAL_TIMEOUT_S,
) -> ApprovalRuntime:
    """Create, start, and register an :class:`ApprovalRuntime` for ``bus``.

    Called by :class:`~yaya.kernel.registry.PluginRegistry` during
    :meth:`~yaya.kernel.registry.PluginRegistry.start` — after plugins
    load (so adapters are subscribed to ``approval.request``) but
    before ``kernel.ready`` fires (so the first ``tool.call.request``
    can reach the runtime).

    Args:
        bus: Live event bus. The runtime subscribes to
            ``approval.response`` on this bus.
        timeout_s: Per-request deadline forwarded to
            :class:`ApprovalRuntime`.

    Returns:
        The newly installed runtime. Callers that want to inject
        fakes for tests can bypass :func:`get_approval_runtime` and
        pass the instance explicitly.
    """
    existing = _runtimes.get(id(bus))
    if existing is not None:
        return existing
    runtime = ApprovalRuntime(bus, timeout_s=timeout_s)
    await runtime.start()
    _runtimes[id(bus)] = runtime
    return runtime


def get_approval_runtime(bus: EventBus) -> ApprovalRuntime | None:
    """Return the runtime registered for ``bus``, or ``None``.

    ``None`` lets :meth:`~yaya.kernel.tool.Tool.pre_approve` fall back
    to the allow-all default when no runtime is installed (the
    pre-#28 behaviour, kept for test harnesses that skip registry
    boot).
    """
    return _runtimes.get(id(bus))


async def uninstall_approval_runtime(bus: EventBus) -> None:
    """Stop and remove the runtime for ``bus``. Idempotent."""
    runtime = _runtimes.pop(id(bus), None)
    if runtime is not None:
        await runtime.stop()


def _clear_approval_runtimes() -> None:
    """Drop every registered runtime. Test-only; not part of the public API."""
    _runtimes.clear()


__all__ = [
    "DEFAULT_APPROVAL_TIMEOUT_S",
    "Approval",
    "ApprovalCancelledError",
    "ApprovalResult",
    "ApprovalRuntime",
    "Response",
    "ToolRejectedError",
    "get_approval_runtime",
    "install_approval_runtime",
    "uninstall_approval_runtime",
]
