/**
 * Stub for `@mariozechner/pi-web-ui/dist/tools/index.js`.
 *
 * The upstream module side-effect-auto-registers tool renderers that
 * transitively pull provider SDKs, lmstudio, ollama, pdfjs, and an
 * iframe sandbox component — all of which either violate the
 * Dependency Rule (lesson 27) or add hundreds of KB that the yaya
 * kernel never uses. See AGENT.md for the full architectural note.
 *
 * We still need `renderTool`, `registerToolRenderer`,
 * `getToolRenderer`, and `setShowJsonMode` as exports because
 * `Messages.ts` calls them. This stub provides minimal
 * implementations:
 *   - `renderTool` returns an empty template; our `<yaya-chat>`
 *     shell renders tool output via `<console-block>` directly from
 *     `tool.start` / `tool.result` frames, so the assistant-message
 *     inline path is intentionally inert.
 *   - `registerToolRenderer` / `getToolRenderer` keep a real Map so
 *     any plugin code that registers continues to work.
 */

import { html, type TemplateResult } from "lit";

export interface ToolRenderer {
	render(
		params: unknown,
		result: unknown,
		isStreaming: boolean,
	): { content: TemplateResult; isCustom: boolean };
}

const toolRenderers = new Map<string, ToolRenderer>();

export function registerToolRenderer(name: string, renderer: ToolRenderer): void {
	toolRenderers.set(name, renderer);
}

export function getToolRenderer(name: string): ToolRenderer | undefined {
	return toolRenderers.get(name);
}

export function setShowJsonMode(_enabled: boolean): void {
	// No-op: yaya renders tool output via `<console-block>` from the
	// WS `tool.result` frames; the JSON/default toggle upstream uses
	// is irrelevant here.
}

export function renderTool(
	_toolName: string,
	_params: unknown,
	_result: unknown,
	_isStreaming: boolean,
): { content: TemplateResult; isCustom: boolean } {
	return { content: html``, isCustom: false };
}
