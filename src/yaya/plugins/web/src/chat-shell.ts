/**
 * Top-level Lit component assembling the yaya chat surface.
 *
 * Composition:
 *   - local `<yaya-bubble>`        — user/assistant bubbles
 *   - auto-growing `<textarea>`    — see `onKeyDown` + `autoGrow` for
 *                                    keybinding + height clamping
 *                                    (max-height mirrored in CSS)
 *   - `ConsoleBlock` (pi-web-ui)   — tool stdout/stderr rendering
 *   - `ThemeToggle` (mini-lit)     — dark/light
 *
 * No agent logic runs here: this is a renderer + WS bridge. The
 * Python kernel owns the agent, keys, and session storage. See
 * lesson #27 for the Dependency-Rule reasoning.
 */

import { LitElement, html, nothing, type TemplateResult } from "lit";
import { customElement, property, state } from "lit/decorators.js";

// Side-effectful imports register the custom elements used below.
// The `@yaya/...` aliases are resolved by Vite and tsconfig to the
// installed package's `dist/` folder; this lets us cherry-pick
// individual modules without loading the barrel index, which would
// drag the full chat panel (and therefore the upstream agent-core
// runtime) into our bundle. See lesson 27 for the architectural
// rationale.
// NOTE: MessageList / StreamingMessageContainer / Messages are no longer
// imported — their rendering shape is pi-ai's `AgentMessage` and passes
// TypeScript but silently renders blank for our `ChatMessage` (bug #71).
// User and assistant bubbles now render via the local `<yaya-bubble>`
// component below; tool output stays on pi-web-ui's `<console-block>`.
import "@yaya/pi-web-ui/components/ConsoleBlock.js";
import "@yaya/mini-lit/ThemeToggle.js";

import type {
	AssistantChatMessage,
	ChatMessage,
	Frame,
	TextContent,
	ToolResultChatMessage,
	UserChatMessage,
} from "./types.js";
import { assertNever } from "./types.js";
import { WsClient, defaultWsUrl } from "./ws-client.js";

type ConnectionStatus = "connecting" | "connected" | "reconnecting";

/**
 * Broadcast the current WS status to any listener (currently
 * `<yaya-app>`'s sidebar footer). We use a window-level CustomEvent
 * rather than a direct reference so the chat component stays
 * addressable in isolation in tests and the sidebar can pick up
 * transitions without a parent-child coupling.
 */
function publishConnectionStatus(status: ConnectionStatus): void {
	window.dispatchEvent(
		new CustomEvent("yaya:connection-status", { detail: { status } }),
	);
}

interface Toast {
	id: number;
	kind: "info" | "error";
	text: string;
}

interface ToolCallState {
	id: string;
	name: string;
	output: string;
	ok?: boolean;
	error?: string;
}

const THEME_KEY = "yaya.theme";

/** Maximum visible textarea height in pixels before internal scrolling kicks in. */
const INPUT_MAX_PX = 240;

function loadTheme(): "light" | "dark" {
	const stored = localStorage.getItem(THEME_KEY);
	if (stored === "light" || stored === "dark") {
		return stored;
	}
	return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme: "light" | "dark"): void {
	const root = document.documentElement;
	if (theme === "dark") {
		root.classList.add("dark");
	} else {
		root.classList.remove("dark");
	}
	localStorage.setItem(THEME_KEY, theme);
}

function emptyUsage(): AssistantChatMessage["usage"] {
	return {
		input: 0,
		output: 0,
		cacheRead: 0,
		cacheWrite: 0,
		totalTokens: 0,
		cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
	};
}

@customElement("yaya-chat")
export class YayaChat extends LitElement {
	@state() private messages: ChatMessage[] = [];
	@state() private streamingMessage: AssistantChatMessage | null = null;
	@state() private status: ConnectionStatus = "connecting";
	@state() private inFlight = false;
	@state() private toasts: Toast[] = [];
	@state() private inputValue = "";
	@state() private pendingToolCalls: Set<string> = new Set();
	@state() private toolCallsById: Map<string, ToolCallState> = new Map();

	private ws: WsClient | null = null;
	private nextToastId = 1;

	protected override createRenderRoot(): HTMLElement | DocumentFragment {
		return this;
	}

	private onNewChat = (): void => {
		this.messages = [];
		this.streamingMessage = null;
		this.inFlight = false;
		this.toolCallsById = new Map();
		this.pendingToolCalls = new Set();
		this.inputValue = "";
	};

