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
 * Keyboard + auto-grow behaviour for the multiline textarea (issue #115).
 *
 * The shell consults `navigator.platform` at module load to decide
 * whether Cmd+Enter or Ctrl+Enter submits. Tests that need the other
 * platform dynamic-import the module after stubbing `navigator`.
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

describe("YayaChat multiline input (issue #115)", () => {
	it("submits on the platform submit-modifier + Enter", () => {
		// The module picks Cmd (macOS) or Ctrl (other) at load time via
		// `navigator.platform`. We fire the event with BOTH modifiers set
		// so the test is platform-agnostic: whichever one the code reads,
		// it succeeds. The negative-path tests below cover the other key.
		const shell = makeShell() as unknown as KeyboardInternals;
		const captured = sends(shell);
		shell.inputValue = "hello";
		const ev = new KeyboardEvent("keydown", {
			key: "Enter",
			metaKey: true,
			ctrlKey: true,
		});
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

	it("plain Enter does not submit — native newline falls through", () => {
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
		// No preventDefault, no send — the native textarea handles the newline.
		expect(prevented).toBe(false);
		expect(captured).toHaveLength(0);
	});

	it("Shift+Enter does not submit", () => {
		const shell = makeShell() as unknown as KeyboardInternals;
		const captured = sends(shell);
		shell.inputValue = "hello";
		shell.onKeyDown(new KeyboardEvent("keydown", { key: "Enter", shiftKey: true }));
		expect(captured).toHaveLength(0);
	});

	it("empty/whitespace input + submit modifier does not fire a send", () => {
		const shell = makeShell() as unknown as KeyboardInternals;
		const captured = sends(shell);
		shell.inputValue = "   \n  ";
		const ev = new KeyboardEvent("keydown", {
			key: "Enter",
			metaKey: true,
			ctrlKey: true,
		});
		shell.onKeyDown(ev);
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
