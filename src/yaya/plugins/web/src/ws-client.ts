/**
 * Minimal WebSocket client for the yaya adapter protocol.
 *
 * Responsibilities:
 *   - Connect to `/ws` and auto-reconnect with exponential backoff.
 *   - Enqueue sends that arrive before the socket is OPEN and flush
 *     them on open.
 *   - Fan out parsed frames to any number of subscribers.
 *   - Emit synthetic `ws.connected` / `ws.disconnected` meta-frames
 *     so UI code has a uniform stream to consume.
 *   - Log (but never crash on) unknown frame kinds — lesson #10.
 */

import type { Frame, InboundFrame, OutboundFrame } from "./types.js";

const KNOWN_KINDS: ReadonlySet<string> = new Set([
	"assistant.delta",
	"assistant.done",
	"tool.start",
	"tool.result",
	"plugin.loaded",
	"plugin.removed",
	"plugin.error",
	"kernel.ready",
	"kernel.shutdown",
	"kernel.error",
]);

const BACKOFF_SCHEDULE_MS = [500, 1000, 2000, 4000, 10000] as const;

export interface WsClientOptions {
	url: string;
	/** Injected constructor — tests pass a fake `WebSocket` impl. */
	webSocketImpl?: typeof WebSocket;
	/** Sink for structured log messages; defaults to `console`. */
	logger?: {
		warn: (...args: unknown[]) => void;
		info: (...args: unknown[]) => void;
	};
	/** Timer primitives — tests can pass `vi` fakes. */
	setTimeout?: (cb: () => void, ms: number) => number;
	clearTimeout?: (handle: number) => void;
}

type Subscriber = (frame: Frame) => void;

export class WsClient {
	private readonly url: string;
	private readonly WebSocketImpl: typeof WebSocket;
	private readonly logger: NonNullable<WsClientOptions["logger"]>;
	private readonly setTimeoutFn: (cb: () => void, ms: number) => number;
	private readonly clearTimeoutFn: (handle: number) => void;

	private socket: WebSocket | null = null;
	private readonly subscribers: Set<Subscriber> = new Set();
	private readonly sendQueue: OutboundFrame[] = [];
	private backoffIdx = 0;
	private reconnectTimer: number | null = null;
	private closedByUser = false;

	constructor(opts: WsClientOptions) {
		this.url = opts.url;
		this.WebSocketImpl = opts.webSocketImpl ?? WebSocket;
		this.logger = opts.logger ?? {
			warn: (...a) => console.warn("[ws]", ...a),
			info: (...a) => console.info("[ws]", ...a),
		};
		this.setTimeoutFn = opts.setTimeout ?? ((cb, ms) => window.setTimeout(cb, ms));
		this.clearTimeoutFn = opts.clearTimeout ?? ((h) => window.clearTimeout(h));
	}

	connect(): void {
		this.closedByUser = false;
		this.openSocket();
	}

	close(): void {
		this.closedByUser = true;
		if (this.reconnectTimer !== null) {
			this.clearTimeoutFn(this.reconnectTimer);
			this.reconnectTimer = null;
		}
		this.socket?.close();
		this.socket = null;
	}

	/** Subscribe to all inbound + meta frames. Returns an unsubscribe fn. */
	onFrame(cb: Subscriber): () => void {
		this.subscribers.add(cb);
		return () => {
			this.subscribers.delete(cb);
		};
	}

	send(frame: OutboundFrame): void {
		if (this.socket && this.socket.readyState === this.WebSocketImpl.OPEN) {
			this.socket.send(JSON.stringify(frame));
			return;
		}
		this.sendQueue.push(frame);
	}

	// -- internals --------------------------------------------------------

	private openSocket(): void {
		const ws = new this.WebSocketImpl(this.url);
		this.socket = ws;

		ws.addEventListener("open", () => {
			this.backoffIdx = 0;
			this.emit({ type: "ws.connected" });
			this.flushQueue();
		});

		ws.addEventListener("message", (ev: MessageEvent) => {
			this.handleMessage(ev.data);
		});

		ws.addEventListener("close", () => {
			this.emit({ type: "ws.disconnected" });
			this.socket = null;
			if (!this.closedByUser) {
				this.scheduleReconnect();
			}
		});

		ws.addEventListener("error", () => {
			// The "close" handler runs next; nothing to do here beyond
			// letting the browser log it.
		});
	}

	private flushQueue(): void {
		const sock = this.socket;
		if (!sock || sock.readyState !== this.WebSocketImpl.OPEN) {
			return;
		}
		while (this.sendQueue.length > 0) {
			const frame = this.sendQueue.shift();
			if (frame === undefined) {
				break;
			}
			sock.send(JSON.stringify(frame));
		}
	}

	private handleMessage(raw: unknown): void {
		if (typeof raw !== "string") {
			this.logger.warn("non-string ws payload dropped");
			return;
		}
		let parsed: unknown;
		try {
			parsed = JSON.parse(raw);
		} catch (err) {
			this.logger.warn("invalid JSON frame dropped:", err);
			return;
		}
		if (
			typeof parsed !== "object" ||
			parsed === null ||
			typeof (parsed as { type: unknown }).type !== "string"
		) {
			this.logger.warn("frame missing string `type` field:", parsed);
			return;
		}
		const frame = parsed as InboundFrame;
		if (!KNOWN_KINDS.has(frame.type)) {
			// Lesson #10: surface unknown kinds so catalog drift between
			// Python and TS is observable at runtime.
			this.logger.warn("unknown ws frame type, forwarding anyway:", frame.type);
		}
		// Reset backoff — we received an actual frame from the server.
		this.backoffIdx = 0;
		this.emit(frame);
	}

	private emit(frame: Frame): void {
		for (const sub of this.subscribers) {
			try {
				sub(frame);
			} catch (err) {
				this.logger.warn("subscriber threw:", err);
			}
		}
	}

	private scheduleReconnect(): void {
		if (this.reconnectTimer !== null) {
			return;
		}
		const delay = BACKOFF_SCHEDULE_MS[Math.min(this.backoffIdx, BACKOFF_SCHEDULE_MS.length - 1)]!;
		this.backoffIdx++;
		this.reconnectTimer = this.setTimeoutFn(() => {
			this.reconnectTimer = null;
			if (!this.closedByUser) {
				this.openSocket();
			}
		}, delay);
	}
}

export function defaultWsUrl(): string {
	const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
	return `${proto}//${window.location.host}/ws`;
}
