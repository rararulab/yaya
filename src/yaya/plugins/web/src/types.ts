/**
 * Discriminated-union WebSocket frame types mirroring the adapter
 * side of `src/yaya/plugins/web/plugin.py::_event_to_frame`.
 *
 * Lesson #19 (compile-time enforcement of catalog drift): every new
 * kernel event kind must add a case here. An `assertNever(frame)`
 * switch in the consumer then forces a compile error until the
 * TypeScript side mirrors the Python catalog.
 */

export interface ToolCall {
	id: string;
	name: string;
	args: Record<string, unknown>;
}

/**
 * Shape of a message that pi-web-ui's `MessageList` can render.
 *
 * We deliberately avoid importing any type alias from the upstream
 * agent-core package, even as a type-only import. AGENT.md section
 * 4 and lesson 27 forbid the dependency because the Dependency Rule
 * is about directional coupling, not about whether the symbol
 * survives compilation. Instead we declare a structural type
 * matching the upstream runtime shape (Message union: user /
 * assistant / toolResult with plain content arrays).
 */
export interface TextContent {
	type: "text";
	text: string;
}

export interface UserChatMessage {
	role: "user";
	content: string | TextContent[];
	timestamp: number;
}

export interface AssistantChatMessage {
	role: "assistant";
	content: (TextContent | { type: "toolCall"; id: string; name: string; arguments: Record<string, unknown> })[];
	api: string;
	provider: string;
	model: string;
	usage: {
		input: number;
		output: number;
		cacheRead: number;
		cacheWrite: number;
		totalTokens: number;
		cost: { input: number; output: number; cacheRead: number; cacheWrite: number; total: number };
	};
	stopReason: "stop" | "length" | "toolUse" | "error" | "aborted";
	timestamp: number;
}

export interface ToolResultChatMessage {
	role: "toolResult";
	toolCallId: string;
	toolName: string;
	content: TextContent[];
	isError: boolean;
	timestamp: number;
}

export type ChatMessage = UserChatMessage | AssistantChatMessage | ToolResultChatMessage;

// -- WebSocket frames ------------------------------------------------------

export type OutboundFrame =
	| { type: "user.message"; text: string }
	| { type: "user.interrupt" };

export type InboundFrame =
	| { type: "assistant.delta"; content: string; session_id: string }
	| { type: "assistant.done"; content: string; tool_calls: ToolCall[]; session_id: string }
	| {
			type: "tool.start";
			id: string;
			name: string;
			args: Record<string, unknown>;
			session_id: string;
	  }
	| {
			type: "tool.result";
			id: string;
			ok: boolean;
			value?: unknown;
			error?: string;
			session_id: string;
	  }
	| { type: "plugin.loaded"; name: string; version: string; category: string; session_id: string }
	| { type: "plugin.removed"; name: string; session_id: string }
	| { type: "plugin.error"; name: string; error: string; session_id: string }
	| { type: "kernel.ready"; version: string; session_id: string }
	| { type: "kernel.shutdown"; reason: string; session_id: string }
	| { type: "kernel.error"; source: string; message: string; session_id: string };

/** Synthetic meta-frames derived from the WS socket state. */
export type MetaFrame =
	| { type: "ws.connected" }
	| { type: "ws.disconnected" };

export type Frame = InboundFrame | MetaFrame;

/**
 * Shape of a single item returned by ``GET /api/sessions/{id}/frames``.
 *
 * Deliberately parallel to :data:`InboundFrame` so the replay reducer
 * runs the same state transitions the live WS path runs — see #162.
 * The only shape not present on the WS catalog is ``user.message``,
 * which exists here so past-user bubbles can be reconstructed during
 * hydration (the live path already has the user text locally when
 * the browser sends it).
 */
export type HistoryFrame =
	| { kind: "user.message"; text: string }
	| { kind: "assistant.done"; content: string; tool_calls: ToolCall[] }
	| { kind: "tool.start"; id: string; name: string; args: Record<string, unknown> }
	| {
			kind: "tool.result";
			id: string;
			ok: boolean;
			value?: unknown;
			error?: string;
	  };

/** Exhaustiveness helper — see lesson #19. */
export function assertNever(x: never): never {
	throw new Error(`Unexpected variant: ${JSON.stringify(x)}`);
}
