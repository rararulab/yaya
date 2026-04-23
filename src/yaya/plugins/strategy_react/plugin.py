"""ReAct strategy plugin — text-based Thought/Action/Observation loop.

Classical ReAct (Yao et al. 2022): the strategy authors a system
prompt that constrains the LLM to emit either
``Thought: ... Action: <tool> Action Input: <json>`` triples or a
``Thought: ... Final Answer: <user-facing text>`` termination. The
strategy parses the assistant's latest content for one of those two
shapes, plus a compatibility ``[TOOL_CALL]`` block emitted by some
providers or prompts:

* A valid Action → ``{next: "tool"}`` with a synthesized tool_call.
* A valid ``[TOOL_CALL]`` JSON block → the same ``{next: "tool"}``.
* A Final Answer → ``{next: "done"}``.
* Neither → append one corrective ``role="user"`` nudge and reroll.
  A second parse failure terminates the turn (no endless retries).

Unlike the previous function-calling implementation, this strategy
does **not** consume ``assistant.tool_calls`` — tool intent rides in
free-form text only. Tool results come back as ``role="user"``
messages whose content starts with ``Observation: …`` (the canonical
ReAct shape). The loop assembles that shape; this module just reads
it.

Provider / model resolution is unchanged from D4b: active instance
id + its ``config["model"]`` from :attr:`ctx.providers`, falling
back to an env sniff when no config store is wired in.
"""

from __future__ import annotations

import json
import os
import re
from typing import TYPE_CHECKING, Any, ClassVar, cast

from yaya.kernel.events import Event
from yaya.kernel.plugin import Category, HealthReport, KernelContext
from yaya.kernel.tool import all_tool_specs

if TYPE_CHECKING:  # pragma: no cover - type-only import.
    from yaya.kernel.providers import ProvidersView

_FALLBACK_OPENAI_PROVIDER = "llm-openai"
_FALLBACK_ECHO_PROVIDER = "llm-echo"
_DEFAULT_MODEL = "gpt-4o-mini"
_FALLBACK_ECHO_MODEL = "echo"

_NAME = "strategy-react"
_VERSION = "0.1.0"

# Marker prefix on the corrective user message. The strategy scans
# recent messages for this marker to detect whether we've already
# used our one retry — presence means "don't nudge again, terminate".
_RETRY_MARKER = "[yaya:react-format-nudge] "

# Regexes for ReAct labels. Anchored with ``re.MULTILINE`` so each
# label sits at the start of its own line, matching the prompt the
# strategy authors. ``DOTALL`` on ``Final Answer`` / ``Action Input``
# lets them span paragraphs.
_FINAL_RE = re.compile(
    r"^Final Answer:\s*(?P<answer>.+?)(?=^Action:|^Thought:|^Final Answer:|\Z)",
    re.MULTILINE | re.DOTALL,
)
_ACTION_RE = re.compile(
    r"^Action:\s*(?P<name>[^\n]+?)\s*$",
    re.MULTILINE,
)
_ACTION_INPUT_RE = re.compile(
    r"^Action Input:\s*(?P<body>.*?)(?=^Action:|^Final Answer:|\Z)",
    re.MULTILINE | re.DOTALL,
)
_TOOL_CALL_RE = re.compile(
    r"^[ \t]*\[TOOL_CALL\]\s*(?P<body>.*?)^[ \t]*\[/TOOL_CALL\][ \t]*$",
    re.MULTILINE | re.DOTALL,
)
# Strip ``` ```json ... ``` ``` fences that some models wrap JSON in.
_CODE_FENCE_RE = re.compile(
    r"^```(?:json)?\s*(?P<inner>.*?)\s*```\s*\Z",
    re.DOTALL,
)


