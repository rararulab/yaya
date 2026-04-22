/**
 * Split a ReAct-style assistant message into its ``Thought`` and
 * ``Final Answer`` pieces for UI folding (#167).
 *
 * The ReAct strategy plugin
 * (``src/yaya/plugins/strategy_react/plugin.py``) constrains the LLM
 * to emit one of two shapes:
 *
 * * ``Thought: ...\nAction: <name>\nAction Input: <json>`` — a tool
 *   call triple. Under the current agent loop the ``Action`` part
 *   never lands in ``assistant.done`` content (tool calls ride as
 *   separate ``tool.start`` / ``tool.result`` frames), so the UI
 *   only ever sees ``Thought:`` here.
 * * ``Thought: <reasoning>\nFinal Answer: <user-facing text>`` — the
 *   terminal shape the user actually wants to read.
 *
 * Rendering the raw text dumps the chain-of-thought straight into
 * the chat bubble. This helper lets the bubble fold the thought
 * behind a ``<details>`` and surface only the final answer by
 * default, while keeping the tape and protocol identical.
 *
 * The regex semantics mirror the Python ``_FINAL_RE`` /
 * ``_ACTION_RE`` from the strategy (``re.MULTILINE | re.DOTALL``)
 * translated to JavaScript's ``m`` + ``s`` flags.
 */

/** Result of splitting a ReAct message. */
export interface ThoughtSplit {
	/** Captured ``Thought:`` body, trimmed; ``null`` when absent. */
	thought: string | null;
	/**
	 * Captured ``Final Answer:`` body, trimmed. Empty string when the
	 * stream carries a ``Thought:`` prefix but no ``Final Answer:``
	 * yet (mid-stream partial). For non-ReAct content this is the
	 * original ``content`` returned verbatim so echo-style providers
	 * render unchanged.
	 */
	answer: string;
}

// Match each label at the start of a line (``^`` via start-of-string
// or the ``\n`` alternative) lazily up to the next ReAct label or
// end of string. We deliberately avoid the ``m`` flag so ``$`` means
// end-of-input (matching Python's ``\Z``). ``[\s\S]`` stands in for
// DOTALL without the ``s`` flag.
const THOUGHT_RE = /(?:^|\n)Thought:\s*([\s\S]+?)(?=\nAction:|\nFinal Answer:|$)/;
const FINAL_RE = /(?:^|\n)Final Answer:\s*([\s\S]+?)(?=\nAction:|\nThought:|\nFinal Answer:|$)/;

/**
 * Return the ``{thought, answer}`` split for a ReAct assistant
 * message.
 *
 * Contract:
 *
 * * ``Thought:`` + ``Final Answer:`` both present → both captured
 *   and trimmed.
 * * ``Thought:`` only (mid-stream) → captured thought, empty
 *   answer. The UI must render the ``<details>`` *collapsed* so the
 *   user never sees a flash of raw reasoning before the final
 *   answer arrives.
 * * ``Final Answer:`` only → ``thought: null`` and the answer
 *   captured.
 * * Neither label present → ``thought: null``, answer is the
 *   original content verbatim (echo-style / non-ReAct providers).
 */
export function splitThoughtFromFinal(content: string): ThoughtSplit {
	const thoughtMatch = THOUGHT_RE.exec(content);
	const finalMatch = FINAL_RE.exec(content);

	if (thoughtMatch === null && finalMatch === null) {
		return { thought: null, answer: content };
	}

	const thought =
		thoughtMatch !== null && thoughtMatch[1] !== undefined
			? thoughtMatch[1].trim()
			: null;
	const answer =
		finalMatch !== null && finalMatch[1] !== undefined
			? finalMatch[1].trim()
			: "";
	return { thought, answer };
}
