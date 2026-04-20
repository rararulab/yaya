/**
 * Tests for the D4d instance-centric LLM Providers settings tab.
 *
 * All HTTP calls are stubbed via ``globalThis.fetch`` so the tests run
 * fully inside jsdom — no network, no Python backend. The test rig
 * installs a small fetch router that dispatches on ``(method, url)``
 * and hands back per-scenario fixtures.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import "../settings-view.js";
import type { YayaSettings } from "../settings-view.js";
import { isValidInstanceId } from "../api.js";

type FetchHandler = (
	method: string,
	url: string,
	body: unknown,
) => Response | Promise<Response>;

function json(payload: unknown, init: ResponseInit = {}): Response {
	return new Response(JSON.stringify(payload), {
		status: init.status ?? 200,
		headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
	});
}

function installFetchStub(handler: FetchHandler): void {
	globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
		const url = typeof input === "string" ? input : input.toString();
		const method = (init?.method ?? "GET").toUpperCase();
		let body: unknown;
		if (typeof init?.body === "string") {
			try {
				body = JSON.parse(init.body);
			} catch {
				body = init.body;
			}
		}
		return handler(method, url, body);
	}) as typeof fetch;
}

async function mount(): Promise<YayaSettings> {
	const el = document.createElement("yaya-settings") as YayaSettings;
	document.body.appendChild(el);
	await el.updateComplete;
	// The component kicks loadTab() from connectedCallback; that is
	// async (two fetch calls in parallel). Flush microtasks until the
	// first render observes the seeded rows.
	await new Promise((resolve) => setTimeout(resolve, 10));
	await el.updateComplete;
	return el;
}

const SEED_PROVIDERS = [
	{
		id: "llm-openai",
		plugin: "llm-openai",
		label: "OpenAI GPT-4",
		active: true,
		config: { model: "gpt-4", api_key: "****1234" },
		config_schema: {
			type: "object",
			properties: {
				api_key: { type: "string", title: "API key" },
				model: { type: "string", title: "Model" },
			},
		},
	},
	{
		id: "llm-openai-2",
		plugin: "llm-openai",
		label: "OpenAI GPT-3.5",
		active: false,
		config: { model: "gpt-3.5-turbo", api_key: "****5678" },
		config_schema: null,
	},
	{
		id: "llm-echo",
		plugin: "llm-echo",
		label: "Echo",
		active: false,
		config: {},
		config_schema: null,
	},
];

const SEED_PLUGINS = [
	{
		name: "llm-openai",
		category: "llm-provider",
		status: "loaded",
		version: "0.1.0",
		enabled: true,
		config_schema: {
			type: "object",
			properties: {
				api_key: { type: "string", title: "API key" },
				model: { type: "string", title: "Model" },
			},
		},
	},
	{
		name: "llm-echo",
		category: "llm-provider",
		status: "loaded",
		version: "0.1.0",
		enabled: true,
	},
];

describe("settings-view instance UI", () => {
	let el: YayaSettings | null = null;

	afterEach(() => {
		el?.remove();
		el = null;
		vi.restoreAllMocks();
	});

	beforeEach(() => {
		// Default handler: seeded list + plugins. Per-test overrides
		// call installFetchStub again before mount.
		installFetchStub((method, url) => {
			if (method === "GET" && url === "/api/llm-providers") {
				return json(SEED_PROVIDERS);
			}
			if (method === "GET" && url === "/api/plugins") {
				return json(SEED_PLUGINS);
			}
			return new Response("not found", { status: 404 });
		});
	});

	it("renders one row per instance with active radio set", async () => {
		el = await mount();
		const rows = el.querySelectorAll(".yaya-row[data-instance-id]");
		expect(rows).toHaveLength(3);
		const active = el.querySelector(
			'[data-instance-id="llm-openai"] input[type="radio"]',
		) as HTMLInputElement;
		expect(active.checked).toBe(true);
		const inactive = el.querySelector(
			'[data-instance-id="llm-openai-2"] input[type="radio"]',
		) as HTMLInputElement;
		expect(inactive.checked).toBe(false);
	});

	it("clicking a non-active radio fires PATCH /active with the instance id", async () => {
		let activePatch: unknown = null;
		installFetchStub((method, url, body) => {
			if (method === "GET" && url === "/api/llm-providers") return json(SEED_PROVIDERS);
			if (method === "GET" && url === "/api/plugins") return json(SEED_PLUGINS);
			if (method === "PATCH" && url === "/api/llm-providers/active") {
				activePatch = body;
				const next = SEED_PROVIDERS.map((p) => ({ ...p, active: p.id === "llm-openai-2" }));
				return json(next);
			}
			return new Response("not found", { status: 404 });
		});
		el = await mount();
		const radio = el.querySelector(
			'[data-instance-id="llm-openai-2"] input[type="radio"]',
		) as HTMLInputElement;
		radio.click();
		await new Promise((resolve) => setTimeout(resolve, 5));
		await el.updateComplete;
		expect(activePatch).toEqual({ name: "llm-openai-2" });
	});

	it("expanding a row renders the schema-driven form + label + action buttons", async () => {
		el = await mount();
		const configureBtn = el.querySelector(
			'[data-instance-id="llm-openai"] .yaya-link',
		) as HTMLButtonElement;
		configureBtn.click();
		await el.updateComplete;
		const body = el.querySelector('[data-instance-id="llm-openai"] .yaya-row-body');
		expect(body).not.toBeNull();
		// Schema labels render (API key, Model) plus inline Label field.
		const labels = Array.from(body?.querySelectorAll(".yaya-form-label") ?? []).map((s) =>
			s.textContent?.trim(),
		);
		expect(labels).toContain("Label");
		expect(labels).toContain("API key");
		expect(labels).toContain("Model");
		expect(body?.querySelector(".yaya-btn")?.textContent?.trim()).toBe("Save");
		expect(body?.querySelector(".yaya-btn-danger")?.textContent?.trim()).toBe("Delete");
	});

	it("Save sends PATCH with only changed fields", async () => {
		let patchBody: unknown = null;
		installFetchStub((method, url, body) => {
			if (method === "GET" && url === "/api/llm-providers") return json(SEED_PROVIDERS);
			if (method === "GET" && url === "/api/plugins") return json(SEED_PLUGINS);
			if (method === "PATCH" && url === "/api/llm-providers/llm-openai") {
				patchBody = body;
				return json({ ...SEED_PROVIDERS[0], label: "Primary" });
			}
			return new Response("not found", { status: 404 });
		});
		el = await mount();
		(el.querySelector('[data-instance-id="llm-openai"] .yaya-link') as HTMLButtonElement).click();
		await el.updateComplete;
		const labelInput = el.querySelector(
			'[data-instance-id="llm-openai"] .yaya-row-body input[type="text"]',
		) as HTMLInputElement;
		labelInput.value = "Primary";
		labelInput.dispatchEvent(new Event("change"));
		await el.updateComplete;
		const saveBtn = Array.from(
			el.querySelectorAll('[data-instance-id="llm-openai"] .yaya-row-actions .yaya-btn'),
		).find((b) => b.textContent?.trim() === "Save") as HTMLButtonElement;
		saveBtn.click();
		await new Promise((resolve) => setTimeout(resolve, 5));
		await el.updateComplete;
		expect(patchBody).toEqual({ label: "Primary" });
	});

	it("Delete with 409 surfaces inline error", async () => {
		installFetchStub((method, url) => {
			if (method === "GET" && url === "/api/llm-providers") return json(SEED_PROVIDERS);
			if (method === "GET" && url === "/api/plugins") return json(SEED_PLUGINS);
			if (method === "DELETE" && url === "/api/llm-providers/llm-openai") {
				return json(
					{ detail: "switch active provider before deleting this one" },
					{ status: 409 },
				);
			}
			return new Response("not found", { status: 404 });
		});
		el = await mount();
		(el.querySelector('[data-instance-id="llm-openai"] .yaya-link') as HTMLButtonElement).click();
		await el.updateComplete;
		(el.querySelector('[data-instance-id="llm-openai"] .yaya-btn-danger') as HTMLButtonElement).click();
		await el.updateComplete;
		const confirmBtn = el.querySelector(".yaya-confirm-delete") as HTMLButtonElement;
		expect(confirmBtn).not.toBeNull();
		confirmBtn.click();
		await new Promise((resolve) => setTimeout(resolve, 5));
		await el.updateComplete;
		const errBox = el.querySelector('[data-instance-id="llm-openai"] .yaya-row-error');
		expect(errBox?.textContent).toMatch(/switch active provider/);
	});

	it("Test connection fires POST and records result as Connected", async () => {
		installFetchStub((method, url) => {
			if (method === "GET" && url === "/api/llm-providers") return json(SEED_PROVIDERS);
			if (method === "GET" && url === "/api/plugins") return json(SEED_PLUGINS);
			if (method === "POST" && url === "/api/llm-providers/llm-openai/test") {
				return json({ ok: true, latency_ms: 42 });
			}
			return new Response("not found", { status: 404 });
		});
		el = await mount();
		const testBtn = el.querySelector(
			'[data-instance-id="llm-openai"] .yaya-test-btn',
		) as HTMLButtonElement;
		testBtn.click();
		await new Promise((resolve) => setTimeout(resolve, 5));
		await el.updateComplete;
		const dot = el.querySelector('[data-instance-id="llm-openai"] .yaya-status-dot');
		expect(dot?.classList.contains("yaya-status-connected")).toBe(true);
	});

	it("Add instance happy path: POST with supplied id, then re-fetch + expand new row", async () => {
		let postBody: unknown = null;
		let listCalls = 0;
		installFetchStub((method, url, body) => {
			if (method === "GET" && url === "/api/llm-providers") {
				listCalls += 1;
				if (listCalls === 1) return json(SEED_PROVIDERS);
				return json([
					...SEED_PROVIDERS,
					{
						id: "llm-openai-3",
						plugin: "llm-openai",
						label: "Third",
						active: false,
						config: {},
						config_schema: null,
					},
				]);
			}
			if (method === "GET" && url === "/api/plugins") return json(SEED_PLUGINS);
			if (method === "POST" && url === "/api/llm-providers") {
				postBody = body;
				return json(
					{
						id: "llm-openai-3",
						plugin: "llm-openai",
						label: "Third",
						active: false,
						config: {},
						config_schema: null,
					},
					{ status: 201 },
				);
			}
			return new Response("not found", { status: 404 });
		});
		el = await mount();
		(el.querySelector(".yaya-add-instance") as HTMLButtonElement).click();
		await el.updateComplete;
		const idInput = el.querySelector(".yaya-modal-card input[type='text']") as HTMLInputElement;
		idInput.value = "llm-openai-3";
		idInput.dispatchEvent(new Event("input"));
		await el.updateComplete;
		(el.querySelector(".yaya-add-submit") as HTMLButtonElement).click();
		await new Promise((resolve) => setTimeout(resolve, 5));
		await el.updateComplete;
		expect(postBody).toMatchObject({ plugin: "llm-openai", id: "llm-openai-3" });
		const rows = el.querySelectorAll(".yaya-row[data-instance-id]");
		expect(rows).toHaveLength(4);
	});

	it("Add instance duplicate id: surfaces inline error", async () => {
		installFetchStub((method, url) => {
			if (method === "GET" && url === "/api/llm-providers") return json(SEED_PROVIDERS);
			if (method === "GET" && url === "/api/plugins") return json(SEED_PLUGINS);
			if (method === "POST" && url === "/api/llm-providers") {
				return json(
					{ detail: "llm-provider instance already exists: llm-openai" },
					{ status: 409 },
				);
			}
			return new Response("not found", { status: 404 });
		});
		el = await mount();
		(el.querySelector(".yaya-add-instance") as HTMLButtonElement).click();
		await el.updateComplete;
		const idInput = el.querySelector(".yaya-modal-card input[type='text']") as HTMLInputElement;
		idInput.value = "llm-openai";
		idInput.dispatchEvent(new Event("input"));
		await el.updateComplete;
		(el.querySelector(".yaya-add-submit") as HTMLButtonElement).click();
		await new Promise((resolve) => setTimeout(resolve, 5));
		await el.updateComplete;
		const err = el.querySelector(".yaya-modal-card .yaya-row-error");
		expect(err?.textContent).toMatch(/already exists/);
	});

	it("Add instance unknown plugin: surfaces 400 inline", async () => {
		installFetchStub((method, url) => {
			if (method === "GET" && url === "/api/llm-providers") return json(SEED_PROVIDERS);
			if (method === "GET" && url === "/api/plugins") return json(SEED_PLUGINS);
			if (method === "POST" && url === "/api/llm-providers") {
				return json(
					{ detail: "'ghost' is not a loaded llm-provider plugin" },
					{ status: 400 },
				);
			}
			return new Response("not found", { status: 404 });
		});
		el = await mount();
		(el.querySelector(".yaya-add-instance") as HTMLButtonElement).click();
		await el.updateComplete;
		const idInput = el.querySelector(".yaya-modal-card input[type='text']") as HTMLInputElement;
		idInput.value = "new-instance";
		idInput.dispatchEvent(new Event("input"));
		await el.updateComplete;
		(el.querySelector(".yaya-add-submit") as HTMLButtonElement).click();
		await new Promise((resolve) => setTimeout(resolve, 5));
		await el.updateComplete;
		const err = el.querySelector(".yaya-modal-card .yaya-row-error");
		expect(err?.textContent).toMatch(/not a loaded llm-provider/);
	});

	it("client-side id validator rejects dots and short ids", () => {
		expect(isValidInstanceId("llm-openai")).toBe(true);
		expect(isValidInstanceId("openai.gpt4")).toBe(false);
		expect(isValidInstanceId("ab")).toBe(false);
		expect(isValidInstanceId("-lead")).toBe(false);
		expect(isValidInstanceId("trail-")).toBe(false);
		expect(isValidInstanceId("CAPS")).toBe(false);
	});
});
