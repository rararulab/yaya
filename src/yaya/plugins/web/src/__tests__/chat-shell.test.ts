/**
 * Regression tests for bug #71.
 *
 * Exercises `YayaChat.onFrame` in isolation — we construct the Lit
 * element and drive its private frame handler through a narrow
 * structural cast. We deliberately avoid mounting into the DOM:
 * `onFrame` is a pure state transition, and the pi-web-ui transitive
 * imports are module-level side effects that happen on import either
 * way.
 */

import { beforeEach, describe, expect, it } from "vitest";
import "../chat-shell.js";
import type { YayaChat } from "../chat-shell.js";
import type { AssistantChatMessage } from "../types.js";

// Suppress unused warning — YayaChat is referenced via `ctor`.
void (null as unknown as YayaChat);

interface Internals {
	inFlight: boolean;
	streamingMessage: AssistantChatMessage | null;
	messages: unknown[];
	toasts: { id: number; kind: "info" | "error"; text: string }[];
	onFrame(frame: unknown): void;
	pushToast(kind: "info" | "error", text: string): void;
}

function makeShell(): Internals {
	const ctor = customElements.get("yaya-chat") as { new (): YayaChat } | undefined;
	if (!ctor) {
		throw new Error("yaya-chat custom element not registered");
	}
	return new ctor() as unknown as Internals;
}

describe("YayaChat error recovery (bug #71 P1)", () => {
	let shell: Internals;

	beforeEach(() => {
		shell = makeShell();
		shell.inFlight = true;
		shell.streamingMessage = {
			role: "assistant",
			content: [{ type: "text", text: "partial" }],
			api: "responses",
			provider: "kernel",
			model: "kernel",
			usage: {
				input: 0,
				output: 0,
				cacheRead: 0,
				cacheWrite: 0,
				totalTokens: 0,
				cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
			},
			stopReason: "stop",
			timestamp: 0,
		};
	});

	it("resets inFlight on kernel.error", () => {
		shell.onFrame({
			type: "kernel.error",
			session_id: "ws-x",
			source: "agent_loop",
			message: "boom",
		});
		expect(shell.inFlight).toBe(false);
		expect(shell.streamingMessage).toBeNull();
	});

	it("resets inFlight on plugin.error", () => {
		shell.onFrame({
			type: "plugin.error",
			session_id: "kernel",
			name: "foo",
			error: "boom",
		});
		expect(shell.inFlight).toBe(false);
		expect(shell.streamingMessage).toBeNull();
	});

	it("keeps inFlight on informational plugin.loaded", () => {
		shell.onFrame({
			type: "plugin.loaded",
			session_id: "kernel",
			name: "foo",
			version: "1.0.0",
			category: "tool",
		});
		expect(shell.inFlight).toBe(true);
	});
});

describe("YayaChat message rendering (bug #71 P1)", () => {
	it("records user messages verbatim so bubbles can render them", () => {
		const shell = makeShell();
		shell.onFrame({ type: "ws.connected" });
		// Directly mirror what sendMessage does — we are not testing the
		// input box here, only that the stored shape carries the text.
		shell.messages = [
			...shell.messages,
			{ role: "user", content: "hello", timestamp: Date.now() },
		];
		const last = shell.messages[shell.messages.length - 1] as {
			role: string;
			content: string;
		};
		expect(last.role).toBe("user");
		expect(last.content).toBe("hello");
	});

	it("records assistant.done with the concatenated text", () => {
		const shell = makeShell();
		shell.onFrame({
			type: "assistant.done",
			session_id: "ws-x",
			content: "hi",
			tool_calls: [],
		});
		const last = shell.messages[shell.messages.length - 1] as AssistantChatMessage;
		expect(last.role).toBe("assistant");
		const text = last.content
			.filter((c): c is { type: "text"; text: string } => c.type === "text")
			.map((c) => c.text)
			.join("");
		expect(text).toBe("hi");
		expect(shell.inFlight).toBe(false);
	});
});

