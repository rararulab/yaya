/**
 * Sidebar regression tests for issue #114.
 *
 * Covers the collapse toggle, localStorage persistence, and the
 * sidebar-footer connection-status dot's four states (including
 * `connecting` so the initial handshake renders amber, not red).
 * The component is mounted into the document body so Lit renders
 * into light DOM — `app-shell.ts` opts out of shadow DOM via
 * `createRenderRoot`, which lets us query its markup with plain
 * `querySelector`.
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import "../app-shell.js";
import type { YayaApp } from "../app-shell.js";

const SIDEBAR_KEY = "yaya.sidebar.collapsed";

function mount(): YayaApp {
	const el = document.createElement("yaya-app") as YayaApp;
	document.body.appendChild(el);
	return el;
}

async function flush(el: YayaApp): Promise<void> {
	// Lit schedules async updates; `updateComplete` resolves after the
	// next render pass, which is the contract we need to observe DOM
	// changes that followed a state mutation.
	await el.updateComplete;
}

describe("yaya-app sidebar (#114)", () => {
	beforeEach(() => {
		window.localStorage.clear();
	});

	afterEach(() => {
		for (const node of Array.from(document.querySelectorAll("yaya-app"))) {
			node.remove();
		}
		window.localStorage.clear();
	});

	it("toggle flips data-collapsed on the sidebar root", async () => {
		const el = mount();
		await flush(el);

		const sidebar = el.querySelector<HTMLElement>(".yaya-sidebar");
		expect(sidebar).not.toBeNull();
		expect(sidebar?.getAttribute("data-collapsed")).toBe("false");

		const toggle = el.querySelector<HTMLButtonElement>(".yaya-sidebar-toggle");
		expect(toggle).not.toBeNull();
		toggle?.click();
		await flush(el);

		expect(
			el.querySelector<HTMLElement>(".yaya-sidebar")?.getAttribute("data-collapsed"),
		).toBe("true");

		toggle?.click();
		await flush(el);
		expect(
			el.querySelector<HTMLElement>(".yaya-sidebar")?.getAttribute("data-collapsed"),
		).toBe("false");
	});

	it("persists collapsed state to localStorage", async () => {
		const el = mount();
		await flush(el);

		const toggle = el.querySelector<HTMLButtonElement>(".yaya-sidebar-toggle");
		toggle?.click();
		await flush(el);
		expect(window.localStorage.getItem(SIDEBAR_KEY)).toBe("1");

		toggle?.click();
		await flush(el);
		expect(window.localStorage.getItem(SIDEBAR_KEY)).toBe("0");
	});

	it("reads persisted collapsed=1 on mount", async () => {
		window.localStorage.setItem(SIDEBAR_KEY, "1");
		const el = mount();
		await flush(el);

		expect(
			el.querySelector<HTMLElement>(".yaya-sidebar")?.getAttribute("data-collapsed"),
		).toBe("true");
	});

	it("setConnectionStatus round-trips through all four states", async () => {
		const el = mount();
		await flush(el);

		const q = () =>
			el.querySelector<HTMLElement>('[data-testid="sidebar-status"]');

		// Default is "connecting" so the dot is amber during the initial
		// handshake, not red (which would be misleading).
		expect(q()?.getAttribute("data-state")).toBe("connecting");
		expect(q()?.textContent).toContain("Connecting");

		el.setConnectionStatus("connected");
		await flush(el);
		expect(q()?.getAttribute("data-state")).toBe("connected");
		expect(q()?.textContent).toContain("Connected");

		el.setConnectionStatus("reconnecting");
		await flush(el);
		expect(q()?.getAttribute("data-state")).toBe("reconnecting");
		expect(q()?.textContent).toContain("Reconnecting");

		el.setConnectionStatus("disconnected");
		await flush(el);
		expect(q()?.getAttribute("data-state")).toBe("disconnected");
		expect(q()?.textContent).toContain("Disconnected");
	});

	it("routes window `yaya:connection-status` events into the sidebar", async () => {
		const el = mount();
		await flush(el);

		window.dispatchEvent(
			new CustomEvent("yaya:connection-status", {
				detail: { status: "connected" },
			}),
		);
		await flush(el);

		expect(
			el
				.querySelector<HTMLElement>('[data-testid="sidebar-status"]')
				?.getAttribute("data-state"),
		).toBe("connected");

		window.dispatchEvent(
			new CustomEvent("yaya:connection-status", {
				detail: { status: "connecting" },
			}),
		);
		await flush(el);
		expect(
			el
				.querySelector<HTMLElement>('[data-testid="sidebar-status"]')
				?.getAttribute("data-state"),
		).toBe("connecting");
	});

	it("gear + collapse-toggle + status all coexist in the sidebar", async () => {
		const el = mount();
		await flush(el);

		expect(el.querySelector(".yaya-sidebar-toggle")).not.toBeNull();
		expect(el.querySelector(".yaya-sidebar-settings")).not.toBeNull();
		expect(el.querySelector('[data-testid="sidebar-status"]')).not.toBeNull();

		// And after collapsing: all three remain in the DOM (labels hide
		// via CSS, not removal).
		const toggle = el.querySelector<HTMLButtonElement>(".yaya-sidebar-toggle");
		toggle?.click();
		await flush(el);

		expect(el.querySelector(".yaya-sidebar-toggle")).not.toBeNull();
		expect(el.querySelector(".yaya-sidebar-settings")).not.toBeNull();
		expect(el.querySelector('[data-testid="sidebar-status"]')).not.toBeNull();
	});
});
