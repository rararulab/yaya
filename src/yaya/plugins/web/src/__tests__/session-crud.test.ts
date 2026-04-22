/**
 * Sidebar session CRUD tests for issue #161.
 *
 * Covers the ⋯ action menu, the inline rename editor, and the
 * confirm-then-delete affordance. Uses a `fetch` stub so we assert the
 * HTTP verbs the sidebar issues against ``/api/sessions`` without
 * standing up the Python backend.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import "../app-shell.js";
import type { SessionRow, YayaApp } from "../app-shell.js";

function row(overrides: Partial<SessionRow> = {}): SessionRow {
	return {
		id: "sid-1",
		tape_name: "ws__sid-1",
		created_at: "2026-04-21T00:00:00Z",
		entry_count: 3,
		last_anchor: "session/start",
		preview: "hello there",
		name: null,
		...overrides,
	} as SessionRow;
}

function mount(): YayaApp {
	const el = document.createElement("yaya-app") as YayaApp;
	document.body.appendChild(el);
	return el;
}

async function flush(el: YayaApp): Promise<void> {
	await el.updateComplete;
}

function installFetchStub(handler: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>): void {
	(globalThis as unknown as { fetch: typeof fetch }).fetch = vi.fn(handler) as unknown as typeof fetch;
}

function jsonResponse(body: unknown, status = 200): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { "Content-Type": "application/json" },
	});
}

describe("yaya-app sidebar session CRUD (#161)", () => {
	beforeEach(() => {
		window.localStorage.clear();
	});

	afterEach(() => {
		for (const node of Array.from(document.querySelectorAll("yaya-app"))) {
			node.remove();
		}
		window.localStorage.clear();
		vi.restoreAllMocks();
	});

	it("renders name when set, preferring it over preview", async () => {
		const sessions: SessionRow[] = [row({ id: "a", name: "Grocery list", preview: "first message" })];
		installFetchStub(async () => jsonResponse({ sessions }));
		const el = mount();
		await flush(el);
		// Let the initial fetch resolve.
		await new Promise((r) => setTimeout(r, 0));
		await flush(el);
		const btn = el.querySelector<HTMLButtonElement>(".yaya-history-item");
		expect(btn?.textContent?.trim()).toBe("Grocery list");
	});

	it("⋯ menu exposes Rename and Delete actions", async () => {
		installFetchStub(async () => jsonResponse({ sessions: [row()] }));
		const el = mount();
		await flush(el);
		await new Promise((r) => setTimeout(r, 0));
		await flush(el);
		const menuBtn = el.querySelector<HTMLButtonElement>('[data-testid="row-menu-btn"]');
		expect(menuBtn).not.toBeNull();
		menuBtn?.click();
		await flush(el);
		expect(el.querySelector('[data-testid="rename-action"]')).not.toBeNull();
		expect(el.querySelector('[data-testid="delete-action"]')).not.toBeNull();
	});

	it("delete flow issues DELETE /api/sessions/:id after confirm", async () => {
		const calls: Array<{ url: string; method: string }> = [];
		installFetchStub(async (input, init) => {
			const url = typeof input === "string" ? input : (input as URL).toString();
			const method = init?.method ?? "GET";
			calls.push({ url, method });
			if (method === "GET") {
				return jsonResponse({ sessions: [row({ id: "to-delete" })] });
			}
			return new Response(null, { status: 204 });
		});
		const el = mount();
		await flush(el);
		await new Promise((r) => setTimeout(r, 0));
		await flush(el);

		el.querySelector<HTMLButtonElement>('[data-testid="row-menu-btn"]')?.click();
		await flush(el);
		el.querySelector<HTMLButtonElement>('[data-testid="delete-action"]')?.click();
		await flush(el);

		const confirmBtn = el.querySelector<HTMLButtonElement>('[data-testid="confirm-delete"]');
		expect(confirmBtn).not.toBeNull();
		confirmBtn?.click();
		// Let the async DELETE + refresh resolve.
		await new Promise((r) => setTimeout(r, 0));
		await flush(el);

		const deleteCall = calls.find((c) => c.method === "DELETE");
		expect(deleteCall?.url).toContain("/api/sessions/to-delete");
	});

	it("rename flow issues PATCH /api/sessions/:id with {name}", async () => {
		const calls: Array<{ url: string; method: string; body: string | null }> = [];
		installFetchStub(async (input, init) => {
			const url = typeof input === "string" ? input : (input as URL).toString();
			const method = init?.method ?? "GET";
			const body = typeof init?.body === "string" ? init.body : null;
			calls.push({ url, method, body });
			if (method === "GET") {
				return jsonResponse({ sessions: [row({ id: "to-rename" })] });
			}
			return jsonResponse({ id: "to-rename", name: "New label" });
		});
		const el = mount();
		await flush(el);
		await new Promise((r) => setTimeout(r, 0));
		await flush(el);

		el.querySelector<HTMLButtonElement>('[data-testid="row-menu-btn"]')?.click();
		await flush(el);
		el.querySelector<HTMLButtonElement>('[data-testid="rename-action"]')?.click();
		await flush(el);

		const input = el.querySelector<HTMLInputElement>(".yaya-history-rename");
		expect(input).not.toBeNull();
		if (!input) return;
		input.value = "New label";
		input.dispatchEvent(new Event("input"));
		input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" }));
		await new Promise((r) => setTimeout(r, 0));
		await flush(el);

		const patchCall = calls.find((c) => c.method === "PATCH");
		expect(patchCall?.url).toContain("/api/sessions/to-rename");
		expect(patchCall?.body).toContain("New label");
	});
});