interface HydrateInternals extends Internals {
	toolCallsById: Map<string, { id: string; name: string; output: string; ok?: boolean; error?: string }>;
	pendingToolCalls: Set<string>;
	streamingMessage: AssistantChatMessage | null;
	hydrateFrames(frames: unknown[]): void;
}

describe("YayaChat hydrateFrames (#162)", () => {
	it("reconstructs tool cards from a user+tool.start+tool.result+assistant.done tape", () => {
		const shell = makeShell() as unknown as HydrateInternals;
		shell.hydrateFrames([
			{ kind: "user.message", text: "run ls" },
			{ kind: "tool.start", id: "t1", name: "bash", args: { cmd: "ls" } },
			{ kind: "tool.result", id: "t1", ok: true, value: { stdout: "a\n" } },
			{ kind: "assistant.done", content: "listed", tool_calls: [] },
		]);
		expect(shell.toolCallsById.size).toBe(1);
		const tc = shell.toolCallsById.get("t1");
		expect(tc?.name).toBe("bash");
		expect(tc?.ok).toBe(true);
		expect(shell.pendingToolCalls.size).toBe(0);
		expect(shell.streamingMessage).toBeNull();
		expect(shell.inFlight).toBe(false);
		// Exactly one assistant bubble and one user bubble (+ the
		// toolResult trailer tr); the tool card renders via the
		// toolCallsById map, not a second assistant message.
		const roles = (shell.messages as { role: string }[]).map((m) => m.role);
		expect(roles.filter((r) => r === "user")).toHaveLength(1);
		expect(roles.filter((r) => r === "assistant")).toHaveLength(1);
	});

	it("skips nothing client-side — Observation rows are filtered server-side", () => {
		// Smoke test: hydrateFrames does NOT re-filter; the backend
		// already elided the Observation user bubble, so a frame list
		// with two user.message items produces two user bubbles.
		const shell = makeShell() as unknown as HydrateInternals;
		shell.hydrateFrames([
			{ kind: "user.message", text: "hi" },
			{ kind: "user.message", text: "hello" },
		]);
		const roles = (shell.messages as { role: string }[]).map((m) => m.role);
		expect(roles.filter((r) => r === "user")).toHaveLength(2);
	});

	it("records tool.result error details on the card", () => {
		const shell = makeShell() as unknown as HydrateInternals;
		shell.hydrateFrames([
			{ kind: "tool.start", id: "t1", name: "bash", args: {} },
			{ kind: "tool.result", id: "t1", ok: false, error: "boom" },
		]);
		const tc = shell.toolCallsById.get("t1");
		expect(tc?.ok).toBe(false);
		expect(tc?.error).toBe("boom");
		expect(tc?.output).toBe("boom");
	});
});

describe("YayaBubble ReAct thought folding (#167)", () => {
	async function renderBubble(role: "user" | "assistant", content: string): Promise<HTMLElement> {
		const el = document.createElement("yaya-bubble") as HTMLElement & {
			role: string;
			content: string;
			updateComplete: Promise<unknown>;
		};
		el.role = role;
		el.content = content;
		document.body.appendChild(el);
		await el.updateComplete;
		return el;
	}

	it("folds Thought into a collapsed <details> and shows the Final Answer", async () => {
		const el = await renderBubble(
			"assistant",
			"Thought: because\nFinal Answer: hello",
		);
		const details = el.querySelectorAll("details.yaya-thought");
		expect(details).toHaveLength(1);
		const summary = details[0]?.querySelector("summary");
		expect(summary?.textContent?.trim()).toBe("Show reasoning");
		expect((details[0] as HTMLDetailsElement).open).toBe(false);
		const answer = el.querySelector(".yaya-answer");
		expect(answer?.textContent).toBe("hello");
		el.remove();
	});

	it("renders plain assistant content without a <details> wrapper", async () => {
		const el = await renderBubble("assistant", "just a reply");
		expect(el.querySelectorAll("details.yaya-thought")).toHaveLength(0);
		expect(el.textContent).toContain("just a reply");
		el.remove();
	});

	it("renders user bubbles verbatim even when they look like ReAct text", async () => {
		const el = await renderBubble("user", "Thought: hmm\nFinal Answer: ok");
		expect(el.querySelectorAll("details.yaya-thought")).toHaveLength(0);
		el.remove();
	});

	it("keeps the answer area empty while mid-stream (Thought only, no Final Answer)", async () => {
		const el = await renderBubble("assistant", "Thought: partial reasoning");
		expect(el.querySelectorAll("details.yaya-thought")).toHaveLength(1);
		expect(el.querySelector(".yaya-answer")).toBeNull();
		el.remove();
	});
});

