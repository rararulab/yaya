/**
 * Unit tests for ``splitThoughtFromFinal`` (#167).
 *
 * The helper mirrors the Python ``_FINAL_RE`` / ``_THOUGHT`` regex
 * shapes from ``src/yaya/plugins/strategy_react/plugin.py``; the
 * scenarios below are the same shapes the strategy parser has to
 * tolerate plus the mid-stream partial that only the UI ever sees.
 */

import { describe, expect, it } from "vitest";
import { splitThoughtFromFinal } from "../thought-split.js";

describe("splitThoughtFromFinal", () => {
	it("splits Thought + Final Answer into both parts", () => {
		expect(splitThoughtFromFinal("Thought: x\nFinal Answer: y")).toEqual({
			thought: "x",
			answer: "y",
		});
	});

	it("returns thought=null when only a Final Answer is present", () => {
		expect(splitThoughtFromFinal("Final Answer: y")).toEqual({
			thought: null,
			answer: "y",
		});
	});

	it("passes plain text through unchanged", () => {
		expect(splitThoughtFromFinal("plain text")).toEqual({
			thought: null,
			answer: "plain text",
		});
	});

	it("captures a mid-stream Thought with no Final Answer yet", () => {
		// Streaming case: the delta frame landed ``Thought:`` content
		// before the model emitted ``Final Answer:``. The UI keeps the
		// <details> collapsed so the user does not see a flash of raw
		// reasoning before the final answer arrives.
		expect(splitThoughtFromFinal("Thought: partial")).toEqual({
			thought: "partial",
			answer: "",
		});
	});

	it("captures trailing content after the Final Answer", () => {
		// Final Answer is the terminal label; anything after it is
		// treated as part of the answer body (matches the Python
		// ``_FINAL_RE`` which runs to ``\Z`` when no further label
		// appears). We trim the leading/trailing whitespace.
		expect(
			splitThoughtFromFinal("Thought: a\nFinal Answer: b\n\nextra"),
		).toEqual({
			thought: "a",
			answer: "b\n\nextra",
		});
	});

	it("handles a multi-line Thought body", () => {
		expect(
			splitThoughtFromFinal(
				"Thought: multi\nline reasoning\nFinal Answer: reply",
			),
		).toEqual({
			thought: "multi\nline reasoning",
			answer: "reply",
		});
	});

	it("trims surrounding whitespace on both parts", () => {
		expect(
			splitThoughtFromFinal("Thought:   x   \nFinal Answer:   y   "),
		).toEqual({ thought: "x", answer: "y" });
	});
});