	override connectedCallback(): void {
		super.connectedCallback();
		applyTheme(loadTheme());
		this.ws = new WsClient({ url: defaultWsUrl() });
		this.ws.onFrame((f) => this.onFrame(f));
		// Surface the initial handshake as `connecting` so the sidebar
		// dot does not flash red between mount and the first
		// `ws.connected` frame.
		publishConnectionStatus("connecting");
		this.ws.connect();
		window.addEventListener("yaya:new-chat", this.onNewChat);
	}

	override disconnectedCallback(): void {
		super.disconnectedCallback();
		this.ws?.close();
		this.ws = null;
		window.removeEventListener("yaya:new-chat", this.onNewChat);
	}

	private fillPrompt(text: string): void {
		this.inputValue = text;
	}

	// -- frame handler ----------------------------------------------------

	private onFrame(frame: Frame): void {
		switch (frame.type) {
			case "ws.connected":
				this.status = "connected";
				publishConnectionStatus("connected");
				return;
			case "ws.disconnected":
				this.status = "reconnecting";
				publishConnectionStatus("reconnecting");
				// An in-flight turn is effectively aborted when the socket
				// drops; reset so the UI becomes interactive again.
				this.inFlight = false;
				this.streamingMessage = null;
				return;
			case "assistant.delta":
				this.applyDelta(frame.content);
				return;
			case "assistant.done":
				this.finishAssistant(frame.content, frame.tool_calls ?? []);
				return;
			case "tool.start":
				this.pendingToolCalls = new Set([...this.pendingToolCalls, frame.id]);
				this.toolCallsById = new Map(this.toolCallsById).set(frame.id, {
					id: frame.id,
					name: frame.name,
					output: "",
				});
				return;
			case "tool.result": {
				const next = new Set(this.pendingToolCalls);
				next.delete(frame.id);
				this.pendingToolCalls = next;
				const tc = this.toolCallsById.get(frame.id);
				if (tc) {
					const output =
						frame.error ?? (typeof frame.value === "string" ? frame.value : JSON.stringify(frame.value ?? ""));
					const updated: ToolCallState = {
						id: tc.id,
						name: tc.name,
						output,
						ok: frame.ok,
						...(frame.error !== undefined ? { error: frame.error } : {}),
					};
					this.toolCallsById = new Map(this.toolCallsById).set(frame.id, updated);
				}
				// Also record as a toolResult message so MessageList can
				// pair it with the assistant turn.
				const tr: ToolResultChatMessage = {
					role: "toolResult",
					toolCallId: frame.id,
					toolName: tc?.name ?? frame.id,
					content: [{ type: "text", text: frame.error ?? String(frame.value ?? "") }],
					isError: !frame.ok,
					timestamp: Date.now(),
				};
				this.messages = [...this.messages, tr];
				return;
			}
			case "plugin.loaded":
				this.pushToast("info", `plugin loaded: ${frame.name}@${frame.version}`);
				return;
			case "plugin.removed":
				this.pushToast("info", `plugin removed: ${frame.name}`);
				return;
			case "plugin.error":
				this.pushToast("error", `plugin error (${frame.name}): ${frame.error}`);
				// Bug #71: an error during a turn must release the input.
				// Without this, the textarea stays disabled until reload.
				this.inFlight = false;
				this.streamingMessage = null;
				return;
			case "kernel.ready":
				this.pushToast("info", `kernel ready (v${frame.version})`);
				return;
			case "kernel.shutdown":
				this.pushToast("info", `kernel shutdown: ${frame.reason}`);
				return;
			case "kernel.error":
				this.pushToast("error", `kernel error (${frame.source}): ${frame.message}`);
				// Bug #71: a kernel-level failure aborts the turn; re-enable input.
				this.inFlight = false;
				this.streamingMessage = null;
				return;
			default:
				return assertNever(frame);
		}
	}

	private applyDelta(content: string): void {
		const existing = this.streamingMessage;
		if (existing === null) {
			this.streamingMessage = {
				role: "assistant",
				content: [{ type: "text", text: content }],
				api: "responses",
				provider: "kernel",
				model: "kernel",
				usage: emptyUsage(),
				stopReason: "stop",
				timestamp: Date.now(),
			};
			return;
		}
		const next: AssistantChatMessage = {
			...existing,
			content: existing.content.map((c, idx) =>
				idx === 0 && c.type === "text" ? { type: "text", text: c.text + content } : c,
			),
		};
		this.streamingMessage = next;
	}