class ReActStrategy:
    """Bundled ReAct strategy plugin."""

    name: str = _NAME
    version: str = _VERSION
    category: Category = Category.STRATEGY
    requires: ClassVar[list[str]] = []

    def subscriptions(self) -> list[str]:
        """Only ``strategy.decide.request`` — the sole request kind for this category."""
        return ["strategy.decide.request"]

    async def on_load(self, ctx: KernelContext) -> None:
        """Log the effective configuration on boot."""
        provider, model = self._provider_and_model(ctx)
        ctx.logger.debug(
            "strategy-react loaded (provider=%s model=%s)",
            provider,
            model,
        )

    async def on_event(self, ev: Event, ctx: KernelContext) -> None:
        """Decide the next step for the turn described by ``ev.payload.state``."""
        if ev.kind != "strategy.decide.request":
            return
        raw_state = ev.payload.get("state")
        if not isinstance(raw_state, dict):
            raise ValueError("strategy.decide.request missing 'state' payload")  # noqa: TRY004

        provider, model = self._provider_and_model(ctx)
        decision = _decide(cast("dict[str, Any]", raw_state), provider=provider, model=model)
        decision["request_id"] = ev.id
        await ctx.emit(
            "strategy.decide.response",
            decision,
            session_id=ev.session_id,
        )

    async def on_unload(self, ctx: KernelContext) -> None:
        """No-op — the strategy holds no resources."""

    async def health_check(self, ctx: KernelContext) -> HealthReport:
        """Surface provider/model resolution without firing a call.

        Uses the exact same resolver as the dispatch path
        (:meth:`_provider_and_model`). When :attr:`ctx.providers`
        yields a concrete instance, report ``ok``; when the resolver
        falls back to an env-sniffed default (no configured
        provider), report ``degraded`` so ``yaya doctor`` tells the
        operator to configure one.
        """
        providers = ctx.providers
        resolved = None
        if providers is not None:
            resolved = ReActStrategy._resolve_from_providers(providers)
        if resolved is not None:
            provider, model = resolved
            return HealthReport(
                status="ok",
                summary=f"provider={provider} model={model}",
            )
        provider, model = ReActStrategy._provider_and_model(ctx)
        return HealthReport(
            status="degraded",
            summary=f"no configured provider; fallback {provider}/{model}",
        )

    @staticmethod
    def _provider_and_model(ctx: KernelContext) -> tuple[str, str]:
        """Return the effective ``(provider, model)`` pair."""
        providers = ctx.providers
        if providers is not None:
            resolved = ReActStrategy._resolve_from_providers(providers)
            if resolved is not None:
                return resolved
        if os.environ.get("OPENAI_API_KEY"):
            return _FALLBACK_OPENAI_PROVIDER, _DEFAULT_MODEL
        return _FALLBACK_ECHO_PROVIDER, _FALLBACK_ECHO_MODEL

    @staticmethod
    def _resolve_from_providers(providers: ProvidersView) -> tuple[str, str] | None:
        """Resolve ``(provider, model)`` from a live :class:`ProvidersView`."""
        active_id = providers.active_id
        instance = None
        if active_id is not None:
            instance = providers.get_instance(active_id)
        if instance is None:
            all_instances = providers.list_instances()
            if not all_instances:
                return None
            instance = all_instances[0]
        model_raw = instance.config.get("model")
        if isinstance(model_raw, str) and model_raw:
            model = model_raw
        elif instance.plugin == _FALLBACK_ECHO_PROVIDER:
            model = _FALLBACK_ECHO_MODEL
        else:
            model = _DEFAULT_MODEL
        return instance.id, model


# ---------------------------------------------------------------------------
# Pure decision function.
# ---------------------------------------------------------------------------


