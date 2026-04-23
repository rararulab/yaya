/**
 * Render markdown safely for assistant bubbles (#184).
 *
 * The chat adapter had been shoving assistant content into a
 * ``whitespace-pre-wrap`` div which dropped tables / bold / lists
 * as raw text. ReAct ``Final Answer:`` bodies use markdown liberally,
 * so without rendering the UI was unreadable the moment a model
 * answered in anything but plain prose.
 *
 * Pipeline: ``marked`` → HTML → ``DOMPurify`` sanitiser. The
 * sanitiser allowlist is deliberately conservative (no script, no
 * iframe, no event handlers, no javascript: / data: URIs) because
 * the rendered source is LLM output and we are a local-first
 * adapter — we trust the kernel, not the model.
 */

import DOMPurify from "dompurify";
import { Marked } from "marked";

// Use a per-module Marked instance so options do not bleed through
// a shared global. GFM is on for tables + fenced code; breaks=true
// turns a newline inside a paragraph into ``<br>`` which matches the
// whitespace-pre-wrap behaviour users previously relied on.
const marked = new Marked({
	gfm: true,
	breaks: true,
});

// DOMPurify allowlist. Anchor targets get rewritten to open in a
// new tab with no referrer (see sanitize hook below) so clicks on
// LLM-authored links cannot break session state.
const ALLOWED_TAGS = [
	"a",
	"blockquote",
	"br",
	"code",
	"em",
	"h2",
	"h3",
	"h4",
	"h5",
	"h6",
	"hr",
	"li",
	"ol",
	"p",
	"pre",
	"strong",
	"table",
	"tbody",
	"td",
	"th",
	"thead",
	"tr",
	"ul",
];

const ALLOWED_ATTR = ["href", "title", "class", "align"];

// Browser-only types (``HTMLAnchorElement`` / ``Element``) are
// unavailable under jsdom during vitest bootstrap, so we type
// DOMPurify's hook argument as ``Element`` via the lib.dom.d.ts
// that's already in the web package's tsconfig.
function hardenLinks(node: Element): void {
	if (node.tagName !== "A") {
		return;
	}
	const anchor = node as HTMLAnchorElement;
	anchor.setAttribute("target", "_blank");
	anchor.setAttribute("rel", "noopener noreferrer");
}

DOMPurify.addHook("afterSanitizeAttributes", hardenLinks);

/**
 * Convert an LLM markdown string into sanitised HTML.
 *
 * Returns an empty string for empty input so callers can branch on
 * ``result === ""`` without length checks.
 */
export function renderMarkdown(source: string): string {
	if (!source) {
		return "";
	}
	const rawHtml = marked.parse(source, { async: false }) as string;
	return DOMPurify.sanitize(rawHtml, {
		ALLOWED_TAGS,
		ALLOWED_ATTR,
		FORBID_TAGS: ["script", "style", "iframe", "object", "embed"],
		FORBID_ATTR: ["onerror", "onload", "onclick", "onmouseover", "srcdoc"],
	});
}