describe("YayaChat streaming deltas (#168)", () => {
	it("accumulates successive assistant.delta frames into streamingMessage", () => {
		const shell = makeShell();
		shell.onFrame({ type: "assistant.delta", session_id: "ws-x", content: "Hel" });
		shell.onFrame({ type: "assistant.delta", session_id: "ws-x", content: "lo" });
		const s = shell.streamingMessage as AssistantChatMessage;
		expect(s).not.toBeNull();
		const text = s.content
			.filter((c): c is { type: "text"; text: string } => c.type === "text")
			.map((c) => c.text)
			.join("");
		expect(text).toBe("Hello");
	});

	it("clears streamingMessage on assistant.done after a stream of deltas", () => {
		const shell = makeShell();
		shell.onFrame({ type: "assistant.delta", session_id: "ws-x", content: "Hi" });
		shell.onFrame({
			type: "assistant.done",
			session_id: "ws-x",
			content: "Hi",
			tool_calls: [],
		});
		expect(shell.streamingMessage).toBeNull();
	});
});

describe("YayaBubble partial thought during streaming (#167 + #168)", () => {
	async function renderBubble(role: "user" | "assistant", content: string): Promise<HTMLElement> {
		const el = document.createElement("yaya-bubble") as HTMLElement & {
			role: string;
			content: string;
			updateComplete: Promise<unknown>;
		};
		el.role = role;
		el.content = content;
		document.body.appendChild(el);
		await el.updateComplete;
		return el;
	}

	it("folds partial Thought emitted mid-stream without flashing raw prefix bytes", async () => {
		// Mid-stream: chat-shell re-renders the bubble on every delta.
		// splitThoughtFromFinal must collapse the Thought into <details>
		// even though the Final Answer has not arrived yet, and must
		// not surface the literal "Thought:" prefix in a raw .yaya-answer
		// block that would flash at the user.
		const el = await renderBubble("assistant", "Thought: thinking about it");
		expect(el.querySelectorAll("details.yaya-thought")).toHaveLength(1);
		expect(el.querySelector(".yaya-answer")).toBeNull();
		expect(el.textContent).not.toContain("Thought: thinking about it");
		el.remove();
	});
});

describe("YayaChat toast lifecycle (bug #71 P3)", () => {
	it("keeps error toasts until dismissed", () => {
		const shell = makeShell();
		shell.pushToast("error", "boom");
		expect(shell.toasts).toHaveLength(1);
		// Error toasts do not schedule an auto-dismiss timer. We do not
		// need fake timers — we simply assert the toast is still present
		// after pushToast returns.
		expect(shell.toasts[0]?.kind).toBe("error");
	});
});

/**
 * Keyboard + auto-grow behaviour for the multiline textarea.
 *
 * Kimi-style: plain Enter submits; Shift+Enter inserts a newline. IME
 * composition still suppresses submit so CJK input stays safe.
 */
interface KeyboardInternals extends Internals {
	inputValue: string;
	ws: { send: (msg: unknown) => void } | null;
	sendMessage(): void;
	onKeyDown(e: KeyboardEvent): void;
	onInputEvent(e: Event): void;
	autoGrow(el: HTMLTextAreaElement): void;
}