def _decide(state: dict[str, Any], *, provider: str, model: str) -> dict[str, Any]:
    """Compute the next step from a loop state snapshot.

    See module docstring for the ReAct protocol this function
    implements. The caller (``on_event``) stamps ``request_id`` on
    the returned payload before emitting — this stays pure for
    testing.
    """
    messages_raw: list[Any] = list(state.get("messages") or [])
    messages: list[dict[str, Any]] = [cast("dict[str, Any]", m) for m in messages_raw if isinstance(m, dict)]

    last_assistant: dict[str, Any] | None = None
    last_assistant_idx: int = -1
    for i, msg in enumerate(messages):
        if msg.get("role") == "assistant":
            last_assistant = msg
            last_assistant_idx = i

    # First turn — nothing to parse yet.
    if last_assistant is None:
        return _llm_decision(provider, model)

    # There are messages after the last assistant (e.g. an Observation
    # appended by the loop after a tool call). The assistant already
    # produced its action and has nothing more to say yet — hand back
    # to the LLM for the next Thought/Action or Final Answer.
    if last_assistant_idx < len(messages) - 1:
        return _llm_decision(provider, model)

    # Parse the assistant's most recent message.
    content = last_assistant.get("content") or ""
    if not isinstance(content, str):
        content = str(content)
    parsed = _parse_assistant(content)

    if parsed[0] == "final":
        return {"next": "done"}

    if parsed[0] == "action":
        _, name, args = parsed
        step = int(state.get("step") or 0)
        return {
            "next": "tool",
            "tool_call": {
                "id": f"rx-{step}",
                "name": str(name),
                "args": cast("dict[str, Any]", args),
            },
        }

    # Parse error. Decide whether we've already nudged once.
    if _has_recent_retry_marker(messages):
        # Second failure in a row — give up without spinning the loop.
        return {"next": "done"}

    reason = str(parsed[1]) if len(parsed) >= 2 else "unparsable response"
    nudge = (
        f"{_RETRY_MARKER}Your last message did not follow the ReAct format. "
        f"{reason}. Please resend using either "
        "'Thought: ...\\nAction: <tool>\\nAction Input: <json>' "
        "or 'Thought: ...\\nFinal Answer: <your reply>'."
    )
    return {
        **_llm_decision(provider, model),
        "messages_append": [{"role": "user", "content": nudge}],
    }


def _llm_decision(provider: str, model: str) -> dict[str, Any]:
    """Build the common ``next="llm"`` decision with the ReAct system prompt."""
    system_prompt = _build_system_prompt(all_tool_specs())
    return {
        "next": "llm",
        "provider": provider,
        "model": model,
        "messages_prepend": [{"role": "system", "content": system_prompt}],
    }


def _has_recent_retry_marker(messages: list[dict[str, Any]]) -> bool:
    """Return True if any of the last few user messages carry the nudge marker."""
    for msg in messages[-4:]:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.startswith(_RETRY_MARKER):
            return True
    return False


def _build_system_prompt(tool_specs: list[dict[str, Any]]) -> str:
    """Compose the ReAct system prompt from the live tool registry.

    Tool specs come in the OpenAI function-calling shape via
    :func:`~yaya.kernel.tool.all_tool_specs`: each entry is
    ``{"type": "function", "function": {"name", "description",
    "parameters"}}``. We render a compact bullet list that the model
    can reason about without requiring structured tool support on
    the provider side.
    """
    lines: list[str] = [
        "You are yaya, an assistant that solves tasks by reasoning in the ReAct style.",
        "",
        "You have access to the following tools:",
    ]
    if not tool_specs:
        lines.append("- (no tools available)")
    else:
        for spec in tool_specs:
            fn_raw = spec.get("function")
            if not isinstance(fn_raw, dict):
                continue
            fn = cast("dict[str, Any]", fn_raw)
            name = str(fn.get("name", "?"))
            desc = str(fn.get("description", "")).strip()
            params_raw = fn.get("parameters")
            params: dict[str, Any] = cast("dict[str, Any]", params_raw) if isinstance(params_raw, dict) else {}
            params_json = json.dumps(params, separators=(",", ":"))
            if len(params_json) > 240:
                params_json = params_json[:237] + "..."
            lines.append(f"- {name}: {desc}")
            lines.append(f"  schema: {params_json}")
    lines += [
        "",
        "Respond using this EXACT format on each turn. Do not add prose",
        "outside the labels. Do not use <think> tags; put all reasoning",
        "after the 'Thought:' label.",
        "",
        "Hard rules for the 'Thought:' line:",
        "- One or two short sentences. Internal scratchpad only.",
        "- Never include lists, tables, code blocks, markdown, or any",
        "  content the user is meant to read. The UI hides 'Thought:'",
        "  behind a collapsed 'Show reasoning' fold — anything placed",
        "  there will NOT be visible by default.",
        "",
        "Tool-call turn:",
        "",
        "Thought: <one short rationale, no content the user needs>",
        "Action: <one tool name from the list above>",
        "Action Input: <a JSON object matching that tool's schema>",
        "",
        "After each Action I will give you:",
        "Observation: <the tool's result as JSON or text>",
        "",
        "Continue the Thought/Action/Action Input cycle as needed. When",
        "you have enough information, emit the terminal shape:",
        "",
        "Thought: <one short rationale, no content the user needs>",
        "Final Answer: <the complete user-facing answer — every list,",
        "table, citation, quote, and formatted value must live here,",
        "never above>",
        "",
        "Never emit both an Action and a Final Answer in the same turn.",
    ]
    return "\n".join(lines)


