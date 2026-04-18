/**
 * Unit tests for WsClient — fake WebSocket + fake timers.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { WsClient } from "../ws-client.js";
import type { Frame } from "../types.js";

type Listener = (ev: unknown) => void;

class FakeSocket {
	static instances: FakeSocket[] = [];
	static readonly CONNECTING = 0;
	static readonly OPEN = 1;
	static readonly CLOSING = 2;
	static readonly CLOSED = 3;
	readonly CONNECTING = 0;
	readonly OPEN = 1;
	readonly CLOSING = 2;
	readonly CLOSED = 3;

	readyState: number = FakeSocket.CONNECTING;
	sent: string[] = [];
	listeners: Record<string, Listener[]> = {};

	constructor(public url: string) {
		FakeSocket.instances.push(this);
	}

	addEventListener(name: string, cb: Listener): void {
		(this.listeners[name] ??= []).push(cb);
	}

	send(data: string): void {
		this.sent.push(data);
	}

	close(): void {
		this.readyState = FakeSocket.CLOSED;
		this.dispatch("close", {});
	}

	// -- test helpers --------------------------------------------------

	open(): void {
		this.readyState = FakeSocket.OPEN;
		this.dispatch("open", {});
	}

	receiveText(raw: string): void {
		this.dispatch("message", { data: raw });
	}

	receive(frame: unknown): void {
		this.receiveText(JSON.stringify(frame));
	}

	triggerClose(): void {
		this.readyState = FakeSocket.CLOSED;
		this.dispatch("close", {});
	}

	private dispatch(name: string, ev: unknown): void {
		for (const cb of this.listeners[name] ?? []) {
			cb(ev);
		}
	}
}

function makeClient() {
	FakeSocket.instances = [];
	const warnings: unknown[][] = [];
	const client = new WsClient({
		url: "ws://test/ws",
		webSocketImpl: FakeSocket as unknown as typeof WebSocket,
		logger: {
			warn: (...a) => warnings.push(a),
			info: () => {},
		},
	});
	return { client, warnings };
}

describe("WsClient", () => {
	beforeEach(() => {
		vi.useRealTimers();
	});

	it("parses every known frame kind", () => {
		const { client } = makeClient();
		const frames: Frame[] = [];
		client.onFrame((f) => frames.push(f));
		client.connect();
		const sock = FakeSocket.instances[0]!;
		sock.open();

		sock.receive({ type: "assistant.delta", content: "hi", session_id: "ws-x" });
		sock.receive({ type: "assistant.done", content: "done", tool_calls: [], session_id: "ws-x" });
		sock.receive({ type: "tool.start", id: "t1", name: "bash", args: {}, session_id: "ws-x" });
		sock.receive({ type: "tool.result", id: "t1", ok: true, value: "ok", session_id: "ws-x" });
		sock.receive({ type: "plugin.loaded", name: "p", version: "1", category: "tool", session_id: "kernel" });
		sock.receive({ type: "plugin.removed", name: "p", session_id: "kernel" });
		sock.receive({ type: "plugin.error", name: "p", error: "boom", session_id: "kernel" });
		sock.receive({ type: "kernel.ready", version: "0.1", session_id: "kernel" });
		sock.receive({ type: "kernel.shutdown", reason: "x", session_id: "kernel" });
		sock.receive({ type: "kernel.error", source: "bus", message: "m", session_id: "kernel" });

		// 1 synthetic open + 10 inbound = 11.
		expect(frames).toHaveLength(11);
		expect(frames[0]).toEqual({ type: "ws.connected" });
		expect(frames[1]!.type).toBe("assistant.delta");
	});

	it("logs a warning but does not crash on unknown frame types", () => {
		const { client, warnings } = makeClient();
		const frames: Frame[] = [];
		client.onFrame((f) => frames.push(f));
		client.connect();
		const sock = FakeSocket.instances[0]!;
		sock.open();

		sock.receive({ type: "x.custom.kind", foo: 1 });
		expect(warnings.some((w) => String(w[0]).includes("unknown ws frame type"))).toBe(true);
		// Still forwarded — subscribers get to decide what to do.
		expect(frames.some((f) => (f as { type: string }).type === "x.custom.kind")).toBe(true);
	});

	it("queues sends before OPEN and flushes on open", () => {
		const { client } = makeClient();
		client.connect();
		const sock = FakeSocket.instances[0]!;
		// Not yet open.
		client.send({ type: "user.message", text: "hi" });
		expect(sock.sent).toHaveLength(0);
		sock.open();
		expect(sock.sent).toEqual([JSON.stringify({ type: "user.message", text: "hi" })]);
	});

	it("emits synthetic ws.connected / ws.disconnected", () => {
		const { client } = makeClient();
		const frames: Frame[] = [];
		client.onFrame((f) => frames.push(f));
		client.connect();
		const sock = FakeSocket.instances[0]!;
		sock.open();
		sock.triggerClose();
		expect(frames.map((f) => f.type)).toContain("ws.connected");
		expect(frames.map((f) => f.type)).toContain("ws.disconnected");
	});

	it("reconnects with exponential backoff", () => {
		vi.useFakeTimers();
		FakeSocket.instances = [];
		const client = new WsClient({
			url: "ws://test/ws",
			webSocketImpl: FakeSocket as unknown as typeof WebSocket,
			logger: { warn: () => {}, info: () => {} },
			setTimeout: ((cb: () => void, ms: number) => setTimeout(cb, ms)) as unknown as (cb: () => void, ms: number) => number,
			clearTimeout: ((h: number) => clearTimeout(h)) as unknown as (h: number) => void,
		});
		client.connect();
		expect(FakeSocket.instances).toHaveLength(1);
		FakeSocket.instances[0]!.triggerClose();
		// First backoff: 500ms.
		vi.advanceTimersByTime(499);
		expect(FakeSocket.instances).toHaveLength(1);
		vi.advanceTimersByTime(2);
		expect(FakeSocket.instances).toHaveLength(2);
		// Drop again — next backoff should be 1000ms.
		FakeSocket.instances[1]!.triggerClose();
		vi.advanceTimersByTime(999);
		expect(FakeSocket.instances).toHaveLength(2);
		vi.advanceTimersByTime(2);
		expect(FakeSocket.instances).toHaveLength(3);
		vi.useRealTimers();
	});
});