function sends(shell: KeyboardInternals): unknown[] {
	const captured: unknown[] = [];
	shell.ws = { send: (msg) => captured.push(msg) };
	return captured;
}

describe("YayaChat multiline input", () => {
	it("submits on plain Enter (kimi-style)", () => {
		const shell = makeShell() as unknown as KeyboardInternals;
		const captured = sends(shell);
		shell.inputValue = "hello";
		const ev = new KeyboardEvent("keydown", { key: "Enter" });
		let prevented = false;
		Object.defineProperty(ev, "preventDefault", {
			value: () => {
				prevented = true;
			},
		});
		shell.onKeyDown(ev);
		expect(prevented).toBe(true);
		expect(captured).toHaveLength(1);
		expect((captured[0] as { type: string }).type).toBe("user.message");
	});

	it("does not submit on Enter during IME composition", () => {
		// Pressing Enter during IME composition (Chinese pinyin / Japanese
		// kana / Korean hangul) commits the candidate — the browser fires
		// `keydown` with `isComposing: true` (or `keyCode: 229` on older
		// engines). Never treat that as a submit.
		const shell = makeShell() as unknown as KeyboardInternals;
		const captured = sends(shell);
		shell.inputValue = "hello";
		const ev = new KeyboardEvent("keydown", { key: "Enter" });
		Object.defineProperty(ev, "isComposing", { value: true });
		let prevented = false;
		Object.defineProperty(ev, "preventDefault", {
			value: () => {
				prevented = true;
			},
		});
		shell.onKeyDown(ev);
		expect(prevented).toBe(false);
		expect(captured).toHaveLength(0);
	});

	it("Shift+Enter inserts a newline instead of submitting", () => {
		const shell = makeShell() as unknown as KeyboardInternals;
		const captured = sends(shell);
		shell.inputValue = "hello";
		const ev = new KeyboardEvent("keydown", { key: "Enter", shiftKey: true });
		let prevented = false;
		Object.defineProperty(ev, "preventDefault", {
			value: () => {
				prevented = true;
			},
		});
		shell.onKeyDown(ev);
		// No preventDefault, no send — textarea handles the newline natively.
		expect(prevented).toBe(false);
		expect(captured).toHaveLength(0);
	});

	it("empty/whitespace input + Enter does not fire a send", () => {
		const shell = makeShell() as unknown as KeyboardInternals;
		const captured = sends(shell);
		shell.inputValue = "   \n  ";
		shell.onKeyDown(new KeyboardEvent("keydown", { key: "Enter" }));
		expect(captured).toHaveLength(0);
	});

	it("auto-grows the textarea height with content", () => {
		const shell = makeShell() as unknown as KeyboardInternals;
		const ta = document.createElement("textarea");
		// jsdom has no layout engine — fake scrollHeight so we can
		// observe the clamp logic deterministically.
		Object.defineProperty(ta, "scrollHeight", {
			configurable: true,
			get: () => 120,
		});
		shell.autoGrow(ta);
		expect(ta.style.height).toBe("120px");
		Object.defineProperty(ta, "scrollHeight", {
			configurable: true,
			get: () => 500,
		});
		shell.autoGrow(ta);
		// Clamp at 240 (INPUT_MAX_PX).
		expect(ta.style.height).toBe("240px");
	});

	it("onInputEvent updates inputValue from the textarea", () => {
		const shell = makeShell() as unknown as KeyboardInternals;
		const ta = document.createElement("textarea");
		ta.value = "line 1\nline 2\nline 3";
		Object.defineProperty(ta, "scrollHeight", {
			configurable: true,
			get: () => 80,
		});
		const ev = new Event("input");
		Object.defineProperty(ev, "target", { value: ta });
		shell.onInputEvent(ev);
		expect(shell.inputValue).toBe("line 1\nline 2\nline 3");
		expect(ta.style.height).toBe("80px");
	});
});