	private finishAssistant(content: string, toolCalls: { id: string; name: string; args: Record<string, unknown> }[]): void {
		const parts: AssistantChatMessage["content"] = [];
		if (content) {
			parts.push({ type: "text", text: content });
		}
		for (const tc of toolCalls) {
			parts.push({ type: "toolCall", id: tc.id, name: tc.name, arguments: tc.args });
		}
		const msg: AssistantChatMessage = {
			role: "assistant",
			content: parts,
			api: "responses",
			provider: "kernel",
			model: "kernel",
			usage: emptyUsage(),
			stopReason: "stop",
			timestamp: Date.now(),
		};
		this.messages = [...this.messages, msg];
		this.streamingMessage = null;
		this.inFlight = false;
	}

	// -- outbound ---------------------------------------------------------

	private sendMessage(): void {
		const text = this.inputValue.trim();
		if (!text || this.inFlight || !this.ws) {
			return;
		}
		const msg: UserChatMessage = {
			role: "user",
			content: text,
			timestamp: Date.now(),
		};
		this.messages = [...this.messages, msg];
		this.inputValue = "";
		// Reset the textarea height after submit. `inputValue` is the
		// source of truth, but the element's inline `style.height` was
		// set by the auto-grow handler and is not cleared automatically
		// when the bound value shrinks.
		const ta = this.querySelector<HTMLTextAreaElement>(".yaya-input");
		if (ta) {
			ta.style.height = "auto";
		}
		this.inFlight = true;
		this.ws.send({ type: "user.message", text });
	}

	/**
	 * Auto-grows the textarea to match its content up to `INPUT_MAX_PX`.
	 *
	 * Sets height to `auto` first so `scrollHeight` reflects the
	 * shrunk content, then clamps to the max. Past the cap the
	 * textarea's internal overflow handles scrolling.
	 */
	private autoGrow(el: HTMLTextAreaElement): void {
		el.style.height = "auto";
		el.style.height = `${Math.min(el.scrollHeight, INPUT_MAX_PX)}px`;
	}

	private onInputEvent(e: Event): void {
		const el = e.target as HTMLTextAreaElement;
		this.inputValue = el.value;
		this.autoGrow(el);
	}

	private onKeyDown(e: KeyboardEvent): void {
		if (e.key !== "Enter") {
			return;
		}
		// IME composition: Enter commits the candidate — never submit.
		// Critical for Chinese/Japanese/Korean input.
		if (e.isComposing || e.keyCode === 229) {
			return;
		}
		// Kimi-style: plain Enter submits, Shift+Enter inserts a newline.
		if (e.shiftKey) {
			return;
		}
		e.preventDefault();
		this.sendMessage();
	}

	private interrupt(): void {
		if (!this.ws) {
			return;
		}
		this.ws.send({ type: "user.interrupt" });
		this.inFlight = false;
		this.streamingMessage = null;
	}

	private toggleTheme(): void {
		const next = document.documentElement.classList.contains("dark") ? "light" : "dark";
		applyTheme(next);
	}

	private pushToast(kind: Toast["kind"], text: string): void {
		const id = this.nextToastId++;
		this.toasts = [...this.toasts, { id, kind, text }];
		// Bug #71: only info toasts auto-dismiss. Error toasts stay until
		// the user acknowledges them (click anywhere on the toast or the
		// close glyph). Otherwise a 6s auto-hide buries root-cause info
		// like a missing API key before the user reads it.
		if (kind === "info") {
			window.setTimeout(() => {
				this.toasts = this.toasts.filter((t) => t.id !== id);
			}, 6000);
		}
	}

	// -- render -----------------------------------------------------------

	private renderToolBlocks(): TemplateResult[] {
		const blocks: TemplateResult[] = [];
		for (const tc of this.toolCallsById.values()) {
			const variant = tc.ok === false ? "error" : "default";
			blocks.push(html`<div class="mb-2">
				<div class="text-xs text-muted-foreground mb-1">tool: ${tc.name}${tc.ok === undefined ? " (running…)" : ""}</div>
				<console-block .content=${tc.output} .variant=${variant}></console-block>
			</div>`);
		}
		return blocks;
	}

