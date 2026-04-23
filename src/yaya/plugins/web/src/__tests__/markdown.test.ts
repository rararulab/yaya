/**
 * Unit tests for ``renderMarkdown`` (#184).
 *
 * The chat adapter had been dumping LLM content into a
 * ``whitespace-pre-wrap`` div — markdown tables / bold / lists
 * showed as raw text. This helper routes content through marked +
 * DOMPurify. Tests pin both positive (the common markdown the model
 * produces gets rendered) and negative (script / event-handler
 * injection is stripped) shapes so a dependency bump cannot silently
 * open an XSS hole.
 */

import { describe, expect, it } from "vitest";
import { renderMarkdown } from "../markdown.js";

describe("renderMarkdown", () => {
	it("returns an empty string for empty input", () => {
		expect(renderMarkdown("")).toBe("");
	});

	it("renders GFM tables", () => {
		const html = renderMarkdown(
			"| col | val |\n|-----|-----|\n| a   | 1   |",
		);
		expect(html).toContain("<table>");
		expect(html).toContain("<th>col</th>");
		expect(html).toContain("<td>a</td>");
	});

	it("renders bold, lists, and inline code", () => {
		const html = renderMarkdown(
			"**yes**\n\n1. one\n2. two\n\nhere is `code`",
		);
		expect(html).toContain("<strong>yes</strong>");
		expect(html).toContain("<ol>");
		expect(html).toContain("<li>one</li>");
		expect(html).toContain("<code>code</code>");
	});

	it("renders blockquotes", () => {
		const html = renderMarkdown("> heads-up");
		expect(html).toContain("<blockquote>");
		expect(html).toContain("heads-up");
	});

	it("strips <script> tags injected via raw HTML", () => {
		const html = renderMarkdown("safe\n\n<script>alert(1)</script>");
		expect(html).not.toContain("<script");
		expect(html).not.toContain("alert(1)");
		expect(html).toContain("safe");
	});

	it("drops event-handler attributes on img-like injection", () => {
		// Marked passes raw HTML through; DOMPurify must scrub the
		// handler. Image tags themselves are not in the allowlist, but
		// checking onerror absence guards against future allowlist
		// loosening.
		const html = renderMarkdown(
			"<img src=x onerror=alert(1)>",
		);
		expect(html).not.toContain("onerror");
		expect(html).not.toContain("alert");
	});

	it("forces target=_blank rel=noopener on anchors", () => {
		const html = renderMarkdown("[yaya](https://example.com)");
		expect(html).toContain("href=\"https://example.com\"");
		expect(html).toContain("target=\"_blank\"");
		expect(html).toContain("rel=\"noopener noreferrer\"");
	});

	it("renders newlines as <br> so ReAct output stays compact", () => {
		const html = renderMarkdown("line one\nline two");
		expect(html).toContain("<br>");
	});
});
