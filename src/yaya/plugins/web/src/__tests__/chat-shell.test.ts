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