	override render(): TemplateResult {
		// Connection state moved into the sidebar footer dot (#114); the
		// chat header no longer renders a duplicate indicator.
		const empty = this.messages.length === 0 && this.streamingMessage === null;
		const quickStart = ["Summarize a file", "Generate a plan", "Review code diff"];

		return html`
			<div class="yaya-chat mx-auto flex w-full max-w-3xl flex-col gap-3 p-4">
				${empty
					? html`<section class="yaya-hero">
							<h1 class="yaya-hero-title">yaya</h1>
							<p class="yaya-hero-sub">A kernel-style agent that grows itself.</p>
							<div class="yaya-chips">
								${quickStart.map(
									(q) => html`<button class="yaya-chip" @click=${() => this.fillPrompt(q)}>${q}</button>`,
								)}
							</div>
						</section>`
					: nothing}

				<section class="flex flex-col gap-2">
					${this.messages.map((m) => {
						if (m.role === "user") {
							const text = typeof m.content === "string" ? m.content : m.content.map((c) => c.text).join("");
							return html`<yaya-bubble role="user" content=${text}></yaya-bubble>`;
						}
						if (m.role === "assistant") {
							const text = m.content
								.filter((c): c is TextContent => c.type === "text")
								.map((c) => c.text)
								.join("");
							return text ? html`<yaya-bubble role="assistant" content=${text}></yaya-bubble>` : nothing;
						}
						// `toolResult` bubbles render via the console blocks below.
						return nothing;
					})}
					${this.streamingMessage
						? html`<yaya-bubble
								role="assistant"
								content=${this.streamingMessage.content
									.filter((c): c is TextContent => c.type === "text")
									.map((c) => c.text)
									.join("")}
							></yaya-bubble>`
						: nothing}
					${this.renderToolBlocks()}
				</section>

				<section class="yaya-composer">
					<textarea
						class="yaya-input"
						rows="3"
						placeholder="Message yaya…"
						.value=${this.inputValue}
						?disabled=${this.inFlight}
						@input=${(e: Event) => this.onInputEvent(e)}
						@keydown=${(e: KeyboardEvent) => this.onKeyDown(e)}
					></textarea>
					${this.inFlight
						? html`<button
								class="yaya-send-btn is-interrupt"
								aria-label="interrupt"
								title="Interrupt"
								@click=${() => this.interrupt()}
							>
								<svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
									<rect x="4" y="4" width="8" height="8" rx="1" />
								</svg>
							</button>`
						: html`<button
								class="yaya-send-btn"
								aria-label="send message"
								title="Send"
								?disabled=${this.inputValue.trim().length === 0}
								@click=${() => this.sendMessage()}
							>
								<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
									<path d="M8 13 V3 M3 8 L8 3 L13 8" />
								</svg>
							</button>`}
				</section>

				<div class="fixed right-4 top-4 flex max-w-xs flex-col gap-2">
					${this.toasts.map(
						(t) => html`<div
							class="relative flex items-start gap-2 rounded border border-border bg-background px-3 py-2 pr-6 text-xs shadow ${t.kind ===
							"error"
								? "text-destructive"
								: "text-foreground"}"
							@click=${() => {
								this.toasts = this.toasts.filter((x) => x.id !== t.id);
							}}
						>
							<span class="flex-1">${t.text}</span>
							<button
								aria-label="dismiss"
								class="absolute right-1 top-1 px-1 leading-none text-muted-foreground hover:text-foreground"
								@click=${(e: MouseEvent) => {
									e.stopPropagation();
									this.toasts = this.toasts.filter((x) => x.id !== t.id);
								}}
							>
								×
							</button>
						</div>`,
					)}
				</div>
			</div>
		`;
	}
}

/**
 * Minimal chat bubble. Rendered in light DOM so the shared Tailwind
 * stylesheet on the host document applies without cloning it into a
 * shadow root. We render our own bubbles rather than pi-web-ui's
 * `<message-list>` because the upstream component consumes pi-ai's
 * `AgentMessage` shape, which passes our TypeScript structural check
 * but silently renders blank at runtime (bug #71).
 */
@customElement("yaya-bubble")
export class YayaBubble extends LitElement {
	@property({ type: String }) override role: "user" | "assistant" = "user";
	@property({ type: String }) content = "";

	protected override createRenderRoot(): HTMLElement | DocumentFragment {
		return this;
	}

	override render(): TemplateResult {
		const isUser = this.role === "user";
		const align = isUser ? "justify-end" : "justify-start";
		const skin = isUser ? "bg-primary text-primary-foreground" : "bg-muted text-foreground";
		return html`
			<div class="flex ${align} my-2">
				<div class="max-w-[75%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm ${skin}">${this.content}</div>
			</div>
		`;
	}
}

declare global {
	interface HTMLElementTagNameMap {
		"yaya-chat": YayaChat;
		"yaya-bubble": YayaBubble;
	}
}