ParseResult = tuple[Any, ...]


def _parse_assistant(text: str) -> ParseResult:
    """Classify an assistant message into ``final`` / ``action`` / ``error``.

    Returns one of:

    * ``("final", answer_text)`` — a ``Final Answer:`` was found. The
      strategy terminates the turn.
    * ``("action", tool_name, args_dict)`` — a well-formed
      ``Action:`` / ``Action Input:`` pair was found. Args are parsed
      as JSON; if Action Input is wrapped in a ```json`` fence the
      fence is stripped first. A ``[TOOL_CALL]`` JSON block with
      ``{"tool": <name>, "tool_input": <args>}`` maps to the same
      result and wins over a nearby ``Final Answer`` marker.
    * ``("error", reason)`` — neither shape is present or the JSON
      could not be parsed. The strategy will issue one corrective
      nudge and reroll; a second error terminates the turn.
    """
    tool_call = _parse_tool_call_block(text)
    if tool_call is not None:
        return tool_call

    # A classical ``Final Answer`` wins if present, even when an
    # accidental ``Action`` appears earlier — the ReAct paper treats
    # Final Answer as strictly terminal.
    final = _FINAL_RE.search(text)
    if final is not None:
        return ("final", final.group("answer").strip())

    action = _ACTION_RE.search(text)
    action_input = _ACTION_INPUT_RE.search(text)
    if action is None or action_input is None:
        return ("error", "missing Action / Action Input")

    name = action.group("name").strip()
    if not name:
        return ("error", "Action name is empty")

    body = action_input.group("body").strip()
    fence = _CODE_FENCE_RE.match(body)
    if fence is not None:
        body = fence.group("inner").strip()

    try:
        parsed: Any = json.loads(body)
    except ValueError as exc:
        return ("error", f"Action Input is not valid JSON ({exc!s})")
    if not isinstance(parsed, dict):
        return ("error", f"Action Input must be a JSON object, got {type(parsed).__name__}")
    return ("action", name, cast("dict[str, Any]", parsed))


def _parse_tool_call_block(text: str) -> ParseResult | None:
    """Parse a provider-style ``[TOOL_CALL]`` block if one is present."""
    match = _TOOL_CALL_RE.search(text)
    if match is None:
        return None

    body = match.group("body").strip()
    fence = _CODE_FENCE_RE.match(body)
    if fence is not None:
        body = fence.group("inner").strip()

    try:
        parsed: Any = json.loads(body)
    except ValueError as exc:
        return ("error", f"TOOL_CALL body is not valid JSON ({exc!s})")
    if not isinstance(parsed, dict):
        return ("error", f"TOOL_CALL body must be a JSON object, got {type(parsed).__name__}")

    payload = cast("dict[str, Any]", parsed)
    tool_raw = payload.get("tool")
    if not isinstance(tool_raw, str) or not tool_raw.strip():
        return ("error", "TOOL_CALL field 'tool' must be a non-empty string")

    args_raw: Any
    if "tool_input" in payload:
        args_raw = payload["tool_input"]
    elif "args" in payload:
        args_raw = payload["args"]
    else:
        args_raw = {}
    if not isinstance(args_raw, dict):
        return ("error", f"TOOL_CALL input must be a JSON object, got {type(args_raw).__name__}")

    return ("action", tool_raw.strip(), cast("dict[str, Any]", args_raw))


__all__ = ["ReActStrategy"]
