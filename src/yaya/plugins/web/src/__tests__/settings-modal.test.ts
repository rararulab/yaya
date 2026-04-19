/**
 * Tests for the settings modal overlay (issue #113).
 *
 * jsdom caveats:
 * - `<dialog>.showModal()` is not implemented in jsdom. The component
 *   under test falls back to toggling the `open` attribute when
 *   `showModal` is unavailable, so the assertions here pivot on the
 *   `dialog.open` property rather than focus/backdrop behavior.
 * - jsdom's default synthetic MouseEvent has `target` set to the
 *   element `dispatchEvent` is called on, which lets us simulate a
 *   backdrop click by dispatching directly on the dialog element.
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import "../app-shell.js";
import type { YayaApp, YayaSettingsModal } from "../app-shell.js";

async function mountApp(): Promise<YayaApp> {
	const el = document.createElement("yaya-app") as YayaApp;
	document.body.appendChild(el);
	await el.updateComplete;
	return el;
}

function getModal(app: YayaApp): YayaSettingsModal {
	const modal = app.querySelector("yaya-settings-modal") as YayaSettingsModal | null;
	if (!modal) throw new Error("settings modal not mounted");
	return modal;
}

function getDialog(modal: YayaSettingsModal): HTMLDialogElement {
	const dialog = modal.querySelector("dialog") as HTMLDialogElement | null;
	if (!dialog) throw new Error("dialog element missing");
	return dialog;
}

describe("settings modal", () => {
	let app: YayaApp;

	beforeEach(async () => {
		// Reset location hash between tests.
		history.replaceState(null, "", location.pathname);
		app = await mountApp();
	});

	afterEach(() => {
		app.remove();
		history.replaceState(null, "", location.pathname);
	});

	it("open() shows the dialog", async () => {
		const modal = getModal(app);
		await modal.open();
		await modal.updateComplete;
		const dialog = getDialog(modal);
		expect(dialog.open).toBe(true);
		expect(modal.isOpen).toBe(true);
	});

	it("close() hides the dialog and dispatches close event", async () => {
		const modal = getModal(app);
		await modal.open();
		await modal.updateComplete;
		let closed = false;
		modal.addEventListener("yaya:settings-close", () => {
			closed = true;
		});
		modal.close();
		const dialog = getDialog(modal);
		expect(dialog.open).toBe(false);
		expect(closed).toBe(true);
	});

	it("ESC (native dialog cancel → close) dispatches close event", async () => {
		const modal = getModal(app);
		await modal.open();
		await modal.updateComplete;
		let closed = false;
		modal.addEventListener("yaya:settings-close", () => {
			closed = true;
		});
		// jsdom does not synthesize ESC → cancel → close on a <dialog>;
		// the browser does. We exercise the close event handler the
		// same way the browser would — by firing a native `close`
		// event on the dialog element.
		const dialog = getDialog(modal);
		dialog.dispatchEvent(new Event("close"));
		expect(closed).toBe(true);
	});

	it("backdrop click closes the dialog", async () => {
		const modal = getModal(app);
		await modal.open();
		await modal.updateComplete;
		const dialog = getDialog(modal);
		expect(dialog.open).toBe(true);
		// A click whose target is the dialog itself (not an inner card)
		// is treated as a backdrop click.
		dialog.dispatchEvent(new MouseEvent("click", { bubbles: true }));
		expect(dialog.open).toBe(false);
	});

	it("close button dismisses the dialog", async () => {
		const modal = getModal(app);
		await modal.open();
		await modal.updateComplete;
		const btn = modal.querySelector(
			".yaya-settings-dialog-close",
		) as HTMLButtonElement | null;
		expect(btn).not.toBeNull();
		btn?.click();
		const dialog = getDialog(modal);
		expect(dialog.open).toBe(false);
	});

	it("clearing the hash on close does not re-open the modal", async () => {
		const modal = getModal(app);
		await modal.open();
		await modal.updateComplete;
		modal.close();
		expect(location.hash).not.toBe("#/settings");
		expect(getDialog(modal).open).toBe(false);
	});

	it("#/settings hash opens the modal on connect", async () => {
		// Remount the component with the settings hash already set.
		app.remove();
		history.replaceState(null, "", `${location.pathname}#/settings`);
		app = await mountApp();
		const modal = getModal(app);
		// Wait for the microtask-queued open + lazy import.
		await new Promise((resolve) => setTimeout(resolve, 20));
		await modal.updateComplete;
		expect(getDialog(modal).open).toBe(true);
	});

	it("sidebar has no Settings nav item", async () => {
		const navItems = app.querySelectorAll(".yaya-nav .yaya-nav-item");
		const labels = Array.from(navItems).map((n) => n.textContent?.trim() ?? "");
		expect(labels.some((l) => l.toLowerCase().includes("settings"))).toBe(false);
	});

	it("sidebar footer exposes a settings button", async () => {
		const btn = app.querySelector(".yaya-sidebar-settings") as HTMLButtonElement | null;
		expect(btn).not.toBeNull();
	});
});
